"""
Execute final repository cleanup safely.
"""
import os
import shutil
import glob
from pathlib import Path

FINAL_DOCS = [
    "FINAL_FEATURE_CONTRACT.json",
    "FINAL_OPTIMIZATION_MATRIX.json",
    "FINAL_PACKAGE_MANIFEST.csv",
    "FINAL_PERFORMANCE_REPORT.md",
    "FINAL_PRODUCTION_BASELINE.json",
    "FINAL_RELEASE_VALIDATION.json",
    "FINAL_RELEASE_VALIDATION.md",
    "FINAL_STABILITY_600S.json",
    "FINAL_VALIDATION_STATUS.json",
]

ROOT_FILES_TO_DELETE = [
    # Old experiment scripts
    "audit_metric_integrity.py",
    "benchmark_dispatch_final.py",
    "benchmark_integrity_ab.py",
    "benchmark_suite.py",
    "camera_constructor_ab.py",
    "create_benchmark_video.py",
    "download_gesture_samples.py",
    "exp_action_dispatch.py",
    "exp_camera_backends.py",
    "exp_camera_reopen_10x.py",
    "exp_camera_startup.py",
    "exp_camera_threading.py",
    "exp_compare_opencv.py",
    "exp_cpu_affinity.py",
    "exp_cpu_tuning.py",
    "exp_live_camera_latency.py",
    "exp_live_e2e_benchmark.py",
    "exp_live_gesture_harness.py",
    "exp_live_stability_10min.py",
    "exp_model_and_prep.py",
    "exp_production_v3_ab_benchmark.py",
    "exp_production_v3_accuracy.py",
    "exp_production_v3_stability.py",
    "generate_baseline_metadata.py",
    "generate_optimization_matrix.py",
    "generate_package_manifest.py",
    "measure_true_startup.py",
    "parse_stability_log.py",
    "run_10min_stability.py",
    "run_ab_experiments.py",
    "run_async_benchmark.py",
    "run_final_reopen_test.py",
    "run_production_v2_10min.py",
    "run_production_v2_suite.py",
    "run_release_stability_600s.py",
    "run_stability_test.py",
    "scratch_import_trace.py",
    "scratch_xref_importers.py",
    "scratch_xref_parse.py",
    "smoke_test_packaged_exe.py",
    "sync_packages_to_exp.py",
    "test_async_accuracy.py",
    "test_async_stability.py",
    "test_camera_backends.py",
    "test_gcs_images.py",
    "test_win32_receiver.py",
    "verify_packaged_exe_all_paths.py",
    "verify_video.py",
    # Old spec file
    "HandSlideController_optimized.spec",
    # Old benchmark & intermediate data files (json, csv)
    "ab_camera_backend.json",
    "ab_num_hands.json",
    "ab_preview.json",
    "ab_resolution.json",
    "ab_running_mode.json",
    "ab_threaded_capture.json",
    "action_dispatch_benchmark.json",
    "async_3way_comparison.json",
    "async_accuracy.json",
    "async_after.json",
    "async_before.json",
    "async_frame_metrics.csv",
    "async_polling.json",
    "async_stability.json",
    "async_stability_10min.json",
    "benchmark_after.json",
    "benchmark_before.json",
    "benchmark_dispatch_final.json",
    "benchmark_frames.csv",
    "benchmark_integrity_ab.json",
    "benchmark_integrity_trials.csv",
    "benchmark_results.json",
    "camera_backend_benchmark.json",
    "camera_backend_trials.csv",
    "camera_constructor_ab.json",
    "camera_reopen_10x.json",
    "camera_reopen_10x_final.json",
    "camera_startup_benchmark.json",
    "camera_threading_results.json",
    "cpu_affinity_benchmark.json",
    "cpu_inference_benchmark.json",
    "cpu_inference_frames.csv",
    "gesture_validation_matrix.json",
    "live_camera_frames.csv",
    "live_camera_latency.json",
    "live_e2e_latency.json",
    "model_variant_benchmark.json",
    "opencv_exp_benchmark.json",
    "optimization_matrix.json",
    "production_v2_accuracy.json",
    "production_v2_benchmark.json",
    "production_v2_frames.csv",
    "production_v2_stability.json",
    "production_v2_stability_10min.json",
    "production_v2_startup.json",
    "production_v3_accuracy.json",
    "production_v3_metric_integrity.json",
    "production_v3_metric_integrity_frames.csv",
    "production_v3_stability.json",
    "production_v3_winrt_benchmark.json",
    "production_v3_winrt_frames.csv",
    "smoke_test_packaged_exe.json",
    "stability_results.json",
    "startup_benchmark.json",
    "ultimate_optimization_matrix.json",
    # Old reports (superseded by docs/final/)
    "benchmark_integrity_report.md",
    "camera_backend_report.md",
    "gpu_feasibility.md",
    "live_stream_report.md",
    "maximum_optimization_report.md",
    "optimization_report.md",
    "production_v3_metric_integrity_report.md",
    "production_v3_report.md",
    "ultimate_optimization_report.md",
    # Old profile dumps
    "profile.txt",
    "profile_after.txt",
    "profile_async.txt",
    "profile_before.txt",
    # Temporary test assets (not used by tests/)
    "benchmark_input.mp4",
    "cam_snap.jpg",
    "pointing_up.jpg",
    "test_like.jpg",
    "test_open.jpg",
    "test_open_palm.jpg",
    "thumbs_up.jpg",
    "victory.jpg",
    "woman_hands.jpg",
]

