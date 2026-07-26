# Build Phases

A step-by-step guide for building ARDD-TP in order. Each phase produces a runnable, testable system before the next begins.

> **Current Status (2026-07-24):** Phase 1, Phase 2, and Phase 2.5 are complete. M0-M13 of the Phase 3-5 plan are implemented and verified (mTLS, MinIO, RBAC, Webhooks, OTel, Kafka SASL, etc). Pipeline bugs fixed: `webhook-receiver` missing curl (PL-4), `temporal-service` Redis test isolation (PL-5), `ingest-gateway` gateway_fatal Kafka pollution (PL-6). 111/111 unit tests passing natively inside Docker. Full end-to-end run verified with `simulate_stream.py`. Remaining: M14 (atomic weight swap), M15 (graph-based linking), M16 (windowing formalization).

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
- [x] JWT refresh logic: silently call `POST /auth/refresh` at 800s (100s before the 900s access-token expiry, SEC-4); falls back to a full `POST /auth/token` re-login if the refresh token itself is missing/expired/revoked

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
locust -f locustfile.py --headless -u 1 -r 1 -t 5m --host=https://localhost:8003 --insecure
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

> **Note:** Kafka TLS (SASL_SSL upgrade) was deferred to Phase 5 at the time this section was written — ✅ done, see M10 below.

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

> **Status (2026-07-07):** Training complete 2026-06-17. EfficientNet-B4 fine-tuned on FF++ c23 (10 epochs, RTX 4050 6GB, AMP FP16). Test AUC 0.9987. Kafka pipeline issues resolved (WSL-3, WSL-5). Rule-based summary pipeline and dashboard UI overhaul complete (Steps 2.5.7–2.5.8). Smoke-test benchmark passed (2+2 videos, AUC 1.0000). Full 140/140 benchmark deferred to Phase 3 (requires gRPC throughput upgrade).

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

### Step 2.5.6 — Benchmark with `video_feeder.py` ⚠️ PARTIAL
- [x] Smoke test (2 real + 2 fake videos, 30 frames, 3 FPS): AUC=1.0000, Accuracy=1.0000, Precision=1.0000, Recall=1.0000
- [ ] Full run against FF++ test set (140 real + 140 fake videos) — deferred to Phase 3 (requires gRPC throughput upgrade to sustain 10+ FPS pipeline)
- [ ] Document full results in `README.md` benchmark table

> **Note:** Both `aggregation-service` and `temporal-service` Kafka consumer tasks now have a retry loop (2026-06-18 fix) — if Kafka is not ready at startup the consumer retries every 5s automatically. `<StrictMode>` removed from frontend — WebSocket cycling issue resolved.

### Step 2.5.7 — Rule-Based Summary Pipeline ✅ COMPLETED 2026-07-07
- [x] `rag-agent/main.py`: `summary: str` added to `AuditResult`; `generate_verdict_via_llm()` returns 4-tier rule-based summaries based on deepfake score × matched signature severity × `artefact_tags` (high/moderate/ambiguous/clean)
- [x] `aggregation-service/main.py`: `_latest_temporal: Dict[str, dict]` — per-stream store updated by `temporal_audit()` endpoint; `_fuse_summary()` combines per-frame Speed Layer summary with latest temporal verdict; `summary` added to `AggregatedResult` model; `summary` and `matched_signature` added to WebSocket broadcast event

### Step 2.5.8 — Dashboard UI Overhaul ✅ COMPLETED 2026-07-07
- [x] `store.ts`: `matched_signature?`, `summary?` on `FrameData`; `stream_id` on `TemporalAudit`; cross-panel linking state (`hoveredFrameIndex`, `selectedFlaggedFrame`); stream selector state (`selectedStream`, `activeStreams`); temporal window progress tracking (`temporalWindowProgress`, `streamWindowCounters`)
- [x] `LiveGraph.tsx`: simplified tooltip showing Speed + Temporal scores with verdicts (fusion formula removed); bidirectional graph↔panel linking via `hoveredFrameIndex`; stream selector `<select>` dropdown (shown when >1 active stream); `<ReferenceDot>` for panel-selected frame
- [x] `AuditPanel.tsx`: fused summary text from latest frame; `matched_signature` pill badge; temporal window progress bar (0–20 frames)
- [x] `FlaggedFrames.tsx`: summary line per row; hover-highlight + auto-scroll driven by `hoveredFrameIndex`; click-to-select sets `selectedFlaggedFrame` for graph dot; filter by `selectedStream`
- [x] `index.css`: softened dark palette (F87171/FCD34D/4ADE80/60A5FA/67E8F9); full light mode via `html.light` class; `gap-3` utility added; `html.light body` gradient override
- [x] `App.tsx`: Sun/Moon theme toggle button in header; `App.css` deleted (was Vite boilerplate)

