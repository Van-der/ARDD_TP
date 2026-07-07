import pytest
from fastapi.testclient import TestClient
from main import app, INTERNAL_API_KEY

client = TestClient(app)
HEADERS = {"X-API-Key": INTERNAL_API_KEY}
BAD_HEADERS = {"X-API-Key": "wrong-key"}


# ── Health ───────────────────────────────────────────────────────────────────

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["service"] == "rag-agent"


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_missing_api_key():
    """401 when X-API-Key is absent (TESTING.md §3 / API_SPEC §1)."""
    r = client.post("/audit", json={"stream_id": "s", "frame_index": 0, "deepfake_score": 0.5})
    assert r.status_code == 401

def test_invalid_api_key():
    """401 when X-API-Key has wrong value (API_SPEC §1)."""
    r = client.post("/audit", json={"stream_id": "s", "frame_index": 0, "deepfake_score": 0.5},
                    headers=BAD_HEADERS)
    assert r.status_code == 401


# ── Payload validation → 422 ─────────────────────────────────────────────────

def test_score_above_range_returns_422():
    """deepfake_score > 1.0 → 422 (API_SPEC §3, TESTING.md §3)."""
    r = client.post("/audit", json={"stream_id": "s", "frame_index": 0, "deepfake_score": 1.5},
                    headers=HEADERS)
    assert r.status_code == 422

def test_score_below_range_returns_422():
    """deepfake_score < 0.0 → 422 (API_SPEC §3)."""
    r = client.post("/audit", json={"stream_id": "s", "frame_index": 0, "deepfake_score": -0.1},
                    headers=HEADERS)
    assert r.status_code == 422

def test_missing_stream_id_returns_422():
    """Missing stream_id → 422 (API_SPEC §3 — 'missing fields')."""
    r = client.post("/audit", json={"frame_index": 0, "deepfake_score": 0.5},
                    headers=HEADERS)
    assert r.status_code == 422

def test_missing_deepfake_score_returns_422():
    """Missing deepfake_score → 422."""
    r = client.post("/audit", json={"stream_id": "s", "frame_index": 0},
                    headers=HEADERS)
    assert r.status_code == 422


# ── Unit: high score → FAIL verdict (mock LLM path) ─────────────────────────

def test_audit_high_score_fail():
    """High deepfake_score with matching signature → FAIL (MOCK_LLM=true default)."""
    r = client.post("/audit",
                    json={"stream_id": "stream_1", "frame_index": 42, "deepfake_score": 0.85},
                    headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["stream_id"] == "stream_1"
    assert d["frame_index"] == 42
    assert d["audit_verdict"] == "FAIL"


# ── Unit: low score (< 0.3) → PASS ──────────────────────────────────────────

def test_audit_low_score_pass():
    """deepfake_score < 0.3 → mock LLM returns PASS (content is confidently real)."""
    r = client.post("/audit",
                    json={"stream_id": "s", "frame_index": 0, "deepfake_score": 0.1},
                    headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["audit_verdict"] == "PASS"
    assert d["confidence"] > 0.0


# ── Unit: borderline score (0.3–0.49) → UNKNOWN ──────────────────────────────

def test_audit_borderline_score_unknown():
    """deepfake_score in [0.3, 0.5) → mock LLM returns UNKNOWN (genuinely uncertain range)."""
    r = client.post("/audit",
                    json={"stream_id": "s", "frame_index": 0, "deepfake_score": 0.4},
                    headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["audit_verdict"] == "UNKNOWN"
    assert d["confidence"] == 0.0


# ── Integration: full RAGAuditVerdict schema (TESTING.md §3) ─────────────────

def test_audit_full_schema():
    """POST valid AuditRequest → response matches RAGAuditVerdict schema exactly (TESTING.md §3, SCHEMA.md §3)."""
    r = client.post("/audit",
                    json={"stream_id": "schema_test", "frame_index": 5, "deepfake_score": 0.8},
                    headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    # All 5 SCHEMA.md §3 fields present with correct types
    assert isinstance(d["stream_id"], str)
    assert isinstance(d["frame_index"], int)
    assert isinstance(d["audit_verdict"], str)
    assert d["matched_signature"] is None or isinstance(d["matched_signature"], str)
    assert isinstance(d["confidence"], float)
    # Value constraints
    assert d["audit_verdict"] in ("PASS", "FAIL", "UNKNOWN")
    assert 0.0 <= d["confidence"] <= 1.0
    assert d["stream_id"] == "schema_test"
    assert d["frame_index"] == 5
