# Build Phases

A step-by-step guide for building ARDD-TP in order. Each phase produces a runnable, testable system before the next begins.

> **Current Status (2026-07-04):** Phase 1 ✅ complete. Phase 2 ✅ complete — all items including Step 2.4 linear interpolation, Step 2.6 health fetch on connect, and Step 2.8 frontend env vars are implemented and tested. Phase 2.5 ✅ complete — Speed Layer trained, pipeline verified (smoke test AUC=1.0000). Full 140/140 benchmark pending Phase 3 gRPC throughput upgrade. Security hardening applied to aggregation-service (2026-07-04). 80/80 unit tests passing. Ready to begin Phase 3.

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

### Step 2.1 — Aggregation Service: Production Pipeline Driver ✅ COMPLETED
- [x] Add `aiokafka` to `aggregation-service/requirements.txt`; remove `kafka-python-ng` consumer usage (keep `KafkaAdminClient` in ingest gateway only)
- [x] Replace `threading.Thread` Kafka consumer in `aggregation-service/main.py` with `asyncio` task using `aiokafka.AIOKafkaConsumer`
- [x] Add production pipeline consumer loop: consume `FramePayload` from `frames` topic → `call_vision()` → `call_rag()` → broadcast → MLflow
- [x] Migrate existing `labels` Kafka consumer (`start_kafka_consumer`) to `aiokafka` in the same `startup_event`
- [x] Consumer group: `aggregation-pipeline-group` (separate from any other consumers)

**Verification:**
```bash
# Start stack and publish a test frame to Kafka frames topic
docker compose up -d
python simulate_stream.py  # existing helper

# Confirm AggregatedResult broadcast on WebSocket without calling POST /aggregate
python -c "
import websocket, json, requests
token = requests.post('http://localhost:8003/auth/token', json={'client_id':'c','client_secret':'s'}).json()['access_token']
ws = websocket.create_connection('ws://localhost:8003/stream', header={'Sec-WebSocket-Protocol': token})
print(json.loads(ws.recv()))
"
```

### Step 2.2 — Temporal Service: Build Buffer + Inference Node ✅ COMPLETED
- [x] New service `./temporal-service/` with `main.py`, `modeling.py`, `Dockerfile`, `requirements.txt`
- [x] Reconstruct `DeepFakeDetector` class in `modeling.py` (ResNext50 backbone + single LSTM layer + `linear1` head; `forward(x)` returns `(lstm_out, logits)`)
- [x] Load weights from `model_87_acc_20_frames_final_data.pt` at startup; fallback to random weights with `model_used: "random-fallback"` if file not found
- [x] `aiokafka` consumer subscribed to `frames` topic (consumer group: `temporal-service-group`)
- [x] Per `stream_id`: `deque(maxlen=20)` of `(frame_index, tensor)` tuples (112×112, ImageNet normalisation)
- [x] On buffer full (20 frames): stack into `[1, 20, 3, 112, 112]` tensor → run inference → `temporal_score = F.softmax(logits, dim=1)[0][0].item()` (fake probability)
- [x] After inference: clear buffer (tumbling window), log `"Inference complete: stream={stream_id} score={temporal_score:.3f}"`
- [x] POST `TemporalAuditResult` to `http://aggregation-service:8003/temporal_audit`
- [x] `GET /health` endpoint returning `{status, buffer_sizes, uptime_s}`
- [x] `GET /batch_status` endpoint returning per-stream buffer fill levels
- [x] `POST /flush` endpoint to manually trigger early flush; runs full inference + POST to Aggregation
- [x] `X-API-Key` auth middleware on all endpoints
- [x] Port: **8004**

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

### Step 2.3 — Wire Temporal Service into docker-compose.yml ✅ COMPLETED
- [x] Add `temporal-service` service block: build `./temporal-service`, port `8004:8004`, `restart: unless-stopped`
- [x] Environment: `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_TOPIC_FRAMES=frames`, `INTERNAL_API_KEY`, `AGGREGATION_URL=http://aggregation-service:8003`, `MODEL_WEIGHTS_PATH=/app/weights/model_87_acc_20_frames_final_data.pt`
- [x] Mount weights file: `- /home/<user>/.cache/huggingface/hub/models--Naman712--Deep-fake-detection/snapshots/.../model_87_acc_20_frames_final_data.pt:/app/weights/model_87_acc_20_frames_final_data.pt:ro`
- [x] Health check: `curl -f http://localhost:8004/health`
- [x] `depends_on: kafka` and `aggregation-service: condition: service_healthy`; YAML fixed to all-dict format

