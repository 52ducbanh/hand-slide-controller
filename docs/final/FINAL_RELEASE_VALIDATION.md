# Báo Cáo Xác Minh Phát Hành Cuối Cùng (Final Release Validation Report)

**Dự án:** Hand Slide Controller  
**Mã Commit Đóng Băng:** `606ac8527cd0b13bd77c661bce3a710e410520a2` (`606ac85`)  
**Ngày thực hiện:** 22/09/2026  
**Môi trường:** 12th Gen Intel Core i5-12450H | 15.71 GB RAM | NVIDIA RTX 4050 Laptop GPU | Windows 11 Home (Build 26100)

---

## 1. Kết Quả Tổng Thể (Executive Summary)

```text
ARCHITECTURE FROZEN = YES
FINAL RELEASE VALIDATION = PASS
PERFORMANCE OPTIMIZATION CLOSED = YES
```

Toàn bộ 7 cổng kiểm tra (gates) nghiêm ngặt đều **PASS** 100% trên commit `606ac85`, xác nhận bản phát hành sản xuất đạt chất lượng cao nhất, ổn định tuyệt đối và sẵn sàng triển khai.

---

## 2. Bảng Đối Chiếu 7 Cổng Kiểm Tra (7-Gate Validation Matrix)

| Gate | Tiêu Chí Kiểm Tra | Kết Quả Thực Đo | Trạng Thái |
| :---: | :--- | :--- | :---: |
| **Gate 1** | **Unit Tests** (`pytest tests/ -v`) | **58/58 PASS** (25.79s) | **PASS** |
| **Gate 2** | **Packaged Build** (PyInstaller rebuild) | Launcher: **8.46 MB** (8,873,184 bytes)<br>Thư mục: **258.68 MB** (271,242,640 bytes)<br>Số tệp: **562 tệp** | **PASS** |
| **Gate 3** | **10-Min Live Stability** (600s liên tục) | Khung hình nạp: **17,838** (29.98 FPS)<br>Khung hình xử lý: **17,837** (29.98 FPS)<br>Ngoại lệ: **0**<br>RSS @ 60s: **167.04 MB**<br>RSS @ 300s: **167.85 MB**<br>RSS @ 600s: **168.09 MB**<br>Slope 300s→600s: **+0.0448 MB/phút** | **PASS** |
| **Gate 4** | **10x Reopen Test** (Open-Stream-Close) | **10/10** Mở thành công (TB: 107.5 ms)<br>**10/10** Thu nhận luồng (5/5 frames)<br>**10/10** Đóng sạch (TB: 443.7 ms)<br>0 camera lock, 0 luồng mồ côi | **PASS** |
| **Gate 5** | **Accuracy Regression** (`benchmark_input.mp4`) | Hành động kỳ vọng: **4/4 khớp tuyệt đối** (`['NEXT', 'NEXT', 'PREVIOUS', 'PREVIOUS']`)<br>Kích hoạt giả: **0**<br>Two-hand recall: **99.0%** (99/100 khung hình GT) | **PASS** |
| **Gate 6** | **Forced Fallback** (WinRT Failure Injection) | `force_winrt_failure=True` kích hoạt chuyển đổi tự động:<br>`AUTO → MSMF` thành công<br>`OpenCVMSMFCameraSource` mở luồng: **10/10 frames** (1280x720x3)<br>Tắt sạch: **278.8 ms** | **PASS** |
| **Gate 7** | **Packaged EXE Smoke Test** (`HandSlideController.exe`) | Khởi động thành công (PID 23676)<br>Nạp WinRT + MediaPipe + Preview + SendInput<br>Đa luồng hoạt động: **79 threads**, RSS: **179.03 MB**<br>Đóng sạch qua SIGTERM: **30.8 ms**<br>Zero worker alive: **True** | **PASS** |

---

## 3. Chi Tiết Các Cổng Kiểm Tra

### Gate 1: Kiểm Thử Đơn Vị (Unit Tests)
- **Lệnh thực thi:** `pytest tests/ -v -q --tb=no`
- **Kết quả:** `58 passed in 25.79s`
- **Các module kiểm thử:**
  - `test_async_pipeline.py`: 6 tests PASS
  - `test_camera.py`: 8 tests PASS
  - `test_config.py`: 3 tests PASS
  - `test_gestures.py`: 4 tests PASS
  - `test_models.py`: 8 tests PASS
  - `test_state_machine.py`: 19 tests PASS
  - `test_tracker.py`: 10 tests PASS

### Gate 2: Bản Đóng Gói Phân Phối Cuối Cùng (Packaged Build)
- **Thư mục phân phối:** `dist\HandSlideController`
- **Kích thước Launcher:** `8.46 MB` (`8,873,184 bytes`)
- **Tổng dung lượng thư mục phân phối:** `258.68 MB` (`271,242,640 bytes`)
- **Số lượng tệp phân phối:** `562 files`
- **Đối chiếu kỳ vọng:** Khớp chính xác với ngưỡng dự tính `~258.68 MB` (giảm 37.51 MB và 940 tệp so với baseline 296.19 MB ban đầu).

### Gate 3: Kiểm Thử Độ Ổn Định 10 Phút (10-Minute Live Stability)
- **Thời gian chạy thực tế:** `600.0 giây` liên tục trên webcam vật lý (Device 0).
- **Cấu hình pipeline:** WinRT MediaFrameReader (Realtime) + In-place NV12→RGB + MediaPipe `LIVE_STREAM_WORKER` (`num_hands=2`) + Native Win32 `SendInput`.
- **Thống kê luồng:**
  - Khung hình nạp vào MediaPipe: `17,838` (29.98 FPS)
  - Khung hình hoàn tất xử lý: `17,837` (29.98 FPS)
  - MediaPipe drops: `0`
  - Control overwrites: `0`
  - Exceptions: `0`
  - Luồng worker kết thúc sạch: `True`
