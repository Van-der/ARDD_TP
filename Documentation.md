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
Ready to begin **Phase 2 (Lambda Architecture Temporal Batch Layer)** starting with modifying the Vision Service to expose the 1024-d feature vector.
