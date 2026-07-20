# System Architecture

## 1. Component Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       ARDD-TP Lambda Architecture                           │
│                                                                             │
│  [Video Source]                                                             │
│       │                                                                     │
│       ▼                                                                     │
│  ┌─────────────┐     ┌──────────┐                                           │
│  │   Ingest    │────▶│  Kafka   │                                           │
│  │   Gateway   │     │  Broker  │                                           │
│  └─────────────┘     └────┬─────┘                                           │
│                           │                                                 │
│             ┌─────────────┴─────────────┐                                   │
│             ▼                           ▼                                   │
│  ┌──────────────────────┐    ┌──────────────────────┐                       │
│  │     SPEED LAYER      │    │     BATCH LAYER      │                       │
│  │   (Vision Service)   │    │  (Temporal Service)  │                       │
│  │ - Frame-by-frame     │    │ - 20-frame tumbling  │                       │
│  │ - EfficientNet + FFT │    │ - Sequence analysis  │                       │
│  │ - 200ms SLA          │    │ - ResNext50+LSTM      │                       │
│  └──────────┬───────────┘    └──────────┬───────────┘                       │
│             │                           │                                   │
│             ▼                           ▼                                   │
│  ┌──────────────────────────────────────────────────┐                       │
│  │               Aggregation Service                │                       │
│  │    Speed Result (200ms)   Batch Result (30s)     │                       │
│  └────────────────────────┬─────────────────────────┘                       │
│                           │                                                 │
│                 ┌─────────┴──────────┐                                      │
│                 ▼                    ▼                                      │
│            [MLflow]         [React Dashboard]                               │
│                             ┌──────────────┐                               │
│                             │  Live Ticker │ ← frame scores (200ms)        │
│                             ├──────────────┤                               │
│                             │  Audit Panel │ ← temporal report (~0.67s)    │
│                             └──────────────┘                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Architectural Pattern: Lambda Architecture

ARDD-TP implements a **Lambda Architecture** — a data processing pattern that combines two parallel, independent processing paths (layers) on the same stream:

| | Speed Layer | Batch Layer |
|---|---|---|
| **Service** | Vision Service | Temporal Service |
| **Trigger** | Every frame (~33ms at 30 FPS) | Every 20 frames (~0.67s at 30 FPS) |
| **Input** | Raw JPEG frame (from `frames` topic) | Raw JPEG frame (from `frames` topic, separate consumer group) |
| **Model** | EfficientNet-B4 + FFT | ResNext50+LSTM (`Naman712/Deep-fake-detection`) |
| **Output** | `deepfake_score` | `temporal_score` + `temporal_verdict` |
| **SLA** | 200ms end-to-end | Best-effort, ~0.67s cycle |
| **Failure impact** | Blocks live dashboard | Dashboard audit panel degrades gracefully |

Both layers feed into the **Aggregation Service**, which merges results and pushes to MLflow and the React Dashboard via WebSocket.

---

## 3. Components

### Ingest Gateway
- **Role:** System entry point. Decodes RTSP/HTTP video, extracts frames, publishes to Kafka.
- **Tech:** Python, OpenCV, FFmpeg.
- **Output:** `FramePayload` → Kafka `frames` topic.

### Kafka Broker
- **Role:** Decouples ingestion from both the Speed and Batch processing layers. Both layers subscribe to the same `frames` topic independently.
- **Topics:** `frames` (all consumers), `labels` (ground truth from operators).
- **Capacity:** Minimum 3 concurrent stream topics.

### Vision Service (Speed Layer)
- **Role:** Runs MTCNN face alignment and EfficientNet-B4 + FFT dual-branch inference on each frame.
- **Tech:** PyTorch, FastAPI.
- **Input:** `FramePayload` via HTTP POST from Aggregation Service.
- **Output:** `VisionResult` — `deepfake_score`, `feature_vector` (1024-d, reserved for future use).
- **Note:** `feature_vector` is the flattened EfficientNet-B4 penultimate-layer tensor. Not consumed by Phase 2 Temporal Service (which uses its own ResNext50 extractor). Retained in the schema for Phase 3+ extensibility.

### Temporal Service (Batch Layer) — *Phase 2*
- **Role:** Runs a pre-trained ResNext50+LSTM sequence model on 20-frame tumbling windows (~0.67s at 30 FPS) to detect temporal anomalies: micro-jitters, unnatural blinking, identity inconsistency across frames.
- **Tech:** PyTorch (ResNext50+LSTM — `Naman712/Deep-fake-detection`, 87% accuracy), `aiokafka`, FastAPI.
- **Input:** Raw `FramePayload` from Kafka `frames` topic (same topic as Vision Service, separate consumer group `temporal-service-group`).
- **Preprocessing:** Each JPEG decoded to 112×112 RGB, ImageNet normalised (`mean=[0.485,0.456,0.406]`, `std=[0.229,0.224,0.225]`), stacked into `[1, 20, 3, 112, 112]` tensor.
- **Buffer:** `deque(maxlen=20)` per `stream_id`; tumbling window — cleared after each inference. ~11 MB per stream at full buffer (20 × 112×112×3 float32).
- **Score mapping:** `temporal_score = F.softmax(logits, dim=1)[0][0].item()` — fake class probability → `[0.0, 1.0]`.
- **Output:** `TemporalAuditResult` every ~0.67s → HTTP POST to Aggregation Service `POST /temporal_audit`.
- **Fallback on incomplete buffer:** Zero-pads to 20 frames, sets `low_confidence_flag: true`.
- **Fallback on sparse buffer (`N < 6`):** Returns `temporal_verdict: "UNKNOWN"` without running inference.
- **Fallback on missing weights:** Uses random-initialised model; sets `model_used: "random-fallback"`.
- **Fallback on frame gaps:** Linearly interpolates missing tensors from adjacent neighbours; logs `frames_interpolated` count.
- **Port:** 8004.