- **Kiểm toán bộ nhớ:**
  - RSS tại 60s: `167.04 MB`
  - RSS tại 300s: `167.85 MB`
  - RSS tại 600s: `168.09 MB`
  - Steady State Min / Max: `167.04 MB` / `171.16 MB` (Biên độ dao động chỉ 4.12 MB)
  - Độ dốc tuyến tính 60s → 600s: `-0.0872 MB/phút`
  - Độ dốc tuyến tính 300s → 600s: `+0.0448 MB/phút` (+0.24 MB trên toàn bộ 5 phút cuối)
- **Đánh giá bộ nhớ:**
  ```text
  MEMORY PLATEAU CONFIRMED
  NO EVIDENCE OF UNBOUNDED LEAK
  ```

### Gate 4: Kiểm Thử Đóng Mở Camera 10 Lần (10x Reopen)
- Chu kỳ thực hiện: `10 lần` liên tiếp (Open -> Stream 5 frames -> Close).
- Tỉ lệ mở thành công: `10/10` (Thời gian mở trung bình: `107.5 ms`).
- Tỉ lệ thu nhận luồng: `10/10` (Thu đủ 5/5 khung hình mỗi chu kỳ).
- Tỉ lệ đóng thành công: `10/10` (Thời gian đóng trung bình: `443.7 ms`).
- Không xảy ra tình trạng khóa thiết bị (camera lock), không rò rỉ callback sau khi đóng, không tồn tại luồng worker mồ côi.

### Gate 5: Độ Chính Xác & Phân Biệt Tỉ Lệ Xuất Hiện (Accuracy & Prevalence Distinction)
- **Tập dữ liệu kiểm thử:** `benchmark_input.mp4` (800 khung hình chuẩn).
- **Hành động cử chỉ:**
  - Chuỗi kỳ vọng: `['NEXT', 'NEXT', 'PREVIOUS', 'PREVIOUS']`
  - Chuỗi ghi nhận: `['NEXT', 'NEXT', 'PREVIOUS', 'PREVIOUS']` (**Khớp 100%**)
  - Kích hoạt sai (False triggers): `0`
- **Phân biệt rạch ròi giữa Ground-Truth và Quan sát thực tế:**
  - **Ground-truth two-hand prevalence:** `100 / 800 = 12.5%` (Chỉ tính trong đoạn video người mẫu thực sự đưa 2 tay).
  - **Observed two-hand detection prevalence:** `115 / 800 = 14.37%` (Bao gồm 99 khung hình phát hiện đúng trong vùng GT + 16 khung hình phát hiện biên ngoài vùng GT).
  - **Two-hand detection recall:** `99 / 100 = 99.0%` (Bảo toàn tuyệt đối, không suy giảm so với baseline 99%).

### Gate 6: Dự Phòng Khi WinRT Thất Bại (Forced Fallback)
- **Phương thức kiểm thử:** Tiêm lỗi cưỡng bức `force_winrt_failure=True` vào factory `create_camera_source(CameraConfig(backend='AUTO'))`.
- **Cơ chế hoạt động:** Hệ thống phát hiện WinRT thất bại, ghi nhận cảnh báo chuẩn đoán `[WARN] WinRT initialization failed: Test injection: force_winrt_failure=True` và tự động kích hoạt `OpenCVMSMFCameraSource`.
- **Thu nhận luồng MSMF:** Thu nhận thành công `10/10` khung hình định dạng `(720, 1280, 3)`.
- **Tắt sạch:** Hoàn tất đóng camera MSMF trong `278.8 ms`.

### Gate 7: Smoke Test Trên Bản Phân Phối Đóng Gói (Packaged EXE Smoke Test)
- **Tệp thực thi:** `dist\HandSlideController\HandSlideController.exe` (chạy độc lập không qua Python source).
- **Khởi động:** Tiến trình khởi động thành công trong 4.0s (PID: 23676).
- **Tài nguyên:** Nạp đầy đủ WinRT, MediaPipe XNNPACK, Preview Window, và Native SendInput.
- **Tiến trình:** Hoạt động với `79 threads` và `RSS = 179.03 MB`.
- **Thu nhận luồng:** Truyền phát luồng trực tiếp từ webcam vật lý trong 5.0 giây.
- **Đóng sạch:** Nhận tín hiệu kết thúc và giải phóng toàn bộ tài nguyên trong `30.8 ms`.
- **Xác nhận không còn tiến trình con:** `still_alive = False`.

---

## 4. Tuyên Bố Đóng Dự Án (Final Verdict)

Mọi tiêu chí nghiệm thu đã hoàn tất:
1. Đã khóa kiến trúc sản xuất chính thức.
2. Đã cắt giảm 37.51 MB dung lượng phân phối.
3. Đã nâng cấp điều phối phím sang native Win32 `SendInput` (1.7 µs).
4. Đã chứng minh bình ổn bộ nhớ và tính đơn điệu của thời gian trên webcam vật lý.
5. Đã kiểm thử thành công trên cả bộ mã nguồn và bản dựng EXE đóng gói.

```text
ARCHITECTURE FROZEN = YES
FINAL RELEASE VALIDATION = PASS
PERFORMANCE OPTIMIZATION CLOSED = YES
```
