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
│                           ▼                                                 │
│                 ┌───────────────────┐                                       │
│                 │  React Dashboard  │                                       │
│                 │  ┌─────────────┐  │                                       │
│                 │  │ Live Ticker │  │  ← frame-by-frame scores              │
│                 │  └─────────────┘  │                                       │
│                 │  ┌─────────────┐  │                                       │
│                 │  │ Audit Panel │  │  ← temporal report (~0.67s)           │
│                 │  └─────────────┘  │                                       │
│                 └───────────────────┘                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Stack

| Layer | Technology |
|---|---|
| Ingest Gateway | Python, OpenCV, FFmpeg |
| Message Broker | Apache Kafka |
| **Speed Layer** — Vision Service | PyTorch — EfficientNet-B4 + FFT, FastAPI |
| **Batch Layer** — Temporal Service | PyTorch — ResNext50+LSTM (`Naman712/Deep-fake-detection`), `aiokafka`, FastAPI |
| Frame Buffer (Batch Layer) | In-memory `deque(maxlen=20)` per stream; Redis in Phase 3 |
| Context Agent | LangChain (RAG, FAISS, `sentence-transformers`, Ollama/Mistral) |
| Aggregation Service | Python, FastAPI, `aiokafka`, WebSockets (JWT) |
| Experiment Tracking | MLflow |
| Frontend | React + TypeScript + Zustand |
| Infrastructure | Docker, Docker Compose |

---

## How It Works — The Lambda Pipeline

Both layers independently consume from the same Kafka `frames` topic.

**Speed Layer — per frame, 200ms SLA:**

1. **Ingest Gateway** decodes a live RTSP/HTTP video feed, extracts frames at up to 30 FPS, and publishes `FramePayload` to the Kafka `frames` topic.
2. **Aggregation Service** (aiokafka consumer) receives each frame and calls **Vision Service** via `POST /infer`.
3. **Vision Service** runs MTCNN face alignment and the EfficientNet-B4 + FFT dual-branch model, returning `deepfake_score`.
4. **Aggregation Service** calls the **RAG Context Agent** with the vision score (100ms budget). Merges both results into an `AggregatedResult` and pushes a live update to the React Dashboard — maintaining the **200ms end-to-end SLA**.

**Batch Layer — every ~0.67s (20 frames at 30 FPS):**

5. **Temporal Service** (separate aiokafka consumer, group `temporal-service-group`) independently receives the same `FramePayload` from the `frames` topic.
6. Each JPEG is decoded to 112×112 RGB and ImageNet-normalised. Frames accumulate in a `deque(maxlen=20)` keyed by `stream_id`.
7. At 20 frames, a `[1, 20, 3, 112, 112]` tensor is built and passed to **ResNext50+LSTM** (`Naman712/Deep-fake-detection`, 87% accuracy). Deque is cleared (tumbling window).
8. `TemporalAuditResult` is POSTed to Aggregation Service and forwarded to the Dashboard's Audit Panel.

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
| Frame gaps in window | Non-contiguous sequence | Linearly interpolate missing frame tensors; log `frames_interpolated` |
| Model weights missing | Startup fallback | Use random-initialised model; set `model_used: "random-fallback"` |

---

## Security

- Internal REST APIs require `X-API-Key` header on every request.
- WebSocket access requires a JWT bearer token (HS256, 1-hour expiry) from `POST /auth/token`, passed via `Sec-WebSocket-Protocol` subprotocol.
- All secrets injected via environment variables — see `.env.example`.
- Kafka uses SASL_PLAINTEXT authentication (Phase 2). Full TLS (SASL_SSL) in Phase 5.

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
# Edit .env — set at minimum: INTERNAL_API_KEY, JWT_SECRET

# 3. Create required volume directories
mkdir -p mlflow-data ollama-data

# 4. Prepare test dataset (for local integration tests)
python prepare_test_dataset.py
```

### Start the Full Stack

```bash
# Start all services in background
docker compose up -d

# Rebuild images after code changes
docker compose up -d --build

# Check container status
docker compose ps

# Tail logs for all services
docker compose logs -f

