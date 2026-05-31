# System Architecture

## 1. Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        ARDD-TP Pipeline                         │
│                                                                 │
│  [Video Source] ──► [Ingest Gateway] ──► [Kafka: frames topic]  │
│                                                  │              │
│                                         [Vision Service]        │
│                                          (EfficientNet+FFT)     │
│                                                  │              │
│                                       [Aggregation Service]     │
│                                         ┌────────┴────────┐     │
│                                    [RAG Agent]       [MLflow]   │
│                                    (LangChain)                  │
│                                         └────────┬────────┘     │
│                                       [WebSocket Broadcaster]   │
│                                                  │              │
│                                        [React Dashboard]        │
└─────────────────────────────────────────────────────────────────┘
```

## 2. Components

### Ingest Gateway
- **Role:** System entry point. Decodes RTSP/HTTP video, extracts frames, publishes to Kafka.
- **Tech:** Python, OpenCV, FFmpeg.
- **Output:** `FramePayload` → Kafka `frames` topic.

### Kafka Broker
- **Role:** Decouples ingestion from processing. Buffers frame payloads.
- **Topics:** `frames` (ingest → vision), extensible to additional stream topics.
- **Capacity:** Minimum 3 concurrent topics.

### Vision Service
- **Role:** Runs MTCNN face alignment and EfficientNet+FFT dual-branch inference on each frame.
- **Tech:** PyTorch, FastAPI.
- **Input:** `FramePayload` from Kafka. **Output:** `VisionResult`.

### Aggregation Service
- **Role:** Orchestrates the sequential Vision → RAG pipeline. Merges results. Emits to MLflow and WebSocket.
- **Tech:** Python, FastAPI.
- **Input:** `VisionResult`. **Output:** `AggregatedResult`.
- **RAG timeout budget:** 150ms. Falls back to Vision-only on breach.

### RAG Context Agent
- **Role:** Semantic search against known threat signatures, conditioned on the Vision score.
- **Tech:** LangChain, FAISS/ChromaDB, Ollama/Mistral.
- **Input:** Vision score + stream metadata. **Output:** `RAGAuditVerdict`.

### MLflow Registry
- **Role:** Logs per-frame telemetry (scores, latency, drift flag). Tracks model performance over time.
- **Drift trigger:** Flags model for retraining when confidence moving average drops below 60%.

### WebSocket Broadcaster
- **Role:** Pushes `AggregatedResult` to connected React clients in real time.
- **Resilience:** Clients reconnect with exponential backoff; stale-data banner shown on disconnect.

### React Dashboard
- **Role:** Renders live inference scores, audit verdicts, latency graphs, and compliance alerts.
- **Tech:** React, TypeScript, Zustand.

## 3. Data Flow Summary

```
FramePayload → VisionResult → AggregatedResult → MLflow log
                                              └─► WebSocket push
```

## 4. Orchestration & Service Discovery

### 4.1 Container Orchestration

All components run as Docker containers managed by **Docker Compose** (`docker-compose.yml`). Each service declares `restart: unless-stopped`.

### 4.2 Service Discovery

Services locate each other via **Docker Compose DNS** — each service is reachable by its Compose service name as a hostname within the shared `ardd_net` bridge network. No external service registry is required for v1.0.0.

| Caller | Target | Address |
|---|---|---|
| Ingest Gateway | Kafka | `kafka:9092` |
| Vision Service (consumer) | Kafka | `kafka:9092` |
| Aggregation Service | Vision Service | `http://vision:8001/infer` |
| Aggregation Service | RAG Agent | `http://rag:8002/audit` |
| Aggregation Service | MLflow | `http://mlflow:5000` |
| React Dashboard | WebSocket | `ws://aggregation:8003/stream` |

### 4.3 Health Checks

Each service exposes a `GET /health` endpoint returning `200 OK`. Docker Compose `healthcheck` blocks use this to gate dependent service startup order:

```
kafka → vision → rag → aggregation → frontend
```

### 4.4 Startup Order (depends_on)

```
zookeeper → kafka → [vision, rag, mlflow] → aggregation → frontend
```

### 4.5 Deployment

Inter-service communication is HTTP/REST for v1.0.0. gRPC migration for the Kafka Consumer ↔ Vision Service hop is planned in Phase 2 (see `ROADMAP.md`).
