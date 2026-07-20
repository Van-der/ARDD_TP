import pytest
from fastapi.testclient import TestClient
from main import app, INTERNAL_API_KEY, init_vector_store

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


# ── Chroma persistence + regression against pre-swap behavior (M7) ──────────

def test_chroma_persists_across_reinit():
    """Re-running init_vector_store() against the same persist_directory
    should not need to re-embed from scratch, and must return identical
    similarity results for a fixed query (persistence actually working)."""
    import main
    query = main.map_score_to_query(0.85)
    before = main.vector_store.similarity_search_with_score(query, k=1)
    init_vector_store()  # re-init against the same on-disk collection
    after = main.vector_store.similarity_search_with_score(query, k=1)
    assert before[0][0].metadata["label"] == after[0][0].metadata["label"]
    assert before[0][1] == pytest.approx(after[0][1], abs=1e-6)

def test_high_score_matches_signature_with_expected_confidence():
    """Regression check: a clearly-high deepfake_score must still match a
    signature and produce the same FAIL/confidence relationship the FAISS
    baseline had (matched_signature present, confidence near the boosted
    score per generate_verdict_via_llm's mock-LLM FAIL branch)."""
    r = client.post("/audit",
                    json={"stream_id": "regression_test", "frame_index": 1, "deepfake_score": 0.85},
                    headers=HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["audit_verdict"] == "FAIL"
    assert d["matched_signature"] is not None
    assert d["confidence"] == pytest.approx(min(0.95, 0.85 + 0.05), abs=1e-6)