**Phase 2.5 exit criteria:**
- [x] `FftMlp`, `extract_faces.py`, `train_vision.py`, updated `vision-service/main.py`, updated `docker-compose.yml` all complete
- [x] Training completes without OOM on RTX 4050 (6GB VRAM) — batch=8 + AMP FP16 required
- [x] Vision Service loads trained weights on startup (`spatial_trained: true`, `fft_mlp_trained: true`, `fusion_trained: true` in `/health`)
- [x] Speed layer test accuracy 99.41%, AUC 0.9987 on FF++ Deepfakes c23 test set
- [x] README benchmark table populated with val/test accuracy
- [x] Kafka pipeline verified end-to-end; WSL-3 (temporal weights path) and WSL-5 (Kafka backlog) resolved
- [x] Rule-based fused summaries flowing from RAG → Aggregation → WebSocket → Dashboard
- [x] Dashboard UI overhaul complete (tooltip, AuditPanel, FlaggedFrames, light/dark mode, stream selector, window progress)
- [ ] Full 140/140 benchmark run (deferred — see Step 2.5.6)

---

## Phase 3 — Performance & Scalability

**Goal:** Handle multiple concurrent streams reliably.

**Prerequisites:** Phase 1 complete and all benchmarks passing.

> **Execution note (2026-07-11):** this phase's items are being executed in a performance/robustness-first order per `/home/vander/.claude/plans/lets-try-to-finish-playful-barto.md` (M0-M16), not the order listed below. See `PLAN/PROFILING.md` for the profiling pass that determined the gRPC item's outcome.

