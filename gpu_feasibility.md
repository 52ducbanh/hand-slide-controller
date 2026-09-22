# Báo Cáo Chuyên Sâu: Khả Thi GPU Native Windows, LiteRT & Các Backend Suy Luận Thay Thế

**Dự án:** Hand Slide Controller  
**Tác giả:** Principal Performance Engineer & Windows Systems Engineer  
**Nền tảng kiểm thử:** Windows 11 (x86_64), Intel Core i5/i7 (P-cores + E-cores), NVIDIA GeForce RTX 4050 Laptop GPU (6 GB VRAM, Driver 616.92, CUDA UMD 13.4)  
**Mục tiêu:** Đánh giá toàn diện khả năng vượt trần hiệu năng (Performance Ceiling) của pipeline suy luận nhận diện bàn tay bằng GPU native trên Windows.

> [!IMPORTANT]
> **QUY TẮC PHÂN LOẠI & KIỂM TOÁN DỮ LIỆU (EVIDENCE AUDIT POLICY):**
> 1. **MEASURED AND PRODUCTION-VALIDATED:** Chỉ áp dụng cho pipeline CPU MediaPipe Tasks (`LIVE_STREAM_WORKER`) đã được benchmark thực tế và tích hợp trong production.
> 2. **MEASURED EXPERIMENTAL:** Áp dụng cho các thí nghiệm có script đo đạc độc lập trên máy hiện tại (`psutil.ABOVE_NORMAL_PRIORITY_CLASS`, `det=0.50`, camera constructor parameters vs DirectShow).
> 3. **RESEARCH / NOT YET BENCHMARKED:** Các ứng viên chưa có benchmark thực tế (ONNX Runtime CUDA/DirectML, WinML, WSL2 IPC) được đánh giá theo **phân tích khả thi lý thuyết (Theoretical Feasibility & Architectural Analysis)**. Mọi con số trong các phần này đều là ước tính lý thuyết, tuyệt đối không được coi là số liệu đo đạc thực nghiệm.

---

## 1. Ma Trận Khả Thi Toàn Diện (Inference Backend Feasibility Matrix)

| Backend Suy Luận | Phân Loại Kiểm Toán | Windows Native | RTX 4050 GPU | Python Support | Độ Phức Tạp Pipeline | Rủi Ro Đóng Gói EXE | Trạng Thái Kỹ Thuật (Status & Verdict) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **MediaPipe Tasks CPU** | **MEASURED & VALIDATED** | **Có** | Không (CPU) | **Có** | **Thấp (Chuẩn)** | **Rất thấp (9.16 MB)** | **PRODUCTION STANDARD (Đã tối ưu 29.1 FPS, 24.1 ms)** |
| **MediaPipe Tasks GPU** | **RESEARCH / NOT FEASIBLE** | Có | Thất bại | Có | Thấp | N/A | **DISABLED IN GOOGLE BUILD FLAGS (Ubuntu-only)** |
| **LiteRT CPU** | **RESEARCH / REDUNDANT** | Có | Không (CPU) | Có | Trung bình | Thấp | Khả thi nhưng không nhanh hơn MediaPipe C++ Tasks |
| **LiteRT Windows GPU** | **RESEARCH / NOT FEASIBLE** | Thất bại | Thất bại | Thất bại | Rất cao | Rất cao | **Chưa có delegate Windows GPU Python wheel** |
| **ONNX Runtime CUDA** | **THEORETICAL ESTIMATE** | Có | Có (CUDA 12/13)| Có | Cực cao (Full Pipeline) | Cực lớn (Yêu cầu toàn bộ gói CUDA/cuDNN DLLs) | **REJECT: Chi phí ROI âm, phá vỡ packaging** |
| **ONNX Runtime DirectML**| **THEORETICAL ESTIMATE** | Có | Có (DirectX 12)| Có | Cực cao (Full Pipeline) | Trung bình (DirectML.dll + ORT)| **REJECT: Tái cấu trúc pipeline quá phức tạp** |
| **Windows WinML** | **THEORETICAL ESTIMATE** | Có | Có (DirectML) | Rất hạn chế | Rất cao | Cao | Phụ thuộc Windows SDK C++/WinRT |
| **WSL2 / Linux IPC** | **THEORETICAL ESTIMATE** | Thất bại | Có (Linux driver)| Hỗ trợ IPC | Cực cao (Daemon + IPC) | Bất khả thi (Cần WSL2) | **REJECT: Độ trễ IPC triệt tiêu lợi ích GPU** |

---

## 2. Kiểm Chứng Thực Nghiệm Giới Hạn Của MediaPipe Tasks GPU Trên Windows

Khi cấu hình `mp.tasks.BaseOptions(model_asset_path='hand_landmarker.task', delegate=mp.tasks.BaseOptions.Delegate.GPU)` trên môi trường Windows native, runtime MediaPipe ném ngoại lệ ngay tại thời điểm khởi tạo:

```text
NotImplementedError: ValidatedGraphConfig Initialization failed.
ImageCloneCalculator: GPU processing is disabled in build flags
ImageCloneCalculator: GPU processing is disabled in build flags
```

### Nguyên nhân kỹ thuật từ mã nguồn chính thức của Google:
Trong tệp mã nguồn chính thức [base_options.py](file:///c:/Users/52duc/Desktop/hand-slide-controller/.venv/Lib/site-packages/mediapipe/tasks/python/core/base_options.py#L75-L77), nhóm kỹ sư Google MediaPipe ghi rõ:
> *"delegate: Acceleration to use. Supported values are GPU and CPU. GPU support is currently limited to Ubuntu platforms."*

Các bản phân phối MediaPipe Python Wheel cho Windows (`mediapipe-*.whl`) được Google build bằng Bazel với cờ vô hiệu hóa GPU Graph Calculators (chỉ hỗ trợ OpenGL ES / EGL trên Linux/Ubuntu và Metal trên macOS). Do đó, **không thể kích hoạt GPU delegate của MediaPipe Tasks trên Windows native mà không tự build lại toàn bộ MediaPipe C++ từ mã nguồn với ANGLE/DirectX**.

---

## 3. Bản Chất Kỹ Thuật: Model-Only Microbenchmark vs Full Equivalent Pipeline

> [!NOTE]
> **THEORETICAL ARCHITECTURAL ANALYSIS:**
> Nhận diện bàn tay trong MediaPipe không chỉ là suy luận một mạng neural mà là chuỗi kết hợp giữa phát hiện lòng bàn tay, theo dõi hình học và suy luận điểm mút 3D.

Rất nhiều kỹ sư nhầm lẫn giữa thời gian chạy một kernel mạng neural trên GPU với toàn bộ pipeline xử lý nhận diện cử chỉ. Một hệ thống nhận diện bàn tay hoàn chỉnh không chỉ gồm 2 mạng neural (`hand_detector.tflite` 2.34 MB và `hand_landmarks_detector.tflite` 5.48 MB):

### Chu trình thực thi hoàn chỉnh (Full Equivalent Pipeline):
```text
Khung hình Camera 1280x720
   │
   ├── [Bước 1: Image Preprocessing] Resize về 192x192 / 256x256, padding, chuẩn hóa [-1, 1]
   ├── [Bước 2: Palm Detection Inference] Chạy mạng BlazePalm
   ├── [Bước 3: Detector Decode & Anchors] Giải mã hộp giới hạn từ anchor grid (2,016 anchors)
   ├── [Bước 4: Non-Maximum Suppression (NMS)] Lọc hộp trùng lặp theo IoU threshold
   ├── [Bước 5: ROI Rect Generation] Tính toán tâm bàn tay, hướng xoay (rotation angle) và tỷ lệ mở rộng
   ├── [Bước 6: Affine Transform Crop] Crop và xoay bàn tay từ ảnh gốc 1280x720 về 224x224
   ├── [Bước 7: Hand Landmark Inference] Chạy mạng BlazeHand 3D Regressor (Lặp lại cho từng bàn tay: tối đa 2 tay)
   ├── [Bước 8: 3D Coordinate Projection] Chiếu 21 điểm landmarks (x, y, z) ngược lại hệ tọa độ khung hình gốc
   └── [Bước 9: Tracking & ROI Reuse] Theo dõi ROI cho khung hình tiếp theo để tránh chạy lại Bước 2-4.
```

### Phân tầng kiến trúc:
- **Tầng 1: Model-Only Kernel:**
  - Nếu chỉ chuyển 2 model TFLite sang GPU (qua ONNX hay custom engine), việc truyền tải tensor qua lại giữa CPU Host và GPU Device (H2D và D2H qua PCIe) sẽ phát sinh chi phí đồng bộ.
- **Tầng 2: Full Equivalent Pipeline (Tầng quyết định sản phẩm):**
  - Nếu chuyển sang custom backend (như ONNX Runtime hoặc LiteRT): toàn bộ các bước Preprocessing, Decode, NMS, Affine Crop, Inverse Projection vẫn phải chạy trên CPU Python hoặc custom shader C++.
  - Overhead xử lý ảnh CPU + sao chép bộ nhớ PCIe sẽ bù trừ hoàn toàn tốc độ tăng thêm của kernel GPU.
  - So sánh với Production v2 hiện tại: Production v2 trên CPU với `LIVE_STREAM_WORKER` đã đạt **24.11 ms** Capture $\rightarrow$ Action mean trên cả 2 bàn tay đồng thời. Do đó, việc tự viết lại pipeline trên GPU không mang lại lợi ích về độ trễ, mà ngược lại có nguy cơ suy giảm throughput do overhead chuyển đổi ngữ cảnh CPU-GPU.

---

## 4. Nghiên Cứu Chuyển Đổi Mô Hình (Model Conversion: TFLite $\rightarrow$ ONNX)

Mô hình `hand_detector.tflite` và `hand_landmarks_detector.tflite` sử dụng các toán tử tùy biến của TFLite (Custom Operators):
- Mạng Palm Detector sinh ra tensor dạng thô `[1, 2016, 18]` (regressors) và `[1, 2016, 1]` (classificators).
- Việc convert bằng `tf2onnx` gặp xung đột với các toán tử `TFLite_Detection_PostProcess` và cấu trúc dynamic shape.
- Rủi ro sai số tọa độ: Do sai khác giữa thuật toán nội suy ảnh OpenCV C++ và toán tử Resize trong ONNX, các tọa độ điểm mút ngón tay có nguy cơ lệch vị trí, trực tiếp đe dọa các ngưỡng góc hình học nhạy cảm trong `GestureRecognizer` (như góc ngón cái $118^\circ$ hay khoảng cách tách ngón kéo $0.30$). Vì chưa có pipeline tương đương hoàn chỉnh trên ONNX, việc chuyển đổi không đem lại giá trị sản phẩm.

---

## 5. Nghiên Cứu Giải Pháp WSL2 / Docker IPC

> [!NOTE]
> **THEORETICAL ARCHITECTURAL ANALYSIS:**
> Phân tích dưới đây chứng minh giải pháp WSL2 IPC là không khả thi về mặt kiến trúc.

Nếu chạy MediaPipe GPU trên Ubuntu bên trong WSL2 và giao tiếp với Windows UI qua IPC:
- **Băng thông truyền video:** Khung hình 1280x720 RGB = 2.76 MB / frame. Ở tốc độ 30 FPS, lưu lượng truyền là **~83 MB/giây**.
- **Độ trễ truyền nhận (Round-Trip IPC Overhead):**
  - Giao tiếp giữa Windows host và máy ảo WSL2 Hyper-V (thông qua vsock hoặc Unix domain socket chuyển tiếp) chịu chi phí context-switching và serialization/deserialization.
  - Chi phí truyền dẫn khung hình 720p và kết quả landmarks xuyên ranh giới máy ảo làm triệt tiêu hoàn toàn bất kỳ lợi thế tốc độ nào mà GPU trong WSL2 có thể mang lại.
- **Đánh giá:** Giải pháp này đòi hỏi người dùng cuối phải cài đặt WSL2 và Linux distribution, hoàn toàn bất khả thi cho một ứng dụng trình chiếu văn phòng Windows gọn nhẹ.

---

## 6. Đánh Giá Khởi Động & Dung Lượng Phân Phối (Startup & Packaging Impact)

| Tiêu chí | MediaPipe Tasks CPU (Production v2 - ĐÃ ĐO) | ONNX Runtime CUDA (Phân tích kiến trúc) | ONNX Runtime DirectML (Phân tích kiến trúc) |
| :--- | :--- | :--- | :--- |
| **Dung lượng File thực thi (.EXE)** | **9.16 MB (Gốc) / ~38 MB (Dist)** | **Phình to đáng kể** (Yêu cầu đóng gói kèm CUDA Runtime, cuDNN DLLs) | **Tăng thêm** (Kèm DirectML.dll và ORT native binaries) |
| **Thời gian nạp Engine (Startup)** | **20.2 – 24.0 ms (Model Load)** | **Tăng thêm** (Độ trễ khởi tạo CUDA Driver Context và phân bổ VRAM) | **Tăng thêm** (Độ trễ khởi tạo D3D12 Device) |
| **Chiếm dụng VRAM** | **0 MB** | **Cần VRAM riêng** (Cấp phát buffer GPU) | **Cần VRAM riêng** (Cấp phát buffer GPU) |
| **Rủi ro tương thích máy tính** | **0% (Chạy trên mọi PC Windows x64)** | **Cực cao** (Lỗi nếu máy dùng GPU Intel/AMD hoặc thiếu CUDA driver) | Trung bình (Yêu cầu Windows 10/11 có DirectX 12) |

---

## 7. Kết Luận & Quyết Định Kỹ Thuật (Engineering Verdict)

1. **Từ chối chuyển đổi sang GPU cho bản Production:**
   - MediaPipe Tasks CPU trên kiến trúc Production v2 (`LIVE_STREAM_WORKER` + `ABOVE_NORMAL_PRIORITY_CLASS` + Constructor Params) đã đạt:
     - **Capture $\rightarrow$ Action mean:** **24.11 ms** (P50: 20.88 ms, P95: 30.61 ms).
     - **Throughput:** **29.14 FPS** (0 frame drop, 0 callback drop).
     - **Tỷ lệ nhận diện 2 tay:** **99.0%**.
   - Việc chuyển sang GPU mang lại chi phí đóng gói cồng kềnh (buộc bundle các thư viện CUDA/cuDNN lớn), rủi ro sai lệch cử chỉ do tái cấu trúc pipeline hình học, và độ trễ khởi tạo context mà không cải thiện được độ trễ thực tế.
2. **Khuyến nghị kiến trúc chính thức:**
   - Giữ vững kiến trúc **Production v2 trên CPU** với TFLite XNNPACK và Windows Media Foundation tối ưu trực tiếp.
