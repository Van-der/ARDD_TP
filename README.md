# ARDD-TP
**Autonomous Real-Time Deepfake Detection & Telemetry Pipeline**

Real-time video stream analysis pipeline with AI-powered deepfake detection, contextual verification, and live telemetry dashboard.

## Stack

| Layer | Technology |
|---|---|
| Ingest Gateway | Python, OpenCV, FFmpeg |
| Message Broker | Apache Kafka |
| Vision Service | PyTorch — EfficientNet-B4 + FFT dual-branch, FastAPI |
| Context Agent | LangChain (RAG, FAISS, Ollama/Mistral) |
| Aggregation Service | Python, FastAPI, WebSockets (JWT) |
| Experiment Tracking | MLflow |
| Frontend | React + TypeScript + Zustand |
| Infrastructure | Docker, Docker Compose |

## How It Works

1. **Ingest Gateway** decodes a live RTSP/HTTP video feed, extracts frames, and publishes them to the Kafka `frames` topic.
2. **Vision Service** consumes each frame, runs MTCNN alignment, and executes the EfficientNet-B4 + FFT dual-branch model.
3. **Aggregation Service** receives the vision result and calls the RAG Context Agent sequentially (100ms timeout budget).
4. **Aggregation Service** merges both results into a canonical `AggregatedResult` payload.
5. Telemetry is logged to MLflow; the aggregated payload is broadcast via WebSocket to the React dashboard.

## Scoring Algorithm

```
# Vision Service
deepfake_score = 0.6 · spatial_score + 0.4 · frequency_score

# Aggregation Service
final_score = clamp(deepfake_score · (1 + 0.15 · rag_boost), 0.0, 1.0)
```

`rag_boost` is applied (`β = 0.15`) only when `audit_verdict == "FAIL"`.  
Alert fires when `final_score > 0.90` for **5 or more consecutive frames** on the same stream.

## Resilience

- **RAG Timeout (100ms):** Falls back to Vision score alone (`audit_verdict: "UNKNOWN"`) to stay within the 200ms end-to-end SLA.
- **Face Alignment Failure:** Bypasses inference, returns neutral score `0.5`, `aligned: false`.
- **MLflow Unavailable:** Buffers up to 100 telemetry entries in memory, flushes on recovery.
- **Throughput Overload:** Dynamically downsamples from 30 FPS to 5 FPS on Kafka lag.
- **WebSocket Disconnect:** Exponential backoff reconnection with stale-data warning banner.

## Security

- Internal REST APIs require `X-API-Key` header on every request.
- WebSocket access requires a JWT bearer token (HS256, 1-hour expiry) from `POST /auth/token`.
- All secrets injected via environment variables — see `.env.example`.

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
# Expected: {"status":"ok","service":"rag-agent","uptime_s":0}

# Aggregation Service (port 8003)
curl http://localhost:8003/health
# Expected: {"status":"ok","service":"aggregation-service","uptime_s":0}

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
# Encode a test JPEG and call /infer
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

# Call /aggregate (triggers Vision + RAG)
python - <<'EOF'
import base64, requests, cv2, numpy as np

img = np.zeros((224, 224, 3), dtype=np.uint8)
_, buf = cv2.imencode('.jpg', img)
b64 = base64.b64encode(buf).decode()

resp = requests.post(
    "http://localhost:8003/aggregate",
    json={"stream_id": "test", "frame_index": 0, "timestamp_ms": 0, "payload": b64},
    headers={"X-API-Key": "your-internal-api-key-here"}
)
print(resp.json())
EOF
```

---

## Running Tests

Each service has a dedicated test suite. Run tests inside the running containers with `docker compose exec`, or directly via Docker for isolated testing.

### Method 1 — Using running containers

```bash
# Vision Service tests (15 tests)
docker compose exec vision-service pytest tests/ -v

# RAG Agent tests (10 tests)
docker compose exec rag-agent pytest tests/ -v

# Aggregation Service tests (14 tests)
docker compose exec aggregation-service pytest tests/ -v

# Run all services in one loop
for svc in vision-service rag-agent aggregation-service; do
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
```

### Test Coverage

| Service | Tests | Coverage |
|---|---|---|
| Vision Service | 15 | Unit: spatial branch, frequency branch, score formula, alignment failure, payload limits. Integration: schema, missing fields, malformed payload, auth. |
| RAG Agent | 10 | Unit: high/low score verdicts. Integration: full schema, all 422 paths, both 401 paths, no-match UNKNOWN. |
| Aggregation Service | 14 | Unit: all 6 TESTING.md §2 cases. Integration: full schema, Vision error 502, WebSocket auth, RAG timeout fallback. |

---

## Service Port Reference

| Service | Internal Port | Host Port | Endpoint |
|---|---|---|---|
| Vision Service | 8001 | 8001 | `POST /infer`, `GET /health` |
| RAG Agent | 8002 | 8002 | `POST /audit`, `GET /health` |
| Aggregation Service | 8003 | 8003 | `POST /aggregate`, `POST /auth/token`, `GET /health`, `ws://.../stream` |
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
# Check env vars match: VISION_URL and RAG_URL (not VISION_SERVICE_URL)
docker compose exec aggregation-service env | grep -E "VISION|RAG"

# Reset everything (destructive — removes all data)
docker compose down -v
rm -rf mlflow-data ollama-data
```

---

## Documentation

| File | Description |
|---|---|
| [`ARCHITECTURE.md`](./PLAN/ARCHITECTURE.md) | Component overview, service discovery, startup order |
| [`FLOW.md`](./PLAN/FLOW.md) | System flow and sequence diagrams |
| [`PRD.md`](./PLAN/PRD.md) | Product requirements |
| [`TRD.md`](./PLAN/TRD.md) | Technical requirements, scoring algorithm, SLA budgets |
| [`API_SPEC.md`](./PLAN/API_SPEC.md) | All service endpoints, auth contracts, Kafka topics |
| [`SCHEMA.md`](./PLAN/SCHEMA.md) | Message, event, and threat signature schemas |
| [`ERROR_HANDLING.md`](./PLAN/ERROR_HANDLING.md) | All failure scenarios and fallback behaviors |
| [`SECURITY.md`](./PLAN/SECURITY.md) | Security spec, threat model, secrets management |
| [`TESTING.md`](./PLAN/TESTING.md) | Testing strategy, accuracy and performance benchmarks |
| [`PHASES.md`](./PLAN/PHASES.md) | Step-by-step build order with verification commands |
| [`ROADMAP.md`](./PLAN/ROADMAP.md) | Implementation phases and future state |
| [`Documentation.md`](./Documentation.md) | Development journal — session-by-session progress log |
