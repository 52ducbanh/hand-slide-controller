import time
import urllib.request
import json
import urllib.parse
import cv2
import mediapipe as mp
from hand_controller.config import AppConfig
from hand_controller.gestures import GestureRecognizer

def search_files(term):
    api = f"https://commons.wikimedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(term)}&srnamespace=6&format=json"
    req = urllib.request.Request(api, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) HandBenchSuite/2.0 (contact: test@example.com)'})
    with urllib.request.urlopen(req) as r:
        d = json.loads(r.read().decode())
        return [i['title'] for i in d.get('query', {}).get('search', [])[:10]]

def download_file(title, out_path):
    api = f"https://commons.wikimedia.org/w/api.php?action=query&titles={urllib.parse.quote(title)}&prop=imageinfo&iiprop=url&format=json"
    req = urllib.request.Request(api, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) HandBenchSuite/2.0 (contact: test@example.com)'})
    with urllib.request.urlopen(req) as r:
        d = json.loads(r.read().decode())
        pages = d['query']['pages']
        url = list(pages.values())[0]['imageinfo'][0]['url']
    
    time.sleep(1.5)
    req2 = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) HandBenchSuite/2.0 (contact: test@example.com)'})
    with urllib.request.urlopen(req2) as resp, open(out_path, 'wb') as f:
        f.write(resp.read())
    return True

def test_image(path):
    img = cv2.imread(path)
    if img is None:
        return []
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    lm = mp.tasks.vision.HandLandmarker.create_from_options(
        mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path='hand_landmarker.task'),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_hands=2,
            min_hand_detection_confidence=0.20,
        )
    )
    res = lm.detect(mp_img)
    rec = GestureRecognizer(AppConfig().gesture)
    gestures = []
    if res.hand_landmarks:
        for lms in res.hand_landmarks:
            gr = rec.classify(lms)
            gestures.append(gr.gesture.name)
    return gestures

if __name__ == '__main__':
    print("Testing existing test_like.jpg:", test_image("test_like.jpg"))
    
    # Search for V-sign
    print("Searching for V sign...")
    v_files = search_files('"victory hand" OR "peace sign" photo')
    print("Candidates:", v_files[:5])
    for vf in v_files[:5]:
        if vf.lower().endswith(('.jpg', '.jpeg', '.png')):
            try:
                print(f"Trying {vf}...")
                download_file(vf, "test_scissors.jpg")
                g = test_image("test_scissors.jpg")
                print(f"Result for {vf}: {g}")
                if "SCISSORS" in g:
                    print("Found SCISSORS!")
                    break
            except Exception as e:
                print(f"Err: {e}")
                time.sleep(2)

    # Search for open hand / 5 fingers
    print("Searching for open hand...")
    o_files = search_files('open palm hand gesture photo')
    print("Candidates:", o_files[:5])
    for of in o_files[:5]:
        if of.lower().endswith(('.jpg', '.jpeg', '.png')):
            try:
                print(f"Trying {of}...")
                download_file(of, "test_open.jpg")
                g = test_image("test_open.jpg")
                print(f"Result for {of}: {g}")
                if "OPEN_PALM" in g:
                    print("Found OPEN_PALM!")
                    break
            except Exception as e:
                print(f"Err: {e}")
                time.sleep(2)
