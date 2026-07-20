#!/usr/bin/env python3
"""
Speed Layer benchmark against the FF++ test split.

Sends frames from the test split through the live Kafka pipeline, collects
deepfake_score values from the aggregation-service WebSocket, then computes
per-video accuracy and AUC.

Test split convention: last 140 videos (sorted by name) from each class.

Usage:
    # Quick check — 10 real + 10 fake, 5 frames each (~30s):
    python run_benchmark.py

    # Full test split — 140 real + 140 fake, 10 frames each (~15min):
    python run_benchmark.py --n-videos 140 --frames-per-video 10

Requirements (all in root requirements.txt):
    kafka-python, websocket-client, scikit-learn, opencv-python, requests
"""

import argparse
import base64
import json
import os
import threading
import time
from pathlib import Path
from collections import defaultdict

import cv2
import requests
from kafka import KafkaProducer
import websocket
from sklearn.metrics import roc_auc_score, accuracy_score

DATASET_ROOT = Path(__file__).parent / "datasets" / "ff++"
REAL_DIR = DATASET_ROOT / "original_sequences" / "youtube" / "c23" / "videos"
FAKE_DIR = DATASET_ROOT / "manipulated_sequences" / "Deepfakes" / "c23" / "videos"

API_URL = "https://localhost:8003"
WS_URL = "wss://localhost:8003/stream"
KAFKA_BOOTSTRAP = "localhost:29092"  # external SASL_SSL_HOST listener (host access)
KAFKA_TOPIC = "frames"
CA_CERT = os.getenv("CA_CERT", str(Path(__file__).parent / "certs" / "ca.crt"))

# ground-truth label encoded in the stream_id prefix
REAL_PREFIX = "bench_real"
FAKE_PREFIX = "bench_fake"


def _get_token() -> str:
    # RBAC (M11): only the hardcoded admin/viewer pairs authenticate now —
    # this script just reads WebSocket scores, so viewer is sufficient.
    verify = CA_CERT if os.path.exists(CA_CERT) else True
    resp = requests.post(f"{API_URL}/auth/token",
                         json={"client_id": "viewer", "client_secret": "viewer-secret-change-me"},
                         timeout=5, verify=verify)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _make_producer() -> KafkaProducer:
    ssl_kwargs = {"ssl_cafile": CA_CERT} if os.path.exists(CA_CERT) else {}
    return KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        security_protocol="SASL_SSL" if ssl_kwargs else "SASL_PLAINTEXT",
        sasl_mechanism="PLAIN",
        sasl_plain_username="admin",
        sasl_plain_password="admin-secret",
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        **ssl_kwargs,
    )


def _test_split(directory: Path, n: int) -> list[Path]:
    """Return the first `n` videos from the last-140 test split (sorted by name)."""
    all_videos = sorted(directory.glob("*.mp4"))
    test_videos = all_videos[-140:]
    return test_videos[:n]


def _send_videos(videos: list[Path], stream_prefix: str,
                 frames_per_video: int, done_event: threading.Event) -> set[str]:
    """
    Publish `frames_per_video` frames for each video to Kafka.
    Sets `done_event` when all frames have been flushed.
    Returns the set of stream_ids created.
    """
    producer = _make_producer()
    stream_ids: set[str] = set()

    for v in videos:
        stream_id = f"{stream_prefix}_{v.stem}"
        stream_ids.add(stream_id)
        cap = cv2.VideoCapture(str(v))
        sent = 0
        while sent < frames_per_video:
            ret, frame = cap.read()
            if not ret:
                # loop if video is shorter than frames_per_video
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
                if not ret:
                    break
            _, buf = cv2.imencode(".jpg", frame)
            producer.send(KAFKA_TOPIC, {
                "stream_id": stream_id,
                "frame_index": sent,
                "timestamp_ms": int(time.time() * 1000),
                "payload": base64.b64encode(buf.tobytes()).decode(),
            }, key=stream_id.encode("utf-8"))
            sent += 1
        cap.release()
        print(f"  [{stream_prefix}] {v.name} — {sent} frames")

    producer.flush()
    producer.close()
    done_event.set()
    return stream_ids


