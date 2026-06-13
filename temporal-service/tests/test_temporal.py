import os
os.environ.setdefault("TESTING", "true")

import io
import base64
import pytest
import numpy as np
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from PIL import Image
import torch

from main import app, INTERNAL_API_KEY, stream_buffers, TARGET_FRAMES, process_frame, model_used

client = TestClient(app)
HEADERS = {"X-API-Key": INTERNAL_API_KEY}
BAD_HEADERS = {"X-API-Key": "wrong-key"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_jpeg_b64(width: int = 224, height: int = 224) -> str:
    img = Image.fromarray(np.zeros((height, width, 3), dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def fill_buffer(stream_id: str, n: int = TARGET_FRAMES) -> None:
    stream_buffers[stream_id].clear()
    jpeg_b64 = make_jpeg_b64()
    for i in range(n):
        stream_buffers[stream_id].append((i, process_frame(jpeg_b64)))


# ── Health ────────────────────────────────────────────────────────────────────

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["service"] == "temporal-service"
    assert isinstance(d["uptime_s"], int)
    assert isinstance(d["buffer_sizes"], dict)


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_batch_status_no_auth():
    assert client.get("/batch_status").status_code == 401

def test_batch_status_bad_auth():
    assert client.get("/batch_status", headers=BAD_HEADERS).status_code == 401

def test_batch_status_ok():
    r = client.get("/batch_status", headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_flush_no_auth():
    assert client.post("/flush", json={"stream_id": "x"}).status_code == 401

def test_flush_bad_auth():
    assert client.post("/flush", json={"stream_id": "x"}, headers=BAD_HEADERS).status_code == 401


# ── Unknown stream_id: flush is a no-op ──────────────────────────────────────

@patch("main.send_audit_result", new_callable=AsyncMock)
def test_flush_unknown_stream_noop(mock_send):
    # stream_id never touched → not in defaultdict → flush skipped
    r = client.post("/flush", json={"stream_id": "never_seen_stream_zzz"}, headers=HEADERS)
    assert r.status_code == 200
    mock_send.assert_not_called()


# ── Empty buffer (key exists, 0 frames): sends UNKNOWN ────────────────────────

@patch("main.send_audit_result", new_callable=AsyncMock)
def test_flush_empty_sends_unknown(mock_send):
    # Accessing then clearing the defaultdict key leaves it present with 0 frames
    fill_buffer("empty_buf_stream", n=5)
    stream_buffers["empty_buf_stream"].clear()
    client.post("/flush", json={"stream_id": "empty_buf_stream"}, headers=HEADERS)
    mock_send.assert_called_once()
    p = mock_send.call_args[0][0]
    assert p["temporal_verdict"] == "UNKNOWN"
    assert p["temporal_score"] == 0.5


# ── Sparse buffer (N < 6): UNKNOWN, no inference ─────────────────────────────

@patch("main.send_audit_result", new_callable=AsyncMock)
def test_flush_sparse_returns_unknown(mock_send):
    sid = "sparse_stream"
    fill_buffer(sid, n=3)
    client.post("/flush", json={"stream_id": sid}, headers=HEADERS)
    mock_send.assert_called_once()
    p = mock_send.call_args[0][0]
    assert p["temporal_verdict"] == "UNKNOWN"
    assert p["low_confidence_flag"] is True
    assert p["frames_interpolated"] == 0
    assert p["temporal_score"] == 0.5


# ── Padded buffer (6 ≤ N < 20): inference runs, low_confidence_flag True ─────

@patch("main.send_audit_result", new_callable=AsyncMock)
def test_flush_padded_buffer_low_confidence(mock_send):
    sid = "padded_stream"
    fill_buffer(sid, n=12)
    client.post("/flush", json={"stream_id": sid}, headers=HEADERS)
    mock_send.assert_called_once()
    p = mock_send.call_args[0][0]
    assert p["low_confidence_flag"] is True
    assert p["frames_interpolated"] == 0
    assert p["temporal_verdict"] in ("PASS", "FAIL")
    assert 0.0 <= p["temporal_score"] <= 1.0


# ── Full buffer (N = 20): full inference, schema validation ──────────────────

@patch("main.send_audit_result", new_callable=AsyncMock)
def test_flush_full_buffer_schema(mock_send):
    sid = "full_stream"
    fill_buffer(sid, n=20)
    r = client.post("/flush", json={"stream_id": sid}, headers=HEADERS)
    assert r.status_code == 200
    mock_send.assert_called_once()
    p = mock_send.call_args[0][0]

    # All 11 TemporalAuditResult fields (SCHEMA.md §4b)
    assert isinstance(p["stream_id"], str)
    assert isinstance(p["window_start_frame"], int)
    assert isinstance(p["window_end_frame"], int)
    assert isinstance(p["window_duration_s"], float)
    assert 0.0 <= p["temporal_score"] <= 1.0
    assert p["temporal_verdict"] in ("PASS", "FAIL")
    assert p["low_confidence_flag"] is False
    assert p["frames_interpolated"] == 0
    assert isinstance(p["model_used"], str)
    assert isinstance(p["latency_ms"], int)
    assert isinstance(p["timestamp_ms"], int)


# ── Tensor shape ──────────────────────────────────────────────────────────────

def test_process_frame_tensor_shape():
    t = process_frame(make_jpeg_b64())
    assert t.shape == torch.Size([3, 112, 112])


# ── window_start_frame / window_end_frame tracking ───────────────────────────

@patch("main.send_audit_result", new_callable=AsyncMock)
def test_window_frame_indices(mock_send):
    sid = "idx_stream"
    stream_buffers[sid].clear()
    jpeg_b64 = make_jpeg_b64()
    for i in range(100, 120):   # frame indices 100–119
        stream_buffers[sid].append((i, process_frame(jpeg_b64)))
    client.post("/flush", json={"stream_id": sid}, headers=HEADERS)
    p = mock_send.call_args[0][0]
    assert p["window_start_frame"] == 100
    assert p["window_end_frame"] == 119


# ── window_duration_s = n_frames / 30.0 ──────────────────────────────────────

@patch("main.send_audit_result", new_callable=AsyncMock)
def test_window_duration_s_correct(mock_send):
    sid = "duration_stream"
    fill_buffer(sid, n=20)
    client.post("/flush", json={"stream_id": sid}, headers=HEADERS)
    p = mock_send.call_args[0][0]
    assert abs(p["window_duration_s"] - 20 / 30.0) < 0.001


# ── frames_interpolated always 0 (zero-pads are not interpolation) ────────────

@patch("main.send_audit_result", new_callable=AsyncMock)
def test_frames_interpolated_always_zero(mock_send):
    for n in (8, 15, 20):
        sid = f"interp_stream_{n}"
        fill_buffer(sid, n=n)
        client.post("/flush", json={"stream_id": sid}, headers=HEADERS)
        p = mock_send.call_args[0][0]
        assert p["frames_interpolated"] == 0


# ── model_used field ──────────────────────────────────────────────────────────

@patch("main.send_audit_result", new_callable=AsyncMock)
def test_model_used_field(mock_send):
    sid = "model_field_stream"
    fill_buffer(sid, n=20)
    client.post("/flush", json={"stream_id": sid}, headers=HEADERS)
    p = mock_send.call_args[0][0]
    assert p["model_used"] == model_used
    assert p["model_used"] in ("resnext50-lstm-v1", "random-fallback")


# ── deque maxlen enforced (capped at TARGET_FRAMES) ──────────────────────────

def test_buffer_maxlen_enforced():
    sid = "maxlen_stream"
    stream_buffers[sid].clear()
    jpeg_b64 = make_jpeg_b64()
    for i in range(TARGET_FRAMES + 10):
        stream_buffers[sid].append((i, process_frame(jpeg_b64)))
    assert len(stream_buffers[sid]) == TARGET_FRAMES


# ── Buffer cleared after flush ────────────────────────────────────────────────

@patch("main.send_audit_result", new_callable=AsyncMock)
def test_buffer_cleared_after_flush(mock_send):
    sid = "clear_stream"
    fill_buffer(sid, n=20)
    assert len(stream_buffers[sid]) == 20
    client.post("/flush", json={"stream_id": sid}, headers=HEADERS)
    assert len(stream_buffers[sid]) == 0


# ── batch_status reflects current buffer sizes ────────────────────────────────

def test_batch_status_reflects_buffers():
    sid = "status_stream"
    fill_buffer(sid, n=7)
    r = client.get("/batch_status", headers=HEADERS)
    entries = {e["stream_id"]: e for e in r.json()}
    assert sid in entries
    assert entries[sid]["buffer_size"] == 7
    assert entries[sid]["target"] == TARGET_FRAMES
