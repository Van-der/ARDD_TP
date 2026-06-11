import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
import jwt
import time

from main import app, INTERNAL_API_KEY, JWT_SECRET

client = TestClient(app)
HEADERS = {"X-API-Key": INTERNAL_API_KEY}
BAD_HEADERS = {"X-API-Key": "wrong-key"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_payload(stream_id="s1", frame_index=0, timestamp_ms=123, payload_b64="dummy"):
    return {"stream_id": stream_id, "frame_index": frame_index,
            "timestamp_ms": timestamp_ms, "payload": payload_b64}


# ── Health ───────────────────────────────────────────────────────────────────

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["service"] == "aggregation-service"


# ── Auth token ───────────────────────────────────────────────────────────────

def test_auth_token_fields():
    """POST /auth/token returns access_token AND expires_in: 3600 (API_SPEC §6)."""
    r = client.post("/auth/token", json={"client_id": "c", "client_secret": "s"})
    assert r.status_code == 200
    d = r.json()
    assert "access_token" in d
    assert d["expires_in"] == 3600

def test_auth_token_empty_creds():
    """Empty credentials return 401."""
    r = client.post("/auth/token", json={"client_id": "", "client_secret": ""})
    assert r.status_code == 401


# ── Auth: X-API-Key ───────────────────────────────────────────────────────────

def test_missing_api_key():
    r = client.post("/aggregate", json=make_payload())
    assert r.status_code == 401

def test_invalid_api_key():
    r = client.post("/aggregate", json=make_payload(), headers=BAD_HEADERS)
    assert r.status_code == 401


# ── Unit: RAG boost applied (TESTING.md §2) ─────────────────────────────────

@patch("main.call_vision")
@patch("main.call_rag")
def test_rag_boost_applied(mock_rag, mock_vision):
    """final_score > deepfake_score when audit_verdict == FAIL (TESTING.md §2)."""
    mock_vision.return_value = {"deepfake_score": 0.8}
    mock_rag.return_value = {"audit_verdict": "FAIL", "matched_signature": "FaceSwap"}
    r = client.post("/aggregate", json=make_payload(stream_id="boost_1"), headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["deepfake_score"] > 0.8
    assert abs(d["deepfake_score"] - 0.92) < 0.001  # 0.8 * 1.15


# ── Unit: RAG boost NOT applied (TESTING.md §2) ─────────────────────────────

@patch("main.call_vision")
@patch("main.call_rag")
def test_rag_boost_not_applied(mock_rag, mock_vision):
    """final_score == deepfake_score when audit_verdict == PASS (TESTING.md §2)."""
    mock_vision.return_value = {"deepfake_score": 0.8}
    mock_rag.return_value = {"audit_verdict": "PASS", "matched_signature": None}
    r = client.post("/aggregate", json=make_payload(stream_id="boost_2"), headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["deepfake_score"] == 0.8

@patch("main.call_vision")
@patch("main.call_rag")
def test_rag_boost_not_applied_unknown(mock_rag, mock_vision):
    """final_score == deepfake_score when audit_verdict == UNKNOWN (TESTING.md §2)."""
    mock_vision.return_value = {"deepfake_score": 0.6}
    mock_rag.return_value = {"audit_verdict": "UNKNOWN", "matched_signature": None}
    r = client.post("/aggregate", json=make_payload(stream_id="boost_3"), headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["deepfake_score"] == 0.6


# ── Unit: final score clamped (TESTING.md §2) ────────────────────────────────

@patch("main.call_vision")
@patch("main.call_rag")
def test_final_score_clamped(mock_rag, mock_vision):
    """final_score <= 1.0 when boost would exceed 1.0 (TESTING.md §2)."""
    mock_vision.return_value = {"deepfake_score": 0.95}
    mock_rag.return_value = {"audit_verdict": "FAIL", "matched_signature": "FaceSwap"}
    r = client.post("/aggregate", json=make_payload(stream_id="clamp_1"), headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["deepfake_score"] == 1.0  # 0.95 * 1.15 = 1.0925 → 1.0


# ── Unit: RAG timeout fallback (TESTING.md §2) ───────────────────────────────

@patch("main.call_vision")
@patch("main.call_rag")
def test_rag_timeout_fallback(mock_rag, mock_vision):
    """RAG timeout → rag_used=false, audit_verdict=UNKNOWN (TESTING.md §2, TRD §3)."""
    import httpx
    mock_vision.return_value = {"deepfake_score": 0.7}
    mock_rag.side_effect = httpx.TimeoutException("Timeout")
    r = client.post("/aggregate", json=make_payload(stream_id="timeout_1"), headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["rag_used"] is False
    assert d["audit_verdict"] == "UNKNOWN"
    assert d["deepfake_score"] == 0.7


# ── Unit: alert threshold & reset (TESTING.md §2) ───────────────────────────

@patch("main.call_vision")
@patch("main.call_rag")
def test_alert_threshold(mock_rag, mock_vision):
    """alert=true after 5 consecutive frames with final_score > 0.90 (TESTING.md §2)."""
    mock_vision.return_value = {"deepfake_score": 0.95}
    mock_rag.return_value = {"audit_verdict": "UNKNOWN"}
    sid = "alert_threshold_stream"
    for i in range(1, 5):
        resp = client.post("/aggregate", json=make_payload(stream_id=sid, frame_index=i), headers=HEADERS)
        assert resp.json()["alert"] is False, f"alert should be False at frame {i}"
    resp = client.post("/aggregate", json=make_payload(stream_id=sid, frame_index=5), headers=HEADERS)
    assert resp.json()["alert"] is True

@patch("main.call_vision")
@patch("main.call_rag")
def test_alert_resets(mock_rag, mock_vision):
    """alert resets to false after a frame with final_score < 0.90 (TESTING.md §2)."""
    mock_vision.return_value = {"deepfake_score": 0.95}
    mock_rag.return_value = {"audit_verdict": "UNKNOWN"}
    sid = "alert_reset_stream"
    # Drive counter to 5
    for i in range(1, 6):
        client.post("/aggregate", json=make_payload(stream_id=sid, frame_index=i), headers=HEADERS)
    # Low-score frame resets
    mock_vision.return_value = {"deepfake_score": 0.5}
    resp = client.post("/aggregate", json=make_payload(stream_id=sid, frame_index=6), headers=HEADERS)
    assert resp.json()["alert"] is False


# ── Integration: Vision error causes 502 (TESTING.md §3) ─────────────────────

@patch("main.call_vision")
def test_vision_error_causes_502(mock_vision):
    """Vision Service error → 502, frame dropped (TESTING.md §3)."""
    mock_vision.side_effect = Exception("Vision Down")
    r = client.post("/aggregate", json=make_payload(stream_id="err_1"), headers=HEADERS)
    assert r.status_code == 502

@patch("main.call_vision")
def test_vision_http_500_causes_502(mock_vision):
    """Vision Service HTTP 500 response → 502 (TESTING.md §3)."""
    from fastapi import HTTPException
    mock_vision.side_effect = HTTPException(status_code=502, detail="Vision Service Error")
    r = client.post("/aggregate", json=make_payload(stream_id="err_2"), headers=HEADERS)
    assert r.status_code == 502


# ── Integration: full AggregatedResult schema (TESTING.md §3) ────────────────

@patch("main.call_vision")
@patch("main.call_rag")
def test_aggregate_full_schema(mock_rag, mock_vision):
    """POST valid FramePayload → full AggregatedResult schema (TESTING.md §3, SCHEMA.md §4)."""
    mock_vision.return_value = {"deepfake_score": 0.6}
    mock_rag.return_value = {"audit_verdict": "PASS", "matched_signature": None}
    r = client.post("/aggregate", json=make_payload(stream_id="schema_test", frame_index=3, timestamp_ms=999), headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    # Assert all 10 SCHEMA.md §4 fields
    assert isinstance(d["stream_id"], str)
    assert isinstance(d["frame_index"], int)
    assert isinstance(d["timestamp_ms"], int)
    assert isinstance(d["deepfake_score"], float)
    assert isinstance(d["audit_verdict"], str)
    assert d["matched_signature"] is None or isinstance(d["matched_signature"], str)
    assert isinstance(d["alert"], bool)
    assert isinstance(d["rag_used"], bool)
    assert isinstance(d["latency_ms"], int)
    assert isinstance(d["drift_flag"], bool)
    # Echo fields
    assert d["stream_id"] == "schema_test"
    assert d["frame_index"] == 3
    assert d["timestamp_ms"] == 999
    assert 0.0 <= d["deepfake_score"] <= 1.0
    assert d["audit_verdict"] in ("PASS", "FAIL", "UNKNOWN")
    assert d["rag_used"] is True
