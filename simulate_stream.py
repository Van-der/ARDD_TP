import os
import time
import json
import base64
import random
from pathlib import Path
import cv2
import numpy as np
from kafka import KafkaProducer

_DEFAULT_CA_CERT = str(Path(__file__).parent / "certs" / "ca.crt")

_MATPLOTLIB_FACE = os.path.expanduser(
    "~/.venvs/ardd-tp/lib/python3.13/site-packages/"
    "matplotlib/mpl-data/sample_data/grace_hopper.jpg"
)

# Score targets:
#   real  → ~0.005  (below ALERT_THRESHOLD=0.25)
#   fake  → ~0.25–0.35 depending on JPEG quality used
FAKE_QUALITIES = [3, 5, 7, 10]  # lower = more artefacts = higher deepfake score


def _load_frames():
    img = cv2.imread(_MATPLOTLIB_FACE)
    if img is None:
        raise FileNotFoundError(f"Grace Hopper image not found at {_MATPLOTLIB_FACE}")

    # Real: clean encode
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    real_b64 = base64.b64encode(buf).decode()

    # Fakes: multiple quality levels give slight score variation
    fakes = []
    for q in FAKE_QUALITIES:
        _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
        img2 = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
        _, buf2 = cv2.imencode(".jpg", img2)
        fakes.append(base64.b64encode(buf2).decode())

    return real_b64, fakes


def _next_block():
    """Return (is_fake, block_length) for the next segment."""
    # ~65% of time in real state, 35% fake
    is_fake = random.random() < 0.35
    if is_fake:
        length = random.randint(6, 18)   # enough to cross the 5-frame alert window
    else:
        length = random.randint(8, 30)   # varying real stretches
    return is_fake, length


def main():
    real_b64, fakes = _load_frames()
    print("Grace Hopper frames ready.")
    print("Connecting to Kafka...")

    ca_cert = os.getenv("CA_CERT", _DEFAULT_CA_CERT)
    ssl_kwargs = {"ssl_cafile": ca_cert} if os.path.exists(ca_cert) else {}
    producer = KafkaProducer(
        bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092"),
        security_protocol="SASL_SSL" if ssl_kwargs else "SASL_PLAINTEXT",
        sasl_mechanism="PLAIN",
        sasl_plain_username="admin",
        sasl_plain_password="admin-secret",
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        **ssl_kwargs,
    )

    stream_id = "live_demo_stream"
    frame_index = 0
    print(f"stream_id={stream_id}  |  ALERT_THRESHOLD=0.25  |  alert after 5 consecutive fake frames")
    print("Pattern: randomized real/fake blocks — Ctrl+C to stop\n")

    try:
        while True:
            is_fake, block_len = _next_block()

            for _ in range(block_len):
                if is_fake:
                    b64 = random.choice(fakes)
                    label = "FAKE"
                else:
                    b64 = real_b64
                    label = "real"

                producer.send("frames", {
                    "stream_id": stream_id,
                    "frame_index": frame_index,
                    "timestamp_ms": int(time.time() * 1000),
                    "payload": b64,
                }, key=stream_id.encode("utf-8"))
                print(f"[{frame_index:05d}] {label}")
                frame_index += 1
                time.sleep(0.15)

    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        producer.close()


if __name__ == "__main__":
    main()
