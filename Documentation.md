# ARDD-TP Development Journal

---

## Session: 2026-06-05 23:44 — 2026-06-06 01:18 IST

### Work Completed

- Installed Docker and Docker Compose on Arch Linux
- Fixed `ingest-gateway/requirements.txt`: replaced `kafka-python==2.0.2` with `kafka-python-ng==2.2.3` (Python 3.14 compatibility fix)
- Created Python venv at `ingest-gateway/.venv` and verified imports work
- Started Zookeeper and Kafka containers: `docker compose up -d zookeeper kafka`
- Verified Kafka is running and responsive
- Ran `python prepare_test_dataset.py` — created 20 test samples (10 REAL, 10 FAKE)
- Updated `.gitignore` to include PLAN docs and NEXT_STEPS.md in repo, keep only `.env` private
- Verified PLAN folder contains no sensitive data (only placeholders)

### Status

Phase 1 Steps 1-2 completed.

---

## Session: 2026-06-10 (Current)

### Work Completed

- Created `vision-service/` directory and structure for Phase 1 Step 3.
- Implemented `vision-service/main.py`: FastAPI app with `POST /infer` and `GET /health` endpoints.
- Added `X-API-Key` authentication middleware.
- Implemented payload size limit handling (max 2MB + base64 overhead).
- Added base64 image decoding and conversion to OpenCV format.
- Integrated MTCNN for face alignment and fallback logic (returning `0.5` deepfake score when no face is found).
- Implemented mock models using `torchvision.models.efficientnet_b4` for the spatial branch and a heuristic FFT-based function for the frequency branch.
- Added final score calculation based on `0.6 * spatial + 0.4 * frequency`.
- Created `vision-service/tests/test_vision.py` containing tests according to `PLAN/TESTING.md` guidelines.
- Configured a Dockerfile for `vision-service` based on `python:3.11-slim`, solving Python 3.14 build compatibility issues with pip packages.
- All unit/integration tests passed successfully within the Docker container.

### Status

Phase 1 Step 3 completed. Ready for Step 4 (RAG Context Agent).

---

## Session: 2026-06-11 (Current)

### Work Completed

- Created `rag-agent/` directory and implemented Step 4.
- Built a custom deterministic embedding model (`SimpleHashEmbeddings`) to allow lightweight, offline FAISS vector store initialization.
- Injected predefined threat signatures from `SCHEMA.md` into the FAISS index.
- Exposed `POST /audit` endpoint utilizing semantic search with a threshold mapping (mapping `deepfake_score` to a text query that overlaps with signature tags).
- Integrated local fallback LLM verdict generation (resolving to `FAIL` and applying `confidence` boosts when matching).
- Created `aggregation-service/` directory and implemented Step 5.
- Orchestrated the Vision -> RAG sequential flow, strictly enforcing a 100ms timeout for the RAG agent (`httpx.TimeoutException` handling).
- Implemented `final_score` clamping, RAG boost (`β = 0.15`), and a 5-frame consecutive rolling alert window.
- Established `POST /auth/token` for JWT generation (HS256) and `ws://.../stream` endpoint for telemetry broadcast.
- Expanded Pytest suites for both services following the exact constraints mapped in `PLAN/TESTING.md`, including edge cases like `test_rag_timeout_fallback`, `test_final_score_clamped`, and `test_alert_threshold_and_reset`.
- Successfully validated both services utilizing Docker containers (`python:3.11-slim`) to sidestep host Python 3.14 dependency build issues.

### Status

Phase 1 Steps 4 and 5 completed. Code is aligned with `PLAN/TESTING.md`, `PLAN/ARCHITECTURE.md`, `PLAN/SCHEMA.md`, and `PLAN/TRD.md`. Ready for Step 6 (MLflow Telemetry).

---

## Session: 2026-06-12 — Phase 2 Architecture Design

### Work Completed

- Designed and documented **Phase 2: Lambda Architecture — Temporal Batch Layer**.
- Decided to evolve the system from a sequential pipeline into a true **Lambda Architecture** with two independent processing paths:
  - **Speed Layer** (Vision Service): frame-by-frame, 200ms SLA — unchanged.
  - **Batch Layer** (new Temporal Service): accumulates 1024-d feature vectors over a 30-second window and runs a pre-trained LSTM/ViT for sequence-level deepfake detection.