def run_benchmark(n_videos: int, frames_per_video: int, pipeline_drain_s: float) -> None:
    # Validate dataset
    if not REAL_DIR.exists() or not FAKE_DIR.exists():
        print(f"[ERROR] Dataset not found at {DATASET_ROOT}")
        return

    real_videos = _test_split(REAL_DIR, n_videos)
    fake_videos = _test_split(FAKE_DIR, n_videos)

    if not real_videos or not fake_videos:
        print("[ERROR] No videos found in the test split.")
        return

    total_expected = (len(real_videos) + len(fake_videos)) * frames_per_video
    print(f"\nBenchmark — {len(real_videos)} real + {len(fake_videos)} fake videos")
    print(f"            {frames_per_video} frames/video → {total_expected} total frames")
    print(f"            drain timeout after send: {pipeline_drain_s}s\n")

    # Collect stream scores keyed by stream_id
    stream_scores: dict[str, list[float]] = defaultdict(list)
    scores_lock = threading.Lock()
    send_done = threading.Event()

    # ── WebSocket collector (runs in this thread until done + drain) ──────────
    token = _get_token()

    ws_app = websocket.WebSocketApp(
        WS_URL,
        subprotocols=[token],  # JWT via subprotocol — matches Starlette accept(subprotocol=token)
        on_message=lambda ws, msg: _on_message(msg, stream_scores, scores_lock),
        on_error=lambda ws, err: print(f"[WS ERROR] {err}"),
    )

    def _stop_ws_when_drained():
        send_done.wait()
        time.sleep(pipeline_drain_s)
        ws_app.close()

    # ── Kafka sender (background thread) ─────────────────────────────────────
    all_stream_ids: set[str] = set()

    def _send_all():
        nonlocal all_stream_ids
        real_ids = _send_videos(real_videos, REAL_PREFIX, frames_per_video, threading.Event())
        fake_ids = _send_videos(fake_videos, FAKE_PREFIX, frames_per_video, threading.Event())
        all_stream_ids = real_ids | fake_ids
        send_done.set()

    sender_thread = threading.Thread(target=_send_all, daemon=True)
    drain_thread = threading.Thread(target=_stop_ws_when_drained, daemon=True)

    sender_thread.start()
    drain_thread.start()

    print("Sending frames and collecting WebSocket scores …  (Ctrl-C to abort)\n")
    sslopt = {"ca_certs": CA_CERT} if os.path.exists(CA_CERT) else None
    try:
        ws_app.run_forever(sslopt=sslopt)
    except KeyboardInterrupt:
        ws_app.close()

    sender_thread.join(timeout=30)

    # ── Compute metrics ───────────────────────────────────────────────────────
    y_true: list[int] = []
    y_score: list[float] = []
    y_pred: list[int] = []
    missing: list[str] = []

    for sid in sorted(all_stream_ids):
        scores = stream_scores.get(sid, [])
        if not scores:
            missing.append(sid)
            continue
        avg_score = sum(scores) / len(scores)
        label = 0 if sid.startswith(REAL_PREFIX) else 1  # 1 = fake
        pred = 1 if avg_score > 0.5 else 0
        y_true.append(label)
        y_score.append(avg_score)
        y_pred.append(pred)

    if len(missing) > 0:
        print(f"\n[WARN] No WebSocket events received for {len(missing)} stream(s): {missing[:5]}…")

    if len(y_true) < 2:
        print("\n[ERROR] Too few results to compute metrics. "
              "Check that the pipeline (aggregation-service, vision-service) is running.")
        return

    # Split by class for per-class accuracy
    real_correct = sum(p == 0 for t, p in zip(y_true, y_pred) if t == 0)
    real_total = y_true.count(0)
    fake_correct = sum(p == 1 for t, p in zip(y_true, y_pred) if t == 1)
    fake_total = y_true.count(1)

    overall_acc = accuracy_score(y_true, y_pred)
    auc = roc_auc_score(y_true, y_score) if len(set(y_true)) > 1 else float("nan")

    print("\n" + "=" * 60)
    print("  SPEED LAYER BENCHMARK RESULTS")
    print("=" * 60)
    print(f"  Videos evaluated : {real_total} real + {fake_total} fake = {len(y_true)} total")
    print(f"  Frames/video     : {frames_per_video}")
    print(f"  Overall accuracy : {overall_acc * 100:.2f}%")
    print(f"  Real accuracy    : {real_correct}/{real_total} = {real_correct/real_total*100:.1f}%")
    print(f"  Fake accuracy    : {fake_correct}/{fake_total} = {fake_correct/fake_total*100:.1f}%")
    print(f"  AUC              : {auc:.4f}")
    print("=" * 60)

    # Raw per-stream table
    print("\n  Per-stream scores (sorted):")
    print(f"  {'stream_id':<35} {'avg_score':>9} {'label':>6} {'pred':>5}")
    print("  " + "-" * 58)
    for sid in sorted(all_stream_ids):
        scores = stream_scores.get(sid, [])
        if not scores:
            continue
        avg = sum(scores) / len(scores)
        label_str = "FAKE" if sid.startswith(FAKE_PREFIX) else "REAL"
        pred_str = "FAKE" if avg > 0.5 else "REAL"
        match = "✓" if label_str == pred_str else "✗"
        print(f"  {sid:<35} {avg:>9.4f} {label_str:>6} {pred_str:>5} {match}")


def _on_message(msg: str, stream_scores: dict, lock: threading.Lock) -> None:
    try:
        data = json.loads(msg)
        if "deepfake_score" in data and "stream_id" in data:
            sid = data["stream_id"]
            if sid.startswith((REAL_PREFIX, FAKE_PREFIX)):
                with lock:
                    stream_scores[sid].append(data["deepfake_score"])
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="FF++ Speed Layer Benchmark")
    parser.add_argument("--n-videos", type=int, default=10,
                        help="Videos per class from test split (default: 10)")
    parser.add_argument("--frames-per-video", type=int, default=5,
                        help="Frames to send per video (default: 5)")
    parser.add_argument("--drain", type=float, default=15.0,
                        help="Seconds to wait after send for pipeline drain (default: 15)")
    args = parser.parse_args()

    run_benchmark(args.n_videos, args.frames_per_video, args.drain)


if __name__ == "__main__":
    main()
