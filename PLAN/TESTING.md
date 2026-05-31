# Testing Strategy & Benchmarks

---

## 1. Testing Layers

| Layer | Scope | Tool |
|---|---|---|
| Unit | Individual functions and model branches | pytest |
| Integration | Service-to-service contracts | pytest + httpx |
| End-to-end | Full pipeline from frame ingest to WebSocket push | pytest + Kafka test client |
| Load | Throughput and latency under sustained traffic | Locust |
| Model | Inference accuracy and scoring correctness | pytest + labelled dataset |

---

## 2. Unit Tests

### Vision Service
- `test_spatial_branch`: assert `spatial_score ∈ [0.0, 1.0]` for a valid face crop.
- `test_frequency_branch`: assert `frequency_score ∈ [0.0, 1.0]` for a valid FFT input.
- `test_score_combination`: assert `deepfake_score = 0.6·spatial + 0.4·frequency` within float tolerance.
- `test_alignment_failure`: assert `deepfake_score == 0.5` and `aligned == false` when MTCNN returns no face.
- `test_payload_too_large`: assert `422` returned for payload exceeding 2MB.

### Aggregation Service
- `test_rag_boost_applied`: assert `final_score > deepfake_score` when `audit_verdict == "FAIL"`.
- `test_rag_boost_not_applied`: assert `final_score == deepfake_score` when `audit_verdict == "PASS"`.
- `test_final_score_clamped`: assert `final_score <= 1.0` when boost would exceed 1.0.
- `test_rag_timeout_fallback`: mock RAG to exceed 150ms; assert `rag_used == false` and `audit_verdict == "UNKNOWN"`.
- `test_alert_threshold`: feed 5 consecutive frames with `final_score = 0.95`; assert `alert == true` on 5th.
- `test_alert_resets`: assert `alert` resets to `false` after a frame with `final_score < 0.90`.

### Ingest Gateway
- `test_fps_downsampling`: assert frame rate drops to 5 FPS when lag flag is set.
- `test_invalid_jpeg`: assert frame is dropped and `decode_error` is incremented for a corrupt JPEG.

---

## 3. Integration Tests

### Vision Service contract
- POST a valid `FramePayload` to `POST /infer`; assert response matches `VisionResult` schema.
- POST a payload with missing `stream_id`; assert `422`.

### RAG Agent contract
- POST a valid vision result to `POST /audit`; assert response matches `RAGAuditVerdict` schema.
- POST with `deepfake_score` outside `[0.0, 1.0]`; assert `422`.

### Aggregation Service contract
- POST a valid `FramePayload` to `POST /aggregate` with Vision and RAG mocked; assert `AggregatedResult` schema.
- POST with Vision Service mocked to return `500`; assert frame is dropped and `pipeline_error` is emitted.

### Authentication
- Call any internal endpoint without `X-API-Key`; assert `401`.
- Call WebSocket upgrade without JWT; assert `401`.
- Call WebSocket upgrade with expired JWT; assert `401`.

---

## 4. End-to-End Tests

1. Publish a synthetic `FramePayload` to the Kafka `frames` topic.
2. Assert a `WebSocketPushEvent` is received on `ws://aggregation:8003/stream` within **200ms**.
3. Assert `AggregatedResult` fields are present and correctly typed.
4. Assert an MLflow run entry exists for the frame.

Repeat with:
- A frame that triggers MTCNN failure (blank image).
- A frame that triggers the RAG timeout (RAG service artificially delayed to 200ms).
- 5 consecutive high-score frames; assert `alert: true` on the 5th WebSocket event.

---

## 5. Model Accuracy Benchmarks

Evaluated on a held-out labelled dataset before any release.

| Metric | Minimum Threshold | Target |
|---|---|---|
| AUC-ROC | ≥ 0.90 | ≥ 0.95 |
| Accuracy (balanced) | ≥ 0.85 | ≥ 0.90 |
| False Positive Rate | ≤ 0.10 | ≤ 0.05 |
| False Negative Rate | ≤ 0.08 | ≤ 0.04 |
| Alignment success rate | ≥ 0.90 | ≥ 0.95 |

A release is blocked if any metric falls below the minimum threshold.

---

## 6. Performance Benchmarks

Measured under sustained load using Locust against a local Docker Compose deployment.

| Metric | Requirement | Measured At |
|---|---|---|
| End-to-end latency (p95) | ≤ 200ms | 30 FPS single stream |
| End-to-end latency (p99) | ≤ 350ms | 30 FPS single stream |
| Vision Service latency (p95) | ≤ 80ms | Isolated, GPU |
| RAG Agent latency (p95) | ≤ 120ms | Isolated |
| Aggregation Service latency (p95) | ≤ 20ms | Excluding upstream calls |
| Throughput | ≥ 3 concurrent streams at 30 FPS | Kafka multi-topic |
| WebSocket broadcast lag (p95) | ≤ 10ms | 10 concurrent clients |

A release is blocked if p95 end-to-end latency exceeds 200ms under the reference load.

---

## 7. Regression & CI

- All unit and integration tests run on every pull request via CI.
- End-to-end tests run nightly against a Docker Compose environment.
- Model accuracy benchmarks run on every model weight update.
- Performance benchmarks run before every release candidate.