- Key design decision: buffer **feature vectors** (900 × 1024 floats = ~3.6 MB/stream), NOT raw frames (900 JPEGs ≈ 450 MB/stream). This makes the batch layer practically deployable in Docker.
- Refined Temporal Model strategy: documented that the LSTM head is tiny (2-5M parameters) because the Speed Layer acts as a feature extractor, making it feasible to train on a single consumer GPU using FaceForensics++, while still maintaining open-source pre-trained models as an option.
- Added PyTorch blueprint for `TemporalBatchAuditor` to `PLAN/TRD.md`.
- Designed three resilience conditions:
  1. Temporal Service crash → Speed Layer unaffected; dashboard shows "Temporal Audit Unavailable".
  2. Incomplete buffer (N < 900) → zero-pad to [900, 1024]; set `low_confidence_flag: true`.
  3. Frame gaps → linear interpolation of missing feature vectors.
- Updated all PLAN documentation files:
  - `README.md` — rewrote with Lambda Architecture diagram, updated stack table, pipeline steps, SLA/resilience tables, port reference.
  - `PLAN/ARCHITECTURE.md` — full Lambda Architecture rewrite with dual-layer data flow.
  - `PLAN/SCHEMA.md` — added `feature_vector` to `VisionResult`; new `TemporalAuditResult` schema.
  - `PLAN/TRD.md` — added Temporal Service to stack, §4b temporal scoring algorithm, updated §6 Future States.
  - `PLAN/ERROR_HANDLING.md` — added §3b Temporal Service error matrix (8 failure scenarios).
  - `PLAN/ROADMAP.md` — inserted Phase 2 (Lambda); renumbered Phase 2→3, Phase 3→4, Phase 4→5.
  - `PLAN/PHASES.md` — inserted full Phase 2 with 8 ordered steps, verification commands, exit criteria; renumbered downstream phases.

### Status

- Initialized React + TypeScript + Vite project for Phase 1 Step 7.
- Implemented Zustand store for managing JWT tokens, WebSocket connectivity, real-time metrics (`frames` array capped at 100 entries), and alert states.
- Created `index.css` implementing a rich aesthetic system (dark mode, glassmorphism, micro-animations, Outfit typography) without Tailwind.
- Built the `LiveGraph` using Recharts to plot real-time deepfake scores.
- Implemented `AuditPanel` tracking the RAG instantaneous verdicts and Phase 2 Temporal Batch verdicts.
- Integrated WebSocket reconnection with exponential backoff and JWT refresh polling in `App.tsx`.
- Resolved TypeScript strict-mode issues.

### Status

**Phase 1 (Core Pipeline MVP) is fully completed.**
The end-to-end pipeline from `Ingest Gateway` -> `Kafka` -> `Vision Service` -> `RAG` -> `Aggregation Service` -> `React Dashboard` is implemented and verified.

---

## Session: 2026-06-13 — Phase 1 Audit + Phase 2 Architecture Grill

### Work Completed

#### Phase 1 Audit

- Audited Phase 1 repo against `PLAN/` guidelines. Found 13 issues across four categories (critical test bugs, functional bugs, documentation errors, security). Documented all in `TaskTo.md` with diffs.
- Verified all 13 issues were already fixed on re-read:
  - `test_vision.py:124` — `HEADERS` undefined (NameError) → fixed
  - `aggregation-service/main.py` — missing `Authorization: Bearer` webhook header → fixed
  - `aggregation-service/main.py` — `"deepfake_alert"` lowercase → `"DEEPFAKE_ALERT"` → fixed
  - Webhook payload missing `matched_signature` and `consecutive_alert_frames` → fixed
  - `docker-compose.yml` missing `WEBHOOK_TOKEN` env var → fixed
  - `rag-agent/main.py` — `uptime_s` hardcoded to `0` → fixed (real `START_TIME` tracking)
  - `PHASES.md` Step 3 checkboxes all unchecked despite full implementation → fixed
  - `PHASES.md` Phase 3/4/5 exit criteria mislabelled (off-by-one) → fixed
  - `ERROR_HANDLING.md` RAG timeout stated as 150ms, contradicts TRD and code (100ms) → fixed
  - `docker-compose.yml` ollama using `latest` tag → pinned to `0.1.32`
  - `vision-service/requirements.txt` unpinned deps → pinned

#### Phase 2 Architecture Grill Session

Ran a full design review session resolving 13 design decisions (D1–D10 + security + Kafka + state). Key outcomes:

**Pipeline driver (D1):** Aggregation Service grows an `aiokafka` consumer loop on the `frames` topic. The old `POST /aggregate` test endpoint is retired as the production driver. Aggregation now owns the Speed Layer pipeline end-to-end.

**Feature vector pipeline dropped (D2):** Original Phase 2 design had Vision Service publishing 1024-d `feature_vector` payloads to Kafka for Temporal Service to consume. **Dropped entirely.** ResNext50+LSTM has its own feature extractor — the Temporal Service subscribes directly to the `frames` topic. `PHASES.md` Steps 2.1 and 2.2 (feature vector extraction and `frames_processed` topic) removed.

