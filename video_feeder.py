#!/usr/bin/env python3
"""
FF++ Video Feeder — publishes real FaceForensics++ frames to Kafka.

Four modes:
  demo         Alternates between one real and one fake video every --switch-every
               seconds. Watch the dashboard live as the Temporal Audit flips.
  eval         Streams all real videos then all fake videos sequentially,
               collecting pipeline scores via WebSocket and computing AUC/accuracy.
  mix          Interleaves 20-frame blocks of real and fake on a SINGLE stream_id
               ("ff_mix"). Each block exactly fills one temporal window, so the
               Temporal Audit flips cleanly between Authentic/Fake every ~2s at 10 FPS.
  multistream  Replays a SINGLE local video under N distinct stream_id tags
               concurrently (cam_01, cam_02, ...). Validates Kafka partitioning /
               consumer-group rebalance / load-balancer routing MECHANICS under N
               concurrent logical streams — NOT a proxy for real camera diversity,
               since every stream replays the same source bytes.

Usage:
  # Demo (default, 15s per video):
  python video_feeder.py

  # Mix mode — best for testing dashboard with both verdicts on one stream:
  python video_feeder.py --mode mix --fps 10

  # Pipeline smoke test (2 videos, 30 frames each — ~3 min total):
  python video_feeder.py --mode eval --max-videos 2 --max-frames 30 --fps 3

  # Full evaluation run (140 per class = FF++ test split):
  # NOTE: Pipeline throughput is ~1.5 FPS. Always reset the Kafka consumer
  # offset before a large eval run to avoid processing old backlogs.
  # See BUGS_AND_ISSUES.md §INCOMPLETE-1 for the reset procedure.
  python video_feeder.py --mode eval --max-videos 140 --fps 1

  # Multi-stream concurrency test (3 simulated streams, same source video):
  python video_feeder.py --mode multistream --streams 3 --max-frames 60 --fps 5
"""

import os
import sys
import time
import json
import base64
import argparse
import threading
import collections
import cv2
import numpy as np
from pathlib import Path
from kafka import KafkaProducer

DATASET_ROOT = Path(__file__).parent / "datasets" / "ff++"
REAL_DIR = DATASET_ROOT / "original_sequences" / "youtube" / "c23" / "videos"
FAKE_DIR = DATASET_ROOT / "manipulated_sequences" / "Deepfakes" / "c23" / "videos"

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
KAFKA_TOPIC = "frames"
API_BASE = os.getenv("EVAL_API_URL", "https://localhost:8003")
WS_URL = os.getenv("EVAL_WS_URL", "wss://localhost:8003/stream")
CA_CERT = os.getenv("CA_CERT", str(Path(__file__).parent / "certs" / "ca.crt"))


def make_producer() -> KafkaProducer:
    ssl_kwargs = {"ssl_cafile": CA_CERT} if os.path.exists(CA_CERT) else {}
    security_protocol = "SASL_SSL" if ssl_kwargs else "SASL_PLAINTEXT"
    return KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        security_protocol=security_protocol,
        sasl_mechanism="PLAIN",
        sasl_plain_username="admin",
        sasl_plain_password="admin-secret",
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        **ssl_kwargs,
    )


