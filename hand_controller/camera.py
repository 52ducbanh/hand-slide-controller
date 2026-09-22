"""Production Camera Capture Abstraction for Hand Slide Controller.

Provides:
- CameraFrame: Application-owned immutable frame container with QPC timestamps.
- CameraSource: Abstract base class for camera capture.
- WinRTCameraSource: Event-driven native Windows Runtime MediaFrameReader with NV12 direct unpack.
- OpenCVMSMFCameraSource: OpenCV Media Foundation backend with constructor parameters.
- create_camera_source: Robust factory with automatic fallback from WinRT to MSMF.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
import asyncio
import ctypes
from dataclasses import dataclass
import sys
import threading
import time
from typing import Any, NamedTuple

import cv2
import numpy as np

from hand_controller.config import CameraConfig

# ---------------------------------------------------------------------------
# QPC Hardware Timing Utilities
# ---------------------------------------------------------------------------
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_qpc = ctypes.c_int64()
_freq = ctypes.c_int64()
_kernel32.QueryPerformanceFrequency(ctypes.byref(_freq))


def get_raw_qpc_sec() -> float:
    """Returns raw hardware timestamp in seconds using QueryPerformanceCounter."""
    _kernel32.QueryPerformanceCounter(ctypes.byref(_qpc))
    return _qpc.value / _freq.value


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
class CameraFrame(NamedTuple):
    """Application-owned camera frame container."""
    image_rgb: np.ndarray             # RGB uint8 array (H, W, 3) ready for MediaPipe
    frame_index: int                  # Monotonically increasing frame index
    capture_time: float               # time.perf_counter() local timestamp
    source_timestamp_sec: float | None # Hardware Media Source timestamp (SystemRelativeTime) in QPC seconds
    app_receive_qpc_sec: float        # Raw QPC timestamp at instant of TryAcquireLatestFrame()
    rgb_ready_time: float             # time.perf_counter() when RGB conversion completed


@dataclass(frozen=True)
class CameraDiagnostics:
    """Camera initialization and runtime diagnostics."""
    requested_backend: str
    selected_backend: str
    winrt_available: bool
    winrt_init_success: bool
    fallback_occurred: bool
    fallback_reason: str | None
    actual_width: int
    actual_height: int
    actual_fps: float
    actual_subtype: str


# ---------------------------------------------------------------------------
# NV12 Stride & Plane Unpacking Utility
# ---------------------------------------------------------------------------
def unpack_nv12_planes(
    src_bytes: memoryview | np.ndarray,
    width: int,
    height: int,
    plane0_stride: int,
    plane0_start: int,
    plane1_stride: int,
    plane1_start: int,
    scratch_nv12: np.ndarray | None = None,
) -> np.ndarray:
    """Unpacks NV12 buffer into a packed (height * 3 // 2, width) array.

    Handles:
    - Tight stride: stride == width and sequential layout -> zero-copy view.
    - Padded stride: stride > width -> row-by-row unpadding into scratch buffer.
    - Raises ValueError if source buffer size is insufficient.
    """
    total_y_bytes = height * plane0_stride
    uv_height = height // 2
    total_uv_bytes = uv_height * plane1_stride
    min_required = max(plane0_start + total_y_bytes, plane1_start + total_uv_bytes)

    src_len = len(src_bytes)
    if src_len < min_required:
        raise ValueError(
            f"Invalid buffer size for NV12: expected at least {min_required} bytes, got {src_len}"
        )

    raw_arr = np.frombuffer(src_bytes, dtype=np.uint8)

    # Tight stride fast-path (stride == width for both planes and standard layout)
    if (
        plane0_stride == width
        and plane1_stride == width
        and plane0_start == 0
        and plane1_start == width * height
    ):
        packed_len = width * height * 3 // 2
        return raw_arr[:packed_len].reshape((height * 3 // 2, width))

    # Padded stride path: unpad rows into scratch buffer
    target_shape = (height * 3 // 2, width)
    if scratch_nv12 is None or scratch_nv12.shape != target_shape:
        scratch_nv12 = np.empty(target_shape, dtype=np.uint8)

    # Copy Y plane rows (excluding row padding)
    for r in range(height):
        y_start = plane0_start + r * plane0_stride
        scratch_nv12[r, :] = raw_arr[y_start : y_start + width]

    # Copy UV plane rows (interleaved UV pairs, excluding padding)
    for r in range(uv_height):
        uv_start = plane1_start + r * plane1_stride
        scratch_nv12[height + r, :] = raw_arr[uv_start : uv_start + width]

    return scratch_nv12


# ---------------------------------------------------------------------------
# Abstract Base Class
# ---------------------------------------------------------------------------
class CameraSource(ABC):
    """Abstract base class for camera capture sources."""

    @abstractmethod
    def open(self) -> bool:
        """Opens and initializes the camera stream. Returns True on success."""
        ...

    @abstractmethod
    def read_latest(self, timeout_sec: float = 0.05) -> tuple[bool, CameraFrame | None]:
        """Reads the latest published camera frame. Non-blocking/bounded wait."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Stops streaming and releases all resources."""
        ...

    @property
    @abstractmethod
    def diagnostics(self) -> CameraDiagnostics:
        """Returns backend diagnostics."""
        ...


# ---------------------------------------------------------------------------
# OpenCV MSMF Camera Source (Production v2 Verified Baseline)
# ---------------------------------------------------------------------------
class OpenCVMSMFCameraSource(CameraSource):
    """OpenCV Media Foundation camera source."""

    def __init__(self, cfg: CameraConfig):
        self._cfg = cfg
        self._cap: cv2.VideoCapture | None = None
        self._frame_index = 0
        self._rgb_ring = [np.zeros((cfg.height, cfg.width, 3), dtype=np.uint8) for _ in range(3)]
        self._ring_idx = 0
        self._diag: CameraDiagnostics = CameraDiagnostics(
            requested_backend=cfg.backend,
            selected_backend="MSMF",
            winrt_available=False,
            winrt_init_success=False,
            fallback_occurred=(cfg.backend.upper() in ["AUTO", "WINRT"]),
            fallback_reason=None,
            actual_width=0,
            actual_height=0,
            actual_fps=0.0,
            actual_subtype="RGB24 (MSMF default)",
        )

    def open(self) -> bool:
        cfg = self._cfg
        cap: cv2.VideoCapture | None = None

        # Constructor parameters initialization
        try:
            params = [
                cv2.CAP_PROP_FRAME_WIDTH, cfg.width,
                cv2.CAP_PROP_FRAME_HEIGHT, cfg.height,
                cv2.CAP_PROP_FPS, cfg.fps,
            ]
            cap = cv2.VideoCapture(cfg.index, cv2.CAP_MSMF, params)
            if not cap.isOpened():
                cap.release()
                cap = None
        except Exception:
            if cap is not None:
                cap.release()
            cap = None

        # Sequential fallback if constructor parameter syntax not supported
        if cap is None or not cap.isOpened():
            cap = cv2.VideoCapture(cfg.index, cv2.CAP_MSMF)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
                cap.set(cv2.CAP_PROP_FPS, cfg.fps)

        if cap is None or not cap.isOpened():
            return False

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = float(cap.get(cv2.CAP_PROP_FPS))

        self._cap = cap
        self._diag = CameraDiagnostics(
            requested_backend=self._cfg.backend,
            selected_backend="MSMF",
            winrt_available=False,
            winrt_init_success=False,
            fallback_occurred=(self._cfg.backend.upper() in ["AUTO", "WINRT"]),
            fallback_reason=getattr(self, "_fallback_reason", None),
            actual_width=actual_w,
            actual_height=actual_h,
            actual_fps=actual_fps,
            actual_subtype="RGB24 (MSMF default)",
        )
        return True

    def read_latest(self, timeout_sec: float = 0.05) -> tuple[bool, CameraFrame | None]:
        if self._cap is None or not self._cap.isOpened():
            return False, None

        qpc_before = get_raw_qpc_sec()
        ret, frame_bgr = self._cap.read()
        if not ret or frame_bgr is None:
            return False, None

        self._frame_index += 1
        self._ring_idx = (self._ring_idx + 1) % 3
        rgb_slot = self._rgb_ring[self._ring_idx]

        cv2.flip(frame_bgr, 1, dst=frame_bgr)
        cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB, dst=rgb_slot)
        t_ready = time.perf_counter()

        cam_frame = CameraFrame(
            image_rgb=rgb_slot,
            frame_index=self._frame_index,
            capture_time=t_ready,
            source_timestamp_sec=None,  # Not observable on OpenCV MSMF
            app_receive_qpc_sec=qpc_before,
            rgb_ready_time=t_ready,
        )
        return True, cam_frame

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def diagnostics(self) -> CameraDiagnostics:
        return self._diag