DIRS_TO_DELETE = [
    "baseline",
    "build",
    "dist/HandSlideController_opt",
    ".venv-exp-winrt",
    ".venv-exp-opencv-standard",
    ".pytest_cache",
]

def run_cleanup():
    total_files_deleted = 0
    total_dirs_deleted = 0
    total_bytes_reclaimed = 0

    # 1. Create docs/final and move FINAL_* files
    os.makedirs("docs/final", exist_ok=True)
    moved_docs = []
    for doc in FINAL_DOCS:
        src = Path(doc)
        dst = Path("docs/final") / doc
        if src.exists():
            shutil.move(str(src), str(dst))
            moved_docs.append(doc)
            print(f"Moved: {doc} -> docs/final/{doc}")

    # 2. Delete specific root files
    for fname in ROOT_FILES_TO_DELETE:
        p = Path(fname)
        if p.exists():
            try:
                sz = p.stat().st_size
                p.unlink()
                total_files_deleted += 1
                total_bytes_reclaimed += sz
                print(f"Deleted file: {fname} ({sz} bytes)")
            except Exception as e:
                print(f"Error deleting file {fname}: {e}")

    # 3. Clean up directories
    for dname in DIRS_TO_DELETE:
        p = Path(dname)
        if p.exists():
            try:
                # Count files and size
                dir_files = 0
                dir_bytes = 0
                for root, _, files in os.walk(str(p)):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            dir_bytes += os.path.getsize(fp)
                            dir_files += 1
                        except OSError:
                            pass
                shutil.rmtree(str(p), ignore_errors=True)
                total_dirs_deleted += 1
                total_files_deleted += dir_files
                total_bytes_reclaimed += dir_bytes
                print(f"Deleted directory: {dname} ({dir_files} files, {dir_bytes / (1024*1024):.2f} MB)")
            except Exception as e:
                print(f"Error deleting directory {dname}: {e}")

    # 4. Clean up all __pycache__ directories
    for root, dirs, files in os.walk(".", topdown=False):
        if ".git" in root or ".venv" in root:
            continue
        for d in dirs:
            if d == "__pycache__":
                dp = os.path.join(root, d)
                try:
                    pyc_count = 0
                    pyc_bytes = 0
                    for f in os.listdir(dp):
                        fp = os.path.join(dp, f)
                        if os.path.isfile(fp):
                            pyc_bytes += os.path.getsize(fp)
                            pyc_count += 1
                    shutil.rmtree(dp, ignore_errors=True)
                    total_dirs_deleted += 1
                    total_files_deleted += pyc_count
                    total_bytes_reclaimed += pyc_bytes
                    print(f"Deleted __pycache__: {dp} ({pyc_count} files)")
                except Exception as e:
                    print(f"Error removing {dp}: {e}")

    mb_reclaimed = total_bytes_reclaimed / (1024 * 1024)
    print("\n================ CLEANUP SUMMARY ================")
    print(f"Total files deleted: {total_files_deleted}")
    print(f"Total directories deleted: {total_dirs_deleted}")
    print(f"Total space reclaimed: {mb_reclaimed:.2f} MB ({mb_reclaimed / 1024:.2f} GB)")
    print(f"Documentation moved to docs/final: {len(moved_docs)} files")

if __name__ == "__main__":
    run_cleanup()