**Temporal model confirmed (D4):** Downloaded `Naman712/Deep-fake-detection` — `model_87_acc_20_frames_final_data.pt` from HuggingFace. Architecture: ResNext50 backbone (`model.0`–`model.7`) + LSTM (`lstm.weight_ih_l0`, `lstm.weight_hh_l0`) + linear head (`linear1.weight`). Input: `[1, 20, 3, 112, 112]` at 112×112, ImageNet-normalised. Score: `F.softmax(logits, dim=1)[0][0].item()` (fake class probability). Only the `.pt` weights file is in cache — `modeling.py` must be reconstructed and committed to `temporal-service/`.

**20-frame tumbling window (D3):** Buffer is `deque(maxlen=20)` per `stream_id`, cleared after each inference (~0.67s at 30 FPS). Sliding window (overlapping inference) noted as future scope.

**Consumer threading model (D5):** Migrated all Kafka consumers in Aggregation Service from `threading.Thread` to `aiokafka` asyncio tasks. `kafka-python-ng` retained only for `KafkaAdminClient` in the Ingest Gateway.

**RAG embeddings (D7):** `SimpleHashEmbeddings` produce meaningless similarity scores (hash bucket vectors have no semantic structure). Replacing with `sentence-transformers` (`all-MiniLM-L6-v2`) in Phase 2. Fixes the broken 0.75 threshold.

**Security fixes (D8/D9):** All four fixed in Phase 2:
  1. `vision-service/main.py:44` — add `weights_only=True` to `torch.load` (prevents arbitrary code execution)
  2. WebSocket JWT: switch from `?token=` URL query param to `Sec-WebSocket-Protocol` subprotocol (JWT in URL leaks to logs)
  3. Frontend: replace hardcoded `CLIENT_ID`/`CLIENT_SECRET` with `VITE_CLIENT_ID`/`VITE_CLIENT_SECRET` env vars
  4. Kafka SASL_PLAINTEXT: add `KAFKA_SASL_MECHANISM`, `KAFKA_SECURITY_PROTOCOL`, credentials to all clients. Full TLS (SASL_SSL) deferred to Phase 5 — college project context.

**In-memory state persistence (D6):** Alert counters and drift history reset on Aggregation Service restart. Accepted as known limitation for Phase 2; Redis migration in Phase 3. Documented in `ERROR_HANDLING.md`.

#### Documentation Updates

Rewrote all PLAN documentation to reflect the above decisions:

- **`PLAN/PHASES.md`** — Phase 2 fully rewritten: Steps 2.1–2.10 (aiokafka pipeline, Temporal Service, docker-compose wiring, buffer resilience, aggregation path, dashboard, RAG fix, security fixes, doc updates, tests). Old feature-vector steps removed. Exit criteria updated.
- **`PLAN/ARCHITECTURE.md`** — Temporal Service block rewritten (ResNext50+LSTM, 20-frame tumbling, `frames` topic consumer). Data Flow diagram updated (removed `feature_vector → Kafka` path; replaced with dual-consumer flow). Lambda table, ASCII diagram, Vision Service note, Aggregation/WebSocket/Dashboard descriptions all updated.
- **`PLAN/FLOW.md`** — Sequence diagram rewritten with parallel `par`/`and` blocks for Speed and Batch layers. Execution Flow section expanded into two numbered paths.
- **`PLAN/ROADMAP.md`** — Phase 1 header and MLflow/React dashboard status fixed (all `✅ Done`). Phase 2 section fully rewritten with new 10-step list, updated SLA table, updated architecture ASCII.
- **`PLAN/ERROR_HANDLING.md`** — Section 3b updated throughout (900→20, N<300/10s→N<6, feature vector decode→JPEG decode, random-fallback on missing weights). Known Limitations block added for restart state loss.
- **`PLAN/TRD.md`** — Tech stack table updated. Batch Layer SLA table rewritten (20 frames, ~0.67s). Section 4b fully rewritten with actual model architecture, input shape, and score formula. Feature vector note added.
- **`PLAN/SCHEMA.md`** — Header and Section 4b updated (20-frame window, `window_duration_s` description, `model_used` example, N<6 threshold note).
- **`TaskTo.md`** — Phase 2 design decisions (D1–D10) and missing tasks (M1–M14) documented.

### Status

Phase 2 architecture fully resolved and documented. Ready to begin Phase 2 implementation:

1. `aggregation-service/main.py` — migrate to `aiokafka` consumer loop
2. `temporal-service/` — create service, reconstruct `modeling.py`, implement 20-frame tumbling pipeline
3. `docker-compose.yml` — wire `temporal-service`, add SASL_PLAINTEXT config
4. `rag-agent/main.py` — replace `SimpleHashEmbeddings` with `sentence-transformers`
5. Security one-liners: `weights_only=True`, JWT subprotocol, VITE_ env vars

