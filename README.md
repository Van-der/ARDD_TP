# ARDD-TP
**Autonomous Real-Time Deepfake Detection & Telemetry Pipeline**

Real-time video stream analysis pipeline built on a **Lambda Architecture** — combining a live frame-by-frame Speed Layer with a temporal Batch Layer that detects sequence-level anomalies every ~0.67 seconds.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       ARDD-TP Lambda Architecture                           │
│                                                                             │
│  [Video Source]                                                             │
│       │                                                                     │
│       ▼                                                                     │
│  ┌─────────────┐     ┌──────────┐                                           │
│  │   Ingest    │────▶│  Kafka   │                                           │
│  │   Gateway   │     │  frames  │                                           │
│  └─────────────┘     └────┬─────┘                                           │
│                           │                                                 │
│             ┌─────────────┴─────────────┐                                   │
│             ▼                           ▼                                   │
│  ┌──────────────────────┐    ┌──────────────────────┐                       │
│  │     SPEED LAYER      │    │     BATCH LAYER      │                       │
│  │   (Vision Service)   │    │  (Temporal Service)  │                       │
│  │ - Frame-by-frame     │    │ - 20-frame tumbling  │                       │
│  │ - EfficientNet + FFT │    │ - Sequence analysis  │                       │
│  │ - 200ms SLA          │    │ - ResNext50 + LSTM   │                       │
│  └──────────┬───────────┘    └──────────┬───────────┘                       │
│             │                           │                                   │
│             ▼                           ▼                                   │
│  ┌──────────────────────────────────────────────────┐                       │
│  │               Aggregation Service                │                       │
│  │   Speed Result (live)   Batch Result (~0.67s)    │                       │
│  └────────────────────────┬─────────────────────────┘                       │
│                           │                                                 │
│                ┌──────────┴──────────┐                                      │
│                ▼                     ▼                                      │
│         ┌───────────┐         ┌───────────┐                                 │
│         │  MLflow   │         │  React    │                                 │
│         │ Telemetry │         │ Dashboard │                                 │
│         └───────────┘         └───────────┘                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Stack

| Layer | Technology |
|---|---|
| Ingest Gateway | Python, OpenCV, FFmpeg, `kafka-python-ng` |
| Message Broker | Apache Kafka (SASL_PLAINTEXT) |
| **Speed Layer** — Vision Service | PyTorch — EfficientNet-B4 + FFT, MTCNN, FastAPI |
| **Batch Layer** — Temporal Service | PyTorch — ResNext50+LSTM (`Naman712/Deep-fake-detection`), `aiokafka`, FastAPI |
| Frame Buffer (Batch Layer) | In-memory `deque(maxlen=20)` per stream; Redis in Phase 3 |
| Context Agent | LangChain (RAG, FAISS, `sentence-transformers`, Ollama/Mistral) |
| Aggregation Service | Python, FastAPI, `aiokafka`, WebSockets (JWT via `Sec-WebSocket-Protocol`) |
| Experiment Tracking | MLflow |
| Frontend | React + TypeScript + Vite + Zustand + Recharts |
| Infrastructure | Docker, Docker Compose |

---

## How It Works — The Lambda Pipeline

Both layers independently consume from the same Kafka `frames` topic.

**Speed Layer — per frame, 200ms SLA:**

1. **Ingest Gateway** decodes a live RTSP/HTTP video feed, extracts frames at up to 30 FPS, and publishes `FramePayload` to the Kafka `frames` topic.
2. **Aggregation Service** (`aiokafka` consumer, group `aggregation-pipeline-group`) receives each frame and calls **Vision Service** via `POST /infer`.
3. **Vision Service** runs MTCNN face alignment and the EfficientNet-B4 + FFT dual-branch model, returning `deepfake_score`.
4. **Aggregation Service** calls the **RAG Context Agent** with the vision score (100ms budget). Merges both results into an `AggregatedResult`, logs telemetry to MLflow, and pushes a live update to the React Dashboard — maintaining the **200ms end-to-end SLA**.

**Batch Layer — every ~0.67s (20 frames at 30 FPS):**

