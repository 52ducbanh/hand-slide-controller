"""Unit tests for camera abstraction, NV12 unpacker, and fallback mechanisms."""

import numpy as np
import pytest

from hand_controller.camera import (
    CameraFrame,
    CameraDiagnostics,
    unpack_nv12_planes,
    create_camera_source,
    OpenCVMSMFCameraSource,
    WinRTCameraSource,
)
from hand_controller.config import CameraConfig


def test_nv12_unpack_tight_stride():
    """Tight stride (stride == width, packed): returns exact view without copy."""
    w, h = 640, 480
    y_size = w * h
    uv_size = w * (h // 2)
    total_bytes = y_size + uv_size

    # Create synthetic tight buffer
    raw = np.arange(total_bytes, dtype=np.uint8)
    unpacked = unpack_nv12_planes(
        src_bytes=memoryview(raw),
        width=w,
        height=h,
        plane0_stride=w,
        plane0_start=0,
        plane1_stride=w,
        plane1_start=y_size,
    )

    assert unpacked.shape == (h * 3 // 2, w)
    assert unpacked.dtype == np.uint8
    # In tight stride, the memory should be a direct view into the buffer
    assert unpacked[0, 0] == 0
    assert unpacked[0, 1] == 1
    assert unpacked[h, 0] == raw[y_size]


def test_nv12_unpack_padded_stride():
    """Padded stride (stride > width): copies and strips row padding."""
    w, h = 4, 4
    stride_y = 6  # 2 bytes padding per Y row
    stride_uv = 6  # 2 bytes padding per UV row
    uv_h = h // 2

    # Y plane: 4 rows of 6 bytes (first 4 are pixels, last 2 are padding = 255)
    # UV plane: 2 rows of 6 bytes (first 4 are UV pixels, last 2 are padding = 255)
    plane0_start = 0
    plane1_start = h * stride_y

    total_len = plane1_start + uv_h * stride_uv
    raw = np.full(total_len, 255, dtype=np.uint8)

    # Populate valid pixel data
    val = 0
    for r in range(h):
        for c in range(w):
            raw[plane0_start + r * stride_y + c] = val
            val += 1

    uv_val = 100
    for r in range(uv_h):
        for c in range(w):
            raw[plane1_start + r * stride_uv + c] = uv_val
            uv_val += 1

    scratch = np.zeros((h * 3 // 2, w), dtype=np.uint8)
    unpacked = unpack_nv12_planes(
        src_bytes=memoryview(raw),
        width=w,
        height=h,
        plane0_stride=stride_y,
        plane0_start=plane0_start,
        plane1_stride=stride_uv,
        plane1_start=plane1_start,
        scratch_nv12=scratch,
    )

    assert unpacked.shape == (6, 4)
    # Check that Y plane received clean pixel values without padding (no 255)
    assert unpacked[0, 0] == 0
    assert unpacked[0, 3] == 3
    assert unpacked[1, 0] == 4
    assert unpacked[3, 3] == 15

    # Check UV plane received clean pixel values without padding
    assert unpacked[4, 0] == 100
    assert unpacked[4, 3] == 103
    assert unpacked[5, 0] == 104
    assert unpacked[5, 3] == 107
    assert 255 not in unpacked


def test_nv12_unpack_invalid_buffer_size():
    """Raises ValueError when source buffer is too small for specified dimensions."""
    w, h = 640, 480
    raw_too_small = np.zeros(100, dtype=np.uint8)

    with pytest.raises(ValueError, match="Invalid buffer size for NV12"):
        unpack_nv12_planes(
            src_bytes=memoryview(raw_too_small),
            width=w,
            height=h,
            plane0_stride=w,
            plane0_start=0,
            plane1_stride=w,
            plane1_start=w * h,
        )


def test_camera_frame_structure_and_types():
    """CameraFrame named tuple holds correct fields and types."""
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame = CameraFrame(
        image_rgb=img,
        frame_index=42,
        capture_time=123.456,
        source_timestamp_sec=789.012,
        app_receive_qpc_sec=789.015,
        rgb_ready_time=123.457,
    )

    assert frame.image_rgb.shape == (720, 1280, 3)
    assert frame.frame_index == 42
    assert frame.capture_time == 123.456
    assert frame.source_timestamp_sec == 789.012
    assert frame.app_receive_qpc_sec == 789.015
    assert frame.rgb_ready_time == 123.457


def test_factory_fallback_auto_with_forced_failure(monkeypatch):
    """When backend='AUTO' and WinRT fails, falls back transparently to MSMF."""
    cfg = CameraConfig(backend="AUTO")

    # Force WinRT failure
    cam = create_camera_source(cfg, force_winrt_failure=True)
    try:
        assert isinstance(cam, OpenCVMSMFCameraSource)
        diag = cam.diagnostics
        assert diag.selected_backend == "MSMF"
        assert diag.fallback_occurred is True
        assert diag.fallback_reason is not None
        assert "force_winrt_failure=True" in diag.fallback_reason
    finally:
        cam.close()


def test_factory_explicit_msmf():
    """When backend='MSMF', uses MSMF directly without fallback."""
    cfg = CameraConfig(backend="MSMF")
    cam = create_camera_source(cfg)
    try:
        assert isinstance(cam, OpenCVMSMFCameraSource)
        diag = cam.diagnostics
        assert diag.selected_backend == "MSMF"
        assert diag.fallback_occurred is False
    finally:
        cam.close()


def test_factory_explicit_winrt_with_failure_raises():
    """When backend='WINRT' explicitly and WinRT fails, raises RuntimeError."""
    cfg = CameraConfig(backend="WINRT")
    with pytest.raises(RuntimeError, match="Explicit WINRT backend requested but failed"):
        create_camera_source(cfg, force_winrt_failure=True)


def test_factory_invalid_backend():
    """Raises ValueError for unrecognized backend."""
    cfg = CameraConfig(backend="DIRECTSHOW_UNSUPPORTED")
    with pytest.raises(ValueError, match="Unknown camera backend"):
        create_camera_source(cfg)


def test_msmf_source_fallback_reason_constructor():
    """Verifies OpenCVMSMFCameraSource accepts fallback_reason directly via constructor."""
    cfg = CameraConfig(backend="AUTO")
    source = OpenCVMSMFCameraSource(cfg, fallback_reason="DirectConstructorTest")
    assert source.diagnostics.fallback_reason == "DirectConstructorTest"
    assert source.diagnostics.selected_backend == "MSMF"
    assert source.diagnostics.fallback_occurred is True
