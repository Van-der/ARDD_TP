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
| Message Broker | Apache Kafka (SASL_SSL — TLS transport + SASL PLAIN auth) |
| **Speed Layer** — Vision Service | PyTorch — EfficientNet-B4 (fine-tuned) + FFT MLP (trained) + isotonic calibration, MTCNN, FastAPI, mTLS |
| **Batch Layer** — Temporal Service | PyTorch — ResNext50+LSTM (`Naman712/Deep-fake-detection`), `aiokafka`, FastAPI, mTLS |
| Frame Buffer (Batch Layer) | Redis List per stream + atomic Lua push/flush |
| Context Agent | LangChain (RAG, persistent ChromaDB, `sentence-transformers`, Ollama/Mistral), mTLS |
| Aggregation Service | Python, FastAPI, `aiokafka`, WebSockets (JWT via `Sec-WebSocket-Protocol`, short-lived access + refresh tokens), TLS (browser-facing) |
| RBAC | Hardcoded admin/viewer role pairs baked into JWT claims |
| Tracing | OpenTelemetry → local Jaeger (no cloud APM) |
| Object Storage | MinIO (S3-compatible) — per-alert-streak segment archival |
| Experiment Tracking | MLflow, behind an nginx basic-auth proxy |
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
spatial_score  = EfficientNet-B4(face_crop_380x380)          # fine-tuned on FF++
freq_score     = FftMlp(radial_fft_bins_64d(face_crop_gray)) # trained on FF++
deepfake_score = sigmoid(10.14 · spatial_score + 7.04 · freq_score − 8.87)
# ^ learned logistic regression fusion; weights from FF++ val set

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

See `PLAN/SECURITY.md` for the full spec. Summary:

- Internal REST APIs require `X-API-Key` header on every request.
- WebSocket access requires a JWT bearer token (HS256, **15-minute** access-token expiry, rotating refresh tokens via `POST /auth/refresh`, self-service revocation via `POST /auth/revoke`) from `POST /auth/token`, passed via `Sec-WebSocket-Protocol` subprotocol header — not the URL, to prevent token leakage in server access logs.
- RBAC: hardcoded admin/viewer role pairs baked into JWT claims; `POST /admin/*` endpoints are role-gated (403 vs 401).
- All secrets injected via environment variables — see `.env.example`.
- Kafka uses SASL_SSL (TLS transport + SASL PLAIN auth). Vision/RAG/Temporal Service require mutual TLS (mTLS); Aggregation Service (browser-facing) uses server-auth TLS only. See `scripts/gen_certs.sh` for the local CA and one-time browser trust-store import.
- MLflow (no native auth) sits behind an `mlflow-proxy` nginx sidecar enforcing HTTP basic auth on its host-exposed port.
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

> **Reclaiming disk space:** repeated `docker compose build`/`up -d --build` cycles during development can accumulate a large build cache. Run `./scripts/docker_cleanup.sh` after a heavy session (age-filtered, non-destructive — only prunes build cache older than 24h and dangling images; pass `--aggressive` for a deeper, full prune).

### Pull the Ollama LLM Model (one-time, optional)

By default the RAG agent runs in mock mode (`MOCK_LLM=true` in `.env`). To use the real Mistral LLM:

```bash
docker exec ollama ollama pull mistral
# Then set MOCK_LLM=false in .env and restart: docker compose up -d rag-agent
```

---

## Running Locally (dashboard in your browser)

The stack runs over TLS/mTLS end-to-end (M10), so there's one extra one-time step versus a plain HTTP app: trusting the local CA.

```bash
# 1. Generate certs (skip if certs/ already exists — it's gitignored, one-time per checkout)
./scripts/gen_certs.sh
# Prints one-time instructions for importing certs/ca.crt into your OS/browser
# trust store (Chrome/Edge: chrome://settings/certificates → Authorities → Import).
# Without this, https://localhost:8003 and the dashboard will show a cert warning.

# 2. Configure secrets (if not already done — see First-Time Setup above)
cp .env.example .env

# 3. Start everything, including the frontend
docker compose up -d --build

# 4. Open the dashboard
#    http://localhost:3000  (the frontend container itself is plain HTTP;
#    only its calls to aggregation-service at :8003 are HTTPS/WSS)
```

By default the dashboard logs in as the read-only **viewer** role. To see the RBAC-gated Admin Panel (circuit-breaker reset), set in `.env` before starting the `frontend` service:

```bash
FRONTEND_CLIENT_ID=admin
FRONTEND_CLIENT_SECRET=<your ADMIN_CLIENT_SECRET value>
```

**Hybrid dev mode** (faster iteration on frontend code): keep the backend dockerized, run the frontend locally with hot reload —

```bash
docker compose up -d aggregation-service vision-service rag-agent temporal-service kafka redis minio otel-collector jaeger webhook-receiver
cd frontend && npm install && npm run dev
```

Vite reads the same `VITE_*` defaults (`https://localhost:8003`), so this works as long as step 1's CA is trusted — CORS is wide open (`*`) so the dev server's port doesn't matter.