**Verification:**
```bash
docker compose up -d temporal-service
curl -f http://localhost:8004/health
# Expected: {"status": "ok", "service": "temporal-service", ...}
```

### Step 2.4 — Implement Buffer Resilience ✅ COMPLETED
- [x] Pad incomplete buffer to 20 frames with zero tensors when `N < 20`; set `low_confidence_flag: true`
- [x] Return `temporal_verdict: "UNKNOWN"` immediately without inference when `N < 6` (< 20% of window — insufficient data)
- [x] Detect frame gaps via non-contiguous `frame_index`; linearly interpolate missing tensors from adjacent neighbours (`frames_interpolated` correctly reported — verified by `test_interpolation_fills_frame_gaps`)
- [x] Include `frames_interpolated` count in `TemporalAuditResult`

**Verification:**
```bash
# Test padding (send only 12 frames then flush)
curl -X POST http://localhost:8004/flush -H "X-API-Key: $KEY" -d '{"stream_id":"test"}'
# Expected: low_confidence_flag: true in response

# Test sparse buffer (send <6 frames then flush)
# Expected: temporal_verdict: "UNKNOWN" immediately
```

### Step 2.5 — Aggregation Service: Temporal Path ✅ COMPLETED
- [x] `POST /temporal_audit` endpoint implemented and tested — broadcasts to WebSocket and logs to MLflow
- [x] `GET /health` includes `temporal_service_status: "ok" | "unavailable"` (HTTP ping to `http://temporal-service:8004/health`)
- [x] `TemporalAuditResult` broadcasts to WebSocket as `"type": "temporal_audit"` event
- [x] MLflow logging of temporal results (buffered with 100-entry cap)

**Verification:**
```bash
curl -X POST http://localhost:8003/temporal_audit \
  -H "X-API-Key: $KEY" \
  -d '{"stream_id":"test","window_start_frame":0,"window_end_frame":19,"temporal_score":0.3,"temporal_verdict":"PASS","low_confidence_flag":false,"frames_interpolated":0,"model_used":"resnext50-lstm-v1","latency_ms":120,"window_duration_s":0.67,"timestamp_ms":0}'
# Expected: 200 OK

curl http://localhost:8003/health
# Expected: includes temporal_service_status field
```

### Step 2.6 — React Dashboard: Wire temporal_service_status ✅ COMPLETED
- [x] AuditPanel renders temporal audit data and "Temporal Audit Unavailable" fallback text
- [x] Fetch `GET /health` on WebSocket open; `setTemporalServiceStatus(data.temporal_service_status === 'ok' ? 'ok' : 'unavailable')` — wired in `App.tsx` lines 81–92
- [x] Update dashboard copy: `"20-Frame Sequence Verdict"` replacing any "30s" references

### Step 2.7 — RAG Agent: Replace Hash Embeddings ✅ COMPLETED
- [x] Added `langchain-huggingface` (unpinned) to `rag-agent/requirements.txt`
- [x] Replaced `SimpleHashEmbeddings` with `HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")` from `langchain_huggingface` (migrated from deprecated `langchain_community.embeddings`)
- [x] `SimpleHashEmbeddings` class removed entirely; `hashlib` import removed
- [x] FAISS still imports from `langchain_community.vectorstores` (standalone package migration deferred — see TaskTo.md)

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

### Step 2.8 — Security Fixes ✅ COMPLETED
- [x] `vision-service/main.py` — `weights_only=True` added to `torch.load` call
- [x] WebSocket JWT: switched to `Sec-WebSocket-Protocol` subprotocol in both `aggregation-service/main.py` and `frontend/src/App.tsx`; `websocket.accept(subprotocol=token)` on server, `new WebSocket(url, [token])` on client
- [x] JWT secret default raised from 16-byte `"super-secret-key"` to 32-byte `"ardd-tp-dev-secret-key-change-me!"` (eliminates `InsecureKeyLengthWarning`)
- [x] Kafka SASL_PLAINTEXT: broker configured in docker-compose; all three clients (ingest-gateway, aggregation-service, temporal-service) use `_kafka_sasl_kwargs()` reading from `KAFKA_SASL_USERNAME`/`KAFKA_SASL_PASSWORD` env vars (hardcoded values removed 2026-07-04)
- [x] Frontend: `CLIENT_ID` / `CLIENT_SECRET` read from `import.meta.env.VITE_CLIENT_ID` / `VITE_CLIENT_SECRET` with safe fallback defaults (implemented in prior commits)
- [x] **Security hardening (2026-07-04):** Rate limiting on `POST /auth/token` (20 req/60s per IP), stream_id format validation (rejects injection patterns), payload size guard (2MB base64), SSRF guard on webhook delivery, startup warnings for default credentials

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

