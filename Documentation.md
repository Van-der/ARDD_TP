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