# ---------------------------------------------------------------------------
# WinRT MediaFrameReader Camera Source (Event-Driven Production v3)
# ---------------------------------------------------------------------------
class WinRTCameraSource(CameraSource):
    """Event-driven Windows Runtime MediaFrameReader camera source."""

    def __init__(self, cfg: CameraConfig):
        self._cfg = cfg
        self._frame_index = 0
        self._rgb_ring = [np.zeros((cfg.height, cfg.width, 3), dtype=np.uint8) for _ in range(3)]
        self._ring_idx = 0
        self._nv12_scratch = np.empty((cfg.height * 3 // 2, cfg.width), dtype=np.uint8)

        self._lock = threading.Lock()
        self._new_frame_event = threading.Event()
        self._latest_frame: CameraFrame | None = None
        self._is_running = False

        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._mc: Any = None
        self._reader: Any = None
        self._event_token: Any = None

        self._diag = CameraDiagnostics(
            requested_backend=cfg.backend,
            selected_backend="WINRT",
            winrt_available=True,
            winrt_init_success=False,
            fallback_occurred=False,
            fallback_reason=None,
            actual_width=0,
            actual_height=0,
            actual_fps=0.0,
            actual_subtype="NV12",
        )

        # Performance profiling counters for FrameArrived handler
        self.frame_arrived_durations_ms: list[float] = []

    def _on_frame_arrived(self, sender: Any, args: Any) -> None:
        """Lightweight FrameArrived handler. Zero WinRT object leaks outside."""
        t_cb_start = time.perf_counter()

        frame = sender.try_acquire_latest_frame()
        if frame is None:
            return

        # Immediate raw QPC timestamp before conversion
        qpc_receive = get_raw_qpc_sec()

        source_ts_sec: float | None = None
        srt = frame.system_relative_time
        if srt:
            source_ts_sec = srt.total_seconds()

        vmf = frame.video_media_frame
        sb = vmf.software_bitmap if vmf else None
        if sb is not None:
            import winrt.windows.graphics.imaging as wgi
            bb = sb.lock_buffer(wgi.BitmapBufferAccessMode.READ)
            ref = bb.create_reference()

            # Zero-copy access to source WinRT buffer via Python buffer protocol
            src_bytes = memoryview(ref)

            # Check plane layouts
            p_count = bb.get_plane_count()
            p0 = bb.get_plane_description(0)
            p1 = bb.get_plane_description(1) if p_count > 1 else None

            p0_stride = p0.stride
            p0_start = p0.start_index
            p1_stride = p1.stride if p1 else self._cfg.width
            p1_start = p1.start_index if p1 else self._cfg.width * self._cfg.height

            # Unpack NV12 (tight stride view or padded row unpad)
            nv12_packed = unpack_nv12_planes(
                src_bytes=src_bytes,
                width=self._cfg.width,
                height=self._cfg.height,
                plane0_stride=p0_stride,
                plane0_start=p0_start,
                plane1_stride=p1_stride,
                plane1_start=p1_start,
                scratch_nv12=self._nv12_scratch,
            )

            # Application-owned preallocated RGB ring slot
            with self._lock:
                self._ring_idx = (self._ring_idx + 1) % 3
                rgb_slot = self._rgb_ring[self._ring_idx]
                self._frame_index += 1
                curr_idx = self._frame_index

            # Single-step color conversion & horizontal flip
            cv2.cvtColor(nv12_packed, cv2.COLOR_YUV2RGB_NV12, dst=rgb_slot)
            cv2.flip(rgb_slot, 1, dst=rgb_slot)
            t_ready = time.perf_counter()

            # Construct immutable application-owned CameraFrame
            cam_frame = CameraFrame(
                image_rgb=rgb_slot,
                frame_index=curr_idx,
                capture_time=t_ready,
                source_timestamp_sec=source_ts_sec,
                app_receive_qpc_sec=qpc_receive,
                rgb_ready_time=t_ready,
            )

            # Publish to single latest-frame slot
            with self._lock:
                self._latest_frame = cam_frame
                self._new_frame_event.set()

            # Release and dispose ALL WinRT frame resources immediately
            ref.close()
            bb.close()
            sb.close()

        frame.close()

        cb_dur = (time.perf_counter() - t_cb_start) * 1000.0
        if len(self.frame_arrived_durations_ms) < 1000:
            self.frame_arrived_durations_ms.append(cb_dur)

    def open(self) -> bool:
        """Starts asynchronous WinRT capture pipeline on dedicated background loop."""
        ready_event = threading.Event()
        init_error: list[Exception] = []

        def _run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            async def _init_async():
                try:
                    import winrt.windows.media.capture as wmc
                    import winrt.windows.media.capture.frames as wmcf

                    settings = wmc.MediaCaptureInitializationSettings()
                    settings.memory_preference = wmc.MediaCaptureMemoryPreference.CPU
                    settings.streaming_capture_mode = wmc.StreamingCaptureMode.VIDEO

                    mc = wmc.MediaCapture()
                    await mc.initialize_with_settings_async(settings)
                    self._mc = mc

                    sources = list(mc.frame_sources.values())
                    if not sources:
                        raise RuntimeError("No frame sources found on MediaCapture")
                    source = sources[0]

                    # Match 1280x720 @ 30 FPS NV12
                    target_fmt = None
                    for f in source.supported_formats:
                        vf = f.video_format
                        if (
                            vf.width == self._cfg.width
                            and vf.height == self._cfg.height
                            and f.subtype.upper() == "NV12"
                        ):
                            target_fmt = f
                            break

                    if target_fmt is not None:
                        await source.set_format_async(target_fmt)

                    reader = await mc.create_frame_reader_async(source)
                    reader.acquisition_mode = wmcf.MediaFrameReaderAcquisitionMode.REALTIME
                    self._reader = reader

                    self._event_token = reader.add_frame_arrived(self._on_frame_arrived)
                    await reader.start_async()
                    self._is_running = True

                    curr_fmt = source.current_format
                    actual_w = curr_fmt.video_format.width
                    actual_h = curr_fmt.video_format.height
                    frame_rate = curr_fmt.frame_rate
                    actual_fps = (
                        frame_rate.numerator / frame_rate.denominator
                        if frame_rate.denominator > 0
                        else 30.0
                    )

                    self._diag = CameraDiagnostics(
                        requested_backend=self._cfg.backend,
                        selected_backend="WINRT",
                        winrt_available=True,
                        winrt_init_success=True,
                        fallback_occurred=False,
                        fallback_reason=None,
                        actual_width=actual_w,
                        actual_height=actual_h,
                        actual_fps=round(actual_fps, 1),
                        actual_subtype=curr_fmt.subtype,
                    )
                    ready_event.set()
                except Exception as ex:
                    init_error.append(ex)
                    ready_event.set()

            loop.run_until_complete(_init_async())
            if self._is_running:
                loop.run_forever()

        self._loop_thread = threading.Thread(target=_run_loop, name="WinRTEventLoop", daemon=True)
        self._loop_thread.start()

        ready_event.wait(timeout=5.0)
        if init_error:
            self.close()
            raise init_error[0]

        return self._is_running

    def read_latest(self, timeout_sec: float = 0.05) -> tuple[bool, CameraFrame | None]:
        """Snapshots latest frame published by FrameArrived handler. No busy polling."""
        if not self._is_running:
            return False, None

        if not self._new_frame_event.wait(timeout=timeout_sec):
            return False, None

        with self._lock:
            self._new_frame_event.clear()
            frame = self._latest_frame

        return (frame is not None), frame

    def close(self) -> None:
        """Stops frame reader and disposes WinRT objects cleanly."""
        self._is_running = False
        if self._loop and self._loop.is_running():
            async def _cleanup_async():
                try:
                    if self._reader is not None:
                        if self._event_token is not None:
                            self._reader.remove_frame_arrived(self._event_token)
                        await self._reader.stop_async()
                        self._reader.close()
                except Exception:
                    pass
                try:
                    if self._mc is not None:
                        self._mc.close()
                except Exception:
                    pass

            try:
                fut = asyncio.run_coroutine_threadsafe(_cleanup_async(), self._loop)
                fut.result(timeout=2.0)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._loop_thread is not None and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=2.0)

        self._reader = None
        self._mc = None
        self._loop = None
        self._loop_thread = None

    @property
    def diagnostics(self) -> CameraDiagnostics:
        return self._diag


# ---------------------------------------------------------------------------
# Factory Function with Transparent Diagnostic Fallback
# ---------------------------------------------------------------------------
def create_camera_source(
    cfg: CameraConfig,
    force_winrt_failure: bool = False,
) -> CameraSource:
    """Creates a camera source based on configuration with transparent fallback.

    - AUTO: Tries WinRT first; if unavailable or initialization fails, falls back to MSMF.
    - WINRT: Requires WinRT; raises RuntimeError if initialization fails.
    - MSMF: Uses OpenCV MSMF directly.
    """
    backend_req = cfg.backend.upper()

    if backend_req in ["AUTO", "WINRT"]:
        winrt_available = False
        winrt_error_reason: str | None = None

        if force_winrt_failure:
            winrt_error_reason = "Test injection: force_winrt_failure=True"
        else:
            try:
                import winrt.windows.media.capture  # noqa: F401
                import winrt.windows.media.capture.frames  # noqa: F401
                winrt_available = True
            except ImportError as err:
                winrt_error_reason = f"WinRT packages not available: {err}"

        if winrt_available and not force_winrt_failure:
            try:
                source = WinRTCameraSource(cfg)
                if source.open():
                    print(
                        f"Camera backend: WINRT ({source.diagnostics.actual_width}x"
                        f"{source.diagnostics.actual_height} @ {source.diagnostics.actual_fps} FPS, "
                        f"subtype={source.diagnostics.actual_subtype})"
                    )
                    return source
                else:
                    winrt_error_reason = "WinRTCameraSource.open() returned False"
            except Exception as ex:
                winrt_error_reason = f"WinRT initialization failed: {ex}"

        # If user explicitly requested WINRT, do not silently ignore configuration
        if backend_req == "WINRT":
            raise RuntimeError(f"Explicit WINRT backend requested but failed: {winrt_error_reason}")

        # Fallback to MSMF for AUTO
        print(f"[WARN] WinRT initialization failed: {winrt_error_reason}")
        print("Falling back to MSMF. Camera backend: MSMF")
        msmf_source = OpenCVMSMFCameraSource(cfg)
        msmf_source._fallback_reason = winrt_error_reason  # type: ignore
        if not msmf_source.open():
            raise RuntimeError(f"MSMF fallback failed to open camera index {cfg.index}")
        return msmf_source

    elif backend_req == "MSMF":
        print("Camera backend: MSMF (Explicitly requested)")
        msmf_source = OpenCVMSMFCameraSource(cfg)
        if not msmf_source.open():
            raise RuntimeError(f"MSMF failed to open camera index {cfg.index}")
        return msmf_source

    else:
        raise ValueError(f"Unknown camera backend: {cfg.backend}. Supported: 'AUTO', 'WINRT', 'MSMF'")