5. **Temporal Service** (separate `aiokafka` consumer, group `temporal-service-group`) independently receives the same `FramePayload` from the `frames` topic.
6. Each JPEG is decoded to 112×112 RGB and ImageNet-normalised. Frames accumulate in a `deque(maxlen=20)` keyed by `stream_id`.
7. At 20 frames, a `[1, 20, 3, 112, 112]` tensor is built and passed to **ResNext50+LSTM** (`Naman712/Deep-fake-detection`, 87% accuracy). The deque is cleared (tumbling window).
8. `TemporalAuditResult` is POSTed to Aggregation Service (`POST /temporal_audit`), which broadcasts it to the Dashboard's Audit Panel via WebSocket.

---

## Scoring Algorithm

```
# Vision Service — Speed Layer (per frame)
deepfake_score = 0.6 · spatial_score + 0.4 · frequency_score
# spatial_score  = EfficientNet-B4 sigmoid on aligned face crop
# frequency_score = FFT branch sigmoid on frequency-domain frame

# Aggregation Service — Speed Path
final_score = clamp(deepfake_score · (1 + 0.15 · rag_boost), 0.0, 1.0)
# rag_boost = 1 only when audit_verdict == "FAIL", else 0

# Temporal Service — Batch Path (every ~0.67s)
frames_tensor  = stack([decode_and_norm(f) for f in deque[-20:]])  # [1, 20, 3, 112, 112]
temporal_score = F.softmax(logits, dim=1)[0][0].item()             # fake class probability
```

**Alert fires** when `final_score > 0.90` for **5 or more consecutive frames** on the same stream.

---

## SLA & Resilience

### Speed Layer (200ms SLA — every frame)

| Failure | System State | Fallback |
|---|---|---|
| RAG timeout (>100ms) | Speed Layer unaffected | Resolve with Vision score only; `audit_verdict: "UNKNOWN"`, `rag_used: false` |
| Face alignment failure | Vision Service bypasses inference | Return `deepfake_score: 0.5`, `aligned: false` |
| Vision Service crash | Frame dropped | Emit `pipeline_error` to WebSocket; dashboard shows last known state |
| MLflow unavailable | Telemetry buffered | Buffer up to 100 entries in memory; flush on recovery |
| WebSocket disconnect | Dashboard freezes last state | Stale-data banner; exponential backoff reconnection |

### Batch Layer (~0.67s audit cycle — every 20 frames)

| Failure | System State | Fallback |
|---|---|---|
| Temporal Service crash or timeout | Speed Layer **unaffected** | Dashboard Audit Panel shows "Temporal Audit Unavailable — Relying on Spatial heuristics" |
| Incomplete buffer (`N < 20`) | Reduced confidence | Zero-pad to 20 frames; set `low_confidence_flag: true`; continue inference |
| Sparse buffer (`N < 6`) | Insufficient data | Skip inference; return `temporal_verdict: "UNKNOWN"` |
| Model weights missing | Startup fallback | Use random-initialised model; set `model_used: "random-fallback"` |

---

## Security

- Internal REST APIs require `X-API-Key` header on every request.
- WebSocket access requires a JWT bearer token (HS256, 1-hour expiry) from `POST /auth/token`, passed via `Sec-WebSocket-Protocol` subprotocol header — not the URL, to prevent token leakage in server access logs.
- All secrets injected via environment variables — see `.env.example`.
- Kafka uses SASL_PLAINTEXT authentication (PLAIN mechanism) across all services. Full TLS (SASL_SSL) is deferred to Phase 5.
- PyTorch model weights loaded with `weights_only=True` to prevent arbitrary code execution.

---

## Getting Started

### Prerequisites

