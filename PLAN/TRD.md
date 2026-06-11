# Technical Requirements Document

**Project:** ARDD-TP
**Version:** 1.0.0

## 1. Tech Stack

| Component | Technology |
|---|---|
| Message Broker | Apache Kafka |
| Ingest Gateway | Python (OpenCV, FFmpeg) |
| **Speed Layer** — Vision Service | PyTorch, FastAPI (EfficientNet-B4 + FFT dual-branch) |
| **Batch Layer** — Temporal Service | PyTorch (Pre-trained LSTM / ViT, DFDC weights), FastAPI |
| Feature Buffer (Batch Layer) | In-memory Python deque / Redis |
| RAG / Context Service | LangChain, FAISS/ChromaDB, Ollama/Mistral |
| Aggregation Service | Python, FastAPI |
| MLOps & Telemetry | MLflow |
| Frontend | React, TypeScript, Zustand, WebSockets |
| Infrastructure | Docker, Docker Compose |

---

## 2. Performance & SLA

- **Latency:** End-to-end processing per frame must not exceed **200ms** (Speed Layer).
- **Throughput:** Must handle concurrent input from at least 3 Kafka topics.
- **Uptime:** All microservices must implement automatic restart policies via Docker Compose.

### Speed Layer Latency Budget (p95, sequential, per frame)

| Hop | Budget |
|---|---|
| Ingest Gateway → Kafka publish | ≤ 10ms |
| Kafka consume → Vision Service inference | ≤ 80ms |
| Vision → Aggregation Service | ≤ 10ms |
| Aggregation → RAG Agent (timeout) | ≤ 100ms |
| Aggregation merge + emit | ≤ 20ms |
| **Total (Speed Layer)** | **≤ 200ms** |

### Batch Layer SLA (per 30-second window)

| Step | Budget |
|---|---|
| Feature vector buffer fill (continuous Kafka consume) | Ongoing |
| LSTM / ViT sequence inference on `[900, 1024]` tensor | ≤ 5s |
| Temporal result POST to Aggregation + WebSocket emit | ≤ 500ms |
| **Total (Batch Layer)** | **Best-effort, ≤ 30s cycle** |

> The Batch Layer operates independently of the Speed Layer. A Temporal Service timeout or crash does **not** affect Speed Layer SLA.

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

### 4.1 Vision Score (EfficientNet-B4 + FFT dual-branch, Speed Layer)

The Vision Service produces a `deepfake_score ∈ [0.0, 1.0]` via a weighted combination of two branches, **and** a `feature_vector` for downstream temporal analysis:

```
deepfake_score  = α · spatial_score + (1 - α) · frequency_score
feature_vector  = efficientnet_b4.penultimate_layer(face_crop)  # shape: [1, 1024] → serialized as float32 bytes
```

- **`spatial_score`** — EfficientNet-B4 sigmoid output on the aligned face crop. Captures texture and identity inconsistencies.
- **`frequency_score`** — FFT branch sigmoid output on the frequency-domain representation of the frame. Captures GAN-specific spectral artefacts.
- **`α = 0.6`** (spatial branch weighted higher; tunable via MLflow experiment config).
- **`feature_vector`** — The 1024-d tensor from the layer immediately preceding the EfficientNet-B4 classification head. Encodes the high-level face representation without the binary decision. This is the input to the Temporal Service.
- If MTCNN alignment fails, `deepfake_score` is set to **0.5** (neutral baseline), both branches are skipped, and `feature_vector` is `null`.

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

### 4b. Temporal Score (LSTM / ViT Sequence Model, Batch Layer) — *Phase 2*

The Temporal Service accumulates `feature_vector` payloads over a **30-second sliding window** (900 vectors at 30 FPS) and runs a pre-trained sequence model:

```
temporal_sequence = stack(feature_vectors[-900:])          # shape: [900, 1024]
temporal_score    = lstm_or_vit_model(temporal_sequence)   # float in [0.0, 1.0]
```

**Temporal Model Strategy:**
- **Pre-trained models:** Drop-in weights are available from open-source repositories (e.g., Hazem020/DeepFake-Detection, saanikagupta/Deepfake-Detection-Challenge, TimeSformer/ViT fine-tuned on Celeb-DF, LipForensics).
- **Training the LSTM Head:** Because the Speed Layer handles heavy feature extraction (reducing 3D video to 1024-d vectors), the Temporal Service LSTM head is tiny (roughly 2 to 5 million parameters). It can be trained on a single consumer GPU in a matter of hours using a subset of the FaceForensics++ dataset.

**Batch Layer Implementation Blueprint (PyTorch):**
```python
import torch
import torch.nn as nn

class TemporalBatchAuditor(nn.Module):
    def __init__(self, feature_dim=1024, hidden_dim=256, lstm_layers=2):
        super(TemporalBatchAuditor, self).__init__()
        
        # The LSTM takes the 1024D vector from your Kafka buffer
        # batch_first=True means we expect input shape: (batch_size, sequence_length, features)
        self.lstm = nn.LSTM(
            input_size=feature_dim,
            hidden_size=hidden_dim,
            num_layers=lstm_layers,
            batch_first=True,
            dropout=0.3
        )
        
        # The final decision layer
        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 1),
            nn.Sigmoid()
        )

    def forward(self, x):
        # x shape from Kafka Buffer: (1, 900, 1024) -> 1 stream, 30 secs at 30fps, 1024 features
        lstm_out, (hidden_state, cell_state) = self.lstm(x)
        
        # We only care about the very last output of the LSTM sequence 
        # (what it concluded after watching all 900 frames)
        final_temporal_state = lstm_out[:, -1, :] 
        
        # Pass it to the classifier to get the final deepfake probability
        verdict = self.classifier(final_temporal_state)
        return verdict
```

**Buffer resilience rules:**

| Condition | Action |
|---|---|
| Buffer has `N < 900` vectors (incomplete window) | Zero-pad tensor to `[900, 1024]`; set `low_confidence_flag: true` |
| `N < 300` vectors (`window_duration_s < 10`) | Return `temporal_verdict: "UNKNOWN"` immediately without running inference |
| Frame gap detected (non-contiguous `frame_index`) | Linearly interpolate missing vectors from adjacent neighbours |

**`temporal_verdict` mapping:**

| `temporal_score` | `temporal_verdict` |
|---|---|
| ≥ 0.85 | `FAIL` |
| 0.40 – 0.85 | `UNKNOWN` |
| < 0.40 | `PASS` |

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

- **Temporal Batch Service (Phase 2):** LSTM/ViT sequence model on 30-second feature-vector windows.
- **gRPC transport (Phase 3):** Replace REST between Kafka Consumer ↔ Vision Service to reduce serialization overhead.
- **Horizontal Vision scaling (Phase 3):** Multiple Vision Service replicas behind a load balancer.
- **ChromaDB persistent store (Phase 3):** Replace in-memory FAISS with persistent ChromaDB.
- **Apache Flink aggregation (Phase 4):** Windowed stream processing and temporal analysis.
- **Automated retraining pipeline (Phase 4):** Auto-trigger fine-tuning on drift detection.
