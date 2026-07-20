# Roadmap

## Phase 1 — Core Pipeline MVP (v1.0.0) ✅ Done

The baseline system as specified in `PRD.md` and `TRD.md`.

| Component | Status | Notes |
|---|---|---|
| Ingest Gateway (RTSP/HTTP → Kafka) | ✅ Done | OpenCV + FFmpeg, dynamic FPS downsampling |
| Kafka broker (single `frames` topic) | ✅ Done | 3 concurrent topic capacity |
| Vision Service (EfficientNet-B4 + FFT) | ✅ Done | MTCNN alignment, FastAPI endpoint |
| RAG Context Agent (LangChain + FAISS) | ✅ Done | Ollama/Mistral, 100ms timeout budget |
| Aggregation Service | ✅ Done | Sequential Vision → RAG, fallback logic |
| MLflow telemetry logging | ✅ Done | Per-frame scores, drift detection |
| WebSocket broadcaster | ✅ Done | Real-time push to React dashboard |
| React dashboard (Zustand) | ✅ Done | Live graphs, compliance alerts, stale-data banner |
| Docker Compose deployment | ✅ Done | All services with auto-restart policies |

---

## Phase 2 — Lambda Architecture: Temporal Batch Layer

Introduces the **Batch Layer** alongside the existing Speed Layer, forming a true Lambda Architecture for dual-SLA deepfake analysis.

### Architecture Upgrade

The system evolves from a simple sequential pipeline to a **split-stream Lambda pipeline**. Both layers independently consume from the same Kafka `frames` topic:

```
             ┌──────────────────────────────────┐
             │    Kafka `frames` topic           │
             └──────────┬───────────────┬────────┘
                        │               │
             ┌──────────▼──┐     ┌──────▼──────────┐
             │Speed Layer  │     │  Batch Layer     │
             │(Vision Svc) │     │ (Temporal Svc)   │
             │ 200ms SLA   │     │ ~0.67s cycle     │
             └──────────┬──┘     └──────┬───────────┘
                        │               │
             ┌──────────▼───────────────▼────────┐
             │         Aggregation Service        │
             └────────────────────────────────────┘
```

### Temporal Service Design

- **Model:** ResNext50+LSTM — `Naman712/Deep-fake-detection` (87% acc), weights in `temporal-service/`
- **Input:** 20 raw JPEG frames → decode to 112×112 RGB → ImageNet norm → `[1, 20, 3, 112, 112]` tensor
- **Window:** 20-frame tumbling (deque cleared after each inference) — ~0.67s at 30 FPS
- **Score:** `temporal_score = F.softmax(logits, dim=1)[0][0].item()` (fake class probability)
- **Consumer group:** `temporal-service-group` (separate from Aggregation Service consumer)

> **Future scope:** Sliding window (overlapping inference every K frames) planned once tumbling baseline is stable.

### Implementation Steps (strict order)

| Step | Task | Notes |
|---|---|---|
| 2.1 | **Aggregation aiokafka consumer loop** — `frames` topic pipeline driver | Replaces test-only `POST /aggregate`; Aggregation owns Speed Layer pipeline |
| 2.2 | **Build Temporal Service** — aiokafka consumer + 20-frame deque buffer | `deque(maxlen=20)` per `stream_id`; tumbling window; subscribes to `frames` topic |
| 2.3 | **docker-compose.yml** — wire `temporal-service` | Port 8004, `AGGREGATION_URL`, health check, `depends_on: kafka` |
| 2.4 | **Integrate ResNext50+LSTM model** — reconstruct `modeling.py` | `DeepFakeDetector` class + `load_model`; fallback to random weights if `.pt` missing |
| 2.5 | **Buffer resilience** — padding and sparse fallback | N<20: zero-pad; N<6: `UNKNOWN` without inference; `low_confidence_flag` |
| 2.6 | **Aggregation temporal path** — receive `TemporalAuditResult`, wire health status | `POST /temporal_audit`; `temporal_service_status` field in health |
| 2.7 | **React Dashboard** — wire `temporal_service_status` | Audit Panel already built in Phase 1; only missing: status field from health endpoint |
| 2.8 | **Security fixes** — `weights_only=True`, JWT subprotocol, VITE_ env vars, Kafka SASL_PLAINTEXT | All 4 in Phase 2; full TLS (SASL_SSL) deferred to Phase 5 |
| 2.9 | **RAG fix** — replace `SimpleHashEmbeddings` with `sentence-transformers` | `all-MiniLM-L6-v2`; fixes meaningless 0.75 threshold |
| 2.10 | **Tests** — Temporal Service unit + integration suite | Buffer fill, tensor shape, padding, sparse fallback, inference, schema validation |

### SLA & Resilience