**Deploying beyond localhost** (a real domain, real TLS, a real reverse proxy) is out of scope for this pass — see `PLAN/SECURITY.md`/`PLAN/PHASES.md` for what's already in place versus genuinely deferred.

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
├── video_feeder.py            # Streams real FF++ videos to Kafka (demo + eval modes)
├── extract_faces.py           # Offline MTCNN face crop extraction from FF++ videos (Phase 2.5)
├── train_vision.py            # EfficientNet-B4 + FFT MLP training script (Phase 2.5)
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
├── vision-service/            # Speed Layer — EfficientNet-B4 + FFT MLP per-frame inference
│   ├── Dockerfile             # python:3.11-slim (Python 3.14 incompatible with facenet-pytorch)
│   ├── main.py
│   ├── modeling.py            # FftMlp class: 64-dim radial FFT bins → fake probability (Phase 2.5)
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
│       ├── App.tsx            # WebSocket connection, JWT auth, reconnection logic, theme toggle
│       ├── index.css          # Softened dark palette + html.light mode
│       ├── main.tsx
│       ├── store.ts           # Zustand state: frames, alerts, JWT, verdictCounts, cross-panel linking, stream selector, window progress
│       ├── store.test.ts
│       └── components/
│           ├── AlertBanner.tsx    # Consecutive-alert warning banner
│           ├── AuditPanel.tsx     # RAG summary, matched_signature pill, window progress bar
│           ├── FlaggedFrames.tsx  # Flagged frame rows with summary, hover-highlight, click-to-graph
│           └── LiveGraph.tsx      # Recharts score graph, simplified tooltip, stream selector, ReferenceDot
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
| MLflow (via mlflow-proxy) | 5000 | Web UI + tracking API, HTTP basic auth (`MLFLOW_PROXY_USER`/`PASSWORD`) |
| Kafka | 9092 / 29092 | Broker (SASL_SSL) — internal / external listener |
| Ollama | 11434 | LLM inference API (internal only) |
| Frontend | 3000 | React dashboard (plain HTTP; its calls to :8003 are HTTPS/WSS) |
| Jaeger | 16686 | Trace UI |
| MinIO | 9002 (API), 9001 (console) | S3-compatible object storage |
| Redis | 6379 | Internal only, not published to host |

---

## Test Coverage

| Service | Tests | Coverage |
|---|---|---|
| Ingest Gateway | 4 | Kafka publish, FPS downsampling, SASL config, auth |
| Vision Service | 20 | Spatial branch, frequency branch, score formula, alignment failure, payload limits, auth, M13 calibration |
| RAG Agent | 13 | FAISS/Chroma search, verdict tiers (score × severity × tags), summary field, similarity threshold, payload validation, auth, mTLS |
| Aggregation Service | 49 | Full pipeline, Vision 502, RAG timeout, WebSocket JWT/refresh/revoke, RBAC, alert window, drift, temporal audit, MLflow buffer, stream_id validation, payload size guard, rate limiting, SASL/mTLS env vars, MinIO archival, webhooks |
| Temporal Service | 20 | Buffer fill, tensor shape, zero-padding, sparse fallback, full inference, interpolation, schema validation, auth |
| Frontend (Vitest) | 5 | Zustand store: frame buffer cap, sticky alert, dismiss, connection state, temporal status |
| **Total** | **111** | **All 111 pass (Python 3.13.13 + Node 24)** |

> Vision-service tests run natively on the host — the Python 3.14 / facenet-pytorch incompatibility no longer applies (host is Python 3.13.13).

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

---

## Benchmark Results — Speed Layer (Phase 2.5)

Trained on FaceForensics++ c23 compression, Deepfakes manipulation subset. Official 720/140/140 train/val/test split. 204,351 MTCNN-aligned face crops extracted at every 5th frame.

| Model | Test Accuracy | Notes |
|---|---|---|
| EfficientNet-B4 (spatial branch) | **99.39%** | Fine-tuned from ImageNet; 10 epochs, AdamW, cosine LR |
| FFT MLP 64-dim radial bins (frequency branch) | 53.3% | Near-random; FF++ Deepfakes c23 leaves no detectable frequency artefacts |
| Fused (logistic regression) | **99.41%** | AUC **0.9987** |

**Training config:** batch=8, EfficientNet LR=1e-4, MLP LR=1e-3, AMP FP16, RTX 4050 6GB.

**Dataset:** [FaceForensics++](https://github.com/ondyari/FaceForensics) — Rössler et al., ICCV 2019. See [`PLAN/REFERENCES.md`](./PLAN/REFERENCES.md) for full citations.

---

## Future Scope

### FFT Frequency Branch — Multi-Method Training

The FFT MLP trained to only 53% accuracy (near-random) on the Deepfakes subset of FF++. This is expected: Deepfakes at c23 compression are high-quality face-swaps that leave minimal frequency-domain artefacts detectable by radial FFT binning.

The frequency branch is architecturally sound and remains in the pipeline with a near-zero learned fusion weight. It can be made meaningful by:

1. **Training on all four FF++ manipulation types** — Face2Face, FaceSwap, and NeuralTextures generate different blending artefacts that do appear in the frequency domain, particularly at region boundaries. A model trained across all four types would give the FFT branch genuine signal to learn.

2. **Richer frequency features** — Replace 64-dim radial bins with DCT block statistics (used in JPEG compression analysis), gradient magnitude histograms, or patch-level DFT features that capture local inconsistencies rather than global radial averages.

3. **Joint training with contrastive loss** — Train the FFT MLP to explicitly contrast real vs. fake frequency patterns rather than binary classification, which may surface subtler artefacts invisible to a standard BCE objective.
