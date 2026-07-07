#!/usr/bin/env python3
"""
extract_faces.py — Offline MTCNN face crop extraction for FF++ training.

Reads FF++ videos (real + Deepfakes), samples every 5th frame, runs MTCNN,
and saves 380×380 JPEG crops to:
    face_crops/real/<video_stem>/frame_NNNNNN.jpg
    face_crops/fake/<video_stem>/frame_NNNNNN.jpg

Expected output: ~180 000 crops from 1000 real + 2000 fake videos.

Requirements: facenet-pytorch, opencv-python, torch
  facenet-pytorch does NOT build on Python 3.14. Run via the vision-service
  Docker image or a Python 3.11 venv:

    # Docker (recommended):
    docker compose build vision-service
    docker run --rm --gpus all \\
      -v $(pwd)/datasets:/datasets:ro \\
      -v $(pwd)/face_crops:/face_crops \\
      -v $(pwd)/extract_faces.py:/extract_faces.py \\
      ardd_tp-vision-service python /extract_faces.py --gpu

    # Python 3.11 venv:
    python3.11 -m venv .venv311 && source .venv311/bin/activate
    pip install facenet-pytorch opencv-python torch
    python extract_faces.py --gpu

Usage:
    python extract_faces.py [--gpu] [--frame-step N]
"""

import argparse
import sys
from pathlib import Path

import cv2
import torch
from PIL import Image

DATASET_ROOT = Path(__file__).parent / "datasets" / "ff++"
REAL_SRC = DATASET_ROOT / "original_sequences" / "youtube" / "c23" / "videos"
FAKE_SRC = DATASET_ROOT / "manipulated_sequences" / "Deepfakes" / "c23" / "videos"
OUT_ROOT  = Path(__file__).parent / "face_crops"

CROP_SIZE = 380


def process_video(mtcnn, video_path: Path, out_dir: Path, frame_step: int) -> tuple[int, int]:
    """Returns (saved, skipped) frame counts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"  [WARN] Cannot open {video_path.name}", flush=True)
        return 0, 0

    saved = skipped = frame_idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_step != 0:
                frame_idx += 1
                continue

            img_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)

            boxes, _ = mtcnn.detect(pil_img)
            if boxes is None or len(boxes) == 0:
                skipped += 1
                frame_idx += 1
                continue

            x1, y1, x2, y2 = [int(b) for b in boxes[0]]
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(img_rgb.shape[1], x2)
            y2 = min(img_rgb.shape[0], y2)
            crop = img_rgb[y1:y2, x1:x2]

            if crop.size == 0:
                skipped += 1
                frame_idx += 1
                continue

            crop_bgr = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_RGB2BGR), (CROP_SIZE, CROP_SIZE))
            cv2.imwrite(str(out_dir / f"frame_{frame_idx:06d}.jpg"), crop_bgr)
            saved += 1
            frame_idx += 1
    finally:
        cap.release()

    return saved, skipped


def run(use_gpu: bool, frame_step: int) -> None:
    try:
        from facenet_pytorch import MTCNN
    except ImportError:
        print("[ERROR] facenet-pytorch is not installed.")
        print("Run via Docker or a Python 3.11 venv — see docstring at top of file.")
        sys.exit(1)

    device = torch.device("cuda" if (use_gpu and torch.cuda.is_available()) else "cpu")
    print(f"Device: {device} | frame_step: every {frame_step}th frame")
    mtcnn = MTCNN(keep_all=False, device=device)

    for label, src_dir, out_base in [
        ("real", REAL_SRC, OUT_ROOT / "real"),
        ("fake", FAKE_SRC, OUT_ROOT / "fake"),
    ]:
        if not src_dir.exists():
            print(f"[ERROR] {src_dir} not found — download FF++ first.")
            sys.exit(1)

        videos = sorted(src_dir.glob("*.mp4"))
        print(f"\n=== {label.upper()} — {len(videos)} videos ===")
        total_saved = total_skipped = 0

        for i, v in enumerate(videos, 1):
            saved, skipped = process_video(mtcnn, v, out_base / v.stem, frame_step)
            total_saved += saved
            total_skipped += skipped
            sys.stdout.write(
                f"\r  [{i:4d}/{len(videos)}] {v.name:<30s} "
                f"saved={saved:4d} no-face={skipped:4d}  |  total={total_saved}"
            )
            sys.stdout.flush()

        print(f"\n  Done — {total_saved} crops saved, {total_skipped} frames skipped")

    print(f"\nAll done. Crops at {OUT_ROOT}/")


def main() -> None:
    parser = argparse.ArgumentParser(description="FF++ face crop extractor (Phase 2.5)")
    parser.add_argument("--gpu", action="store_true", help="Use GPU for MTCNN")
    parser.add_argument("--frame-step", type=int, default=5,
                        help="Sample every Nth frame (default: 5)")
    args = parser.parse_args()
    run(use_gpu=args.gpu, frame_step=args.frame_step)


if __name__ == "__main__":
    main()
