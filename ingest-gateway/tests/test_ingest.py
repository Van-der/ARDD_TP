import pytest
import cv2
import numpy as np
import base64
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ["TESTING"] = "1"

from main import IngestGateway


def make_jpeg_b64(width=100, height=100) -> str:
    """Create a valid synthetic JPEG and return base64-encoded string."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (100, 150, 200)  # solid color
    _, buf = cv2.imencode('.jpg', img)
    return base64.b64encode(buf.tobytes()).decode('utf-8')


class MockCap:
    """Minimal mock of cv2.VideoCapture."""
    def __init__(self, opened=True, return_frame=True):
        self._opened = opened
        self._return_frame = return_frame

    def isOpened(self):
        return self._opened

    def read(self):
        if not self._return_frame:
            return False, None
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        return True, img

    def release(self):
        pass


# ─── test_fps_downsampling ────────────────────────────────────────────────────

def test_fps_downsampling():
    """TESTING.md §2: assert frame rate drops to 5 FPS when downsample flag is set."""
    gw = IngestGateway.__new__(IngestGateway)
    gw.fps_target = 30
    gw.current_fps = 30
    gw.downsample_active = False
    gw.kafka_bootstrap = 'kafka:9092'
    gw.consumer_group = 'vision-service-group'
    gw.fps_downsample_threshold = 500

    # Simulate downsample activation
    gw.downsample_active = True
    gw.check_and_adjust_fps()  # in TESTING mode reads flag directly
    assert gw.current_fps == 5, f"Expected 5 FPS in downsample mode, got {gw.current_fps}"

    # Simulate recovery
    gw.downsample_active = False
    gw.check_and_adjust_fps()
    assert gw.current_fps == 30, f"Expected 30 FPS after recovery, got {gw.current_fps}"


# ─── test_invalid_jpeg ────────────────────────────────────────────────────────

def test_invalid_jpeg(monkeypatch):
    """TESTING.md §2: assert decode_error is incremented for a corrupt JPEG."""
    gw = IngestGateway.__new__(IngestGateway)
    gw.kafka_bootstrap = 'kafka:9092'
    gw.kafka_topic = 'frames'
    gw.stream_id = 'test_stream'
    gw.frame_index = 0
    gw.decode_error = 0

    # Provide a mock cap that returns a frame
    gw.cap = MockCap(opened=True, return_frame=True)

    # Patch imencode to produce corrupt bytes
    def bad_imencode(ext, frame):
        return True, np.frombuffer(b'\xff\x00CORRUPT', dtype=np.uint8)

    monkeypatch.setattr(cv2, 'imencode', bad_imencode)

    # Patch imdecode to simulate corrupt check failure
    monkeypatch.setattr(cv2, 'imdecode', lambda *a, **kw: None)

    result = gw.extract_and_publish_frame()
    assert result is False, "Should return False for corrupt JPEG"
    assert gw.decode_error == 1, f"Expected decode_error=1, got {gw.decode_error}"


# ─── test_kafka_publish_retry ─────────────────────────────────────────────────

def test_kafka_publish_retry(monkeypatch):
    """Kafka publish should retry 3 times before dropping frame and logging kafka_publish_error."""
    gw = IngestGateway.__new__(IngestGateway)
    gw.kafka_bootstrap = 'kafka:9092'
    gw.kafka_topic = 'frames'
    gw.stream_id = 'test_stream'
    gw.frame_index = 0
    gw.decode_error = 0

    gw.cap = MockCap(opened=True, return_frame=True)

    call_count = {"n": 0}

    class MockProducer:
        def send(self, topic, payload):
            call_count["n"] += 1
            raise Exception("Kafka broker unavailable")

    gw.producer = MockProducer()

    # Patch imencode to return valid bytes so corrupt check passes
    real_img = np.zeros((100, 100, 3), dtype=np.uint8)
    _, real_buf = cv2.imencode('.jpg', real_img)
    real_bytes = real_buf.tobytes()

    def good_imencode(ext, frame):
        return True, real_buf

    monkeypatch.setattr(cv2, 'imencode', good_imencode)

    # imdecode should succeed (valid JPEG)
    monkeypatch.setattr(cv2, 'imdecode', lambda *a, **kw: real_img)

    # Patch sleep to not actually sleep
    monkeypatch.setattr('time.sleep', lambda s: None)

    result = gw.extract_and_publish_frame()
    assert result is False
    assert call_count["n"] == 3, f"Expected 3 Kafka publish attempts, got {call_count['n']}"


# ─── test_exponential_backoff_connect ─────────────────────────────────────────

def test_exponential_backoff_connect(monkeypatch):
    """Stream reconnect should attempt up to 5 times with exponential backoff."""
    gw = IngestGateway.__new__(IngestGateway)
    gw.kafka_bootstrap = 'kafka:9092'
    gw.kafka_topic = 'frames'
    gw.stream_id = 'test_stream'
    gw.rtsp_source = 'rtsp://bad-host/stream'
    gw.MAX_CONSECUTIVE_FAILURES = 5
    gw.consecutive_failures = 0

    sleep_calls = []
    monkeypatch.setattr('time.sleep', lambda s: sleep_calls.append(s))

    # Make VideoCapture always fail
    class BadCap:
        def isOpened(self): return False
        def release(self): pass

    monkeypatch.setattr(cv2, 'VideoCapture', lambda src: BadCap())

    class MockProducer:
        def send(self, t, p): pass
        def flush(self): pass

    gw.producer = MockProducer()

    result = gw.connect_to_stream()
    assert result is False
    # Should have slept 5 times (once after each failed attempt except last)
    assert len(sleep_calls) == 5, f"Expected 5 backoff sleeps, got {len(sleep_calls)}"
    # Check backoff is increasing
    assert sleep_calls[0] < sleep_calls[1], "Backoff should be increasing"