---

## Session: 2026-06-14 — Phase 2 Implementation + Audit

### Work Completed

#### Phase 2 Implementation

- **Kafka SASL_PLAINTEXT** wired end-to-end. Broker configured in `docker-compose.yml` using Confluent cp-kafka's double-underscore env var convention (`KAFKA_LISTENER_NAME_SASL__PLAINTEXT_PLAIN_SASL_JAAS_CONFIG`). `_kafka_sasl_kwargs()` helper added to `ingest-gateway/main.py`, `aggregation-service/main.py`, and `temporal-service/main.py` — reads `KAFKA_SECURITY_PROTOCOL`/`KAFKA_SASL_USERNAME`/`KAFKA_SASL_PASSWORD` from env; wired into all `KafkaProducer`, `KafkaAdminClient`, and `AIOKafkaConsumer` instances.

- **`temporal-service/` built and tested.** `modeling.py` reconstructed: `DeepFakeDetector` with ResNext50 backbone + single LSTM layer + `linear1` head. Weights loaded from `model_87_acc_20_frames_final_data.pt` at startup; falls back to random-initialised weights with `model_used: "random-fallback"` if file not found. `frames_consumer_task` subscribes to `frames` Kafka topic; per-`stream_id` `deque(maxlen=20)` of `(frame_index, tensor)` tuples; on full buffer: stack to `[1, 20, 3, 112, 112]`, run inference, POST `TemporalAuditResult` to aggregation, clear buffer.

- **`temporal-service/tests/test_temporal.py`** — 19 tests written and passing. Covers: health schema, batch_status auth, flush auth, unknown-stream noop, empty buffer → UNKNOWN, sparse (N<6) → UNKNOWN without inference, padded (N=12) → `low_confidence_flag: true`, full (N=20) → all 11 `TemporalAuditResult` fields validated, tensor shape `[3, 112, 112]`, window frame indices, `window_duration_s`, `frames_interpolated` always 0, `model_used`, deque `maxlen` enforcement, buffer cleared after flush, batch_status reflects live buffers. Note: linear interpolation test deferred (feature not implemented).

- **`aggregation-service/main.py`** — JWT secret default raised from `"super-secret-key"` (16 bytes) to `"ardd-tp-dev-secret-key-change-me!"` (32 bytes); eliminates `InsecureKeyLengthWarning`. Dead `ConnectionManager.connect()` method removed. `_evict_oldest(d, cap=1000)` added and called after every `alert_counters` and `drift_history` update to bound unbounded defaultdict growth.

- **`rag-agent/main.py`** — `HuggingFaceEmbeddings` import migrated from deprecated `langchain_community.embeddings` to `langchain_huggingface`; `langchain-huggingface` added to `rag-agent/requirements.txt`.

- **`tests/e2e/test_pipeline_e2e.py`** — fixed WebSocket connection (was `?token=` query param, now `subprotocols=[token]`); fixed `KafkaProducer` missing SASL credentials; added `KAFKA_SASL_USERNAME`/`KAFKA_SASL_PASSWORD` env var reads.

- **`docker-compose.yml`** — `temporal-service.depends_on` fixed from invalid mixed list+dict YAML to all-dict format with `kafka: condition: service_started` and `aggregation-service: condition: service_healthy`.

#### Phase 2 Audit Findings

Ran full audit of Phases 1 and 2 code. All critical issues fixed during session. Key findings:

- **Memory leak fixed:** `alert_counters` and `drift_history` in aggregation-service were unbounded `defaultdict` objects growing permanently per unique `stream_id`. Ingest-gateway generates a new `stream_id = f"stream_{int(time.time())}"` on each restart, so every restart creates a new key. Fixed with `_evict_oldest` helper (drops oldest half when dict exceeds 1000 keys).
- **YAML syntax bug fixed:** `temporal-service.depends_on` in `docker-compose.yml` mixed list and dict syntax under the same key, causing `docker compose up` to fail silently.
- **JWT secret too short:** Default secret was 16 bytes, triggering `InsecureKeyLengthWarning` on every JWT encode/decode in 11 tests. Raised to 32 bytes.
- **WebSocket JWT exposure:** Both the E2E test and the original WebSocket implementation leaked JWT in the URL (`?token=...`), which appears in server access logs. Migrated to `Sec-WebSocket-Protocol` subprotocol header.
- **Test infrastructure note:** Vision-service tests cannot run on host Python 3.14 due to `facenet-pytorch` pulling `Pillow` build-from-source, which fails with `KeyError: '__version__'`. Must run in Docker (`python:3.11-slim`). All other service test suites run in host venvs.