- [x] **M0/M1: gRPC transport — evaluated, declined.** Profiling (`PLAN/PROFILING.md`) showed REST/serialization overhead is only ~7-10ms (~7% of round trip); the real bottleneck is RAG's real-LLM call (~1s median) blocking the per-frame consumer loop. Fixed instead by decoupling the RAG call per stream (M1) — see `aggregation-service/main.py`'s `_process_frame_locked`/per-stream `asyncio.Lock`.
- [x] **M3: Kafka partitioning.** `frames`/`labels` topics increased to 6 partitions (`scripts/ensure_kafka_partitions.py`, `KAFKA_NUM_PARTITIONS: 6` for fresh deployments); all producers now key by `stream_id` for per-stream ordering.
- [x] **M6: Vision Service horizontal scaling — mechanism demo, DNS-based (not Traefik).** `container_name`/fixed host port removed so `docker compose up -d --scale vision-service=N` works. Traefik was tried first but its bundled Docker client can't negotiate with this environment's Docker Engine (29.4.0, "moby v2" API 1.54 — confirmed via direct `/_ping` testing: explicit old-version requests return HTTP 400, which Traefik's client mishandles by falling back to a hardcoded API 1.24 that the daemon then rejects). Fell back to Compose's embedded DNS (127.0.0.11), which round-robins across a scaled service's replica IPs natively; `call_vision()` already creates a fresh `httpx.AsyncClient` per request (no persistent pooling), so each request re-resolves DNS. **Verified live:** 3 replicas split ~evenly (8/10/8 requests over 60 frames); killing one replica mid-load caused only 2 brief failures (absorbed as normal per-request 502s, M5's circuit breaker never needed to trip) before Compose's DNS excluded the dead replica. As expected for a single physical GPU, this is a distribution/failover mechanism demo — no throughput gain claimed.
- [x] **M7: Replace in-memory FAISS with persistent ChromaDB.** `rag-agent/main.py`'s `init_vector_store()` swapped to `Chroma.from_documents(..., persist_directory=CHROMA_PERSIST_DIR, collection_metadata={"hnsw:space":"l2"})` — `l2` pinned explicitly since Chroma's cosine default would've silently broken the existing `1.0 - (l2_dist/2.0)` conversion and `>=0.75` match threshold. New `chroma_data` named volume persists `/data/chroma`. Kept `langchain==0.1.12`/`langchain-community==0.0.28` (jumping to `langchain-community==0.4.2` pulled in the whole LangChain 1.x ecosystem via `langchain-classic`, forcing `pydantic>=2.7.4` — far bigger blast radius than a vector-store swap warrants); pinned `chromadb==0.4.24` (era-appropriate, prebuilt wheel available for the container's Python 3.11 even though it fails to compile on this host's Python 3.13 venv — host pytest uses `chromadb==1.5.9` instead, verified equivalent behavior). **Real bug found and fixed:** Chroma's metadata store only accepts scalar values (str/int/float/bool) — unlike FAISS's in-memory dict, it rejected the `artefact_tags` list outright. Now stored comma-joined and split back to a list at the read site in `audit()` before reaching `generate_verdict_via_llm()`. Verified end-to-end live (real Chroma + real Ollama, not mocked): `POST /audit` with a high score returned `FAIL`, a real `matched_signature`, and a correct confidence — `chroma.sqlite3` confirmed persisted in the container's volume.
- [x] **M3: Kafka rebalance handling — retargeted.** Vision-service has no Kafka consumer (pure REST) — this item originally mis-targeted it. Cooperative-sticky rebalance (`StickyPartitionAssignor` + `ConsumerRebalanceListener`) implemented instead on the consumers that actually exist: aggregation-service's `aggregation-pipeline-group`/`aggregation-labels-group` and temporal-service's `temporal-service-group`. `container_name:` removed from both services' compose blocks so `--scale` works for rebalance testing.
- [x] **M4: Redis feature buffer** (was in `PLAN/ROADMAP.md`'s Phase 3 table but missing from this checklist). `redis:7-alpine` service added; aggregation-service's `alert_counters`/`drift_history` and temporal-service's `stream_buffers` are now Redis-backed (atomic Lua push-and-claim script for the temporal tumbling window, preventing double-inference races across replicas), falling back to the original in-memory structures if Redis is unreachable — verified live via a 3-stream smoke test with real Redis key writes.
- [x] **M2: Multi-stream simulation harness.** `locustfile_multi.py` created (N Locust users pinned to distinct `stream_id`s, real encoded JPEGs — fixed a pre-existing `locustfile.py` bug where `MOCK_PAYLOAD` was raw zero bytes, causing every request to 502). `video_feeder.py` gained `--mode multistream --streams N --source {real,fake}`, replaying the same local FF++ video under different `stream_id` tags via parallel producer tasks. Explicitly validates partitioning/rebalance/LB-routing mechanics, not real camera diversity — stated in the script docstring and every place these results are cited.
- [x] **M5: Circuit breaker on Vision/RAG calls.** `call_vision()`/`call_rag()` wrapped with `aiobreaker.CircuitBreaker` (not `pybreaker` — its `call_async()` requires an undeclared `tornado` dependency and raises `NameError` without it). Configurable `fail_max`/`*_BREAKER_RESET_S` env vars; an open breaker fails fast instead of waiting out the full httpx timeout. `vision_circuit_state`/`rag_circuit_state` added to `/health`.

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
locust -f locustfile_multi.py --headless -u 3 -r 3 -t 5m --host=https://localhost:8003 --insecure

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

> **Execution note (2026-07-11):** items below are being built as M13-M16 of `/home/vander/.claude/plans/lets-try-to-finish-playful-barto.md`, not in the order listed here.

- [ ] Migrate Aggregation Service logic to Apache Flink for windowed stream processing
- [ ] Build graph-based threat intelligence DB to link synthetic identities across streams
- [ ] Automated retraining pipeline:
  - Pre-load new model weights into memory before cutover
  - Atomic swap: replace active weights pointer under a read-write lock; in-flight requests complete on old weights
  - Rollback: if the new weights produce a drift flag within the first 500 frames post-swap, automatically revert to the previous checkpoint and alert
- [x] **M13: Post-hoc confidence calibration layer.** New `fit_calibration.py` — deliberately does **not** retrain the spatial/FFT branches or refit the fusion logistic regression (a fresh retrain would produce different weights, breaking the goal of calibration tied to the actually-deployed model); instead it loads the exact weights vision-service already runs, does one `@torch.no_grad()` forward pass over the same val split `train_vision.py` already uses (72/14/14, 28,643 val crops), computes fused scores with the existing `fusion_alpha.npy`, saves `calibration_val_scores.npy`/`calibration_val_labels.npy`, and fits an `sklearn.isotonic.IsotonicRegression` → `calibration.pkl`. vision-service loads it at startup (`CALIBRATION_PATH` env var, same load/fallback pattern as `spatial_trained`/`fft_mlp_trained`/`fusion_trained`) and applies it inside `fuse()` right after the fusion sigmoid — only on the fusion-params path, not the 0.6/0.4 fallback blend, since the calibrator was fit against the fusion-driven score distribution specifically. `calibration_trained` added to `/health`. **Verified live, not just unit-tested:** loaded the real `calibration_val_scores.npy`/`calibration_val_labels.npy`/`calibration.pkl` produced by the actual training run and computed real before/after metrics — AUC essentially unchanged (0.99780 → 0.99791, confirming isotonic regression's rank-order-preservation guarantee empirically, not just by construction) while false-positive rate at the 0.90 alert threshold dropped from 0.29% to 0.10% (41→14 false positives out of 14,322 real-labelled val frames) — the real acceptance metric per this milestone's own verify criteria. 4 new vision-service unit tests (20 total, was 16): calibrator passthrough when unset, isotonic mapping actually applied, monotonicity preserved, `/health` reports the new flag.

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
locust -f locustfile.py --headless -u 1 -r 1 -t 2m --host=https://localhost:8003 --insecure &
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

> **Execution note (2026-07-11):** OpenTelemetry (M8) and webhook fan-out (M9) were pulled forward and completed as part of the performance/robustness-first sequence in `/home/vander/.claude/plans/lets-try-to-finish-playful-barto.md` — see notes below. Remaining Phase 5 items (M10-M16) continue in that plan's order.

- [x] **M12: Stream segment archival to MinIO.** New `minio` service (local S3-compatible object storage, real S3 credentials never needed). `process_frame_payload()` reuses the existing `alert_counters`/`ALERT_WINDOW` streak logic — on the exact frame where `consecutive_alerts == ALERT_WINDOW` (not `>=`, so it fires once per streak, not on every subsequent alerted frame), `asyncio.create_task(_archive_segment(...))` uploads the already-in-hand raw JPEG (`payload.payload`, previously discarded) to `s3://ardd-segments/{stream_id}/frame_{frame_index}.jpg` via boto3 (`asyncio.to_thread`, since boto3 has no async API). Per-frame JPEGs, not video re-encoding. **Two real bugs found and fixed during verification:** (1) boto3's default connect timeout (~60s) plus this WSL host's DNS behavior — unlike Docker's embedded DNS, a real (or negative) DNS answer for an unresolvable hostname here takes ~10s, confirmed directly via `socket.getaddrinfo()`, not bounded by botocore's `connect_timeout` since that only wraps the post-DNS TCP handshake — meant two *pre-existing* tests (`test_alert_threshold`, `test_alert_resets`, both already driving 5 consecutive alerting frames for unrelated reasons) started spawning real, slow, unmocked archival attempts that stalled the rest of the suite via thread-pool exhaustion (each `asyncio.to_thread` call occupies an executor slot for the full ~10s). Fixed both directions: added an explicit short `botocore.config.Config(connect_timeout=3, read_timeout=5, retries={"max_attempts":1})` to the MinIO client (a real production robustness fix — a down/unreachable MinIO must never let a fire-and-forget path hang), and mocked `main._upload_segment_sync` in the two affected pre-existing tests (a real test-hygiene fix, not a workaround — they were never about archival). (2) Along the way, discovered host pytest runs had never been setting `TESTING=1`, despite the code having `if not os.getenv("TESTING")` guards in several places specifically to skip real inter-service network calls during tests (e.g. `/health`'s temporal-service check) — this was already slowing test runs, just not enough to look like a hang until M12's compounding factor above made it obvious. **Verified live, not just unit-tested:** two new pytest tests confirm archival fires exactly once per streak and re-fires after a reset (aggregation-service: 44 tests, was 42); a real fake-video run through `video_feeder.py` produced `INFO:main:Archived alert-streak-start segment: cam_01/frame_4.jpg` in the logs, and a direct boto3 `list_objects_v2` against the live MinIO bucket confirmed `cam_01/frame_4.jpg` (64049 bytes) actually present.
- [x] **M9: Webhook multi-target fan-out.** `_deliver_webhook()` generalized to accept `WEBHOOK_TARGETS` (a JSON array of `{"url","token","format"}` objects, replacing the old single `WEBHOOK_URL`/`WEBHOOK_TOKEN`), fanned out concurrently via `asyncio.gather(..., return_exceptions=True)` so one bad target can't block delivery to the others. `_format_for_target()` reshapes the payload per target: `"generic"` (default) passes it through as-is, `"slack"` produces Slack's incoming-webhook `{"text": ...}` shape. New local `webhook-receiver/` FastAPI stub (logs received payloads, `GET /received` for inspection) is the default demo target — no real Slack/PagerDuty account needed. Same per-target SSRF guard (`_valid_webhook_url`) reused at startup and delivery time. **Verified live:** a real `DEEPFAKE_ALERT` fired by `video_feeder.py` fake-video traffic was fanned out and landed in `webhook-receiver`'s `/received` list; aggregation-service logs confirmed `Webhook delivered to http://webhook-receiver:9000/webhook on attempt 1` on every alert frame.
- [x] **M11: RBAC dashboard (hardcoded role pairs).** `/auth/token` previously authenticated *any* non-empty `client_id`/`client_secret` with zero lookup — a confirmed real gap, not a misunderstanding. Now matches against `ADMIN_CLIENT_ID`/`ADMIN_CLIENT_SECRET` and `VIEWER_CLIENT_ID`/`VIEWER_CLIENT_SECRET` env vars (defaults: `admin`/`admin-secret-change-me`, `viewer`/`viewer-secret-change-me`), bakes a `role` claim into the JWT, and rejects anything else with `401`. New `require_role(role)` dependency: `401` if the bearer token is missing/invalid/expired (unauthenticated), `403` if it's valid but the wrong role (authenticated, unauthorized) — a distinction the M11 plan called out explicitly. New `POST /admin/reset_breaker` (role-gated) manually closes a stuck-open vision/rag circuit breaker instead of waiting out `*_BREAKER_RESET_S` — a genuinely useful ops action, not a placeholder invented just to have something to gate. Frontend: `src/jwt.ts`'s `decodeJwtRole()` reads the JWT's role claim client-side (no round-trip); `App.tsx` renders the new `AdminPanel` component only when `role === 'admin'`. **This is an intentionally breaking change** — every hardcoded test credential across the repo (`"c"/"s"`, `"bench"/"bench"`, `"eval_runner"/"eval_runner"`, `"test_client"/"test_secret"`) stopped authenticating and was updated to the `viewer` pair: `aggregation-service/tests/test_aggregation.py`, `run_benchmark.py`, `video_feeder.py`, `tests/e2e/test_pipeline_e2e.py`, `frontend/src/App.tsx`'s default, and `docker-compose.yml`'s `VITE_CLIENT_ID`/`VITE_CLIENT_SECRET` defaults. **Verified live:** `/auth/token` issues correct `role` claims for both pairs and `401`s a stale test credential; `/admin/reset_breaker` returns `401` with no token, `403` for a valid viewer token, `200` (and an actually-open breaker closes) for a valid admin token — all three cases also covered by new pytest tests (aggregation-service: 42 tests, was 36). Frontend: 3 new/updated test files (`jwt.test.ts`, `App.test.tsx`) prove the admin panel is hidden for a viewer-role token and rendered for an admin-role token (13 frontend tests total, up from 2 pre-existing store tests) — this also surfaced and fixed two pre-existing frontend test-infra gaps unrelated to RBAC logic itself: no `jsdom` environment was configured for component tests (host had never run one before), and React Testing Library wasn't being cleaned up between tests (`afterEach(cleanup)` was missing). Docker build for both aggregation-service and frontend succeeded; frontend container reports `healthy` and serves `200`. **Not manually browser-tested** — this environment has no browser access, so admin-panel visibility was verified via the automated component tests above rather than clicking through the actual dashboard.
- [x] **M8: OpenTelemetry tracing.** Local `otel-collector` + `jaeger` pair added to `docker-compose.yml` (no cloud APM). Client spans for `call_vision`/`call_rag` in aggregation-service; server spans (`mtcnn_detect`/`spatial_branch`/`freq_branch`) in vision-service's `infer()`; `vector_search`/`generate_verdict_via_llm` spans in rag-agent's `audit()`. **Two real bugs found and fixed during verification:** (1) containers had been `restart`ed instead of recreated, so the new `OTEL_EXPORTER_OTLP_ENDPOINT` env var never reached them — needs `docker-compose up -d` (recreate), not `restart`; (2) aggregation-service's and rag-agent's images were never rebuilt with the OTel packages actually in `requirements.txt`, and rebuilding aggregation-service surfaced a real dependency conflict (`mlflow==2.11.1` needs `protobuf<5`; `opentelemetry-proto==1.43.0` needs `protobuf>=5.0`) — fixed by pinning aggregation-service alone to an older pre-protobuf-5 OTel release train (`opentelemetry-sdk==1.24.0`, `opentelemetry-instrumentation-fastapi==0.45b0`, `opentelemetry-exporter-otlp==1.24.0`). **Verified live:** `curl 'http://localhost:16686/api/services'` lists all three instrumented services with real per-hop trace spans after driving traffic.
- [x] **M10: Certificate management + mTLS.** `scripts/gen_certs.sh` generates a local self-signed root CA + per-service leaf certs (runs `openssl` inside a throwaway `alpine/openssl` container — not installed on this host). Vision/RAG/Temporal require a client cert (`--ssl-cert-reqs 2`, full mTLS); Aggregation Service is `--ssl-cert-reqs 1` (CERT_OPTIONAL) since it's the one service the browser dashboard and host-side scripts hit directly — a client cert can't reasonably be required there. Kafka upgraded to SASL_SSL (PEM keystore/truststore via KIP-651, no keytool/JKS) — TLS transport plus the existing SASL PLAIN auth, not mTLS (a Kafka client cert would be redundant with SASL). WebSocket is `wss://` automatically once uvicorn is TLS-wrapped. MLflow and Ollama excluded from the mesh (`mlflow server`'s CLI has no TLS flags at all; Ollama is third-party) — plaintext, documented in `SECURITY.md`. Webhook targets excluded too (`WEBHOOK_TARGETS` can point at arbitrary external URLs like a real Slack webhook, which can't present our CA's client cert). Cert rotation is restart-based (`docker-compose restart`/`up -d` after re-running `gen_certs.sh`), not hot-reload — documented limitation, not a gap. **Two real bugs found and fixed during verification:** (1) aggregation-service's outbound calls to vision-service/rag-agent/temporal-service only set `verify=` (server trust) but never `cert=` (its own client identity) — since those three are CERT_REQUIRED, this made aggregation-service's own `/health` report `temporal_service_status: "unavailable"` even though temporal-service was healthy; fixed by adding a `_client_cert()` helper presenting aggregation-service's own cert on those calls. (2) The CA cert generated without explicit `keyUsage`/`basicConstraints` extensions passed `curl --cacert` but failed Python's `ssl` module with `CERTIFICATE_VERIFY_FAILED: CA cert does not include key usage extension` — curl is lenient, Python's ssl (and likely browsers) are RFC-5280-strict; fixed by adding `-addext 'basicConstraints=critical,CA:true'` and `-addext 'keyUsage=critical,keyCertSign,cRLSign'` to the CA generation step. **Verified live, not just unit-tested:** all 4 rebuilt services report `healthy`; `curl --cacert ca.crt https://localhost:8003/health` succeeds with no client cert (CERT_OPTIONAL); the same call to rag-agent (CERT_REQUIRED) fails the TLS handshake as expected (new `tests/e2e/test_pipeline_e2e.py::test_mtls_rejects_client_without_cert`, passing); a real fake-video run through `video_feeder.py` flowed Kafka→vision-service→rag-agent→aggregation-service→webhook-receiver entirely over TLS and delivered a genuine `DEEPFAKE_ALERT`; a Python `requests`+`websocket-client` script completed the full `/auth/token` + `wss://.../stream` handshake using only CA trust, matching what the browser dashboard needs.

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