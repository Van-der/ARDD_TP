# Build Phases

A step-by-step guide for building ARDD-TP in order. Each phase produces a runnable, testable system before the next begins.

> **Current Status:** Phase 1, Steps 1-5 completed. Ready for Step 6 (MLflow Telemetry).

---

## Phase 1 — Core Pipeline (MVP)

**Goal:** Frames flow from a video source through to the React dashboard end-to-end.

### Step 1 — Infrastructure ✅ COMPLETED
- [x] Write `docker-compose.yml` with Zookeeper, Kafka, and placeholder services
- [x] Configure `ardd_net` bridge network and `restart: unless-stopped` on all services
- [x] Create `.env.example` with all required secrets
- [x] Prepare test dataset: labelled real/fake frame samples for unit tests, integration fixtures, and model accuracy benchmarks (see `TESTING.md §5`)

**Verification commands (already run):**
```bash
# Test infrastructure files exist
python test_infrastructure.py  # ✅ PASSED

# Prepare test dataset
python prepare_test_dataset.py  # ✅ READY

# Verify test dataset created
ls -la test_dataset/  # ✅ EXISTS
```

### Step 2 — Ingest Gateway ✅ COMPLETED
- [x] Decode RTSP/HTTP stream with OpenCV + FFmpeg
- [x] Extract frames and encode as JPEG
- [x] Publish `FramePayload` to Kafka `frames` topic
- [x] Implement FPS downsampling (30 → 5) on lag detection

**Verification commands (already run):**
```bash
# Test ingest gateway standalone
cd ingest-gateway
python -c "import main; print('Import OK')"  # ✅ PASSED

# Check dependencies
pip install -r requirements.txt  # ✅ INSTALLED

# Test with a local test video
python -c "
import cv2
import numpy as np
img = np.zeros((224, 224, 3), dtype=np.uint8)
cv2.imwrite('test_frame.jpg', img)
print('OpenCV test OK')
"  # ✅ PASSED
```

### Step 3 — Vision Service
- [ ] FastAPI app with `POST /infer` and `GET /health`
- [ ] MTCNN face alignment
- [ ] EfficientNet-B4 spatial branch
- [ ] FFT frequency branch
- [ ] Combine scores: `deepfake_score = 0.6·spatial + 0.4·frequency`
- [ ] Alignment failure fallback: return `deepfake_score: 0.5`, `aligned: false`
- [ ] `X-API-Key` auth middleware

**Verification commands:**
```bash
# Test FastAPI app starts
cd vision-service
python -c "from fastapi import FastAPI; app = FastAPI(); print('FastAPI OK')"

# Test MTCNN import (if available)
python -c "try: import mtcnn; print('MTCNN OK') except: print('MTCNN not installed')"

# Test PyTorch
python -c "import torch; print(f'PyTorch {torch.__version__} OK')"
```

### Step 4 — RAG Context Agent ✅ COMPLETED
- [x] FastAPI app with `POST /audit` and `GET /health`
- [x] Load threat signatures into FAISS in-memory vector store
- [x] Semantic search with similarity threshold ≥ 0.75
- [x] LangChain + Ollama/Mistral verdict generation
- [x] `X-API-Key` auth middleware

**Verification commands:**
```bash
# Test LangChain imports
cd rag-agent
python -c "import langchain; print(f'LangChain {langchain.__version__} OK')"

# Test FAISS
python -c "import faiss; print('FAISS OK')"

# Test Ollama connection
curl -f http://ollama:11434/api/version || echo "Ollama not running"
```

### Step 5 — Aggregation Service ✅ COMPLETED
- [x] FastAPI app with `POST /aggregate`, `POST /auth/token`, `GET /health`
- [x] Call Vision → RAG sequentially; enforce 100ms RAG timeout
- [x] Compute `final_score` with RAG boost (`β = 0.15` on `FAIL`)
- [x] Clamp `final_score` to `[0.0, 1.0]`
- [x] Rolling 5-frame alert window; set `alert: true` when `final_score > 0.90`
- [x] Emit `AggregatedResult` to MLflow and WebSocket broadcaster
- [x] JWT issuance (HS256, 1hr expiry) on `POST /auth/token`
- [x] Kafka `labels` topic consumer: receive `GroundTruthLabel` events, join to stored `AggregatedResult` by `(stream_id, frame_index)`, forward labelled frames to drift monitor
- [x] In-memory label buffer: hold up to 500 unmatched labels; drop oldest on overflow; flush matched labels immediately

