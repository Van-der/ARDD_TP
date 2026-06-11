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
│  │ - Frame-by-frame     │    │ - 30-sec feature buf │                       │
│  │ - EfficientNet + FFT │    │ - Sequence analysis  │                       │
│  │ - 200ms SLA          │    │ - LSTM / ViT Model   │                       │
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
│                             │  Audit Panel │ ← temporal report (30s)       │
│                             └──────────────┘                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Architectural Pattern: Lambda Architecture

ARDD-TP implements a **Lambda Architecture** — a data processing pattern that combines two parallel, independent processing paths (layers) on the same stream:

| | Speed Layer | Batch Layer |
|---|---|---|
| **Service** | Vision Service | Temporal Service |
| **Trigger** | Every frame (~33ms at 30 FPS) | Every 30 seconds (900 frames) |
| **Input** | Raw JPEG frame | 1024-d feature vectors from Vision Service |
| **Model** | EfficientNet-B4 + FFT | Pre-trained LSTM / ViT (DFDC weights) |
| **Output** | `deepfake_score` + `feature_vector` | `temporal_score` + `temporal_verdict` |
| **SLA** | 200ms end-to-end | Best-effort, 30s cycle |
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
- **Output:** `VisionResult` — including `deepfake_score` **and** `feature_vector` (1024-d).
- **Note:** The `feature_vector` is the flattened tensor from the penultimate EfficientNet-B4 layer, before the final classifier head. It is published back to Kafka for the Temporal Service to consume (Phase 2).

### Temporal Service (Batch Layer) — *Phase 2*
- **Role:** Accumulates 1024-d feature vectors over a 30-second sliding window. Runs a pre-trained sequence model (LSTM or ViT) to detect temporal anomalies: micro-jitters, unnatural blinking, audio-visual desynchronization.
- **Tech:** PyTorch (pre-trained DFDC LSTM/ViT weights), Python deque / Redis.
- **Input:** `feature_vector` payloads from Kafka `frames` topic (decoded from `VisionResult`).
- **Buffer:** 900 vectors × 1024 floats = ~3.6 MB per stream (negligible vs. raw frame buffering).
- **Output:** `TemporalAuditResult` every 30 seconds → HTTP POST to Aggregation Service.
- **Fallback on incomplete buffer:** Zero-pads tensor to `[900, 1024]`, sets `low_confidence_flag: true`.
- **Fallback on frame gaps:** Linearly interpolates missing feature vectors from adjacent neighbours.
- **Port:** 8004.

### RAG Context Agent
- **Role:** Semantic search against known threat signatures, conditioned on the Vision score.
- **Tech:** LangChain, FAISS/ChromaDB, Ollama/Mistral.
- **Input:** Vision score + stream metadata. **Output:** `RAGAuditVerdict`.

### Aggregation Service
- **Role:** Orchestrates two independent result streams:
  1. **Speed path:** Vision result → RAG → live `AggregatedResult` (200ms SLA).
  2. **Batch path:** Temporal result → periodic `TemporalAuditResult` merge every 30 seconds.
- **Tech:** Python, FastAPI.
- **Output:** Emits to MLflow and WebSocket broadcaster.
- **RAG timeout budget:** 100ms.

### MLflow Registry
- **Role:** Logs per-frame telemetry (scores, latency, drift flag). Logs per-30s temporal audit results. Tracks model performance over time.
- **Drift trigger:** Flags model for retraining when confidence moving average drops below 60%.

### WebSocket Broadcaster
- **Role:** Pushes both live `AggregatedResult` (every frame) and periodic `TemporalAuditResult` (every 30s) to connected React clients.
- **Resilience:** Clients reconnect with exponential backoff; stale-data banner shown on disconnect.

### React Dashboard
- **Role:** Renders two panels:
  1. **Live Ticker** — real-time frame-by-frame deepfake scores (Speed Layer).
  2. **Audit Panel** — periodic 30-second temporal analysis report (Batch Layer): "Temporal Analysis: 98% Natural Continuity. No micro-jitters detected."
- **Tech:** React, TypeScript, Zustand.

---

## 4. Data Flow Summary

```
FramePayload
    │
    ├──▶ Vision Service ──▶ VisionResult ──▶ Aggregation ──▶ RAG ──▶ AggregatedResult
    │                             │                                         │
    │                        feature_vector ─── Kafka ──▶ Temporal Service  │
    │                                                          │             │
    │                                              TemporalAuditResult       │
    │                                                          │             │
    └──────────────────────────────────────────── Aggregation ◀─────────────┘
                                                       │
                                          ┌────────────┴───────────┐
                                       [MLflow]           [WebSocket → Dashboard]
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
