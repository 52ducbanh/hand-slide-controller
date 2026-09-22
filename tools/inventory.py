"""
Inventory and classify every file in the repository.
"""
import os
import sys
import json
import subprocess
from datetime import datetime

KEEP_PRODUCTION_EXACT = {
    "main.py",
    "hand_landmarker.task",
    "HandSlideController.spec",
    ".gitignore",
    "run.bat",
    "build_exe.bat",
    "README.md",
    "requirements.txt",
}

FINAL_DOCS = {
    "FINAL_FEATURE_CONTRACT.json",
    "FINAL_OPTIMIZATION_MATRIX.json",
    "FINAL_PACKAGE_MANIFEST.csv",
    "FINAL_PERFORMANCE_REPORT.md",
    "FINAL_PRODUCTION_BASELINE.json",
    "FINAL_RELEASE_VALIDATION.json",
    "FINAL_RELEASE_VALIDATION.md",
    "FINAL_STABILITY_600S.json",
    "FINAL_VALIDATION_STATUS.json",
}

def get_tracked_files():
    res = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return set(res.stdout.splitlines())

def scan():
    tracked = get_tracked_files()
    inventory = []

    # Gather production file content for reference checking
    prod_refs = []
    for root, _, files in os.walk("hand_controller"):
        for f in files:
            p = os.path.join(root, f)
            with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                prod_refs.append(fh.read())
    with open("main.py", "r", encoding="utf-8", errors="ignore") as fh:
        prod_refs.append(fh.read())
    with open("HandSlideController.spec", "r", encoding="utf-8", errors="ignore") as fh:
        spec_content = fh.read()

    test_refs = []
    for root, _, files in os.walk("tests"):
        for f in files:
            p = os.path.join(root, f)
            with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                test_refs.append(fh.read())

    prod_text = "\n".join(prod_refs)
    test_text = "\n".join(test_refs)

    for root, dirs, files in os.walk("."):
        # Ignore .git and venvs
        if ".git" in root or ".venv" in root:
            continue
        for f in files:
            rel = os.path.normpath(os.path.join(root, f)).replace("\\", "/")
            if rel.startswith("tools/"):
                continue
            size = os.path.getsize(rel)
            ext = os.path.splitext(rel)[1].lower()
            mtime = datetime.fromtimestamp(os.path.getmtime(rel)).isoformat()
            is_tracked = rel in tracked

            basename = os.path.basename(rel)

            ref_prod = (rel in prod_text or basename in prod_text)
            ref_test = (rel in test_text or basename in test_text)
            ref_spec = (rel in spec_content or basename in spec_content)

            # Classification logic
            group = None
            reason = ""

            if (
                rel.startswith("hand_controller/")
                or rel in KEEP_PRODUCTION_EXACT
                or rel.startswith("dist/HandSlideController/")
            ):
                group = "A"
                reason = "Core production code, configuration, task asset, or packaged distribution"
            elif rel.startswith("tests/"):
                group = "B"
                reason = "Permanent unit test suite"
            elif rel in FINAL_DOCS or rel.startswith("docs/final/"):
                group = "C"
                reason = "Final verified engineering report or validation artifact"
            elif (
                rel.startswith("exp_")
                or rel.startswith("benchmark_")
                or rel.startswith("audit_")
                or rel.startswith("run_")
                or rel.startswith("test_")
                or rel.startswith("scratch_")
                or rel.startswith("smoke_test_")
                or rel.startswith("verify_")
                or rel.startswith("generate_")
                or rel.startswith("parse_")
                or rel.startswith("sync_")
                or rel.startswith("measure_")
                or rel.startswith("create_")
                or rel.startswith("download_")
                or rel.startswith("camera_")
                or rel.startswith("cpu_")
                or rel.startswith("async_")
                or rel.startswith("live_")
                or rel.startswith("production_v2_")
                or rel.startswith("production_v3_")
                or rel.startswith("ultimate_")
                or rel.startswith("model_variant_")
                or rel.startswith("opencv_exp_")
                or rel.startswith("action_dispatch_")
                or rel.startswith("stability_")
                or rel.startswith("startup_")
                or rel.startswith("ab_")
                or rel.startswith("optimization_")
                or rel.startswith("maximum_")
                or rel.startswith("baseline/")
                or rel == "HandSlideController_optimized.spec"
                or ext in [".jpg", ".mp4", ".csv"]
                or (rel.startswith("dist/") and "HandSlideController_opt" in rel)
                or rel.startswith("build/")
                or rel.startswith("__pycache__/")
                or rel.startswith(".pytest_cache/")
            ):
                group = "D"
                reason = "Obsolete experiment script, interim report, raw trace, or temporary benchmark asset"
            else:
                group = "E"
                reason = "Requires explicit review"

            inventory.append({
                "path": rel,
                "size_bytes": size,
                "extension": ext,
                "git_tracked": is_tracked,
                "last_modified": mtime,
                "referenced_by_production": ref_prod,
                "referenced_by_tests": ref_test,
                "referenced_by_spec": ref_spec,
                "group": group,
                "reason": reason
            })

    return inventory

if __name__ == "__main__":
    inv = scan()
    groups = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
    bytes_by_group = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
    for item in inv:
        groups[item["group"]] += 1
        bytes_by_group[item["group"]] += item["size_bytes"]

    print("=== INVENTORY SCAN COMPLETE ===")
    for g, count in sorted(groups.items()):
        mb = bytes_by_group[g] / (1024 * 1024)
        print(f"Group {g}: {count} files ({mb:.2f} MB)")

    if groups["E"] > 0:
        print("\nReview Uncertain items (Group E):")
        for item in inv:
            if item["group"] == "E":
                print(f"  {item['path']} ({item['size_bytes']} bytes)")

    with open("tools/inventory.json", "w", encoding="utf-8") as fh:
        json.dump(inv, fh, indent=2)
    print("\nSaved detailed inventory to tools/inventory.json")
