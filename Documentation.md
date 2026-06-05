# ARDD-TP Development Log

## Last Updated: 2026-06-06 01:02 IST

---

## Completed Work

### Phase 1, Step 1 — Infrastructure ✅

**What was done:**
- Docker & Docker Compose installed on Arch Linux
- `docker-compose.yml` verified with Zookeeper, Kafka, and placeholder services
- `.env.example` exists; `.env` configured with secrets
- `test_infrastructure.py` passed
- `prepare_test_dataset.py` executed successfully

**Test dataset created:**
- 20 total samples (10 REAL, 10 FAKE)
- Located at `test_dataset/`
- Metadata: `test_dataset/metadata.json`
- Test payloads: `test_dataset/test_payloads/frame_payload.json`

**Verification:**
```bash
docker compose up -d zookeeper kafka
docker compose ps
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list
```

---

### Phase 1, Step 2 — Ingest Gateway ✅

**What was done:**
- Fixed `requirements.txt`: replaced `kafka-python==2.0.2` with `kafka-python-ng==2.2.3` (Python 3.14 compatibility)
- Created Python virtual environment at `ingest-gateway/.venv`
- Verified imports work: `python -c "import main; print('Import OK')"`
- OpenCV tested and working

**Fixes applied:**
- `kafka-python` was broken on Python 3.14 (`ModuleNotFoundError: No module named 'kafka.vendor.six.moves'`)
- Switched to `kafka-python-ng` which has proper Python 3.14 support

**Verification:**
```bash
cd ingest-gateway
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -c "import main; print('Import OK')"
```

---

## Current Status

- Phase 1, Steps 1-2: **COMPLETED**
- Phase 1, Step 3 (Vision Service): **NOT STARTED**
- Phase 1, Step 4 (RAG Context Agent): **NOT STARTED**
- Phase 1, Step 5 (Aggregation Service): **NOT STARTED**
- Phase 1, Step 6 (MLflow Telemetry): **NOT STARTED**
- Phase 1, Step 7 (WebSocket & React Dashboard): **NOT STARTED**

---

## Running Services

| Service | Status | Port |
|---------|--------|------|
| Zookeeper | Running | 2181 |
| Kafka | Running | 9092 |
| Vision Service | Not built | — |
| RAG Agent | Not built | — |
| Aggregation Service | Not built | — |
| MLflow | Not started | — |
| Frontend | Not built | — |

---

## Next Steps

Proceed to **Step 3 — Vision Service**:
1. Create `vision-service/` directory
2. Implement FastAPI app with `POST /infer` and `GET /health`
3. Add MTCNN face alignment
4. Implement EfficientNet + FFT dual-branch model
5. Test with prepared dataset