#### Test Results Summary

| Service | Tests | Result |
|---|---|---|
| `aggregation-service` | 22 | ✅ 22/22 pass |
| `temporal-service` | 19 | ✅ 19/19 pass |
| `rag-agent` | 6 | ✅ 6/6 pass |
| `ingest-gateway` | 6 | ✅ 6/6 pass |
| `vision-service` | N/A | Requires Docker (Python 3.14 incompatibility) |
| **Total** | **53** | **✅ 53/53** |

#### Documentation Updates

- **`PLAN/PHASES.md`** — Steps 2.1–2.10 checkbox audit: all completed items marked `[x]`; Step 2.4 linear interpolation and Step 2.6 frontend health fetch marked as deferred with `[ ]`. Phase 2 exit criteria updated to reflect actual status.
- **`PLAN/ERROR_HANDLING.md`** — §3b frame-gaps row updated: linear interpolation marked NOT IMPLEMENTED, `frames_interpolated` always 0. Known Limitations block updated: eviction fix noted; linear interpolation deferred; restart state-loss risk unchanged (Phase 3 Redis).
- **`TaskTo.md`** — Rewritten: Phase 1/2 audit items summarised as resolved. New "PENDING FOR TOMORROW" section added with 6 concrete deprecation fixes (FastAPI `on_event`, `langchain-community` FAISS, `httpx2`, Python 3.14 vision-service tests, linear interpolation, `mlflow_buffer` O(n) pop).

### Status

Phase 2 ✅ substantially complete. 53/53 unit tests passing across 4 services. Two items deferred: linear interpolation (`frames_interpolated` always 0) and frontend health-status wiring. Six deprecation items documented in `TaskTo.md` for next session. Ready to begin Phase 3 once deprecation items addressed.

---

## Session: 2026-06-16/17 — Pipeline Fixes, UI Updates & Speed Layer Training Design

### Work Completed

#### Pipeline Bug Fixes (post-Phase 2 audit)

- **Frontend unhealthy** — `curl` not present in `node:20-alpine` image; added `RUN apk add --no-cache curl` to `frontend/Dockerfile`.
- **Kafka unreachable from host** — `kafka:9092` not resolvable outside Docker; added `127.0.0.1 kafka` to `/etc/hosts`. Allows `simulate_stream.py` and `video_feeder.py` to reach the broker from the host.
- **CORS 405 on `/auth/token`** — FastAPI had no OPTIONS handler; added `CORSMiddleware` to `aggregation-service/main.py` (`allow_origins=["*"]`).
- **Temporal weights not found** — HuggingFace snapshot directory uses symlinks that don't resolve inside Docker bind mounts; changed `docker-compose.yml` volume to point directly at the blob file (`~/.cache/huggingface/hub/models--Naman712--Deep-fake-detection/blobs/<sha>`).
- **Temporal key mismatch** — Naman712 checkpoint stores weights under `model.*` keys; our `DeepFakeDetector` class uses `backbone.*`; added key remapping on load in `temporal-service/main.py`.
- **Temporal always authentic** — `nn.LSTM` defaults to `bias=True`; Naman712 was trained with `bias=False`; fixed in `temporal-service/modeling.py`.

Full pipeline now working end-to-end: `simulate_stream.py` / `video_feeder.py` → Kafka → Aggregation → Vision + RAG → WebSocket → Dashboard.

#### Frontend: Verdict Tally UI

- Added `VerdictCounts` interface (`PASS`, `FAIL`, `UNKNOWN` counters) and state to `frontend/src/store.ts`.
- `setTemporalAudit` now increments the corresponding `verdictCounts` bucket on every temporal audit event.
- Added `resetVerdictCounts` action.
- Added **"Temporal Verdict Tally"** card to `frontend/src/components/AuditPanel.tsx`: three coloured counters (REAL / FAKE / UNKNOWN), a Reset button, and a `"X audits — Y% flagged fake"` summary line shown once at least one verdict is recorded.

#### FaceForensics++ Dataset

- Obtained official access approval; downloaded the `download-FaceForensics.py` script from the EU2 server.
- Downloaded initial 50 real + 50 fake videos at c23 compression for quick smoke-testing.
- Full dataset download (1000 real + 2000 fake) running in background (`/tmp/ff_original.log`, `/tmp/ff_deepfakes.log`).
- Added `download-FaceForensics.py` and `datasets/` to `.gitignore` (download script contains private URL; dataset is too large to commit).

#### `video_feeder.py` — FF++ Video Feeder