# Tail logs for a specific service
docker compose logs -f vision-service
docker compose logs -f rag-agent
docker compose logs -f aggregation-service
docker compose logs -f temporal-service
docker compose logs -f mlflow

# Stop all services (keeps volumes)
docker compose down

# Stop and delete all volumes (destructive)
docker compose down -v
```

> **Note:** The first startup will pull Docker images and build the services. Allow 2–3 minutes. Services wait for their dependencies to be healthy before starting.

### Pull the Ollama LLM Model (one-time)

By default, the RAG agent runs in mock mode (`MOCK_LLM=true` in `.env`). To use the real Mistral LLM:

```bash
# Pull the model into the ollama container
docker exec ollama ollama pull mistral

# Then set MOCK_LLM=false in .env and restart:
docker compose up -d rag-agent
```

---

## Health Endpoint Verification

After `docker compose up -d`, wait ~30 seconds for services to initialize:

```bash
# Vision Service (port 8001)
curl http://localhost:8001/health
# Expected: {"status":"ok","service":"vision-service","uptime_s":<N>}

# RAG Agent (port 8002)
curl http://localhost:8002/health
# Expected: {"status":"ok","service":"rag-agent","uptime_s":<N>}

# Aggregation Service (port 8003)
curl http://localhost:8003/health
# Expected: {"status":"ok","service":"aggregation-service","uptime_s":<N>}

# Temporal Service (port 8004)
curl http://localhost:8004/health
# Expected: {"status":"ok","service":"temporal-service","buffer_size":<N>,"uptime_s":<N>}

# MLflow UI (port 5000)
curl http://localhost:5000
# Or open http://localhost:5000 in your browser

# Kafka — list topics
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list

# Automated infrastructure check
python test_infrastructure.py
```

### Test the Infer Endpoint (Vision Service)

```bash
python - <<'EOF'
import base64, requests, cv2, numpy as np

img = np.zeros((224, 224, 3), dtype=np.uint8)
_, buf = cv2.imencode('.jpg', img)
b64 = base64.b64encode(buf).decode()

