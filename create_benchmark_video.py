"""Generates the standardized multi-segment benchmark video benchmark_input.mp4.
Contains 8 distinct segments (800 frames @ 30 FPS, 1280x720):
1. No hand (Idle background) - 100 frames
2. 1 Hand steady (Neutral) - 100 frames
3. Scissors gesture (Trigger Left) - 100 frames
4. Like gesture (Trigger Right) - 100 frames
5. Open Palm gesture (Trigger Blackout) - 100 frames
6. Fast motion (Speed stress test) - 100 frames
7. 2 Hands simultaneous - 100 frames
8. Far hand (Distance/scale test) - 100 frames
"""

import cv2
import numpy as np

def place_image(canvas, img, center_x, center_y, scale=1.0):
    """Place img onto canvas centered at (center_x, center_y) with scale."""
    h, w = img.shape[:2]
    nw, nh = max(10, int(w * scale)), max(10, int(h * scale))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
    
    ch, cw = canvas.shape[:2]
    x1 = center_x - nw // 2
    y1 = center_y - nh // 2
    x2 = x1 + nw
    y2 = y1 + nh
    
    # Clip coordinates
    src_x1 = max(0, -x1)
    src_y1 = max(0, -y1)
    src_x2 = nw - max(0, x2 - cw)
    src_y2 = nh - max(0, y2 - ch)
    
    dst_x1 = max(0, x1)
    dst_y1 = max(0, y1)
    dst_x2 = min(cw, x2)
    dst_y2 = min(ch, y2)
    
    if dst_x2 > dst_x1 and dst_y2 > dst_y1 and src_x2 > src_x1 and src_y2 > src_y1:
        canvas[dst_y1:dst_y2, dst_x1:dst_x2] = resized[src_y1:src_y2, src_x1:src_x2]

def create_benchmark_video(out_path="benchmark_input.mp4", width=1280, height=720, fps=30):
    print("Loading reference assets...")
    img_bg = cv2.imread("cam_snap.jpg")
    if img_bg is None or img_bg.shape[:2] != (height, width):
        img_bg = cv2.resize(img_bg, (width, height)) if img_bg is not None else np.full((height, width, 3), 40, dtype=np.uint8)
    
    img_scissors = cv2.imread("victory.jpg")
    img_like = cv2.imread("thumbs_up.jpg")
    img_open = cv2.imread("test_open_palm.jpg")
    img_2hands = cv2.imread("woman_hands.jpg")
    
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(out_path, fourcc, fps, (width, height))
    
    frames_per_seg = 100
    
    # Segment 1: No Hand (100 frames)
    print("Writing Segment 1: No Hand...")
    for _ in range(frames_per_seg):
        out.write(img_bg)
        
    # Segment 2: 1 Hand Steady (100 frames)
    print("Writing Segment 2: 1 Hand Steady...")
    for _ in range(frames_per_seg):
        frame = img_bg.copy()
        place_image(frame, img_like, width // 2, height // 2, scale=1.0)
        out.write(frame)
        
    # Segment 3: Scissors Gesture (100 frames: steady -> left movement -> steady)
    print("Writing Segment 3: Scissors Gesture...")
    for i in range(frames_per_seg):
        frame = img_bg.copy()
        if i < 25:
            cx = width // 2
        elif i < 65:
            progress = (i - 25) / 40.0
            cx = int(width // 2 - progress * 260)
        else:
            cx = width // 2 - 260
        place_image(frame, img_scissors, cx, height // 2, scale=1.1)
        out.write(frame)
        
    # Segment 4: Like Gesture (100 frames: steady -> right movement -> steady)
    print("Writing Segment 4: Like Gesture...")
    for i in range(frames_per_seg):
        frame = img_bg.copy()
        if i < 25:
            cx = width // 2
        elif i < 65:
            progress = (i - 25) / 40.0
            cx = int(width // 2 + progress * 260)
        else:
            cx = width // 2 + 260
        place_image(frame, img_like, cx, height // 2, scale=1.0)
        out.write(frame)
        
    # Segment 5: Open Palm Gesture (100 frames: steady -> hold -> steady)
    print("Writing Segment 5: Open Palm Gesture...")
    for i in range(frames_per_seg):
        frame = img_bg.copy()
        place_image(frame, img_open, width // 2, height // 2, scale=0.45)
        out.write(frame)
        
    # Segment 6: Fast Motion (100 frames: rapid sine wave oscillation across frame)
    print("Writing Segment 6: Fast Motion...")
    for i in range(frames_per_seg):
        frame = img_bg.copy()
        cx = int(width // 2 + 400 * np.sin(i * 0.35))
        cy = int(height // 2 + 100 * np.cos(i * 0.35))
        place_image(frame, img_scissors, cx, cy, scale=1.0)
        out.write(frame)
        
    # Segment 7: 2 Hands (100 frames: both hands present simultaneously)
    print("Writing Segment 7: 2 Hands...")
    for _ in range(frames_per_seg):
        frame = img_bg.copy()
        place_image(frame, img_2hands, width // 2, height // 2, scale=0.7)
        out.write(frame)
        
    # Segment 8: Far Hand (100 frames: hand scaled down to distance threshold)
    print("Writing Segment 8: Far Hand...")
    for _ in range(frames_per_seg):
        frame = img_bg.copy()
        place_image(frame, img_scissors, width // 2, height // 2, scale=0.60)
        out.write(frame)
        
    out.release()
    print(f"Successfully generated {out_path} (800 frames, 1280x720, 30 FPS).")

if __name__ == "__main__":
    create_benchmark_video()