- Created `video_feeder.py` at the repo root.
- Reads FF++ videos from `datasets/ff++/` and publishes frames to Kafka with SASL_PLAINTEXT.
- **`demo` mode** (default): alternates one real and one fake video every `--switch-every` seconds so the Temporal Audit panel visibly flips between Authentic / Fake on the live dashboard.
- **`eval` mode**: streams all real videos then all fake videos sequentially, printing ground-truth labels alongside Temporal Service scores — used for FF++ benchmark collection.

#### Speed Layer Training Design (Grill-Me Session, 2026-06-17)

Ran a full 15-question design review to lock every decision before writing any training code.

| Decision | Choice | Rationale |
|---|---|---|
| Face extraction | Pre-extract offline (MTCNN, saved crops) | Avoids paying MTCNN cost ~180K times during training; inference pipeline unchanged (MTCNN runs live) |
| FFT features | Radial bins, 64-dim | Fixed-length, rotationally invariant; works well for compression artefacts |
| Fusion method | Logistic regression (learned α) | Interpretable, 2 parameters, no risk of fusion overfitting |
| Augmentation | Safe set (HFlip / brightness / crop) | Preserves frequency-domain structure; no GAN-style augmentation |
| Dataset split | Official FF++ 720/140/140 | Comparable to published benchmarks |
| Frame sampling | Every 5th frame | ~3 600 frames/video → ~180K crops from 1000 real + 2000 fake |
| Class imbalance | `CrossEntropyLoss(weight=[2.0, 1.0])` | Uses all data; no undersampling |
| Batch size | 16 | RTX 4050 6GB VRAM ceiling with full EfficientNet-B4 |
| EfficientNet LR | 1e-4 with cosine annealing | Standard fine-tuning LR for ImageNet-pretrained backbone |
| FFT MLP LR | 1e-3 | Small network; learns faster |
| Epochs | 10 | Enough for convergence; early stopping not used (large dataset is the regulariser) |
| LR schedule | Cosine annealing | Smooth decay; better than step for fine-tuning |
| Model save | `state_dict` only | Consistent with how Naman712 weights are loaded; portable across refactors |
| Early stopping | Disabled — run all 10 epochs | Full dataset + augmentation + dropout(0.3) on MLP handle overfitting |
| MLP dropout | p=0.3 on hidden layer | Lightweight regularisation in place of early stopping |

#### Pre-Training File Checklist

These files must be created/modified before training begins:

**Create (new):**
1. `extract_faces.py` — offline MTCNN face crop extraction from FF++ videos
2. `train_vision.py` — EfficientNet-B4 + FFT MLP training script (weighted loss, cosine LR, state_dict save)
3. `vision-service/modeling.py` — `FftMlp` class definition (64 → 32 → 1 + dropout)

**Modify (existing):**
4. `vision-service/main.py` — replace hardcoded FFT heuristic with trained `FftMlp` + load checkpoint
5. `docker-compose.yml` — add GPU passthrough block to `vision-service`:
   ```yaml
   deploy:
     resources:
       reservations:
         devices:
           - driver: nvidia
             count: 1
             capabilities: [gpu]
   ```

### Status

Pipeline MVP fully working end-to-end with real FF++ videos. Speed layer training design locked (15 decisions). Pre-training file checklist documented. Next: write `extract_faces.py` → `train_vision.py` → update `vision-service/` → run training on RTX 4050.

---

## Session: 2026-06-17 — Phase 2.5 Implementation & Training

### Work Completed

#### Phase 2.5 Files Written

- **`vision-service/modeling.py`** (new) — single source of truth for both training and inference. Exports `compute_fft_features()` (64-dim radial FFT bins, normalised), `SpatialBranch` (EfficientNet-B4 + sigmoid head), `FftMlp` (Linear 64→32, ReLU, Dropout 0.3, Linear 32→1, Sigmoid), and `IMAGENET_MEAN` / `IMAGENET_STD` constants. Importing from one file guarantees train/inference distribution parity.

- **`extract_faces.py`** (new, repo root) — offline MTCNN face crop extraction from FF++ c23 videos. Scans both `original_sequences/youtube/c23/videos/` and `manipulated_sequences/Deepfakes/c23/videos/`. Samples every 5th frame (`--frame-step 5`). Crops resized to 380×380 JPEG under `face_crops/{real,fake}/<stem>/frame_NNNNNN.jpg`. Requires `facenet-pytorch`; run inside `ardd_tp-vision-service` Docker container (Python 3.14 host incompatible). Result: **204,351 crops** — 102,211 real + 102,140 fake across 1000 dirs each.

- **`train_vision.py`** (new, repo root) — trains `SpatialBranch` and `FftMlp` simultaneously: one DataLoader, two optimisers, two AMP GradScalers. Weighted BCE loss (`fake_weight=2.0`). Official FF++ split 72%/14%/14% by video dirs. Fits `sklearn.LogisticRegression` fusion on val set; saves all three artifacts to `model-weights/`.

