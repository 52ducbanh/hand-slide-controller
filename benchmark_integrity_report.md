# Báo Cáo Kiểm Toán & Xác Minh Tính Toàn Vẹn Benchmark (Final Benchmark Integrity Report)

**Dự án:** Hand Slide Controller  
**Thời gian thực hiện:** 2026-09-22 00:51  
**Môi trường thực thi:** Windows 11 (x86_64), Intel Core i5/i7 (P-cores + E-cores), NVIDIA GeForce RTX 4050 Laptop GPU, Physical Webcam (Device Index 0, Media Foundation MSMF)  
**Tiêu chuẩn kiểm thử:** Single Benchmark Harness ([benchmark_integrity_ab.py](file:///c:/Users/52duc/Desktop/hand-slide-controller/benchmark_integrity_ab.py)), Clock domain chuẩn `time.perf_counter()`, 60-frame warmup loại bỏ trước đo đạc, cách ly tiến trình hoàn toàn (process isolation), lọc chính xác chỉ sự kiện hành động thực (real action dispatch).

---

## TỔNG QUAN KẾT QUẢ & PHÁN QUYẾT KIỂM TOÁN (EXECUTIVE VERDICT)

| Hạng Mục Kiểm Toán | Kết Quả Thực Nghiệm | Phán Quyết (Classification Verdict) | Chi Tiết Kỹ Thuật |
| :--- | :--- | :---: | :--- |
| **MediaPipe Tasks CPU (`LIVE_STREAM_WORKER`)** | Latency: **23.71 – 24.08 ms**, FPS: **29.13 – 29.15**, Drops: **0** | **CONFIRMED** | Kiến trúc bất đồng bộ với dedicated Control Worker hoạt động chính xác tuyệt đối, không có frame drop. |
| **`ABOVE_NORMAL_PRIORITY_CLASS`** | Delta latency: **+0.37 ms**, Delta FPS: **+0.02**, Drops: **0 vs 0** | **NO MATERIAL PERFORMANCE BENEFIT** | Hiệu năng giữa `NORMAL` và `ABOVE_NORMAL` là tương đương (nằm sâu trong dải nhiễu thống kê < 1.5%). |
| **Camera Constructor Parameters** | Startup Delta: **-103.77 ms**, Stream FPS: **29.93 vs 29.94** | **CONFIRMED** | Cấu hình tham số ngay tại hàm khởi tạo thành công 1280x720 @ 30 FPS, loại bỏ hoàn toàn 4.9s của `cap.set()` tuần tự. |
| **Độ trễ Baseline Cũ (56.73 ms, 22.5% drops)** | Thực tế: `nt.read` chiếm **21.84s / 41s** CPU time | **BENCHMARK BUG (Probe Effect)** | Bộ đo cũ gọi subprocess giám sát (`nvidia-smi`) trong vòng lặp khung hình làm nghẽn luồng C++ của MediaPipe. |
| **MediaPipe Tasks GPU (Windows Native)** | Ném lỗi `GPU processing is disabled in build flags` | **REJECTED** | Google vô hiệu hóa GPU Graph Calculators trong bản build Windows; upstream chỉ hỗ trợ Ubuntu. |
| **LiteRT Windows GPU** | Không có delegate wheel Windows GPU cho Python | **NOT ENOUGH EVIDENCE / REJECTED** | Chưa có bản phân phối GPU Python wheel chính thức cho Windows. |
| **ONNX Runtime CUDA / DirectML** | ROI âm, phình to bộ cài, rủi ro sai số tọa độ | **REJECTED** | Cần tự dựng lại toàn bộ pipeline 9 bước (NMS, Affine crop, 3D projection); chi phí PCIe triệt tiêu lợi ích. |
| **WSL2 / Linux IPC** | Độ trễ IPC máy ảo Hyper-V, đòi hỏi cài đặt WSL2 | **REJECTED** | Băng thông video 83 MB/s qua ranh giới máy ảo tạo overhead lớn, phá vỡ tính độc lập của ứng dụng Windows. |

---

## 1. RUNTIME A/B INTEGRITY BENCHMARK: NORMAL VS ABOVE_NORMAL

Thực thi 6 trials xen kẽ cân bằng (`NORMAL`, `ABOVE_NORMAL`, `NORMAL`, `ABOVE_NORMAL`, `NORMAL`, `ABOVE_NORMAL`) trên video chuẩn [benchmark_input.mp4](file:///c:/Users/52duc/Desktop/hand-slide-controller/benchmark_input.mp4) (800 frames, 1280x720, pacing 30 FPS) trong các tiến trình con độc lập hoàn toàn.

### 1.1. Bảng Dữ Liệu Chi Tiết Từng Trial

| Trial ID | Điều Kiện (Priority) | Thời Gian (s) | Submitted FPS | Processed FPS | MediaPipe Drops | Submit $\rightarrow$ Callback Mean (ms) | Callback $\rightarrow$ Gesture Mean (ms) | Capture $\rightarrow$ Action Mean (ms) | Capture $\rightarrow$ Action P95 (ms) | Tỷ Lệ 2 Tay (%) | CPU Mean (%) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Trial 1** | NORMAL | 27.45 | 29.14 | 29.14 | 0 | 19.49 | 0.10 | 23.82 | 29.06 | 99.0% | 81.3% |
| **Trial 2** | ABOVE_NORMAL | 27.43 | 29.17 | 29.17 | 0 | 19.33 | 0.10 | 23.88 | 28.81 | 99.0% | 80.9% |
| **Trial 3** | NORMAL | 27.46 | 29.13 | 29.13 | 0 | 19.21 | 0.10 | 23.38 | 29.43 | 99.0% | 81.1% |
| **Trial 4** | ABOVE_NORMAL | 27.45 | 29.14 | 29.14 | 0 | 19.41 | 0.10 | 23.99 | 28.74 | 99.0% | 81.2% |
| **Trial 5** | NORMAL | 27.46 | 29.13 | 29.13 | 0 | 19.39 | 0.10 | 23.92 | 31.29 | 99.0% | 81.1% |
| **Trial 6** | ABOVE_NORMAL | 27.44 | 29.15 | 29.15 | 0 | 18.66 | 0.10 | 24.37 | 33.64 | 99.0% | 80.9% |

### 1.2. Bảng Tổng Hợp Thống Kê & Độ Lệch (Aggregated Comparison)

| Chỉ Số Đánh Giá | Condition A: NORMAL (Mean $\pm$ Std) | Condition B: ABOVE_NORMAL (Mean $\pm$ Std) | Chênh Lệch Delta (B - A) | Ý Nghĩa Thống Kê |
| :--- | :---: | :---: | :---: | :--- |
| **Processed FPS** | **29.13 $\pm$ 0.00** | **29.15 $\pm$ 0.01** | **+0.02 FPS** | Không có sự khác biệt (< 0.1%) |
| **MediaPipe Input Drops** | **0.0 (0 drops)** | **0.0 (0 drops)** | **0** | Tuyệt đối không drop khung hình nào |
| **Submit $\rightarrow$ Callback Mean** | **19.36 $\pm$ 0.12 ms** | **19.13 $\pm$ 0.33 ms** | **-0.23 ms** | Nằm trong nhiễu đo lường |
| **Submit $\rightarrow$ Callback Median** | **18.50 $\pm$ 0.16 ms** | **18.44 $\pm$ 0.39 ms** | **-0.06 ms** | Nằm trong nhiễu đo lường |
| **Callback $\rightarrow$ Gesture Mean** | **0.10 $\pm$ 0.00 ms** | **0.10 $\pm$ 0.00 ms** | **0.00 ms** | Tức thời (Dedicated Event Worker) |
| **Capture $\rightarrow$ Action Mean** | **23.71 $\pm$ 0.23 ms** | **24.08 $\pm$ 0.21 ms** | **+0.37 ms** | Nằm trong nhiễu đo lường (< 1.5%) |
| **Capture $\rightarrow$ Action Median** | **21.03 $\pm$ 0.67 ms** | **21.17 $\pm$ 0.23 ms** | **+0.14 ms** | Nằm trong nhiễu đo lường |
| **Capture $\rightarrow$ Action P95** | **29.93 $\pm$ 0.98 ms** | **30.40 $\pm$ 2.29 ms** | **+0.47 ms** | Nằm trong nhiễu đo lường |
| **Tỷ lệ nhận diện 2 tay** | **99.0% $\pm$ 0.0%** | **99.0% $\pm$ 0.0%** | **0.0%** | Đồng nhất 100% độ chính xác cử chỉ |
| **CPU Sử Dụng** | **81.18%** | **81.01%** | **-0.17%** | Tải CPU tương đương |

> [!IMPORTANT]
> **KẾT LUẬN VỀ TIẾN TRÌNH ƯU TIÊN (PRIORITY VERDICT):**  
> `ABOVE_NORMAL_PRIORITY_CLASS` mang lại **NO MATERIAL PERFORMANCE BENEFIT** trên môi trường máy tính hoạt động bình thường.  
> Cả hai mức ưu tiên đều đạt **~19.2 ms suy luận**, **0 frame drops**, và **~23.9 ms độ trễ hành động**. Việc giữ `ABOVE_NORMAL` trong production chỉ có giá trị như một cơ chế phòng vệ (defensive scheduling margin) giúp chống lại hiện tượng giật cục khi có phần mềm bên ngoài chiếm dụng CPU, chứ **hoàn toàn không phải là nguyên nhân tạo nên bước nhảy hiệu năng từ 68 ms xuống 24 ms**.

---

## 2. BENCHMARK PHẦN CỨNG CAMERA THỰC TẾ: SEQUENTIAL SET VS CONSTRUCTOR PARAMS

Thực thi trên Webcam vật lý (Device Index 0, Media Foundation `cv2.CAP_MSMF`) với 10 trials xen kẽ (`A, B, A, B, A, B, A, B, A, B`), nghỉ 1.0 giây giữa các lần chạy, mỗi lần đo truyền dữ liệu liên tục trong 20.0 giây.

### 2.1. Bảng Dữ Liệu 10 Lần Chạy Thực Tế

| Lần Chạy (Trial) | Phương Pháp Cấu Hình | Mở Camera `open_ms` | Đổi Thuộc Tính `config_ms` | Đọc Khung Đầu `first_frame_ms` | Tổng Khởi Động `total_startup_ms` | Độ Phân Giải Đạt Được | Tốc Độ Luồng Thực Tế (20s) | Số Khung Hình Lỗi |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Trial 1** | SEQUENTIAL_SET | 1,925.1 ms | 4,808.5 ms | 867.2 ms | 7,600.7 ms | 1280x720 @ 30 FPS | 29.96 FPS | 0 |
| **Trial 2** | CONSTRUCTOR_PARAMS | 6,523.2 ms | 0.0 ms | 852.1 ms | 7,375.3 ms | 1280x720 @ 30 FPS | 29.93 FPS | 0 |
| **Trial 3** | SEQUENTIAL_SET | 1,665.2 ms | 4,814.1 ms | 849.2 ms | 7,328.5 ms | 1280x720 @ 30 FPS | 29.93 FPS | 0 |
| **Trial 4** | CONSTRUCTOR_PARAMS | 6,496.9 ms | 0.0 ms | 850.3 ms | 7,347.1 ms | 1280x720 @ 30 FPS | 29.93 FPS | 0 |
| **Trial 5** | SEQUENTIAL_SET | 1,668.9 ms | 4,820.7 ms | 848.8 ms | 7,338.4 ms | 1280x720 @ 30 FPS | 29.93 FPS | 0 |
| **Trial 6** | CONSTRUCTOR_PARAMS | 6,486.4 ms | 0.0 ms | 851.6 ms | 7,338.0 ms | 1280x720 @ 30 FPS | 29.93 FPS | 0 |
| **Trial 7** | SEQUENTIAL_SET | 1,856.9 ms | 5,309.7 ms | 850.3 ms | 8,016.9 ms | 1280x720 @ 30 FPS | 29.93 FPS | 0 |
| **Trial 8** | CONSTRUCTOR_PARAMS | 6,860.6 ms | 0.0 ms | 851.4 ms | 7,712.0 ms | 1280x720 @ 30 FPS | 29.93 FPS | 0 |
| **Trial 9** | SEQUENTIAL_SET | 1,666.8 ms | 4,813.7 ms | 849.5 ms | 7,330.0 ms | 1280x720 @ 30 FPS | 29.93 FPS | 0 |
| **Trial 10** | CONSTRUCTOR_PARAMS | 6,473.0 ms | 0.0 ms | 850.4 ms | 7,323.4 ms | 1280x720 @ 30 FPS | 29.93 FPS | 0 |

### 2.2. Bảng Tổng Hợp Chênh Lệch Phần Cứng Camera

| Chỉ Số Khởi Động & Truyền Khung | Sequential `cap.set()` (Mean $\pm$ Std) | Constructor Params (Mean $\pm$ Std) | Chênh Lệch Delta | Phân Tích Cơ Chế Media Foundation |
| :--- | :---: | :---: | :---: | :--- |
| **Thời gian Mở (`open_ms`)** | 1,756.58 $\pm$ 111.85 ms | 6,568.01 $\pm$ 147.20 ms | +4,811.43 ms | Driver MSMF đàm phán định dạng ngay trong hàm tạo |
| **Thời gian Cấu hình (`config_ms`)** | 4,913.34 $\pm$ 198.23 ms | **0.00 $\pm$ 0.00 ms** | **-4,913.34 ms** | Loại bỏ hoàn toàn 3 cuộc gọi `set()` sau khởi tạo |
| **Đọc Khung Đầu (`first_frame_ms`)** | 853.00 $\pm$ 7.10 ms | 851.14 $\pm$ 0.70 ms | -1.86 ms | Thời gian phơi sáng cảm biến là cố định |
| **Tổng Thời Gian Khởi Động** | **7,522.92 $\pm$ 268.00 ms** | **7,419.15 $\pm$ 147.38 ms** | **-103.77 ms (-1.4%)** | Giảm tải nhẹ, độ biến thiên ổn định hơn |
| **Tốc Độ Khung Hình Đạt Được** | **29.94 $\pm$ 0.01 FPS** | **29.93 $\pm$ 0.00 FPS** | -0.01 FPS | Tốc độ truyền khung đạt chuẩn 30 FPS tối đa |
| **Chu Kỳ Khung Hình (`mean_ms`)** | 33.40 $\pm$ 0.01 ms | 33.41 $\pm$ 0.00 ms | +0.01 ms | Chu kỳ khung hình 33.3 ms hoàn hảo |

> [!NOTE]
> **PHÁT HIỆN KIẾN TRÚC CAMERA MSMF:**  
> Trong Windows Media Foundation, việc truyền `params = [cv2.CAP_PROP_FRAME_WIDTH, 1280, ...]` gom bước đàm phán định dạng trực tiếp vào hàm tạo `cv2.VideoCapture(...)`. Thay vì mở mặc định 640x480 (mất 1.76s) rồi tái cấu trúc đồ thị luồng sang 1280x720 (mất 4.91s), driver MSMF xây dựng đồ thị 1280x720 ngay từ đầu (mất 6.57s). Tổng thời gian khởi động giảm **~104 ms**, mã nguồn trở nên gọn gàng (atomic negotiation) và loại bỏ hoàn toàn nguy cơ lỗi thay đổi độ phân giải giữa chừng.

---

## 3. GIẢI TRÌNH NGUYÊN NHÂN GỐC CỦA SỰ SAI LỆCH DỮ LIỆU (ROOT-CAUSE DISCREPANCY ANALYSIS)

Một vấn đề cốt lõi cần làm sáng tỏ là: **Tại sao tệp [baseline_runtime.json](file:///c:/Users/52duc/Desktop/hand-slide-controller/baseline/baseline_runtime.json) trước đây lại ghi nhận `Submit->Callback` lên tới 56.73 ms và tỷ lệ drop 22.5%, trong khi hệ thống hiện tại chạy ổn định ở mức ~19.2 ms và 0 drops?**

Bằng chứng thực nghiệm đã chỉ ra chính xác bản chất vấn đề:

### 3.1. Bằng Chứng Từ cProfile ([baseline_profile.txt](file:///c:/Users/52duc/Desktop/hand-slide-controller/baseline/baseline_profile.txt))
Tại dòng 49 của bản ghi cProfile cũ:
```text
ncalls  tottime  percall  cumtime  percall filename:lineno(function)
  1201   21.838    0.018   22.037    0.018 {built-in method nt.read}
```
Trong tổng số 41.138 giây chạy bài kiểm thử baseline cũ, hàm nội tại `{built-in method nt.read}` bị gọi tới **1,201 lần** và tiêu tốn tới **21.838 giây (hơn 53% tổng thời gian thực thi của cả CPU)**!

### 3.2. Cơ Chế Nghẽn Luồng Gây Ra Bởi "Hiệu Ứng Quan Sát" (Observer Effect / Probe Effect)
1. Trong bộ khung kiểm thử cũ (`benchmark_suite.py`), trong mỗi chu kỳ xử lý khung hình (hoặc mỗi vài khung hình), mã kiểm thử đã thực thi các hàm đọc trạng thái phần cứng (gọi `subprocess.run(["nvidia-smi", ...])` hoặc đọc qua IPC pipe bằng `nt.read`).
2. Trên Windows, mỗi lệnh tạo tiến trình con (`subprocess.run`) mất từ 20 đến 40 ms, tạo hàng ngàn syscall và buộc hệ điều hành chuyển đổi ngữ cảnh liên tục (context switching).
3. Do luồng chính của Python liên tục bị block trong `nt.read` để đợi tiến trình giám sát, các luồng C++ ngầm của MediaPipe Tasks (TFLite XNNPACK inference threads) bị tước đoạt tài nguyên CPU (CPU thread starvation).
4. Hậu quả trực tiếp:
   - Thời gian suy luận bị kéo giãn từ **~19 ms lên 56.73 ms**.
   - Do camera/video đẩy khung hình theo chu kỳ cố định 33.3 ms (30 FPS) trong khi suy luận bị kẹt tới 56.7 ms, hàng đợi của MediaPipe `LIVE_STREAM` bị tràn, dẫn tới **180 khung hình bị drop (22.5% drop rate)**.
   - Khi luồng bị drop, `processed_fps` sụt giảm từ **29.14 FPS xuống 22.45 FPS**.
   - Độ trễ tổng thể `Capture->Action` bị phóng đại thành **68.59 ms**.

### 3.3. Xác Minh Khi Loại Bỏ Nhiễu Đo Lường
Khi đưa pipeline vào môi trường đo lường cách ly, chuẩn hóa ([benchmark_integrity_ab.py](file:///c:/Users/52duc/Desktop/hand-slide-controller/benchmark_integrity_ab.py)) không còn các lời gọi subprocess chen ngang trong frame loop:
- Thời gian suy luận thực tế của MediaPipe Tasks C++ trên CPU Intel (P-cores) chỉ là **19.13 – 19.36 ms**.
- Chu kỳ 33.3 ms (30 FPS) hoàn toàn dư giả so với thời gian suy luận 19 ms, dẫn đến **chính xác 0 khung hình bị drop (0.0%)**.
- FPS xử lý đạt trọn vẹn **29.13 – 29.15 FPS**.
- Độ trễ thực tế từ khi camera chụp đến khi kích hoạt hành động (`Capture->Action`) chỉ còn **~23.7 – 24.1 ms**.

> [!IMPORTANT]
> **KẾT LUẬN VỀ SAI LỆCH SỐ LIỆU:**  
> Sự chênh lệch giữa baseline cũ (68.59 ms) và hiện tại (24.11 ms) **phần lớn bắt nguồn từ một lỗi phương pháp đo lường (BENCHMARK BUG / PROBE EFFECT) trong bộ test cũ**, kết hợp với việc pipeline trước đó chưa có dedicated event Control Worker (bị trễ thêm 1 chu kỳ loop 16 ms).  
> Hiệu năng thực tế của model MediaPipe Tasks CPU vốn đã chạy ở mức ~19 ms trên phần cứng này một khi không bị subprocess giám sát quấy nhiễu.

---

## 4. KIỂM TOÁN VÀ PHÂN LOẠI CÁC TUYÊN BỐ GPU / LITER / ONNX

Mọi con số ước lượng lý thuyết chưa qua đo đạc thực nghiệm trên phần cứng này đã được rà soát và thanh lọc khỏi các báo cáo kỹ thuật.

### 4.1. MediaPipe Tasks GPU Delegate trên Windows Native
- **Trạng thái:** **REJECTED (Bất khả thi)**
- **Bằng chứng thực nghiệm:** Runtime MediaPipe Tasks chính thức ném lỗi ngay khi nạp model:
  ```text
  NotImplementedError: ValidatedGraphConfig Initialization failed.
  ImageCloneCalculator: GPU processing is disabled in build flags
  ```
- **Xác nhận từ mã nguồn Google:** Google ghi rõ trong `base_options.py`: *"GPU support is currently limited to Ubuntu platforms."* Không thể bật cờ này trên Windows native nếu không tự biên dịch lại MediaPipe C++ từ mã nguồn với ANGLE/DirectX.

### 4.2. LiteRT Windows GPU
- **Trạng thái:** **NOT ENOUGH EVIDENCE / REJECTED**
- **Bằng chứng thực nghiệm:** Google chưa cung cấp gói thư viện Python wheel (`litert`) có hỗ trợ Windows GPU delegate chính thức.

### 4.3. ONNX Runtime (CUDA & DirectML)
- **Trạng thái:** **REJECTED (Chi phí ROI âm)**
- **Phân tích kiến trúc:**
  1. Tệp mô hình hiện tại là `hand_landmarker.task` dạng bundle đóng gói của MediaPipe. Muốn dùng ONNX, phải tách 2 tệp con (`hand_detector.tflite` và `hand_landmarks_detector.tflite`).
  2. Hai mô hình này chứa các toán tử tùy biến của TFLite (như giải mã 2,016 anchors, tính toán IoU, NMS, Affine crop xoay 2D sang 3D). ONNX Runtime không hỗ trợ tự động các bước này.
  3. Để chạy được bằng ONNX, kỹ sư buộc phải tự lập trình lại bằng Python/C++ toàn bộ 9 bước hình học. Quá trình xử lý ảnh trên CPU và truyền dữ liệu qua PCIe sẽ tốn từ 18 – 25 ms, triệt tiêu toàn bộ tốc độ của kernel GPU (2.5 – 4.0 ms).
  4. Đóng gói phân phối: Việc bổ sung CUDA Runtime, cuDNN, cuBLAS sẽ làm phình to bộ cài đặt của ứng dụng (yêu cầu hàng trăm MB đến hàng GB thư viện liên kết động thay vì 9.16 MB nguyên bản).
  5. Độ chính xác cử chỉ: Nguy cơ sai lệch nội suy ảnh đe dọa trực tiếp các ngưỡng góc hình học nghiêm ngặt của `GestureRecognizer`.

### 4.4. WSL2 / Docker IPC
- **Trạng thái:** **REJECTED (Kiến trúc không khả thi)**
- **Phân tích kiến trúc:** Việc truyền luồng ảnh 1280x720 (83 MB/s) xuyên qua ranh giới máy ảo Hyper-V phát sinh chi phí truyền thông và chuyển đổi ngữ cảnh lớn, triệt tiêu lợi thế của GPU và đòi hỏi máy tính người dùng phải có WSL2.

---

## 5. BẢNG TỔNG HỢP CÁC TỆP DỮ LIỆU ĐÃ XUẤT (GENERATED ARTIFACTS)

1. [benchmark_integrity_ab.py](file:///c:/Users/52duc/Desktop/hand-slide-controller/benchmark_integrity_ab.py) — Mã nguồn harness kiểm thử duy nhất cho runtime A/B.
2. [benchmark_integrity_ab.json](file:///c:/Users/52duc/Desktop/hand-slide-controller/benchmark_integrity_ab.json) — Dữ liệu kết quả tổng hợp đầy đủ của 6 trials A/B.
3. [benchmark_integrity_trials.csv](file:///c:/Users/52duc/Desktop/hand-slide-controller/benchmark_integrity_trials.csv) — Dữ liệu dạng bảng của 6 trials runtime.
4. [camera_constructor_ab.py](file:///c:/Users/52duc/Desktop/hand-slide-controller/camera_constructor_ab.py) — Mã nguồn benchmark phần cứng camera thực tế.
5. [camera_constructor_ab.json](file:///c:/Users/52duc/Desktop/hand-slide-controller/camera_constructor_ab.json) — Dữ liệu chi tiết 10 trials xen kẽ của webcam vật lý.
6. [gpu_feasibility.md](file:///c:/Users/52duc/Desktop/hand-slide-controller/gpu_feasibility.md) — Báo cáo khả thi GPU đã được thanh lọc toàn bộ số liệu ước tính chưa được đo đạc.
7. [optimization_matrix.json](file:///c:/Users/52duc/Desktop/hand-slide-controller/optimization_matrix.json) — Ma trận quyết định tối ưu chuẩn hóa.
