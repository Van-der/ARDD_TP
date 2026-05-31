# Technical Requirements Document

**Project:** ARDD-TP
**Version:** 1.0.0

## 1. Tech Stack

| Component | Technology |
|---|---|
| Message Broker | Apache Kafka |
| Ingest Gateway | Python (OpenCV, FFmpeg) |
| Vision Service | PyTorch, FastAPI (EfficientNet + FFT dual-branch) |
| RAG / Context Service | LangChain, FAISS/ChromaDB, Ollama/Mistral |
| Aggregation Service | Python, FastAPI |
| MLOps & Telemetry | MLflow |
| Frontend | React, TypeScript, Zustand, WebSockets |
| Infrastructure | Docker, Docker Compose |

---

## 2. Performance & SLA

- **Latency:** End-to-end processing per frame must not exceed **200ms**.
- **Throughput:** Must handle concurrent input from at least 3 Kafka topics.
- **Uptime:** All microservices must implement automatic restart policies via Docker Compose.

### Latency Budget Breakdown (p95, sequential)

| Hop | Budget |
|---|---|
| Ingest Gateway → Kafka publish | ≤ 10ms |
| Kafka consume → Vision Service inference | ≤ 80ms |
| Vision → Aggregation Service | ≤ 10ms |
| Aggregation → RAG Agent (timeout) | ≤ 100ms |
| Aggregation merge + emit | ≤ 20ms |
| **Total** | **≤ 200ms** |

> RAG timeout is set to **100ms** — not 150ms — to leave 20ms for Aggregation overhead after Vision's 80ms p95 budget.

---

## 3. Module Specifications

- **Ingest Gateway:** Decodes RTSP/HTTP video stream, extracts frames via OpenCV/FFmpeg, encodes as JPEG, publishes `FramePayload` to the Kafka `frames` topic.
- **Kafka Consumer:** Reads binary payloads, decodes to OpenCV tensors, forwards to Vision Service.
- **FastAPI Vision Node:** Exposes `POST /infer`; accepts tensors, runs MTCNN alignment, executes EfficientNet-FFT model, returns `VisionResult`.
- **Aggregation Service:** Receives `VisionResult`, calls RAG Context Agent sequentially, merges results into `AggregatedResult`, emits to MLflow and WebSocket. RAG timeout budget: **100ms**.
- **LangChain Auditor:** Retrieves metadata via semantic search using the vision score as query context; applies strict refusal-based guardrails for unidentifiable inputs.

---

## 4. Scoring Algorithm

### 4.1 Vision Score (EfficientNet + FFT dual-branch)

The Vision Service produces a `deepfake_score ∈ [0.0, 1.0]` via a weighted combination of two branches:

```
deepfake_score = α · spatial_score + (1 - α) · frequency_score
```

- **`spatial_score`** — EfficientNet-B4 sigmoid output on the aligned face crop. Captures texture and identity inconsistencies.
- **`frequency_score`** — FFT branch sigmoid output on the frequency-domain representation of the frame. Captures GAN-specific spectral artefacts.
- **`α = 0.6`** (spatial branch weighted higher; tunable via MLflow experiment config).
- If MTCNN alignment fails, `deepfake_score` is set to **0.5** (neutral baseline) and both branches are skipped.

### 4.2 Final Combined Score (Aggregation Service)

The Aggregation Service computes a `final_score` from the vision score and RAG confidence:

```
final_score = deepfake_score · (1 + β · rag_boost)
final_score = clamp(final_score, 0.0, 1.0)
```

Where:
- **`rag_boost`** — applied only when `audit_verdict == "FAIL"`: `β = 0.15`. Increases the score when RAG confirms a known threat signature.
- **`rag_boost = 0`** when `audit_verdict == "PASS"`, `"UNKNOWN"`, or `rag_used == false`.

### 4.3 Alert Threshold

An `alert: true` flag is set on `AggregatedResult` when `final_score > 0.90` for **5 or more consecutive frames** on the same `stream_id`.

### 4.4 Drift Detection & Ground Truth

MLflow computes a rolling average of `deepfake_score` over the last **100 frames** per stream. If the average drops below **0.60** on frames with a confirmed real ground-truth label, `drift_flag: true` is set and the model is flagged for retraining.

**Ground truth mechanism:** Ground truth labels are supplied via a separate `GroundTruthLabel` event published to the Kafka `labels` topic by an external labelling service or operator. The Aggregation Service joins incoming labels to stored `AggregatedResult` records by `(stream_id, frame_index)` and passes labelled frames to the MLflow drift monitor.

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "label": "string (REAL | FAKE)",
  "labelled_by": "string (OPERATOR | AUTO_LABEL_SERVICE)",
  "timestamp_ms": "integer"
}
```

Frames without a ground-truth label are excluded from drift calculation. Drift is only evaluated on `label: "REAL"` frames — a drop in score on real frames indicates the model is becoming less confident on authentic content.

---

## 5. Failure Scenarios

See `ERROR_HANDLING.md` for the full failure matrix.

Key current-phase behaviours:
- **Face alignment failure:** Vision bypasses inference, returns neutral score 0.5.
- **RAG timeout (>100ms):** Aggregation resolves with Vision score only (`rag_used: false`).
- **Vision Service unavailable:** Frame dropped, `pipeline_error` emitted to WebSocket.
- **MLflow unavailable:** Telemetry buffered in memory (up to 100 entries), flushed on recovery.

---

## 6. Future States

> Items below are **not** part of the v1.0.0 implementation. See `ROADMAP.md` for phasing.

- **gRPC transport:** Replace REST between Kafka Consumer ↔ Vision Service to reduce serialization overhead (Phase 2).
- **Horizontal Vision scaling:** Multiple Vision Service replicas behind a load balancer (Phase 2).
- **ChromaDB persistent store:** Replace in-memory FAISS with persistent ChromaDB (Phase 2).
- **Apache Flink aggregation:** Windowed stream processing and temporal analysis (Phase 3).
- **Automated retraining pipeline:** Auto-trigger fine-tuning on drift detection (Phase 3).