- **`vision-service/main.py`** (rewritten) — loads `SpatialBranch`, `FftMlp`, and fusion params from `model-weights/` volume mounts. Critical fix: ImageNet normalisation now applied at inference (was completely absent — train/inference mismatch). FFT computed on face crop (not full frame), matching training distribution. `fuse()` uses learned logistic regression coefficients when available, falls back to 0.6/0.4 hardcode. `/health` now reports `spatial_trained`, `fft_mlp_trained`, `fusion_trained` flags.

- **`docker-compose.yml`** (updated) — GPU passthrough added to `vision-service` (`deploy.resources.reservations.devices`). Three volume mounts for weight files from `model-weights/`. `MODEL_WEIGHTS_PATH`, `FFT_MLP_WEIGHTS_PATH`, `FUSION_WEIGHTS_PATH` env vars wired.

#### Training Run — RTX 4050 6GB

Training ran entirely inside the `ardd_tp-vision-service` Docker container to work around Python 3.14 host incompatibility.

**Issues encountered and fixed:**

1. **CUDA OOM at batch=16** — EfficientNet-B4 at 380×380 requires ~5.3 GB; RTX 4050 has 5.66 GB. Fixed: batch=8 + `torch.cuda.amp.GradScaler` (AMP FP16) + `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.

2. **BCELoss unsafe with autocast** — `RuntimeError: binary_cross_entropy and BCELoss are unsafe to autocast`. Fix: forward pass inside `with autocast():`, loss computed **outside** it after casting preds to `.float()`. Pattern applied to both branches.

**Results (FF++ c23, Deepfakes subset, 1000 real + 1000 fake videos):**

| Model | Test Accuracy | Notes |
|---|---|---|
| EfficientNet-B4 (spatial) | **99.39%** | 10 epochs, AdamW lr=1e-4, cosine annealing |
| FFT MLP 64-dim (frequency) | 53.3% | Near-random; c23 compression leaves no frequency artefacts |
| Fused (logistic regression) | **99.41%** | AUC **0.9987** |

Fusion weights: `sigmoid(10.1361·spatial + 7.0433·freq − 8.8748)`

Saved artifacts:
- `model-weights/efficientnet_b4_ff++.pt` (71 MB — requires git-lfs: `yay -S git-lfs && git lfs install`)
- `model-weights/fft_mlp_ff++.pt` (11 KB)
- `model-weights/fusion_alpha.npy` (140 B)

Vision service health confirmed: `spatial_trained: true`, `fft_mlp_trained: true`, `fusion_trained: true`.

#### References Added

Three BibTeX entries added to `PLAN/REFERENCES.md`: FaceForensics (2018), FaceForensics++ ICCV 2019, Google/JigSaw DFDC 2019.

#### Documentation Updates

- `README.md` — updated Scoring Algorithm section with learned fusion weights; added Benchmark Results table; added Future Scope section (FFT frequency branch: multi-method training, DCT features, contrastive loss).
- `PLAN/PHASES.md` — Phase 2.5 all steps marked complete except Step 2.5.6 (benchmark eval run).
- `.gitignore` — added `*.pt` / `!model-weights/*.pt` negation, `checkpoints/`, `face_crops/`, `datasets/`, `download-FaceForensics.py`.
- `.gitattributes` — added git-lfs tracking for `model-weights/*.pt`.
- `requirements.txt` — added `scikit-learn`.

### Outstanding Issues

**Aggregation service Kafka consumer crashed (2026-06-17):** At startup, the `start_frames_consumer` asyncio task failed with `KafkaConnectionError` (Kafka not yet ready). The exception was never retrieved and the task did not restart. Consumer group `aggregation-pipeline-group` now shows no active members with 3,400 frame lag. **Fix: `docker compose restart aggregation-service`.** → Resolved in next session (2026-06-18) with retry loop.

**WebSocket cycling (cosmetic):** Rapid open/close pattern in UI logs is caused by React StrictMode double-invocation of effects in dev mode (frontend runs `npm run dev` in Docker). → Resolved in next session (2026-06-18) by removing `<StrictMode>`.

### Status

Phase 2.5 training complete. All weight files written and loaded by vision-service. Benchmark eval run (Step 2.5.6) pending resolution of aggregation service consumer crash.

---

## Session: 2026-06-18 — Pipeline Stability, Dashboard Improvements & Ollama Integration

### Work Completed

#### Pipeline Stability — Kafka Consumer Retry Loop

Both `temporal-service` and `aggregation-service` had a silent failure mode: if Kafka was not yet ready when the container started, the `aiokafka` consumer task crashed with `KafkaConnectionError`, the exception was never retrieved, and the task never restarted. The consumer group showed no active members indefinitely.

Fixed in both services by wrapping the consumer bootstrap in a `while True / await asyncio.sleep(5)` retry loop. The consumer now retries every 5 seconds until Kafka is reachable, then runs normally. Applies to all three consumer coroutines:
- `temporal-service/main.py` — `frames_consumer_task()`
- `aggregation-service/main.py` — `start_frames_consumer()` and `start_labels_consumer()`

#### Frontend — WebSocket Stability Fix

Removed `<StrictMode>` from `frontend/src/main.tsx`. React StrictMode in dev mode intentionally double-invokes effects, causing the WebSocket to open and immediately close on every cycle. This prevented `temporal_audit` WebSocket messages from being received, so the Temporal Verdict Tally never incremented. Removing StrictMode stabilised the connection.

#### Dashboard — Flagged Frames Panel

Added `frontend/src/components/FlaggedFrames.tsx` — a scrollable panel below the live graph showing the last 30 frames where `deepfake_score > 0.5`, newest first. Each entry shows stream ID, frame index, a colour-coded score bar (cyan → orange → red at 50/70/90%), and an ALERT badge if the rolling 5-frame alert fired. `flaggedFrames: FrameData[]` added to the Zustand store, populated by `addFrame` without affecting the existing 100-frame graph buffer.

#### Dashboard — Alert Banner Sticky Fix

The alert banner previously mirrored `frame.alert` directly (a boolean per frame from the server), causing it to mount/unmount on every frame as the score fluctuated around the 90% threshold. Replaced with a sticky model:
- `activeAlert` is set to `true` on the first alert frame and stays `true` until the user clicks ✕ dismiss.
- `alertConsecutiveCount` increments on every incoming alert frame and is shown in the banner header.
- `dismissAlert()` action resets both fields.
- `AlertBanner` now accepts `onDismiss` prop and renders the consecutive count.

#### Dashboard — Graph Hover Tooltip

Replaced the default Recharts tooltip with a custom `CustomTooltip` component in `LiveGraph.tsx`. On hover shows:
- Deepfake Score (colour-coded: green/orange/red at 50/70/90%)
- Fusion formula: `sigmoid(10.14·spatial + 7.04·freq − 8.87)`
- RAG Verdict (PASS / FAIL / UNKNOWN)
- RAG boost indicator (+15%) when `audit_verdict === 'FAIL'`
- Alert badge when `alert: true`

#### Dashboard — Label & Naming Fixes

- **Temporal Verdict Tally**: labels changed from "REAL / FAKE / UNKNOWN" to "Real windows / Fake windows / Unknown" with a "20-frame sequence windows" subtitle — clarifies these are window counts not individual frame counts.
- **Continuity Score** renamed to **Authenticity Confidence** with colour coding (green ≥ 60%, orange 40–60%, red < 40%) and label `"X% confident real"`.

#### `video_feeder.py` — Mix Mode

Added `--mode mix` that sends alternating 20-frame blocks of real and fake frames on a single `stream_id: ff_mix`. Each block exactly fills one temporal window, so the Temporal Audit verdict flips between Authentic and Fake every ~2 seconds at 10 FPS. Useful for verifying the full pipeline (speed layer + batch layer + tally) without needing to wait for a video switch.

#### Ollama Integration — Real Mistral Enabled

Host system Ollama (v0.30.5) has `mistral:latest` (4.4 GB) and `gemma4:e4b` (9.6 GB) installed at `/var/lib/ollama/`. The Docker Ollama container previously used an empty `./ollama-data` volume (no models). Fixed by:
1. Changed Ollama Docker image from `ollama/ollama:0.1.32` to `ollama/ollama:latest`.
2. Mounted `/var/lib/ollama:/root/.ollama/models:ro` — the host's `blobs/` and `manifests/` directories match what the container expects under `models/`.
3. Set `MOCK_LLM=false` in `rag-agent` environment (was `${MOCK_LLM:-true}`).
4. RAG agent uses `mistral` only (hardcoded at `rag-agent/main.py:147`) — gemma4 is never called.

#### TypeScript Fix

`FrameData` is a TypeScript interface (type-only). Vite 8 (Rolldown bundler) requires `import type` for type-only imports. Fixed `LiveGraph.tsx` to use `import type { FrameData } from '../store'` instead of a value import.

### Status

Full pipeline operational: video_feeder → Kafka → Aggregation (retry) → Vision (trained EfficientNet+FFT) + RAG (real Mistral) → WebSocket → Dashboard (stable, sticky alerts, hover tooltip, flagged frames panel, mix mode). Temporal service also has retry loop and fires verdicts every 20 frames. Benchmark eval run (Step 2.5.6) ready to execute.
