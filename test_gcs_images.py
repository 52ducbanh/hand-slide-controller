import urllib.request
import cv2
import mediapipe as mp
from hand_controller.config import AppConfig
from hand_controller.gestures import GestureRecognizer

base_urls = [
    "https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/thumbs_up.jpg",
    "https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/victory.jpg",
    "https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/pointing_up.jpg",
    "https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/open_palm.jpg",
    "https://storage.googleapis.com/mediapipe-tasks/hand_landmarker/woman_hands.jpg",
    "https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/sample_image.jpg",
]

for url in base_urls:
    fname = url.split('/')[-1]
    try:
        urllib.request.urlretrieve(url, fname)
        img = cv2.imread(fname)
        if img is not None:
            print(f"Successfully downloaded {fname} shape {img.shape}")
        else:
            print(f"Downloaded {fname} but invalid image")
    except Exception as e:
        print(f"Failed {fname}: {e}")