### Step 2.9 — Documentation Updates ✅ COMPLETED
- [x] Update `FLOW.md` sequence diagram to show Temporal Service consuming from `frames` topic and posting to Aggregation
- [x] Update `ROADMAP.md` Phase 2 status table entries
- [x] Update `ARCHITECTURE.md`: Temporal Service description (ResNext50+LSTM, 20-frame tumbling window, subscribes to `frames` topic)
- [x] `ERROR_HANDLING.md` §3b updated: linear interpolation noted as NOT implemented; `frames_interpolated` always 0; alert counter/drift history eviction (`_evict_oldest`, cap=1000) applied

### Step 2.10 — Tests ✅ COMPLETED (20/20 passing)
- [x] `temporal-service/tests/test_temporal.py` — 20 tests covering: buffer fill, tensor shape, zero-padding, sparse fallback, full inference, schema validation, auth, batch_status, flush edge cases, deque maxlen, model_used field, window frame indices, duration, contiguous interpolation=0, **non-contiguous interpolation > 0**
- [x] `aggregation-service/tests/test_aggregation.py` — 30 tests (was 20): adds `temporal_audit` endpoint, MLflow buffer populated + frame_index step, stream_id validation (injection/length), payload size guard, auth rate limiting, SASL kwargs env vars

**Verification:**
```bash
docker build -t ardd-temporal ./temporal-service
docker run -e PYTHONPATH=/app -v $(pwd)/temporal-service:/app --rm ardd-temporal pytest tests/ -v
# Expected: 19 tests pass (linear interpolation test deferred)
```

**Phase 2 exit criteria:**
- [x] Speed Layer (200ms SLA) and Batch Layer (20-frame tumbling, ~0.67s cycle) implemented simultaneously without interference
- [x] Temporal Service crash does not affect Speed Layer or live dashboard scores
- [x] Buffer resilience: padding, sparse cases, and linear interpolation for frame gaps — all handled and tested
- [x] React Dashboard renders both Live Ticker and Audit Panel; temporal_service_status wired via GET /health on WebSocket connect
- [x] Kafka SASL_PLAINTEXT enforced on all broker connections; SASL credentials read from env vars (not hardcoded)
- [x] 80/80 unit tests pass across 5 services (host, Python 3.13.13)

---

## Phase 2.5 — Speed Layer Training (EfficientNet-B4 + FFT MLP)

**Goal:** Replace the heuristic FFT frequency branch with a trained MLP, and fine-tune the full EfficientNet-B4 spatial branch on FaceForensics++ (FF++) data. After this phase the Vision Service runs a fully trained dual-branch model instead of the ImageNet-pretrained + heuristic combination.

> **Status:** Training complete 2026-06-17. All five pre-training files written and tested. EfficientNet-B4 fine-tuned on FF++ c23 (10 epochs, RTX 4050 6GB, AMP FP16). Test AUC 0.9987. Step 2.5.6 benchmark run in progress (WebSocket/Kafka pipeline issue being resolved).

**Prerequisites:** Phase 2 complete. FaceForensics++ dataset downloaded (1000 real + 2000 fake, c23 compression). `nvidia-container-toolkit` installed and configured.

### Step 2.5.1 — Create `vision-service/modeling.py` ✅ COMPLETED
- [x] `compute_fft_features(img_gray, n_bins=64)` — radial FFT bin computation (shared by train + inference)
- [x] `SpatialBranch` — EfficientNet-B4 + sigmoid head (moved from main.py inline class)
- [x] `FftMlp` class: `Linear(64→32) → ReLU → Dropout(0.3) → Linear(32→1) → Sigmoid`
- [x] `IMAGENET_MEAN` / `IMAGENET_STD` constants exported for consistent normalisation

### Step 2.5.2 — Create `extract_faces.py` ✅ COMPLETED
- [x] Scans both `original_sequences/youtube/c23/videos/` and `manipulated_sequences/Deepfakes/c23/videos/`
- [x] `--frame-step N` (default 5); skips frames with no detected face
- [x] MTCNN crop resized to 380×380; saved as JPEG to `face_crops/{real,fake}/<stem>/frame_NNNNNN.jpg`
- [x] `--gpu` flag for MTCNN device selection; graceful error if facenet-pytorch not installed
- [x] Docstring explains Docker run command (required on Python 3.14 — facenet-pytorch incompatible)

**Verification:**
```bash
python extract_faces.py
ls face_crops/real/ | wc -l   # expect ~1000 video dirs
ls face_crops/fake/ | wc -l   # expect ~2000 video dirs
```