- [Docker & Docker Compose](https://docs.docker.com/get-docker/) v24+
- Git

### First-Time Setup

```bash
# 1. Clone the repo
git clone <repository-url>
cd ARDD_TP

# 2. Configure secrets
cp .env.example .env
# Edit .env — set at minimum: INTERNAL_API_KEY, JWT_SECRET, KAFKA_SASL_USERNAME, KAFKA_SASL_PASSWORD

# 3. Create required volume directories
mkdir -p mlflow-data ollama-data
```

### Start the Full Stack

```bash
# Start all services in background
docker compose up -d

# Rebuild images after code changes
docker compose up -d --build

# Tail logs for a specific service
docker compose logs -f aggregation-service

# Stop all services (keeps volumes)
docker compose down

# Stop and delete all volumes
docker compose down -v
```

> **First startup:** Docker will pull images and build services — allow 2–3 minutes. Services wait for their dependencies to become healthy before starting.

### Pull the Ollama LLM Model (one-time, optional)

By default the RAG agent runs in mock mode (`MOCK_LLM=true` in `.env`). To use the real Mistral LLM:

```bash
docker exec ollama ollama pull mistral
# Then set MOCK_LLM=false in .env and restart: docker compose up -d rag-agent
```

---

## File Structure

```
ARDD_TP/
├── .dockerignore
├── .env.example               # Secret/env var template — copy to .env
├── .gitignore
├── docker-compose.yml         # Full service orchestration
├── locustfile.py              # Load test definition (Locust)
├── prepare_test_dataset.py    # Generates labelled frame samples for tests
├── simulate_stream.py         # Publishes synthetic frames to Kafka for local dev
├── test_infrastructure.py     # Smoke-tests that required files and dirs exist
├── README.md
├── Documentation.md           # Session-by-session development journal
├── TaskTo.md                  # Pending deprecation fixes and deferred features
│
├── PLAN/                      # Design documentation
│   ├── API_SPEC.md            # All endpoints, auth contracts, Kafka topics
│   ├── ARCHITECTURE.md        # Component overview and Lambda Architecture detail
│   ├── ERROR_HANDLING.md      # Failure scenarios and fallback behaviours
│   ├── FLOW.md                # Mermaid sequence diagrams (Speed + Batch layers)
│   ├── PHASES.md              # Step-by-step build order with exit criteria
│   ├── PRD.md                 # Product requirements
│   ├── REFERENCES.md          # Models, datasets, and library citations
│   ├── ROADMAP.md             # Implementation phases and future state
│   ├── SCHEMA.md              # Message, event, and threat signature schemas
│   ├── SECURITY.md            # Security spec, threat model, secrets management
│   ├── TESTING.md             # Testing strategy and accuracy benchmarks
│   └── TRD.md                 # Technical requirements and scoring algorithms
│
├── ingest-gateway/            # Decodes RTSP/HTTP stream → Kafka frames topic
│   ├── Dockerfile
│   ├── main.py
│   ├── requirements.txt
│   └── tests/
│       └── test_ingest.py
│
├── vision-service/            # Speed Layer — EfficientNet-B4 + FFT per-frame inference
│   ├── Dockerfile             # python:3.11-slim (Python 3.14 incompatible with facenet-pytorch)
│   ├── main.py
│   ├── requirements.txt
│   └── tests/
│       └── test_vision.py
│
├── rag-agent/                 # RAG context — FAISS + sentence-transformers + Ollama/Mistral
│   ├── Dockerfile
│   ├── main.py
│   ├── requirements.txt
│   └── tests/
│       └── test_rag.py
│
├── aggregation-service/       # Pipeline orchestrator — Speed path, WebSocket, MLflow, alerts
│   ├── Dockerfile
│   ├── main.py
│   ├── requirements.txt
│   └── tests/
│       └── test_aggregation.py
│
├── temporal-service/          # Batch Layer — ResNext50+LSTM 20-frame tumbling window
│   ├── Dockerfile
│   ├── main.py
│   ├── modeling.py            # DeepFakeDetector: ResNext50 backbone + LSTM + linear head
│   ├── requirements.txt
│   ├── weights/               # Mount model_87_acc_20_frames_final_data.pt here via Docker
│   └── tests/
│       ├── __init__.py
│       └── test_temporal.py
│
├── frontend/                  # React + TypeScript dashboard
│   ├── Dockerfile
│   ├── index.html
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── public/
│   │   ├── favicon.svg
│   │   └── icons.svg
│   └── src/
│       ├── App.tsx            # WebSocket connection, JWT auth, reconnection logic
│       ├── App.css
│       ├── index.css
│       ├── main.tsx
│       ├── store.ts           # Zustand state: frames, alerts, JWT, connection status
│       ├── store.test.ts
│       └── components/
│           ├── AlertBanner.tsx    # Consecutive-alert warning banner
│           ├── AuditPanel.tsx     # Temporal batch audit results display
│           └── LiveGraph.tsx      # Real-time deepfake score chart (Recharts)
│
├── tests/
│   └── e2e/
│       └── test_pipeline_e2e.py  # End-to-end: Kafka → WebSocket latency under 200ms
│
├── mlflow-data/               # MLflow experiment storage (Docker volume mount)
└── ollama-data/               # Ollama model cache (Docker volume mount)
```

---

## Service Port Reference

| Service | Port | Endpoints |
|---|---|---|
| Vision Service | 8001 | `POST /infer`, `GET /health` |
| RAG Agent | 8002 | `POST /audit`, `GET /health` |
| Aggregation Service | 8003 | `POST /auth/token`, `POST /temporal_audit`, `GET /health`, `ws://.../stream` |
| Temporal Service | 8004 | `GET /health`, `GET /batch_status`, `POST /flush` |
| MLflow | 5000 | Web UI + tracking API |
| Kafka | 9092 | Broker (SASL_PLAINTEXT) |
| Ollama | 11434 | LLM inference API |
| Frontend | 3000 | React dashboard |

---

## Test Coverage

| Service | Tests | Status |
|---|---|---|
| Ingest Gateway | 6 | Kafka publish, FPS downsampling, SASL config |
| Vision Service | 16 | Spatial branch, frequency branch, score formula, alignment failure, payload limits, auth |
| RAG Agent | 6 | FAISS search, verdict generation, similarity threshold, auth |
| Aggregation Service | 22 | Full pipeline, Vision 502, RAG timeout fallback, WebSocket JWT, alert window, drift detection |
| Temporal Service | 19 | Buffer fill, tensor shape, zero-padding, sparse fallback, full inference, schema validation, auth |
| **Total** | **69** | **53 pass on host** · vision-service requires Docker (Python 3.14 / facenet-pytorch incompatibility) |

---

## Documentation

| File | Description |
|---|---|
| [`PLAN/ARCHITECTURE.md`](./PLAN/ARCHITECTURE.md) | Component overview, Lambda Architecture detail, service discovery, startup order |
| [`PLAN/FLOW.md`](./PLAN/FLOW.md) | Mermaid sequence diagrams — Speed Layer and Batch Layer parallel flows |
| [`PLAN/PRD.md`](./PLAN/PRD.md) | Product requirements |
| [`PLAN/TRD.md`](./PLAN/TRD.md) | Technical requirements, scoring algorithms, SLA budgets |
| [`PLAN/API_SPEC.md`](./PLAN/API_SPEC.md) | All service endpoints, auth contracts, Kafka topics |
| [`PLAN/SCHEMA.md`](./PLAN/SCHEMA.md) | Message, event, and threat signature schemas |
| [`PLAN/ERROR_HANDLING.md`](./PLAN/ERROR_HANDLING.md) | All failure scenarios, fallback behaviours, known limitations |
| [`PLAN/SECURITY.md`](./PLAN/SECURITY.md) | Security spec, threat model, secrets management |
| [`PLAN/TESTING.md`](./PLAN/TESTING.md) | Testing strategy, accuracy and performance benchmarks |
| [`PLAN/PHASES.md`](./PLAN/PHASES.md) | Step-by-step build order with exit criteria (Phases 1–5) |
| [`PLAN/ROADMAP.md`](./PLAN/ROADMAP.md) | Implementation phases and future state |
| [`PLAN/REFERENCES.md`](./PLAN/REFERENCES.md) | Models, datasets, and library citations |
| [`Documentation.md`](./Documentation.md) | Development journal — session-by-session progress log |
| [`TaskTo.md`](./TaskTo.md) | Pending deprecation fixes and deferred features |
