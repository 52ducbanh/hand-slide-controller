import cv2
import mediapipe as mp
from hand_controller.config import AppConfig
from hand_controller.gestures import GestureRecognizer

cap = cv2.VideoCapture('benchmark_input.mp4')
rec = GestureRecognizer(AppConfig().gesture)
lm = mp.tasks.vision.HandLandmarker.create_from_options(
    mp.tasks.vision.HandLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path='hand_landmarker.task'),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_hands=2,
    )
)

seg_counts = {i: {'hands': 0, 'gestures': {}} for i in range(1, 9)}
f_idx = 0
while True:
    ret, frame = cap.read()
    if not ret:
        break
    seg = (f_idx // 100) + 1
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    res = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), f_idx * 33 + 1)
    num_hands = len(res.hand_landmarks) if res.hand_landmarks else 0
    seg_counts[seg]['hands'] += num_hands
    if res.hand_landmarks:
        for l in res.hand_landmarks:
            g = rec.classify(l).gesture.name
            seg_counts[seg]['gestures'][g] = seg_counts[seg]['gestures'].get(g, 0) + 1
    f_idx += 1
cap.release()
print('Total frames processed:', f_idx)
for seg, data in seg_counts.items():
    print(f"Seg {seg}: total hands={data['hands']}, gestures={data['gestures']}")