**Verification commands:**
```bash
# Test JWT generation
cd aggregation-service
python -c "
import jwt, time
secret = 'test'
token = jwt.encode({'exp': time.time() + 3600}, secret, algorithm='HS256')
print(f'JWT generation OK: {token[:20]}...')
"

# Test Kafka consumer
python -c "
from kafka import KafkaConsumer
print('Kafka-python import OK')
"
```

### Step 6 — MLflow Telemetry
- [ ] Add MLflow service to Docker Compose
- [ ] Log per-frame telemetry from Aggregation Service
- [ ] Implement in-memory buffer (100 entries) for MLflow unavailability
- [ ] Drift monitor: rolling 100-frame average on `REAL`-labelled frames; set `drift_flag: true` below 60%

**Verification commands:**
```bash
# Test MLflow connection
curl -f http://localhost:5000 || echo "MLflow not running"

# Test MLflow Python client
python -c "import mlflow; print(f'MLflow {mlflow.__version__} OK')"
```

### Step 7 — WebSocket & React Dashboard
- [ ] WebSocket broadcaster on `ws://aggregation:8003/stream` with JWT auth
- [ ] React + TypeScript + Zustand app
- [ ] Live score graph, audit verdict display, compliance alert banner
- [ ] Stale-data banner on disconnect; exponential backoff reconnection
- [ ] JWT refresh logic: silently call `POST /auth/token` at 55 minutes (5 minutes before expiry); retry once on failure before prompting re-login

**Verification commands:**
```bash
# Test React build
cd frontend
npm install
npm run build

# Test WebSocket connection
python -c "
import websocket
try:
    ws = websocket.WebSocket()
    print('WebSocket client OK')
except:
    print('WebSocket not available')
"
```

**Phase 1 exit criteria:**
- End-to-end latency p95 ≤ 200ms on a single stream at 30 FPS
- All unit and integration tests pass (see `TESTING.md`)
- Locust load test: single stream at 30 FPS sustained for 5 minutes, p95 ≤ 200ms, p99 ≤ 350ms, zero dropped frames
- `docker compose up` starts the full system cleanly

**Exit verification commands:**
```bash
# Start full stack
docker compose up -d

# Wait for services to be healthy
sleep 30

# Test health endpoints
curl -f http://localhost:8001/health  # Vision
curl -f http://localhost:8002/health  # RAG
curl -f http://localhost:8003/health  # Aggregation
curl -f http://localhost:5000         # MLflow

# Run unit tests
pytest

# Run integration tests
pytest tests/integration/

# Run Locust load test
locust -f locustfile.py --headless -u 1 -r 1 -t 5m --host=http://localhost:8003
```

---

## Phase 2 — Performance & Scalability

**Goal:** Handle multiple concurrent streams reliably.

**Prerequisites:** Phase 1 complete and all benchmarks passing.

- [ ] Replace REST (Kafka Consumer ↔ Vision Service) with gRPC
- [ ] Expand Kafka to support ≥ 3 concurrent stream topics
- [ ] Add Vision Service replicas behind a load balancer in Docker Compose
- [ ] Replace in-memory FAISS with persistent ChromaDB
- [ ] Kafka rebalance handling: Vision Service consumers implement cooperative rebalance (`CooperativeStickyAssignor`); pause frame processing during partition reassignment and resume cleanly without duplicate or dropped frames

**Verification commands:**
```bash
# Test gRPC
python -c "import grpc; print(f'gRPC {grpc.__version__} OK')"

# Test multiple Kafka topics
kafka-topics --bootstrap-server localhost:9092 --list

# Test ChromaDB
python -c "import chromadb; print('ChromaDB OK')"

# Test Kafka rebalance
# Add a new Vision Service replica
docker compose up -d --scale vision-service=2

# Monitor consumer group
kafka-consumer-groups --bootstrap-server localhost:9092 --describe --group vision-consumers
```

**Phase 2 exit criteria:**
- 3 concurrent streams at 30 FPS each, p95 latency ≤ 200ms
- Vision Service scales horizontally without dropping frames
- Kafka consumer group rebalance (add/remove replica) completes without frame loss

**Exit verification commands:**
```bash
# Load test with 3 streams
locust -f locustfile_multi.py --headless -u 3 -r 3 -t 5m --host=http://localhost:8003

# Check frame drop rate
grep "dropped_frames" logs/*.log | tail -5

# Test rebalance
docker compose up -d --scale vision-service=3
sleep 10
docker compose up -d --scale vision-service=2
# Verify no frame loss in logs
```

---

## Phase 3 — Advanced Analytics

**Goal:** Temporal analysis and cross-stream threat intelligence.

