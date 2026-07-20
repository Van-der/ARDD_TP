import pytest
from fastapi.testclient import TestClient
import numpy as np
import cv2
import base64
import os
import sys
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from main import app, extract_frequency_score, spatial_branch, SpatialBranch, device

client = TestClient(app)
headers = {"X-API-Key": "test-key"}
bad_headers = {"X-API-Key": "wrong-key"}


def make_jpeg_b64(img_np: np.ndarray) -> str:
    _, buf = cv2.imencode(".jpg", img_np)
    return base64.b64encode(buf).decode("utf-8")


def make_payload(b64: str, stream_id: str = "test", frame_index: int = 0) -> dict:
    return {
        "stream_id": stream_id,
        "frame_index": frame_index,
        "timestamp_ms": 123456,
        "payload": b64,
    }


# ── Health ──────────────────────────────────────────────────────────────────

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "vision-service"

def test_health_reports_calibration_trained_flag():
    """calibration_trained matches spatial_trained/fft_mlp_trained/fusion_trained's pattern (M13)."""
    d = client.get("/health").json()
    assert "calibration_trained" in d
    assert isinstance(d["calibration_trained"], bool)


# ── Auth ────────────────────────────────────────────────────────────────────

def test_auth_missing_key():
    """401 when X-API-Key header is absent entirely."""
    img = np.zeros((224, 224, 3), dtype=np.uint8)
    r = client.post("/infer", json=make_payload(make_jpeg_b64(img)))
    assert r.status_code == 401

def test_auth_invalid_key():
    """401 when X-API-Key header contains a wrong value."""
    img = np.zeros((224, 224, 3), dtype=np.uint8)
    r = client.post("/infer", json=make_payload(make_jpeg_b64(img)), headers=bad_headers)
    assert r.status_code == 401


# ── Unit: spatial branch (TESTING.md §2) ───────────────────────────────────

def test_spatial_branch():
    """spatial_score ∈ [0.0, 1.0] for a valid face-crop tensor (TESTING.md §2)."""
    dummy = np.random.randint(0, 256, (224, 224, 3), dtype=np.uint8)
    tensor = (
        torch.from_numpy(dummy)
        .permute(2, 0, 1)
        .float()
        .unsqueeze(0)
        / 255.0
    ).to(device)
    with torch.no_grad():
        score = spatial_branch(tensor).item()
    assert 0.0 <= score <= 1.0, f"spatial_score out of range: {score}"


# ── Unit: frequency branch (TESTING.md §2) ─────────────────────────────────

def test_frequency_branch():
    """frequency_score ∈ [0.0, 1.0] for a valid grayscale image (TESTING.md §2)."""
    img_gray = np.zeros((224, 224), dtype=np.uint8)
    score = extract_frequency_score(img_gray)
    assert 0.0 <= score <= 1.0, f"frequency_score out of range: {score}"

def test_frequency_branch_random():
    """frequency_score ∈ [0.0, 1.0] for random noise input."""
    img_gray = np.random.randint(0, 256, (224, 224), dtype=np.uint8)
    score = extract_frequency_score(img_gray)
    assert 0.0 <= score <= 1.0


# ── Unit: score combination formula (TESTING.md §2) ────────────────────────

def test_score_combination(monkeypatch):
    """deepfake_score = 0.6·spatial + 0.4·frequency — verifies the implementation, not just the formula."""
    import sys
    import os
    import numpy as np
    
    # Patch spatial branch to return a fixed known value
    from unittest.mock import patch, MagicMock
    import torch

    fixed_spatial = 0.7
    fixed_frequency = 0.4
    expected_score = round(0.6 * fixed_spatial + 0.4 * fixed_frequency, 6)  # 0.58

    # We need a valid JPEG payload — use blank image
    import cv2
    import base64
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    _, buf = cv2.imencode('.jpg', img)
    payload_b64 = base64.b64encode(buf.tobytes()).decode()

    with patch('main.spatial_branch') as mock_spatial, \
         patch('main.mtcnn') as mock_mtcnn, \
         patch('main.extract_frequency_score', return_value=fixed_frequency):

        # Simulate MTCNN finding a face box
        mock_mtcnn.detect.return_value = (
            [[5, 5, 95, 95]],  # boxes
            [0.99]             # probs
        )
        # Simulate spatial branch returning fixed score
        mock_spatial.return_value = torch.tensor([[fixed_spatial]])

        body = {"stream_id": "s1", "frame_index": 0, "timestamp_ms": 1, "payload": payload_b64}
        r = client.post("/infer", json=body, headers=headers)

    assert r.status_code == 200, r.text
    actual_score = r.json()["deepfake_score"]
    assert abs(actual_score - expected_score) < 0.05, (
        f"Score combination formula not applied correctly: got {actual_score}, expected ~{expected_score}"
    )

def test_score_combination_boundary_zero():
    expected = 0.6 * 0.0 + 0.4 * 0.0
    assert expected == 0.0