def send_video(producer: KafkaProducer, video_path: Path, stream_id: str,
               fps: int, max_seconds: float | None = None,
               max_frames: int = 0, label: str = "UNKNOWN") -> int:
    """
    Read frames from video_path and publish to Kafka.
    Returns total frames sent.
    max_frames: cap per-video frame count (0 = no cap).
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
            if max_frames > 0 and frame_index >= max_frames:
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
            producer.send(KAFKA_TOPIC, payload, key=stream_id.encode("utf-8"))
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


def run_mix(fps: int) -> None:
    """Send alternating 20-frame blocks of real/fake on the same stream_id.

    Each block = exactly one temporal window. At 10 FPS the pattern flips every 2s,
    so you'll see the Temporal Verdict Tally increment on both REAL and FAKE sides.
    """
    real_videos = sorted(REAL_DIR.glob("*.mp4"))
    fake_videos = sorted(FAKE_DIR.glob("*.mp4"))

    if not real_videos or not fake_videos:
        print("[ERROR] No videos found. Check datasets/ff++ structure.")
        return

    producer = make_producer()
    interval = 1.0 / fps
    stream_id = "ff_mix"
    frame_index = 0
    real_idx = fake_idx = 0

    print(f"Mix mode — alternating 20-frame blocks at {fps} FPS on stream '{stream_id}'")
    print("Temporal window fires every 20 frames → verdict flips ~every 2s.\n")
    print("  Ctrl-C to stop.\n")

    def open_cap(path: Path) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open {path}")
        return cap

    try:
        while True:
            for label, videos, idx_ref in [
                ("REAL", real_videos, [real_idx]),
                ("FAKE", fake_videos, [fake_idx]),
            ]:
                video_path = videos[idx_ref[0] % len(videos)]
                cap = open_cap(video_path)
                sent = 0
                print(f"  [{label}] {video_path.name}")

                while sent < 20:
                    ret, frame = cap.read()
                    if not ret:
                        # loop the video if it runs out before 20 frames
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ret, frame = cap.read()
                        if not ret:
                            break

                    _, buf = cv2.imencode(".jpg", frame)
                    producer.send(KAFKA_TOPIC, {
                        "stream_id": stream_id,
                        "frame_index": frame_index,
                        "timestamp_ms": int(time.time() * 1000),
                        "payload": base64.b64encode(buf.tobytes()).decode("utf-8"),
                    }, key=stream_id.encode("utf-8"))
                    frame_index += 1
                    sent += 1
                    sys.stdout.write(f"\r    frame {frame_index:5d}  block {sent:2d}/20")
                    sys.stdout.flush()
                    time.sleep(interval)

                cap.release()
                idx_ref[0] += 1

            real_idx += 1
            fake_idx += 1
            print()  # newline after \r

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        producer.close()


def run_multistream(fps: int, streams: int, max_frames: int, source: str) -> None:
    """Replay a single local video under N distinct stream_id tags concurrently.

    This validates Kafka partitioning / consumer-group rebalance / load-balancer
    routing MECHANICS under N concurrent logical streams. It is NOT a proxy for
    real-world camera diversity — every simulated stream replays the same source
    video's bytes.
    """
    videos = sorted(REAL_DIR.glob("*.mp4")) if source == "real" else sorted(FAKE_DIR.glob("*.mp4"))
    if not videos:
        print(f"[ERROR] No {source} videos found. Check datasets/ff++ structure.")
        return

    video_path = videos[0]
    producer = make_producer()  # kafka-python's KafkaProducer.send() is thread-safe

    print(f"Multi-stream mode — replaying {video_path.name} ({source}) as "
          f"{streams} concurrent simulated streams at {fps} FPS")
    print("NOTE: validates partitioning/rebalance/load-balancer routing mechanics only —")
    print("      not a proxy for real camera diversity (all streams share source bytes).\n")

    def worker(idx: int) -> None:
        stream_id = f"cam_{idx:02d}"
        send_video(producer, video_path, stream_id=stream_id,
                   fps=fps, max_frames=max_frames, label=source.upper())

    threads = [threading.Thread(target=worker, args=(i,), daemon=True)
               for i in range(1, streams + 1)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        producer.close()


def _get_jwt_token() -> str | None:
    """Fetch a JWT token from the aggregation-service auth endpoint."""
    try:
        import ssl
        import urllib.request
        # RBAC (M11): only the hardcoded admin/viewer pairs authenticate now —
        # this script just reads WebSocket scores, so viewer is sufficient.
        body = json.dumps({"client_id": "viewer", "client_secret": "viewer-secret-change-me"}).encode()
        req = urllib.request.Request(
            f"{API_BASE}/auth/token",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        ctx = ssl.create_default_context(cafile=CA_CERT) if os.path.exists(CA_CERT) else None
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            return json.loads(resp.read())["access_token"]
    except Exception as e:
        print(f"  [WARN] Could not get JWT token: {e}")
        return None


class _WsCollector(threading.Thread):
    """Background WebSocket listener that collects per-stream final_scores."""

    def __init__(self, token: str):
        super().__init__(daemon=True)
        self.token = token
        # stream_id -> list of final_score floats
        self.scores: dict[str, list[float]] = collections.defaultdict(list)
        self._stop = threading.Event()
        self.error: str | None = None

    def run(self) -> None:
        import socket
        try:
            import websocket as ws_lib
            # Use subprotocols= (not header=) so websocket-client handles the
            # Sec-WebSocket-Protocol handshake correctly with Starlette's accept().
            sslopt = {"ca_certs": CA_CERT} if os.path.exists(CA_CERT) else None
            ws = ws_lib.create_connection(
                WS_URL,
                subprotocols=[self.token],
                timeout=10,
                sslopt=sslopt,
            )
            ws.settimeout(1.0)
            while not self._stop.is_set():
                try:
                    raw = ws.recv()
                    data = json.loads(raw)
                    if data.get("type") == "temporal_audit":
                        continue
                    sid = data.get("stream_id", "")
                    # aggregation-service broadcasts final_score under key "deepfake_score"
                    score = data.get("deepfake_score")
                    if sid and score is not None:
                        self.scores[sid].append(float(score))
                except socket.timeout:
                    continue  # expected when no message arrives within 1s
                except Exception as e:
                    err = str(e).lower()
                    if "timed out" in err or "timeout" in err:
                        continue
                    # Real connection error — stop collecting
                    self.error = str(e)
                    break
            ws.close()
        except Exception as e:
            self.error = str(e)

    def stop(self) -> None:
        self._stop.set()


def _print_metrics(scores: dict[str, list[float]], real_prefix: str, fake_prefix: str) -> None:
    """Compute and print binary classification metrics from collected scores."""
    y_true, y_score, y_pred = [], [], []
    for sid, vals in scores.items():
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        if sid.startswith(real_prefix):
            y_true.append(0)
        elif sid.startswith(fake_prefix):
            y_true.append(1)
        else:
            continue
        y_score.append(avg)
        y_pred.append(1 if avg >= 0.5 else 0)

    n = len(y_true)
    if n == 0:
        print("  No scored streams collected — metrics unavailable.")
        return

    correct = sum(t == p for t, p in zip(y_true, y_pred))
    tp = sum(t == 1 and p == 1 for t, p in zip(y_true, y_pred))
    fp = sum(t == 0 and p == 1 for t, p in zip(y_true, y_pred))
    fn = sum(t == 1 and p == 0 for t, p in zip(y_true, y_pred))

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    accuracy = correct / n

    try:
        from sklearn.metrics import roc_auc_score
        auc = roc_auc_score(y_true, y_score)
        auc_str = f"{auc:.4f}"
    except Exception:
        auc_str = "N/A (sklearn not available)"

    print(f"\n{'='*50}")
    print(f"  End-to-End Pipeline Benchmark Results")
    print(f"{'='*50}")
    print(f"  Streams scored : {n}  ({sum(1 for t in y_true if t==0)} real, {sum(1 for t in y_true if t==1)} fake)")
    print(f"  Accuracy       : {accuracy:.4f}  ({correct}/{n})")
    print(f"  Precision      : {precision:.4f}")
    print(f"  Recall         : {recall:.4f}")
    print(f"  AUC            : {auc_str}")
    print(f"{'='*50}\n")


def run_eval(fps: int, max_videos: int, max_frames: int = 0) -> None:
    all_real = sorted(REAL_DIR.glob("*.mp4"))
    all_fake = sorted(FAKE_DIR.glob("*.mp4"))

    # Honour --max-videos (0 = no limit)
    real_videos = all_real[:max_videos] if max_videos > 0 else all_real
    fake_videos = all_fake[:max_videos] if max_videos > 0 else all_fake

    print(f"Eval mode — {len(real_videos)} real + {len(fake_videos)} fake videos at {fps} FPS")
    if max_videos > 0:
        print(f"  (limited to {max_videos} per class via --max-videos)")

    # Start WebSocket metric collector
    token = _get_jwt_token()
    collector: _WsCollector | None = None
    if token:
        collector = _WsCollector(token)
        collector.start()
        print("  WebSocket metric collector started.")
    else:
        print("  WebSocket unavailable — metrics will not be collected.")
    print()

    producer = make_producer()
    real_prefix = "eval_real_"
    fake_prefix = "eval_fake_"
    total_real = total_fake = 0
    try:
        print("=== REAL VIDEOS ===")
        for i, v in enumerate(real_videos):
            print(f"[{i+1:3d}/{len(real_videos)}] REAL  {v.name}")
            n = send_video(producer, v, stream_id=f"{real_prefix}{v.stem}",
                           fps=fps, max_frames=max_frames, label="REAL")
            total_real += n

        print("\n=== FAKE VIDEOS ===")
        for i, v in enumerate(fake_videos):
            print(f"[{i+1:3d}/{len(fake_videos)}] FAKE  {v.name}")
            n = send_video(producer, v, stream_id=f"{fake_prefix}{v.stem}",
                           fps=fps, max_frames=max_frames, label="FAKE")
            total_fake += n

    except KeyboardInterrupt:
        print("\nStopped early.")
    finally:
        producer.close()

    print(f"\nFrames sent: {total_real} real + {total_fake} fake")

    # Give pipeline time to process remaining frames from Kafka.
    # Measured throughput: ~1.5-2 FPS (vision+RAG ~500ms/frame on CPU/GPU).
    # Drain = total_frames / 1.5, min 20s, max 90s.
    if collector:
        drain_secs = max(20, min(90, int((total_real + total_fake) / 1.5)))
        print(f"Waiting {drain_secs}s for pipeline to drain remaining frames...")
        time.sleep(drain_secs)
        collector.stop()
        collector.join(timeout=3)
        if collector.error:
            print(f"  [WARN] WebSocket collector error: {collector.error}")
        print(f"  Streams in collector: {list(collector.scores.keys())}")
        _print_metrics(collector.scores, real_prefix, fake_prefix)


def main() -> None:
    parser = argparse.ArgumentParser(description="FF++ Video Feeder for ARDD-TP")
    parser.add_argument("--mode", choices=["demo", "eval", "mix", "multistream"], default="demo")
    parser.add_argument("--fps", type=int, default=10,
                        help="Frames per second to publish (default: 10)")
    parser.add_argument("--switch-every", type=int, default=15,
                        help="Demo mode: seconds per video before switching (default: 15)")
    parser.add_argument("--max-videos", type=int, default=0,
                        help="Eval mode: max videos per class (0 = all, 140 = FF++ test split)")
    parser.add_argument("--max-frames", type=int, default=0,
                        help="Eval mode: max frames per video (0 = all). Use 20-50 for quick pipeline smoke tests")
    parser.add_argument("--streams", type=int, default=3,
                        help="Multistream mode: number of concurrent simulated stream_ids (default: 3)")
    parser.add_argument("--source", choices=["real", "fake"], default="fake",
                        help="Multistream mode: replay a real or fake video (default: fake)")
    args = parser.parse_args()

    if not REAL_DIR.exists() or not FAKE_DIR.exists():
        print(f"[ERROR] Dataset not found at {DATASET_ROOT}")
        print("Expected structure:")
        print(f"  {REAL_DIR}")
        print(f"  {FAKE_DIR}")
        sys.exit(1)

    if args.mode == "demo":
        run_demo(fps=args.fps, switch_every=args.switch_every)
    elif args.mode == "mix":
        run_mix(fps=args.fps)
    elif args.mode == "multistream":
        run_multistream(fps=args.fps, streams=args.streams,
                         max_frames=args.max_frames, source=args.source)
    else:
        run_eval(fps=args.fps, max_videos=args.max_videos, max_frames=args.max_frames)


if __name__ == "__main__":
    main()