**Prerequisites:** Phase 2 complete.

- [ ] Migrate Aggregation Service logic to Apache Flink for windowed stream processing
- [ ] Build graph-based threat intelligence DB to link synthetic identities across streams
- [ ] Automated retraining pipeline:
  - Pre-load new model weights into memory before cutover
  - Atomic swap: replace active weights pointer under a read-write lock; in-flight requests complete on old weights
  - Rollback: if the new weights produce a drift flag within the first 500 frames post-swap, automatically revert to the previous checkpoint and alert
- [ ] Post-hoc confidence calibration layer on Vision scores

**Verification commands:**
```bash
# Test Flink
docker exec -it flink-jobmanager ./bin/flink list

# Test graph DB
python -c "import networkx as nx; G = nx.Graph(); print('NetworkX OK')"

# Test model swap
curl -X POST http://localhost:8001/admin/swap_weights -H "X-API-Key: ${INTERNAL_API_KEY}"

# Monitor drift after swap
tail -f logs/aggregation.log | grep -i drift
```

**Phase 3 exit criteria:**
- Drift-triggered retraining completes and new weights are deployed without service restart
- Atomic swap verified: zero inference errors during cutover under load
- Rollback verified: bad weights trigger automatic revert within 500 frames
- Cross-stream identity linking demonstrated on two simultaneous streams

**Exit verification commands:**
```bash
# Trigger retraining
curl -X POST http://localhost:8001/admin/retrain -H "X-API-Key: ${INTERNAL_API_KEY}"

# Monitor retraining job
tail -f logs/retraining.log

# Test atomic swap under load
locust -f locustfile.py --headless -u 1 -r 1 -t 2m --host=http://localhost:8003 &
curl -X POST http://localhost:8001/admin/swap_weights -H "X-API-Key: ${INTERNAL_API_KEY}"
# Check for errors in logs

# Test rollback
# Deploy bad weights, wait for drift, verify rollback
grep -i "rollback" logs/vision.log
```

---

## Phase 4 — Hardening & Compliance

**Goal:** Production-ready audit trail and operational tooling.

**Prerequisites:** Phase 3 complete.

- [ ] Stream segment archival to object storage on `alert: true` (≥5 consecutive frames)
- [ ] Webhook integrations (Slack, PagerDuty, or SIEM) for threat escalation
- [ ] Role-based access control on React dashboard
- [ ] Instrument all service hops with OpenTelemetry traces; validate 200ms SLA per frame
- [ ] Certificate management + mTLS setup:
  - Issue per-service TLS certificates (CA or cert-manager)
  - Enable mTLS on all internal service links (Vision, RAG, Aggregation, MLflow)
  - Automate certificate rotation before expiry; services reload certificates without restart
  - Replace plaintext WebSocket with WSS using the same CA

**Verification commands:**
```bash
# Test object storage
aws s3 ls s3://ardd-segments/ || echo "S3 not configured"

# Test webhook
curl -X POST http://localhost:8003/test_webhook -H "X-API-Key: ${INTERNAL_API_KEY}"

# Test OpenTelemetry
curl -f http://localhost:4318/health

# Test mTLS
openssl s_client -connect vision-service:8001 -CAfile ca.crt -cert client.crt -key client.key

# Test certificate rotation
# Deploy new certs
./rotate_certs.sh
# Verify services pick them up
ps aux | grep -i reload
```

**Phase 4 exit criteria:**
- Flagged stream segments retrievable from object storage
- OpenTelemetry trace shows per-hop latency for every frame
- All internal links verified mTLS with `openssl s_client`
- Certificate rotation tested: rotated cert picked up without service downtime
- All endpoints pass a security review against `SECURITY.md`

**Exit verification commands:**
```bash
# Verify segment archival
aws s3 ls s3://ardd-segments/ | wc -l

# Verify OpenTelemetry traces
curl http://localhost:16686/api/traces?service=vision-service

# Verify mTLS on all links
for service in vision-service:8001 rag-agent:8002 aggregation-service:8003 mlflow:5000; do
  echo "Testing $service..."
  openssl s_client -connect $service -CAfile ca.crt -cert client.crt -key client.key </dev/null 2>/dev/null | grep "Verify return code"
done

# Test certificate rotation
# Set certs to expire in 1 day
./set_cert_expiry.sh 1
# Wait for auto-rotation
sleep 65
# Verify new certs in use
openssl s_client -connect vision-service:8001 </dev/null 2>/dev/null | grep "Not After"

# Security scan
trivy image ardd-tp/vision-service:latest
```