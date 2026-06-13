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

## Phase 3 — Performance & Scalability

Improvements to throughput and inter-service communication efficiency.

| Item | Description |
|---|---|
| gRPC transport | Replace REST between Kafka Consumer ↔ Vision Service to reduce serialization overhead |
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
| Confidence calibration | Post-hoc calibration layer on Vision scores to reduce false-positive rate |

---

## Phase 5 — Hardening & Compliance

Production readiness and audit trail.

| Item | Description |
|---|---|
| Stream segment archival | Persist flagged segments (score >0.90 for ≥5 frames) to object storage |
| Webhook alert integrations | Connect threat escalation webhooks to Slack, PagerDuty, or SIEM |
| Role-based dashboard access | Auth layer on React dashboard for compliance environments |
| End-to-end latency audit | Instrument each hop with OpenTelemetry traces to validate 200ms SLA per frame |
| mTLS | Certificate management and mutual TLS on all internal service links |
