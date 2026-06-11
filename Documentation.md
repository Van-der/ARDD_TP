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