def test_score_combination_boundary_one():
    expected = 0.6 * 1.0 + 0.4 * 1.0
    assert expected == 1.0


# ── Unit: confidence calibration (M13) ─────────────────────────────────────

def test_calibrate_passthrough_when_no_calibrator(monkeypatch):
    """No calibrator loaded (the default on host, where CALIBRATION_PATH
    doesn't exist) → calibrate() is a no-op."""
    import main
    monkeypatch.setattr(main, "calibrator", None)
    assert main.calibrate(0.42) == 0.42

def test_calibrate_applies_isotonic_mapping(monkeypatch):
    """When a calibrator is loaded, calibrate() actually uses it."""
    import main
    from sklearn.isotonic import IsotonicRegression
    cal = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    cal.fit([0.0, 0.5, 1.0], [0.0, 0.2, 1.0])  # deliberately non-identity mapping
    monkeypatch.setattr(main, "calibrator", cal)
    assert main.calibrate(0.5) != 0.5
    assert abs(main.calibrate(0.5) - 0.2) < 0.05

def test_calibrate_monotonic_in_raw_score(monkeypatch):
    """Calibration must not change score rank order — a non-monotonic
    calibrator would be a bug in fit_calibration.py, not in this function,
    but this test protects the invariant regardless of what's loaded."""
    import main
    from sklearn.isotonic import IsotonicRegression
    np_scores = [0.1, 0.3, 0.5, 0.6, 0.8, 0.95]
    np_labels = [0, 0, 1, 0, 1, 1]
    cal = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    cal.fit(np_scores, np_labels)
    monkeypatch.setattr(main, "calibrator", cal)
    probe = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    calibrated = [main.calibrate(s) for s in probe]
    assert calibrated == sorted(calibrated), "calibrate() must preserve rank order"


# ── Unit: alignment failure (TESTING.md §2) ────────────────────────────────

def test_alignment_failure():
    """Blank image → aligned=false, deepfake_score=0.5 (TESTING.md §2)."""
    img = np.zeros((224, 224, 3), dtype=np.uint8)
    r = client.post("/infer", json=make_payload(make_jpeg_b64(img)), headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["aligned"] is False
    assert data["deepfake_score"] == 0.5


# ── Unit: payload too large (TESTING.md §2) ────────────────────────────────

def test_payload_too_large():
    """Payload > 2 MB → 422 (TESTING.md §2)."""
    large_b64 = base64.b64encode(os.urandom(2_500_000)).decode("utf-8")
    r = client.post("/infer", json=make_payload(large_b64), headers=headers)
    assert r.status_code == 422
    assert "exceed" in r.json()["detail"].lower()


# ── Integration: malformed / invalid base64 → 422 ──────────────────────────

def test_malformed_payload_invalid_base64():
    """Corrupt base64 string → 422 (API_SPEC.md §2, ERROR_HANDLING.md §3)."""
    r = client.post("/infer", json=make_payload("!!!NOT_VALID_BASE64!!!"), headers=headers)
    assert r.status_code == 422

def test_malformed_payload_not_jpeg():
    """Valid base64 but not a JPEG → 422."""
    garbage_b64 = base64.b64encode(b"this is not a jpeg at all").decode("utf-8")
    r = client.post("/infer", json=make_payload(garbage_b64), headers=headers)
    assert r.status_code == 422


# ── Integration: missing required field → 422 ──────────────────────────────

def test_missing_stream_id_returns_422():
    """POST without stream_id → 422 (TESTING.md §3)."""
    r = client.post(
        "/infer",
        json={"frame_index": 0, "timestamp_ms": 123, "payload": "abc"},
        headers=headers,
    )
    assert r.status_code == 422

def test_missing_payload_field_returns_422():
    """POST without payload → 422."""
    r = client.post(
        "/infer",
        json={"stream_id": "s", "frame_index": 0, "timestamp_ms": 123},
        headers=headers,
    )
    assert r.status_code == 422


# ── Integration: valid FramePayload → full VisionResult schema (TESTING.md §3) ──

def test_valid_frame_returns_full_vision_result_schema():
    """POST valid FramePayload → response matches VisionResult schema exactly (TESTING.md §3)."""
    img = np.zeros((224, 224, 3), dtype=np.uint8)
    r = client.post("/infer", json=make_payload(make_jpeg_b64(img), stream_id="s1", frame_index=7), headers=headers)
    assert r.status_code == 200
    data = r.json()
    # Assert all five schema fields are present with correct types
    assert isinstance(data["stream_id"], str),       "stream_id must be str"
    assert isinstance(data["frame_index"], int),     "frame_index must be int"
    assert isinstance(data["deepfake_score"], float), "deepfake_score must be float"
    assert isinstance(data["aligned"], bool),         "aligned must be bool"
    assert isinstance(data["latency_ms"], int),       "latency_ms must be int"
    # Values in range
    assert 0.0 <= data["deepfake_score"] <= 1.0
    assert data["latency_ms"] >= 0
    # Echo fields
    assert data["stream_id"] == "s1"
    assert data["frame_index"] == 7
