# ARDD-TP

Real-time video stream analysis pipeline with AI-powered deepfake detection, contextual verification, and live telemetry dashboard.

## Stack

| Layer | Technology |
|---|---|
| Ingest Gateway | Python, OpenCV, FFmpeg |
| Message Broker | Apache Kafka |
| Vision Service | PyTorch — EfficientNet + FFT dual-branch, FastAPI |
| Context Agent | LangChain (RAG, FAISS/ChromaDB, Ollama/Mistral) |
| Aggregation Service | Python, FastAPI |
| Experiment Tracking | MLflow |
| Frontend | React + TypeScript + Zustand |
| Transport | WebSocket (JWT auth) |
| Infrastructure | Docker, Docker Compose |

## How It Works

1. Ingest Gateway decodes a live RTSP/HTTP video feed, extracts frames, and publishes them to the Kafka `frames` topic.
2. Vision Service consumes each frame, runs MTCNN alignment, and executes the EfficientNet+FFT dual-branch model.
3. Aggregation Service receives the vision result and calls the RAG Context Agent sequentially (RAG uses the vision score as query context).
4. Aggregation Service merges both results into a canonical `AggregatedResult` payload.
5. Telemetry is logged to MLflow; the aggregated payload is broadcast via WebSocket to the React dashboard.

## Scoring

```
deepfake_score  = 0.6 · spatial_score + 0.4 · frequency_score
final_score     = clamp(deepfake_score · (1 + 0.15 · rag_boost), 0.0, 1.0)
```

`rag_boost` is applied only when the RAG agent returns `audit_verdict: "FAIL"`.

## Resilience

- **RAG Timeout:** Falls back to Vision score alone (`audit_verdict: "UNKNOWN"`) if RAG exceeds **100ms**, keeping end-to-end latency within the **200ms SLA** (Vision ≤80ms + RAG ≤100ms + overhead ≤20ms).
- **Face Alignment Failure:** Bypasses inference, returns neutral score `0.5`, logs `unaligned_frame` to MLflow.
- **Model Drift:** Auto-flags for retraining when rolling confidence average drops below 60% on ground-truth-labelled real frames.
- **WebSocket Disconnect:** Exponential backoff reconnection with stale-data indicator.
- **MLflow Unavailable:** Buffers up to 100 telemetry entries in memory, flushes on recovery.
- **Throughput Overload:** Dynamically downsamples from 30 FPS to 5 FPS.

## Security

- Internal REST APIs authenticated via `X-API-Key` header.
- WebSocket access requires a JWT bearer token (HS256, 1hr expiry).
- Kafka transport secured with SASL_SSL.
- All secrets injected via environment variables; see `.env.example`.

## Docs

| File | Description |
|---|---|
| [`ARCHITECTURE.md`](./PLAN/ARCHITECTURE.md) | Component overview, service discovery, startup order |
| [`FLOW.md`](./PLAN/FLOW.md) | System flow and sequence diagrams |
| [`PRD.md`](./PLAN/PRD.md) | Product requirements |
| [`TRD.md`](./PLAN/TRD.md) | Technical requirements, module specs, scoring algorithm |
| [`API_SPEC.md`](./PLAN/API_SPEC.md) | Service endpoints, authentication, message contracts |
| [`SCHEMA.md`](./PLAN/SCHEMA.md) | Message, event, and threat signature database schemas |
| [`ERROR_HANDLING.md`](./PLAN/ERROR_HANDLING.md) | All failure scenarios and responses |
| [`SECURITY.md`](./PLAN/SECURITY.md) | Security specifications, threat model, secrets management |
| [`TESTING.md`](./PLAN/TESTING.md) | Testing strategy, accuracy benchmarks, performance benchmarks |
| [`ROADMAP.md`](./PLAN/ROADMAP.md) | Current implementation vs. future phases |
