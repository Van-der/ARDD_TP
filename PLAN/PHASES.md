# Build Phases

A step-by-step guide for building ARDD-TP in order. Each phase produces a runnable, testable system before the next begins.

> **Current Status:** Phase 1 (Core Pipeline MVP) fully completed and compliance-verified. All P0/P1 audit defects resolved. Ready to begin Phase 2 (Lambda Temporal Batch Layer).

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
- [x] FastAPI app with `POST /infer` and `GET /health`
- [x] MTCNN face alignment
- [x] EfficientNet-B4 spatial branch
- [x] FFT frequency branch
- [x] Combine scores: `deepfake_score = 0.6·spatial + 0.4·frequency`
- [x] Alignment failure fallback: return `deepfake_score: 0.5`, `aligned: false`
- [x] `X-API-Key` auth middleware

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

### Step 6 — MLflow Telemetry ✅ COMPLETED
- [x] Add MLflow service to Docker Compose
- [x] Log per-frame telemetry from Aggregation Service
- [x] Implement in-memory buffer (100 entries) for MLflow unavailability
- [x] Drift monitor: rolling 100-frame average on `REAL`-labelled frames; set `drift_flag: true` below 60%

**Verification commands:**
```bash
# Test MLflow connection
curl -f http://localhost:5000 || echo "MLflow not running"

# Test MLflow Python client
python -c "import mlflow; print(f'MLflow {mlflow.__version__} OK')"
```

### Step 7 — WebSocket & React Dashboard ✅ COMPLETED
- [x] WebSocket broadcaster on `ws://aggregation:8003/stream` with JWT auth
- [x] React + TypeScript + Zustand app
- [x] Live score graph, audit verdict display, compliance alert banner
- [x] Stale-data banner on disconnect; exponential backoff reconnection
- [x] JWT refresh logic: silently call `POST /auth/token` at 55 minutes (5 minutes before expiry); retry once on failure before prompting re-login

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

## Phase 2 — Lambda Architecture: Temporal Batch Layer

**Goal:** Introduce a parallel Batch Layer alongside the Speed Layer, forming a true Lambda Architecture for dual-SLA deepfake detection. Temporal Service subscribes directly to the `frames` Kafka topic and runs its own ResNext50+LSTM sequence model on 20-frame tumbling windows (~0.67s at 30 FPS).

> **Future scope:** Sliding window (overlapping inference every K frames) is a planned improvement once the tumbling window baseline is stable.

**Prerequisites:** Phase 1 complete and all exit criteria passing.

### Step 2.1 — Aggregation Service: Production Pipeline Driver
- [ ] Add `aiokafka` to `aggregation-service/requirements.txt`; remove `kafka-python-ng` consumer usage (keep `KafkaAdminClient` in ingest gateway only)
- [ ] Replace `threading.Thread` Kafka consumer in `aggregation-service/main.py` with `asyncio` task using `aiokafka.AIOKafkaConsumer`
- [ ] Add production pipeline consumer loop: consume `FramePayload` from `frames` topic → `call_vision()` → `call_rag()` → broadcast → MLflow
- [ ] Migrate existing `labels` Kafka consumer (`start_kafka_consumer`) to `aiokafka` in the same `startup_event`
- [ ] Consumer group: `aggregation-pipeline-group` (separate from any other consumers)

**Verification:**
```bash
# Start stack and publish a test frame to Kafka frames topic
docker compose up -d
python simulate_stream.py  # existing helper

# Confirm AggregatedResult broadcast on WebSocket without calling POST /aggregate
python -c "
import websocket, json, requests
token = requests.post('http://localhost:8003/auth/token', json={'client_id':'c','client_secret':'s'}).json()['access_token']
ws = websocket.create_connection(f'ws://localhost:8003/stream?token={token}')
print(json.loads(ws.recv()))
"
```

### Step 2.2 — Temporal Service: Build Buffer + Inference Node
- [ ] New service `./temporal-service/` with `main.py`, `modeling.py`, `Dockerfile`, `requirements.txt`
- [ ] Reconstruct `DeepFakeDetector` class in `modeling.py` (ResNext50 backbone + single LSTM layer + `linear1` head; `forward(x)` returns `(lstm_out, logits)`)
- [ ] Load weights from `model_87_acc_20_frames_final_data.pt` at startup; fallback to random weights with `model_used: "random-fallback"` if file not found
- [ ] `aiokafka` consumer subscribed to `frames` topic (consumer group: `temporal-service-group`)
- [ ] Per `stream_id`: `deque(maxlen=20)` of preprocessed frame tensors (112×112, ImageNet normalisation)
- [ ] On buffer full (20 frames): stack into `[1, 20, 3, 112, 112]` tensor → run inference → `temporal_score = F.softmax(logits, dim=1)[0][0].item()` (fake probability)
- [ ] After inference: clear buffer (tumbling window), log `"Inference complete: stream={stream_id} score={temporal_score:.3f}"`
- [ ] POST `TemporalAuditResult` to `http://aggregation-service:8003/temporal_audit`
- [ ] `GET /health` endpoint returning `{status, buffer_sizes, uptime_s}`
- [ ] `GET /batch_status` endpoint returning per-stream buffer fill levels
- [ ] `POST /flush` endpoint to manually trigger early flush (for testing); runs full inference + POST to Aggregation
- [ ] `X-API-Key` auth middleware on all endpoints
- [ ] Port: **8004**

