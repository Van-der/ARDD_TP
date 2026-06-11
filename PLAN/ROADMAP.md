# Roadmap

## Phase 1 — Core Pipeline MVP (v1.0.0) ✅ In Progress

The baseline system as specified in `PRD.md` and `TRD.md`.

| Component | Status | Notes |
|---|---|---|
| Ingest Gateway (RTSP/HTTP → Kafka) | ✅ Done | OpenCV + FFmpeg, dynamic FPS downsampling |
| Kafka broker (single `frames` topic) | ✅ Done | 3 concurrent topic capacity |
| Vision Service (EfficientNet-B4 + FFT) | ✅ Done | MTCNN alignment, FastAPI endpoint |
| RAG Context Agent (LangChain + FAISS) | ✅ Done | Ollama/Mistral, 100ms timeout budget |
| Aggregation Service | ✅ Done | Sequential Vision → RAG, fallback logic |
| MLflow telemetry logging | ⏳ Next | Per-frame scores, drift detection |
| WebSocket broadcaster | ✅ Done | Real-time push to React dashboard |
| React dashboard (Zustand) | Planned | Live graphs, compliance alerts, stale-data banner |
| Docker Compose deployment | ✅ Done | All services with auto-restart policies |

---

## Phase 2 — Lambda Architecture: Temporal Batch Layer

Introduces the **Batch Layer** alongside the existing Speed Layer, forming a true Lambda Architecture for dual-SLA deepfake analysis.

### Architecture Upgrade

The system evolves from a simple sequential pipeline to a **split-stream Lambda pipeline**:

```
             ┌──────────────────────────────────┐
             │           Kafka Broker            │
             └──────────┬───────────────┬────────┘
                        │               │
             ┌──────────▼──┐     ┌──────▼──────────┐
             │Speed Layer  │     │  Batch Layer     │
             │(Vision Svc) │     │ (Temporal Svc)   │
             │ 200ms SLA   │     │  30s audit cycle │
             └──────────┬──┘     └──────┬───────────┘
                        │               │
             ┌──────────▼───────────────▼────────┐
             │         Aggregation Service        │
             └────────────────────────────────────┘
```

### Implementation Steps (strict order)

| Step | Task | Notes |
|---|---|---|
| 2.1 | **Modify Vision Service** — extract and expose `feature_vector` | Flattened 1024-d tensor from penultimate EfficientNet-B4 layer; included in `VisionResult`; base64-encoded for Kafka |
| 2.2 | **Update Kafka schema** — `VisionResult` gains `feature_vector` field | `numpy.float32.tobytes()` → base64; Temporal consumer decodes with `numpy.frombuffer()` |
| 2.3 | **Build Temporal Service** — Kafka consumer + in-memory feature buffer | Python deque (maxlen=900); print "Buffer Full: 900 frames" every 30s then clear |
| 2.4 | **Integrate pre-trained model** — drop in LSTM/ViT weights | DFDC champion LSTM *or* TimeSformer/ViT; run on `[900, 1024]` tensor; output `temporal_score` |
| 2.5 | **Implement buffer resilience** — padding and interpolation | Zero-pad incomplete tensors; linear interpolation for frame gaps; `low_confidence_flag` |
| 2.6 | **Aggregation integration** — receive and merge `TemporalAuditResult` | Batch path runs independently of Speed path; merge into periodic WebSocket event |
| 2.7 | **React Dashboard Audit Panel** — 30s periodic temporal report UI | "Temporal Analysis of last 30s: 98% Natural Continuity. No micro-jitters detected." |
| 2.8 | **Tests** — Temporal Service unit + integration suite | Buffer fill, tensor shape, padding, interpolation, LSTM inference, batch audit schema |

### Temporal Model Strategy

| Strategy | Description |
|---|---|
| **Pre-trained Models** | Grab open-source drop-in weights (e.g., DFDC LSTM from Kaggle champions, TimeSformer/ViT fine-tuned on Celeb-DF, LipForensics). |
| **Training the LSTM Head** | Since the Vision Service extracts 1024-d features, the LSTM head is tiny (2-5 million parameters) and can be trained in hours on a single consumer GPU using FaceForensics++. |

### SLA & Resilience

| Failure | Impact | Response |
|---|---|---|
| Temporal Service crash | Speed Layer **unaffected** | Dashboard Audit Panel shows "Temporal Audit Unavailable — Relying on Spatial heuristics" |
| Buffer incomplete (`N < 900`) | Reduced confidence | Zero-pad to `[900, 1024]`; set `low_confidence_flag: true` |
| Buffer too sparse (`N < 300`, `< 10s`) | Insufficient data | Skip inference; return `temporal_verdict: "UNKNOWN"` |
| Frame gaps in window | Sequence discontinuity | Linear interpolation from adjacent feature vectors |

### New Service

- **Temporal Service** — `./temporal-service/`
- **Port:** 8004
- **Endpoints:** `GET /health`, `GET /batch_status`, `POST /flush`

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