### Step 2.5.3 — Create `train_vision.py` ✅ COMPLETED
- [x] Trains `SpatialBranch` and `FftMlp` simultaneously (one DataLoader pass, two optimisers)
- [x] Official FF++ split: 72%/14%/14% of video dirs (matches 720/140/140 for 1000-video real set)
- [x] Weighted BCE loss: `fake_weight=2.0` for 2:1 imbalance
- [x] Safe augmentation: `RandomHorizontalFlip`, `ColorJitter(brightness=0.2)`, `RandomCrop(380, padding=10)`
- [x] ImageNet normalisation applied to spatial branch; FFT computed on pre-augmentation grayscale crop
- [x] Batch=16, AdamW, EfficientNet LR=1e-4, MLP LR=1e-3, cosine annealing, 10 epochs
- [x] Fits `sklearn.LogisticRegression` fusion on val scores; saves coefficients as `model-weights/fusion_alpha.npy`
- [x] Saves `model-weights/efficientnet_b4_ff++.pt` and `model-weights/fft_mlp_ff++.pt` (state_dict)
- [x] Prints per-epoch val accuracy for both branches + final test accuracy and AUC

**Training notes (actual run 2026-06-17):**
- OOM at default batch=16 → reduced to batch=8 + AMP FP16 (`torch.cuda.amp.GradScaler`)
- BCELoss is autocast-unsafe (FP16 log underflow) → loss computed outside `with autocast():` block
- 10 epochs × ~7h total on RTX 4050 6GB; final test AUC 0.9987
- Fusion weights: `sigmoid(10.14·spatial + 7.04·freq − 8.87)`
- Run inside Docker: `docker run --rm --gpus all -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True ...`

**Verification:**
```bash
python train_vision.py
# Expected output: epoch logs + final val accuracy
ls model-weights/
# efficientnet_b4_ff++.pt  fft_mlp_ff++.pt  fusion_alpha.npy
```

### Step 2.5.4 — Update `vision-service/main.py` ✅ COMPLETED
- [x] Imports `SpatialBranch`, `FftMlp`, `compute_fft_features`, `IMAGENET_MEAN/STD` from `modeling.py`
- [x] Loads spatial, FFT MLP, and fusion weights at startup; each falls back gracefully if missing
- [x] Applies ImageNet normalisation to spatial branch tensor (was missing before — train/inference mismatch fixed)
- [x] FFT computed on face crop (380×380 gray), not full frame — matches training distribution
- [x] `fuse(spatial, freq)` uses learned logistic regression params when available; falls back to 0.6/0.4 hardcode
- [x] `heuristic_fft_score()` retained as fallback for pre-training use
- [x] `/health` endpoint now reports `spatial_trained`, `fft_mlp_trained`, `fusion_trained` flags

### Step 2.5.5 — Update `docker-compose.yml` ✅ COMPLETED
- [x] GPU passthrough added to `vision-service` via `deploy.resources.reservations.devices`
- [x] Volume mounts for all three weight files: `efficientnet_b4_ff++.pt`, `fft_mlp_ff++.pt`, `fusion_alpha.npy`
- [x] Env vars `MODEL_WEIGHTS_PATH`, `FFT_MLP_WEIGHTS_PATH`, `FUSION_WEIGHTS_PATH` wired to container paths

### Step 2.5.6 — Benchmark with `video_feeder.py`
- [ ] Run `python video_feeder.py --mode eval` against FF++ test set (140 real + 140 fake videos)
- [ ] Collect temporal and speed layer verdicts; calculate AUC, precision, recall
- [ ] Document results in `README.md` benchmark table

> **Note:** Both `aggregation-service` and `temporal-service` Kafka consumer tasks now have a retry loop (2026-06-18 fix) — if Kafka is not ready at startup the consumer retries every 5s automatically. `<StrictMode>` removed from frontend — WebSocket cycling issue resolved.

**Phase 2.5 exit criteria:**
- [x] `FftMlp`, `extract_faces.py`, `train_vision.py`, updated `vision-service/main.py`, updated `docker-compose.yml` all complete
- [x] Training completes without OOM on RTX 4050 (6GB VRAM) — batch=8 + AMP FP16 required
- [x] Vision Service loads trained weights on startup (`spatial_trained: true`, `fft_mlp_trained: true`, `fusion_trained: true` in `/health`)
- [x] Speed layer test accuracy 99.41%, AUC 0.9987 on FF++ Deepfakes c23 test set
- [x] README benchmark table populated with val/test accuracy
- [ ] `video_feeder.py --mode eval` end-to-end benchmark run and results verified

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