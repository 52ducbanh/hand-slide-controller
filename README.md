# Hand Slide Controller

Hệ thống điều khiển trình chiếu slide không chạm độ trễ thấp thông qua nhận diện cử chỉ bàn tay thời gian thực trên Windows 10/11.

---

## 1. Tính Năng Chính (Core Features)

* **Điều khiển slide mượt mà qua cử chỉ:**
  * 👍 **Thumbs Up (Like):** Chuyển sang slide kế tiếp (`Next Slide` / Phím `Right Arrow`).
  * ✌️ **Scissors (Victory):** Quay lại slide trước đó (`Previous Slide` / Phím `Left Arrow`).
  * ✋ **Blackout / Reset:** Tạm dừng hiển thị hoặc đặt lại trạng thái.
* **Hỗ trợ đồng thời 2 bàn tay (`num_hands = 2`):** Theo dõi độc lập, tự động lọc nhiễu và ngăn chặn kích hoạt chéo giữa hai tay.
* **Pipeline thời gian thực độ trễ siêu thấp:**
  * **WinRT MediaFrameReader (Realtime Mode):** Thu nhận khung hình event-driven ở tầng Windows Runtime, chuyển đổi NV12 sang RGB in-place, loại bỏ hoàn toàn busy-polling và camera thread backlog.
  * **Tự động chuyển đổi dự phòng (Automatic Fallback):** Khi WinRT không khả dụng, hệ thống tự động fallback mượt mà sang OpenCV MSMF (`OpenCVMSMFCameraSource`).
  * **MediaPipe Tasks Live Stream Async:** Phân tách luồng thu nhận camera và suy luận AI MediaPipe qua worker packets.
  * **Native Win32 `SendInput`:** Điều phối tín hiệu bàn phím trực tiếp qua Win32 API (`user32.SendInput`) với độ trễ chỉ **1.7 µs** (nhanh gấp 3.8 lần giải pháp giả lập cấp cao).
  * **Âm thanh phản hồi trực tiếp:** Tích hợp `winsound` native của Windows, không phụ thuộc thư viện âm thanh ngoài.

---

## 2. Cấu Trúc Dự Án (Project Structure)

```text
hand-slide-controller/
├── main.py                          # Điểm khởi chạy ứng dụng chính
├── hand_landmarker.task             # Mô hình MediaPipe Hand Landmarker
├── HandSlideController.spec         # Cấu hình đóng gói PyInstaller tối ưu (~258 MB)
├── requirements.txt                 # Danh mục phụ thuộc chính
├── run.bat                          # Script khởi chạy nhanh trên Windows
├── build_exe.bat                    # Script đóng gói EXE tự động
├── .gitignore                       # Quy tắc loại trừ tệp tạm và build
│
├── hand_controller/                 # Mã nguồn kiến trúc ứng dụng
│   ├── __init__.py
│   ├── actions.py                   # Win32 SendInput & Audio Feedback
│   ├── app.py                       # Runtime controller & rendering
│   ├── camera.py                    # WinRT & MSMF Camera Abstraction
│   ├── config.py                    # Configuration dataclasses
│   ├── geometry.py                  # Các phép tính hình học 2D/3D
│   ├── gestures.py                  # Nhận diện tư thế bàn tay & ngón tay
│   ├── hand_features.py             # Trích xuất đặc trưng khung xương
│   ├── models.py                    # Data classes & Gesture Enums
│   ├── renderer.py                  # Giao diện hiển thị trực quan (HUD)
│   ├── state_machine.py             # Máy trạng thái hữu hạn (FSM)
│   └── tracker.py                   # Theo dõi bàn tay liên khung hình
│
├── tests/                           # Bộ kiểm thử đơn vị (58 tests)
│   ├── test_async_pipeline.py
│   ├── test_camera.py
│   ├── test_config.py
│   ├── test_gestures.py
│   ├── test_models.py
│   ├── test_state_machine.py
│   └── test_tracker.py
│
├── docs/                            # Tài liệu kỹ thuật & nghiệm thu
│   └── final/
│       ├── FINAL_FEATURE_CONTRACT.json
│       ├── FINAL_OPTIMIZATION_MATRIX.json
│       ├── FINAL_PACKAGE_MANIFEST.csv
│       ├── FINAL_PERFORMANCE_REPORT.md
│       ├── FINAL_PRODUCTION_BASELINE.json
│       ├── FINAL_RELEASE_VALIDATION.json
│       ├── FINAL_RELEASE_VALIDATION.md
│       ├── FINAL_STABILITY_600S.json
│       └── FINAL_VALIDATION_STATUS.json
│
├── tools/                           # Công cụ bảo trì & quản trị mã nguồn
│   ├── cleanup.py
│   └── inventory.py
│
└── dist/
    └── HandSlideController/         # Bản phân phối thực thi độc lập (.EXE)
```

---

## 3. Cài Đặt & Khởi Chạy (Getting Started)

### Yêu cầu hệ thống:
* **Hệ điều hành:** Windows 10 (version 1809 trở lên) hoặc Windows 11.
* **Webcam:** 720p @ 30 FPS (hoặc tích hợp sẵn).
* **Python:** 3.10 – 3.12 (khuyến nghị 3.12).

### Cài đặt môi trường:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Khởi chạy chương trình:
* **Cách 1 (Nhanh nhất):** Nhấp đúp chuột vào file `run.bat`.
* **Cách 2 (CLI):**
  ```powershell
  .\.venv\Scripts\python.exe main.py
  ```

---

## 4. Kiểm Thử & Đóng Gói (Testing & Packaging)

### Chạy bộ kiểm thử đơn vị:
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```
*(Yêu cầu kết quả: 58/58 passed)*

### Đóng gói ứng dụng sang file `.exe`:
* Nhấp đúp chuột vào `build_exe.bat` hoặc chạy lệnh:
  ```powershell
  .\.venv\Scripts\pyinstaller.exe HandSlideController.spec --noconfirm
  ```
* Bản dựng phân phối độc lập sẽ được tạo tại:
  `dist\HandSlideController\HandSlideController.exe`

---

## 5. Báo Cáo Đo Đạc & Nghiệm Thu (Validation & Records)

Toàn bộ thông số đo đạc hiệu năng thực nghiệm, kiểm toán bộ nhớ 10 phút liên tục và ma trận xác minh phát hành được lưu trữ đầy đủ trong thư mục `docs/final/`:
* [FINAL_RELEASE_VALIDATION.md](file:///docs/final/FINAL_RELEASE_VALIDATION.md): Báo cáo nghiệm thu phát hành cuối cùng (7/7 gates PASS).
* [FINAL_PERFORMANCE_REPORT.md](file:///docs/final/FINAL_PERFORMANCE_REPORT.md): Báo cáo đo đạc chi tiết độ trễ từng chặng, kiến trúc WinRT và so sánh A/B.
* [FINAL_STABILITY_600S.json](file:///docs/final/FINAL_STABILITY_600S.json): Dữ liệu telemetry chạy liên tục 600 giây trên webcam vật lý (xác nhận bình ổn bộ nhớ 168 MB, độ dốc +0.04 MB/phút).