> **Future scope:** Sliding window (overlapping inference every K frames) planned once tumbling baseline is stable.

### RAG Context Agent
- **Role:** Semantic search against known threat signatures, conditioned on the Vision score.
- **Tech:** LangChain, ChromaDB (persistent, M7 — replaced the original in-memory FAISS store), Ollama/Mistral.
- **Input:** Vision score + stream metadata. **Output:** `RAGAuditVerdict`.

### Aggregation Service
- **Role:** Orchestrates two independent result streams:
  1. **Speed path:** aiokafka consumer on `frames` → Vision HTTP → RAG → live `AggregatedResult` (200ms SLA).
  2. **Batch path:** Temporal Service POSTs `TemporalAuditResult` every ~0.67s → merge into next broadcast.
- **Tech:** Python, FastAPI.
- **Output:** Emits to MLflow and WebSocket broadcaster.
- **RAG timeout budget:** 100ms.

### MLflow Registry
- **Role:** Logs per-frame telemetry (scores, latency, drift flag). Logs per-30s temporal audit results. Tracks model performance over time.
- **Drift trigger:** Flags model for retraining when confidence moving average drops below 60%.

### WebSocket Broadcaster
- **Role:** Pushes both live `AggregatedResult` (every frame) and periodic `TemporalAuditResult` (every ~0.67s) to connected React clients.
- **Resilience:** Clients reconnect with exponential backoff; stale-data banner shown on disconnect.

### React Dashboard
- **Role:** Renders two panels:
  1. **Live Ticker** — real-time frame-by-frame deepfake scores (Speed Layer).
  2. **Audit Panel** — periodic ~0.67s temporal analysis report (Batch Layer, 20-frame window): "Temporal Analysis: 98% Natural Continuity. No micro-jitters detected."
- **Tech:** React, TypeScript, Zustand.

---

## 4. Data Flow Summary

```
Kafka `frames` topic
    │
    ├──▶ Aggregation Service (aiokafka consumer)
    │         │
    │         ▼
    │    Vision Service (HTTP /infer) ──▶ VisionResult
    │         │
    │         ▼
    │    RAG Agent (HTTP /audit) ──▶ AggregatedResult ──▶ [MLflow + WebSocket]
    │
    └──▶ Temporal Service (aiokafka consumer, group: temporal-service-group)
              │  deque(maxlen=20) per stream_id
              ▼
         ResNext50+LSTM inference (every 20 frames)
              │
              ▼
         HTTP POST /temporal_audit ──▶ Aggregation Service ──▶ [WebSocket → Dashboard]
```

---

## 5. Orchestration & Service Discovery

### 5.1 Container Orchestration

All components run as Docker containers managed by **Docker Compose** (`docker-compose.yml`). Each service declares `restart: unless-stopped`.

### 5.2 Service Discovery

Services locate each other via **Docker Compose DNS** — each service is reachable by its Compose service name as a hostname within the shared `ardd_net` bridge network.

| Caller | Target | Address |
|---|---|---|
| Ingest Gateway | Kafka | `kafka:9092` |
| Vision Service (consumer) | Kafka | `kafka:9092` |
| Temporal Service (consumer) | Kafka | `kafka:9092` |
| Aggregation Service | Vision Service | `http://vision-service:8001/infer` |
| Aggregation Service | RAG Agent | `http://rag-agent:8002/audit` |
| Aggregation Service | Temporal Service | `http://temporal-service:8004/batch_status` |
| Aggregation Service | MLflow | `http://mlflow:5000` |
| React Dashboard | WebSocket | `ws://aggregation:8003/stream` |

### 5.3 Health Checks

Each service exposes a `GET /health` endpoint returning `200 OK`. The Temporal Service additionally exposes `GET /batch_status` showing buffer fill level.

### 5.4 Startup Order (depends_on)

```
zookeeper → kafka → [vision, rag, mlflow] → [aggregation, temporal] → frontend
```

### 5.5 Deployment

Inter-service communication is HTTP/REST for v1.0.0 (Speed Layer) and HTTP/REST for Temporal batch report delivery. gRPC migration for the Kafka Consumer ↔ Vision Service hop is planned in Phase 3 (see `ROADMAP.md`).