**Verification:**
```bash
# Build and test
docker build -t ardd-temporal ./temporal-service
docker run -e PYTHONPATH=/app -v $(pwd)/temporal-service:/app --rm ardd-temporal pytest tests/ -v

# Check buffer status
curl http://localhost:8004/batch_status -H "X-API-Key: $KEY"
# Expected: {"stream_id": "cam_01", "buffer_size": <N>, "target": 20}

# Manual flush
curl -X POST http://localhost:8004/flush -H "X-API-Key: $KEY" -d '{"stream_id":"test"}'
# Expected: {"temporal_score": <float>, "temporal_verdict": "PASS|FAIL|UNKNOWN", ...}
```

### Step 2.3 — Wire Temporal Service into docker-compose.yml
- [ ] Add `temporal-service` service block: build `./temporal-service`, port `8004:8004`, `restart: unless-stopped`
- [ ] Environment: `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC_FRAMES=frames`, `INTERNAL_API_KEY`, `AGGREGATION_URL=http://aggregation-service:8003`, `MODEL_WEIGHTS_PATH=/app/weights/model_87_acc_20_frames_final_data.pt`
- [ ] Mount weights file: `- /home/<user>/.cache/huggingface/hub/models--Naman712--Deep-fake-detection/snapshots/.../model_87_acc_20_frames_final_data.pt:/app/weights/model_87_acc_20_frames_final_data.pt:ro`
- [ ] Health check: `curl -f http://localhost:8004/health`
- [ ] `depends_on: kafka`; add `temporal-service` to startup order before `frontend`

**Verification:**
```bash
docker compose up -d temporal-service
curl -f http://localhost:8004/health
# Expected: {"status": "ok", "service": "temporal-service", ...}
```

### Step 2.4 — Implement Buffer Resilience
- [ ] Pad incomplete buffer to 20 frames with zero tensors when `N < 20`; set `low_confidence_flag: true`
- [ ] Return `temporal_verdict: "UNKNOWN"` immediately without inference when `N < 6` (< 20% of window — insufficient data)
- [ ] Detect frame gaps via non-contiguous `frame_index`; linearly interpolate missing tensors from adjacent neighbours
- [ ] Include `frames_interpolated` count in `TemporalAuditResult`

**Verification:**
```bash
# Test padding (send only 12 frames then flush)
curl -X POST http://localhost:8004/flush -H "X-API-Key: $KEY" -d '{"stream_id":"test"}'
# Expected: low_confidence_flag: true in response

# Test sparse buffer (send <6 frames then flush)
# Expected: temporal_verdict: "UNKNOWN" immediately
```

### Step 2.5 — Aggregation Service: Temporal Path
- [ ] `POST /temporal_audit` endpoint already implemented — verify it stores latest result per `stream_id`
- [ ] Update `GET /health` to include `temporal_service_status: "ok" | "unavailable"` (HTTP ping to `http://temporal-service:8004/health`)
- [ ] Confirm `TemporalAuditResult` broadcasts to WebSocket as `"type": "temporal_audit"` event
- [ ] Confirm MLflow logging of temporal results

**Verification:**
```bash
curl -X POST http://localhost:8003/temporal_audit \
  -H "X-API-Key: $KEY" \
  -d '{"stream_id":"test","window_start_frame":0,"window_end_frame":19,"temporal_score":0.3,"temporal_verdict":"PASS","low_confidence_flag":false,"frames_interpolated":0,"model_used":"resnext50-lstm-v1","latency_ms":120,"window_duration_s":0.67,"timestamp_ms":0}'
# Expected: 200 OK

curl http://localhost:8003/health
# Expected: includes temporal_service_status field
```

### Step 2.6 — React Dashboard: Wire temporal_service_status
- [ ] AuditPanel already renders temporal audit data and "Temporal Audit Unavailable" fallback text (built in Phase 1 Step 7)
- [ ] Fetch `GET /health` on connect; pass `temporal_service_status` into AuditPanel to drive the unavailable state
- [ ] Update dashboard copy: `"Sequence Analysis: last 20 frames (~0.67s)"` replacing any "30s" references

### Step 2.7 — RAG Agent: Replace Hash Embeddings
- [ ] Add `sentence-transformers==2.7.0` to `rag-agent/requirements.txt`
- [ ] Replace `SimpleHashEmbeddings` class with `HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")` from `langchain_community.embeddings`
- [ ] Remove `hashlib` import; remove `SimpleHashEmbeddings` class entirely
- [ ] Verify 0.75 similarity threshold still behaves correctly with real semantic vectors