| Failure | Impact | Response |
|---|---|---|
| Temporal Service crash | Speed Layer **unaffected** | Dashboard Audit Panel shows "Temporal Audit Unavailable — Relying on Spatial heuristics" |
| Buffer incomplete (`N < 20`) | Reduced confidence | Zero-pad to 20 frames; set `low_confidence_flag: true` |
| Buffer too sparse (`N < 6`) | Insufficient data | Skip inference; return `temporal_verdict: "UNKNOWN"` |
| Frame gaps in window | Sequence discontinuity | Linear interpolation from adjacent tensors; log `frames_interpolated` |

### New Service

- **Temporal Service** — `./temporal-service/`
- **Port:** 8004
- **Endpoints:** `GET /health`, `GET /batch_status`

---

## Phase 2.5 — Speed Layer Training ✅ Done

Replaces the ImageNet-pretrained EfficientNet-B4 + heuristic FFT branch with a fully trained dual-branch model on FaceForensics++ data.

| Component | Status | Notes |
|---|---|---|
| Dataset | ✅ Done | FF++ c23, 1000 real + 2000 fake; 50+50 test sample ready |
| `vision-service/modeling.py` — `FftMlp` class | ✅ Done | 64→32→1 + Dropout(0.3) |
| `extract_faces.py` | ✅ Done | MTCNN offline crop extraction, every 5th frame |
| `train_vision.py` | ✅ Done | EfficientNet-B4 fine-tune + FFT MLP training |
| `vision-service/main.py` — replace heuristic | ✅ Done | Loads trained `FftMlp` + learned `alpha` fusion; M13 adds isotonic calibration on top |
| `docker-compose.yml` — GPU passthrough | ✅ Done | `nvidia-container-toolkit`, `deploy.resources.reservations.devices` on vision-service |
| FF++ benchmark results | ✅ Done | AUC 0.9987 pre-calibration; 0.99780→0.99791 post-calibration (M13) |

**Design decisions locked 2026-06-17:** radial FFT bins (64-dim), logistic regression fusion (learned α), safe augmentation, official 720/140/140 split, every-5th-frame sampling, weighted loss `[2.0, 1.0]`, batch=16, EfficientNet LR=1e-4 + MLP LR=1e-3, cosine annealing, 10 epochs, state_dict save, no early stopping, MLP dropout(0.3).

---

## Phase 3 — Performance & Scalability

Improvements to throughput and inter-service communication efficiency.

| Item | Description |
|---|---|
| ~~gRPC transport~~ — evaluated, declined | Profiling (`PLAN/PROFILING.md`) showed REST/serialization overhead is only ~7-10ms (~7% of round trip); the real bottleneck under real LLM inference is RAG's tinyllama call (~1s median). gRPC migration would not meaningfully help — see M1 in the implementation plan for the revised scope (decouple RAG from the blocking per-frame path instead). |
| Multi-topic ingestion | Expand beyond 3 Kafka topics for multi-stream support |
| Vision Service horizontal scaling | Run multiple Vision Service replicas behind a load balancer |
| ChromaDB persistent store | Replace in-memory FAISS with persistent ChromaDB for RAG knowledge base |
| Redis feature buffer | Replace in-memory Python deque with Redis for multi-replica Temporal Service support |

---

## Phase 4 — Advanced Analytics

Temporal analysis and cross-stream intelligence.

| Item | Description |
|---|---|
| Apache Flink aggregation | Migrate Aggregation Service logic to Flink for windowed stream processing |
| Graph threat intelligence DB | Link recurring synthetic identities across independent streams |
| Automated retraining pipeline | Trigger fine-tuning job automatically when drift flag is raised; push new weights to Vision Service without downtime |
| Confidence calibration ✅ | Isotonic regression on the fused score, fit against the same val split used for fusion — measured live: AUC unchanged (0.9978→0.9979), false-positive rate at the 0.90 alert threshold cut from 0.29% to 0.10% |

---

## Phase 5 — Hardening & Compliance

Production readiness and audit trail.

| Item | Description |
|---|---|
| Stream segment archival ✅ | Local MinIO (S3-compatible, no real S3 account needed); one JPEG per alert streak (not every alerted frame) uploaded to `s3://ardd-segments/{stream_id}/frame_{frame_index}.jpg` |
| Webhook alert integrations ✅ | Multi-target fan-out (`WEBHOOK_TARGETS` JSON array) to a local demo receiver (generic format) and/or a real Slack incoming webhook (`format: "slack"`) — no PagerDuty/SIEM account needed for a local college-project deployment |
| Role-based dashboard access ✅ | Hardcoded admin/viewer role pairs baked into JWT claims (not a persistent user store); `require_role()` gates admin-only endpoints (403 vs 401); frontend hides admin UI client-side from the decoded role claim |
| End-to-end latency audit ✅ | Local `otel-collector` + Jaeger (no cloud APM); per-hop spans on every service |
| mTLS ✅ | Local self-signed CA (`scripts/gen_certs.sh`); full mTLS on Vision/RAG/Temporal, TLS-only (browser-facing) on Aggregation Service, SASL_SSL on Kafka; MLflow/Ollama/webhook targets excluded by design (no TLS support / third-party / arbitrary external endpoint) |
