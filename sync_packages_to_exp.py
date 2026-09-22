import os
import shutil

src_site = os.path.abspath(".venv/Lib/site-packages")
dst_site = os.path.abspath(".venv-exp-opencv-standard/Lib/site-packages")

print(f"Syncing packages from {src_site} to {dst_site}...")

skip_prefixes = [
    "cv2",
    "opencv_",
    "numpy",
    "__pycache__"
]

for item in os.listdir(src_site):
    if any(item.startswith(p) for p in skip_prefixes):
        continue
    src_path = os.path.join(src_site, item)
    dst_path = os.path.join(dst_site, item)
    if os.path.exists(dst_path):
        continue
    if os.path.isdir(src_path):
        shutil.copytree(src_path, dst_path)
    else:
        shutil.copy2(src_path, dst_path)

# Also copy scripts if pytest/pyinstaller needed
src_bin = os.path.abspath(".venv/Scripts")
dst_bin = os.path.abspath(".venv-exp-opencv-standard/Scripts")
for script in ["pytest.exe", "pyinstaller.exe"]:
    s_path = os.path.join(src_bin, script)
    d_path = os.path.join(dst_bin, script)
    if os.path.exists(s_path) and not os.path.exists(d_path):
        shutil.copy2(s_path, d_path)

print("Sync completed!")