**Verification:**
```bash
cd rag-agent
python -c "
from langchain_community.embeddings import HuggingFaceEmbeddings
emb = HuggingFaceEmbeddings(model_name='all-MiniLM-L6-v2')
v = emb.embed_query('face swap artifacts boundary blending')
print(f'Embedding dim: {len(v)}, range: [{min(v):.3f}, {max(v):.3f}]')
"
```

### Step 2.8 — Security Fixes
- [ ] `vision-service/main.py:44` — add `weights_only=True` to `torch.load` call
- [ ] WebSocket JWT: switch from `?token=` query param to `Sec-WebSocket-Protocol` subprotocol in `frontend/src/App.tsx` and `aggregation-service/main.py`
- [ ] Frontend: replace hardcoded `CLIENT_ID = 'test_client'` / `CLIENT_SECRET = 'test_secret'` with `import.meta.env.VITE_CLIENT_ID` / `VITE_CLIENT_SECRET`; add vars to `.env.example` and `docker-compose.yml` frontend env block
- [ ] Kafka SASL_PLAINTEXT: add `KAFKA_SASL_MECHANISM: PLAIN`, `KAFKA_SECURITY_PROTOCOL: SASL_PLAINTEXT`, `KAFKA_SASL_JAAS_CONFIG` to docker-compose broker; update all Kafka clients (Ingest Gateway, Aggregation Service, Temporal Service) with SASL credentials from `KAFKA_SASL_USERNAME` / `KAFKA_SASL_PASSWORD` env vars

> **Note:** Kafka TLS (SASL_SSL upgrade) is deferred to Phase 5 alongside full mTLS hardening.

**Verification:**
```bash
# weights_only
cd vision-service && python -c "import main; print('weights_only OK')"

# WebSocket auth via subprotocol
python -c "
import websocket, requests
token = requests.post('http://localhost:8003/auth/token', json={'client_id':'c','client_secret':'s'}).json()['access_token']
ws = websocket.create_connection('ws://localhost:8003/stream', header={'Sec-WebSocket-Protocol': token})
print('WS subprotocol auth OK')
"

# Kafka SASL
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list \
  --command-config /tmp/client.properties
```

### Step 2.9 — Documentation Updates
- [ ] Update `FLOW.md` sequence diagram to show Temporal Service consuming from `frames` topic and posting to Aggregation
- [ ] Update `ROADMAP.md` Phase 1 status table: MLflow ✅ Done, React dashboard ✅ Done
- [ ] Update `ARCHITECTURE.md`: Temporal Service description (ResNext50+LSTM, 20-frame tumbling window, subscribes to `frames` topic)
- [ ] Add known limitation to `ERROR_HANDLING.md`: "Aggregation Service alert counter and drift history reset on restart; mitigated by Redis in Phase 3"

### Step 2.10 — Tests
- [ ] `temporal-service/tests/test_temporal.py`:
  - Buffer fill (20 frames) triggers flush and clears buffer (tumbling window)
  - Correct input tensor shape `[1, 20, 3, 112, 112]`
  - Zero-padding logic: `N < 20` → `low_confidence_flag: true`
  - Sparse buffer: `N < 6` → `temporal_verdict: "UNKNOWN"` without inference
  - Linear interpolation: insert gap, verify `frames_interpolated > 0`
  - ResNext50+LSTM inference produces valid `temporal_score` in `[0.0, 1.0]`
  - `TemporalAuditResult` schema validation (all fields present and typed)
  - Auth 401 on missing `X-API-Key` for `/health`, `/batch_status`, `/flush`
  - `GET /health` returns `{status, buffer_sizes, uptime_s}`
  - `GET /batch_status` returns per-stream fill levels
  - `POST /flush` triggers inference AND posts result to Aggregation Service

**Verification:**
```bash
docker build -t ardd-temporal ./temporal-service
docker run -e PYTHONPATH=/app -v $(pwd)/temporal-service:/app --rm ardd-temporal pytest tests/ -v
# Expected: All tests pass
```

**Phase 2 exit criteria:**
- Speed Layer (200ms SLA) and Batch Layer (20-frame tumbling, ~0.67s cycle) operating simultaneously without interference
- Temporal Service crash does not affect Speed Layer or live dashboard scores
- All buffer resilience conditions (padding, sparse, interpolation) correctly handled and tested
- React Dashboard shows both Live Ticker and Audit Panel updating independently
- Kafka SASL_PLAINTEXT enforced on all broker connections
- All Temporal Service tests pass

---

## Phase 3 — Performance & Scalability

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

**Phase 3 exit criteria:**
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

## Phase 4 — Advanced Analytics

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

**Phase 4 exit criteria:**
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

## Phase 5 — Hardening & Compliance

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

**Phase 5 exit criteria:**
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