resp = requests.post(
    "http://localhost:8001/infer",
    json={"stream_id": "test", "frame_index": 0, "timestamp_ms": 0, "payload": b64},
    headers={"X-API-Key": "your-internal-api-key-here"}
)
print(resp.json())
EOF
# Expected: {"stream_id":"test","frame_index":0,"deepfake_score":0.5,"aligned":false,"latency_ms":<N>}
```

### Test the Full Pipeline (Aggregation Service)

```bash
# Get a JWT token first
TOKEN=$(curl -s -X POST http://localhost:8003/auth/token \
  -H "Content-Type: application/json" \
  -d '{"client_id":"test","client_secret":"test"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Token: $TOKEN"

# Publish a test frame via the Ingest Gateway (triggers full Speed Layer pipeline)
python simulate_stream.py
```

---

## Running Tests

Each service has a dedicated test suite. Run tests inside the running containers with `docker compose exec`, or directly via Docker for isolated testing.

### Method 1 — Using running containers

```bash
# Vision Service tests
docker compose exec vision-service pytest tests/ -v

# RAG Agent tests
docker compose exec rag-agent pytest tests/ -v

# Aggregation Service tests
docker compose exec aggregation-service pytest tests/ -v

# Temporal Service tests (Phase 2)
docker compose exec temporal-service pytest tests/ -v

# Run all services in one loop
for svc in vision-service rag-agent aggregation-service temporal-service; do
  echo "=== Testing $svc ==="
  docker compose exec $svc pytest tests/ -v
done
```

### Method 2 — Standalone Docker (no running stack required)

```bash
# Build and test Vision Service
docker build -t ardd-vision ./vision-service
docker run -e PYTHONPATH=/app -v $(pwd)/vision-service:/app --rm ardd-vision pytest tests/ -v

# Build and test RAG Agent
docker build -t ardd-rag ./rag-agent
docker run -e PYTHONPATH=/app -v $(pwd)/rag-agent:/app --rm ardd-rag pytest tests/ -v

# Build and test Aggregation Service
docker build -t ardd-aggregation ./aggregation-service
docker run -e PYTHONPATH=/app -v $(pwd)/aggregation-service:/app --rm ardd-aggregation pytest tests/ -v

# Build and test Temporal Service (Phase 2)
docker build -t ardd-temporal ./temporal-service
docker run -e PYTHONPATH=/app -v $(pwd)/temporal-service:/app --rm ardd-temporal pytest tests/ -v
```

### Test Coverage

| Service | Tests | Coverage |
|---|---|---|
| Vision Service | 16 | Unit: spatial branch, frequency branch, score formula, alignment failure, payload limits. Integration: schema, missing fields, malformed payload, auth. |
| RAG Agent | 10 | Unit: high/low score verdicts. Integration: full schema, all 422 paths, both 401 paths, no-match UNKNOWN. |
| Aggregation Service | 15 | Unit: all 6 TESTING.md §2 cases. Integration: full schema, Vision error 502, WebSocket auth, RAG timeout fallback. |
| Temporal Service | ⏳ Phase 2 | Buffer fill, tensor shape, padding logic, sparse fallback, ResNext50+LSTM inference, batch audit schema. |

---

## Service Port Reference

| Service | Internal Port | Host Port | Endpoints |
|---|---|---|---|
| Vision Service | 8001 | 8001 | `POST /infer`, `GET /health` |
| RAG Agent | 8002 | 8002 | `POST /audit`, `GET /health` |
| Aggregation Service | 8003 | 8003 | `POST /auth/token`, `GET /health`, `ws://.../stream` |
| Temporal Service | 8004 | 8004 | `GET /health`, `GET /batch_status` |
| MLflow | 5000 | 5000 | Web UI + tracking API |
| Kafka | 9092 | 9092 | Broker |
| Ollama | 11434 | 11434 | LLM inference API |
| Frontend | 3000 | 3000 | React dashboard |

---

## Troubleshooting

```bash
# Service not starting — check logs
docker compose logs <service-name>

# Port already in use
sudo lsof -i :<port>

# Force recreate containers after env changes
docker compose up -d --force-recreate

# Rebuild a single service image
docker compose build vision-service && docker compose up -d vision-service

# Aggregation service can't connect to Vision or RAG
docker compose exec aggregation-service env | grep -E "VISION|RAG"

# Reset everything (destructive — removes all data)
docker compose down -v
rm -rf mlflow-data ollama-data
```

---

## Documentation

| File | Description |
|---|---|
| [`ARCHITECTURE.md`](./PLAN/ARCHITECTURE.md) | Component overview, Lambda Architecture detail, service discovery, startup order |
| [`FLOW.md`](./PLAN/FLOW.md) | Mermaid sequence diagrams — Speed Layer and Batch Layer parallel flows |
| [`PRD.md`](./PLAN/PRD.md) | Product requirements |
| [`TRD.md`](./PLAN/TRD.md) | Technical requirements, scoring algorithms, SLA budgets |
| [`API_SPEC.md`](./PLAN/API_SPEC.md) | All service endpoints, auth contracts, Kafka topics |
| [`SCHEMA.md`](./PLAN/SCHEMA.md) | Message, event, and threat signature schemas |
| [`ERROR_HANDLING.md`](./PLAN/ERROR_HANDLING.md) | All failure scenarios and fallback behaviours |
| [`SECURITY.md`](./PLAN/SECURITY.md) | Security spec, threat model, secrets management |
| [`TESTING.md`](./PLAN/TESTING.md) | Testing strategy, accuracy and performance benchmarks |
| [`PHASES.md`](./PLAN/PHASES.md) | Step-by-step build order with verification commands |
| [`ROADMAP.md`](./PLAN/ROADMAP.md) | Implementation phases and future state |
| [`REFERENCES.md`](./PLAN/REFERENCES.md) | Models, datasets, and library citations |
| [`Documentation.md`](./Documentation.md) | Development journal — session-by-session progress log |
| [`TaskTo.md`](./TaskTo.md) | Phase audit findings and Phase 2 design decisions |
