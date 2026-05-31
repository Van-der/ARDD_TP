# Roadmap

## Phase 1 — Current Implementation (v1.0.0)

The baseline system as specified in `PRD.md` and `TRD.md`.

| Component | Status | Notes |
|---|---|---|
| Ingest Gateway (RTSP/HTTP → Kafka) | Planned | OpenCV + FFmpeg, dynamic FPS downsampling |
| Kafka broker (single `frames` topic) | Planned | 3 concurrent topic capacity |
| Vision Service (EfficientNet + FFT) | Planned | MTCNN alignment, FastAPI endpoint |
| RAG Context Agent (LangChain + FAISS) | Planned | Ollama/Mistral, 150ms timeout budget |
| Aggregation Service | Planned | Sequential Vision → RAG, fallback logic |
| MLflow telemetry logging | Planned | Per-frame scores, drift detection |
| WebSocket broadcaster | Planned | Real-time push to React dashboard |
| React dashboard (Zustand) | Planned | Live graphs, compliance alerts, stale-data banner |
| Docker Compose deployment | Planned | All services with auto-restart policies |

---

## Phase 2 — Performance & Scalability

Improvements to throughput and inter-service communication efficiency.

| Item | Description |
|---|---|
| gRPC transport | Replace REST between Kafka Consumer ↔ Vision Service to reduce serialization overhead |
| Multi-topic ingestion | Expand beyond 3 Kafka topics for multi-stream support |
| Vision Service horizontal scaling | Run multiple Vision Service replicas behind a load balancer |
| ChromaDB persistent store | Replace in-memory FAISS with persistent ChromaDB for RAG knowledge base |

---

## Phase 3 — Advanced Analytics

Temporal analysis and cross-stream intelligence.

| Item | Description |
|---|---|
| Apache Flink aggregation | Migrate Aggregation Service logic to Flink for windowed stream processing and temporal analysis |
| Graph threat intelligence DB | Link recurring synthetic identities across independent streams |
| Automated retraining pipeline | Trigger fine-tuning job automatically when drift flag is raised; push new weights to Vision Service without downtime |
| Confidence calibration | Post-hoc calibration layer on Vision scores to reduce false-positive rate |

---

## Phase 4 — Hardening & Compliance

Production readiness and audit trail.

| Item | Description |
|---|---|
| Stream segment archival | Persist flagged segments (score >0.90 for ≥5 frames) to object storage |
| Webhook alert integrations | Connect threat escalation webhooks to Slack, PagerDuty, or SIEM |
| Role-based dashboard access | Auth layer on React dashboard for compliance environments |
| End-to-end latency audit | Instrument each hop with OpenTelemetry traces to validate 200ms SLA per frame |
