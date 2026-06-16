#!/usr/bin/env python3
"""
FF++ Video Feeder — publishes real FaceForensics++ frames to Kafka.

Two modes:
  demo   Alternates between one real and one fake video every --switch-every
         seconds. Watch the dashboard live as the Temporal Audit flips.
  eval   Streams all real videos then all fake videos sequentially,
         printing ground-truth labels so you can compare against scores.

Usage:
  # Demo (default, 15s per video):
  python video_feeder.py

  # Demo with custom switch interval:
  python video_feeder.py --mode demo --switch-every 20

  # Full evaluation run:
  python video_feeder.py --mode eval --fps 10
"""

import os
import sys
import time
import json
import base64
import argparse
import cv2
import numpy as np
from pathlib import Path
from kafka import KafkaProducer

DATASET_ROOT = Path(__file__).parent / "datasets" / "ff++"
REAL_DIR = DATASET_ROOT / "original_sequences" / "youtube" / "c23" / "videos"
FAKE_DIR = DATASET_ROOT / "manipulated_sequences" / "Deepfakes" / "c23" / "videos"

KAFKA_BOOTSTRAP = "localhost:9092"
KAFKA_TOPIC = "frames"


def make_producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        security_protocol="SASL_PLAINTEXT",
        sasl_mechanism="PLAIN",
        sasl_plain_username="admin",
        sasl_plain_password="admin-secret",
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )


def send_video(producer: KafkaProducer, video_path: Path, stream_id: str,
               fps: int, max_seconds: float | None = None,
               label: str = "UNKNOWN") -> int:
    """
    Read frames from video_path and publish to Kafka.
    Returns total frames sent.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"  [ERROR] Could not open {video_path.name}")
        return 0

    frame_index = 0
    interval = 1.0 / fps
    deadline = time.time() + max_seconds if max_seconds else None
    label_str = f"[{label}]"

    try:
        while True:
            if deadline and time.time() >= deadline:
                break

            ret, frame = cap.read()
            if not ret:
                break  # video ended

            _, buf = cv2.imencode(".jpg", frame)
            payload = {
                "stream_id": stream_id,
                "frame_index": frame_index,
                "timestamp_ms": int(time.time() * 1000),
                "payload": base64.b64encode(buf.tobytes()).decode("utf-8"),
            }
            producer.send(KAFKA_TOPIC, payload)
            frame_index += 1

            sys.stdout.write(
                f"\r  {label_str} {video_path.name} — frame {frame_index:4d}"
                f"  (stream: {stream_id})"
            )
            sys.stdout.flush()
            time.sleep(interval)

    except KeyboardInterrupt:
        raise
    finally:
        cap.release()
        print()  # newline after \r

    return frame_index


def run_demo(fps: int, switch_every: int) -> None:
    real_videos = sorted(REAL_DIR.glob("*.mp4"))
    fake_videos = sorted(FAKE_DIR.glob("*.mp4"))

    if not real_videos or not fake_videos:
        print("[ERROR] No videos found. Check datasets/ff++ structure.")
        return

    producer = make_producer()
    print(f"Demo mode — switching every {switch_every}s at {fps} FPS")
    print("Open http://localhost:3000 and watch the Temporal Audit panel flip.\n")
    print("  Real stream → expect low scores, Authentic verdict")
    print("  Fake stream → expect high scores, Fake verdict\n")

    real_idx = 0
    fake_idx = 0
    cycle = 0

    try:
        while True:
            # Real segment
            rv = real_videos[real_idx % len(real_videos)]
            print(f"[Cycle {cycle+1}] Switching to REAL  → {rv.name}")
            send_video(producer, rv, stream_id="ff_real",
                       fps=fps, max_seconds=switch_every, label="REAL")

            # Fake segment
            fv = fake_videos[fake_idx % len(fake_videos)]
            print(f"[Cycle {cycle+1}] Switching to FAKE  → {fv.name}")
            send_video(producer, fv, stream_id="ff_fake",
                       fps=fps, max_seconds=switch_every, label="FAKE")

            real_idx += 1
            fake_idx += 1
            cycle += 1

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        producer.close()


def run_eval(fps: int) -> None:
    real_videos = sorted(REAL_DIR.glob("*.mp4"))
    fake_videos = sorted(FAKE_DIR.glob("*.mp4"))

    producer = make_producer()
    print(f"Eval mode — {len(real_videos)} real + {len(fake_videos)} fake videos at {fps} FPS\n")

    total_real = total_fake = 0
    try:
        print("=== REAL VIDEOS ===")
        for i, v in enumerate(real_videos):
            print(f"[{i+1:3d}/{len(real_videos)}] REAL  {v.name}")
            n = send_video(producer, v, stream_id=f"eval_real_{v.stem}",
                           fps=fps, label="REAL")
            total_real += n

        print("\n=== FAKE VIDEOS ===")
        for i, v in enumerate(fake_videos):
            print(f"[{i+1:3d}/{len(fake_videos)}] FAKE  {v.name}")
            n = send_video(producer, v, stream_id=f"eval_fake_{v.stem}",
                           fps=fps, label="FAKE")
            total_fake += n

    except KeyboardInterrupt:
        print("\nStopped early.")
    finally:
        producer.close()

    print(f"\nDone — {total_real} real frames + {total_fake} fake frames sent.")


def main() -> None:
    parser = argparse.ArgumentParser(description="FF++ Video Feeder for ARDD-TP")
    parser.add_argument("--mode", choices=["demo", "eval"], default="demo")
    parser.add_argument("--fps", type=int, default=10,
                        help="Frames per second to publish (default: 10)")
    parser.add_argument("--switch-every", type=int, default=15,
                        help="Demo mode: seconds per video before switching (default: 15)")
    args = parser.parse_args()

    if not REAL_DIR.exists() or not FAKE_DIR.exists():
        print(f"[ERROR] Dataset not found at {DATASET_ROOT}")
        print("Expected structure:")
        print(f"  {REAL_DIR}")
        print(f"  {FAKE_DIR}")
        sys.exit(1)

    if args.mode == "demo":
        run_demo(fps=args.fps, switch_every=args.switch_every)
    else:
        run_eval(fps=args.fps)


if __name__ == "__main__":
    main()
