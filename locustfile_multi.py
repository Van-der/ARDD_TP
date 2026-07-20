"""
Multi-stream Locust load test — each simulated user is pinned to its own
stream_id (cam_01, cam_02, ...), all sending real encoded JPEGs against the
same source frame.

This validates Kafka partitioning / consumer-group rebalance / load-balancer
routing *mechanics* under N concurrent logical streams — it does NOT validate
real-world camera diversity, since every stream replays the same frame bytes.

Usage (--insecure skips CA verification for the local self-signed cert, M10):
  locust -f locustfile_multi.py --headless -u 3 -r 3 -t 2m --host=https://localhost:8003 --insecure
"""
import time
import base64
import os
import itertools
import cv2
import numpy as np
from locust import HttpUser, task, between

_mock_frame = np.zeros((224, 224, 3), dtype=np.uint8)
_, _mock_jpeg = cv2.imencode(".jpg", _mock_frame)
MOCK_PAYLOAD = base64.b64encode(_mock_jpeg.tobytes()).decode("utf-8")
API_KEY = os.getenv("INTERNAL_API_KEY", "test-key")

_stream_id_counter = itertools.count(1)


class MultiStreamLoadTest(HttpUser):
    wait_time = between(0.033, 0.033)  # ~30 FPS per simulated stream

    def on_start(self):
        self.stream_id = f"cam_{next(_stream_id_counter):02d}"
        self.frame_idx = 0

    @task
    def send_frame(self):
        payload = {
            "stream_id": self.stream_id,
            "frame_index": self.frame_idx,
            "timestamp_ms": int(time.time() * 1000),
            "payload": MOCK_PAYLOAD,
        }
        with self.client.post(
            "/aggregate",
            json=payload,
            headers={"X-API-Key": API_KEY},
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed with status {response.status_code}: {response.text}")

        self.frame_idx += 1
