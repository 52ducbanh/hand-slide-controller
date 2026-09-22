import os
import csv
import re
import json

DIST_DIR = os.path.abspath("dist/HandSlideController")
XREF_FILE = os.path.abspath("build/HandSlideController/xref-HandSlideController.html")
MANIFEST_FILE = os.path.abspath("FINAL_PACKAGE_MANIFEST.csv")

# Load xref content if available
xref_content = ""
if os.path.exists(XREF_FILE):
    with open(XREF_FILE, "r", encoding="utf-8", errors="replace") as f:
        xref_content = f.read()

def find_importers_in_xref(pkg_name):
    if not xref_content:
        return "UNKNOWN"
    pattern = rf'<a name="{re.escape(pkg_name)}"><\/a>.*?imported by:(.*?)(?=<div class="node"|$)'
    matches = re.findall(pattern, xref_content, re.DOTALL)
    importers = []
    for m in matches:
        links = re.findall(r'href="#([^"]+)"', m)
        importers.extend(links)
    if importers:
        return "; ".join(importers[:5])
    # Check if mentioned anywhere
    if f">{pkg_name}<" in xref_content or f'"{pkg_name}"' in xref_content:
        return "INDIRECT_TRANSITIVE"
    return "NONE_FOUND"

# Scan all files in dist
records = []
total_bytes = 0

for root, dirs, files in os.walk(DIST_DIR):
    for f in files:
        full_path = os.path.join(root, f)
        rel_path = os.path.relpath(full_path, DIST_DIR)
        size_bytes = os.path.getsize(full_path)
        size_mb = round(size_bytes / (1024 * 1024), 4)
        total_bytes += size_bytes

        # Determine origin package
        parts = rel_path.split(os.sep)
        if parts[0] == "HandSlideController.exe":
            origin_pkg = "LAUNCHER"
        elif parts[0] == "_internal":
            if len(parts) > 1:
                origin_pkg = parts[1]
            else:
                origin_pkg = "_internal_root"
        else:
            origin_pkg = parts[0]

        # Clean origin_pkg name
        origin_clean = origin_pkg.split(".")[0]

        # Analyze runtime requirement and candidates
        imported_by = "INTERNAL"
        runtime_required = "YES"
        feature_required = "YES"
        candidate_for_removal = "NO"
        evidence = "Core runtime"

        if "matplotlib" in rel_path.lower():
            origin_pkg = "matplotlib"
            imported_by = "mouseinfo (pyautogui dep)"
            runtime_required = "NO"
            feature_required = "NO"
            candidate_for_removal = "YES"
            evidence = "Never imported by hand_controller; unused in production"
        elif "sounddevice" in rel_path.lower():
            origin_pkg = "sounddevice"
            imported_by = "mediapipe.tasks.python.audio.core.audio_record"
            runtime_required = "NO"
            feature_required = "NO"
            candidate_for_removal = "YES"
            evidence = "audio_feedback uses stdlib winsound; audio tasks unused"
        elif any(k in rel_path.lower() for k in ["tcl", "tk", "_tkinter"]):
            origin_pkg = "tkinter/tcl"
            imported_by = "mouseinfo/pymsgbox (pyautogui deps)"
            runtime_required = "NO"
            feature_required = "NO"
            candidate_for_removal = "YES"
            evidence = "No GUI toolkit required; OpenCV handles all window rendering"
        elif "_avif" in rel_path.lower() or "_webp" in rel_path.lower():
            origin_pkg = "PIL.codecs"
            imported_by = "PIL.features"
            runtime_required = "NO"
            feature_required = "NO"
            candidate_for_removal = "YES"
            evidence = "Optional image codecs not needed for runtime RGB processing"
        elif "certifi" in rel_path.lower():
            origin_pkg = "certifi"
            imported_by = "urllib3/ssl"
            runtime_required = "NO"
            feature_required = "NO"
            candidate_for_removal = "CONDITIONAL"
            evidence = "Offline application without network requests"
        elif any(k in rel_path.lower() for k in ["libcrypto", "libssl", "_ssl"]):
            origin_pkg = "openssl"
            imported_by = "python_ssl"
            runtime_required = "CONDITIONAL"
            feature_required = "NO"
            candidate_for_removal = "CONDITIONAL"
            evidence = "SSL/crypto library for network HTTPS, offline app"
        elif "cv2" in rel_path.lower():
            origin_pkg = "opencv"
            imported_by = "hand_controller.camera, hand_controller.renderer"
            runtime_required = "YES"
            feature_required = "YES"
            candidate_for_removal = "NO"
            evidence = "Primary camera conversion (NV12->RGB), fallback MSMF, imshow"
        elif "mediapipe" in rel_path.lower():
            origin_pkg = "mediapipe"
            imported_by = "hand_controller.app"
            runtime_required = "YES"
            feature_required = "YES"
            candidate_for_removal = "NO"
            evidence = "HandLandmarker core AI inference engine"
        elif "winrt" in rel_path.lower():
            origin_pkg = "winrt"
            imported_by = "hand_controller.camera"
            runtime_required = "YES"
            feature_required = "YES"
            candidate_for_removal = "NO"
            evidence = "Primary Windows MediaCapture real-time camera backend"
        elif "numpy" in rel_path.lower():
            origin_pkg = "numpy"
            imported_by = "cv2, mediapipe, hand_controller"
            runtime_required = "YES"
            feature_required = "YES"
            candidate_for_removal = "NO"
            evidence = "NDArray buffer management for frame processing"
        elif "hand_landmarker.task" in rel_path:
            origin_pkg = "model_asset"
            imported_by = "hand_controller.config"
            runtime_required = "YES"
            feature_required = "YES"
            candidate_for_removal = "NO"
            evidence = "Official Google MediaPipe Hand Landmarker neural network bundle"

        records.append({
            "path": rel_path,
            "size_bytes": size_bytes,
            "size_mb": size_mb,
            "origin_package": origin_pkg,
            "imported_by": imported_by,
            "runtime_required": runtime_required,
            "feature_required": feature_required,
            "candidate_for_removal": candidate_for_removal,
            "evidence": evidence
        })

# Sort descending by size
records.sort(key=lambda x: x["size_bytes"], reverse=True)

with open(MANIFEST_FILE, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=[
        "path", "size_bytes", "size_mb", "origin_package",
        "imported_by", "runtime_required", "feature_required",
        "candidate_for_removal", "evidence"
    ])
    writer.writeheader()
    writer.writerows(records)

print(f"Generated {MANIFEST_FILE} with {len(records)} entries. Total size: {round(total_bytes/(1024*1024), 2)} MB")

# Print top 20 largest files
print("\nTop 20 largest files in dist:")
for r in records[:20]:
    print(f"  {r['size_mb']:>8.2f} MB | {r['origin_package']:<15} | {r['path']}")
