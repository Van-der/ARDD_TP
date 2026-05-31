# ARDD-TP

Real-time video stream analysis pipeline with AI-powered deepfake detection, contextual verification, and live telemetry dashboard.

## Stack

| Layer | Technology |
|---|---|
| Ingestion | Apache Kafka |
| Vision Model | PyTorch — EfficientNet + FFT dual-branch |
| Context Agent | LangChain (RAG, FAISS/ChromaDB, Ollama/Mistral) |
| Experiment Tracking | MLflow |
| Frontend | React + TypeScript + Zustand |
| Transport | WebSocket |
| Infrastructure | Docker, Docker Compose |

## How It Works

1. Video stream is chunked into frames and published to Kafka.
2. Vision Service runs MTCNN alignment and dual-branch inference on each frame.
3. RAG agent verifies results against known threat signatures.
4. Scores and telemetry are logged to MLflow.
5. Aggregated results are pushed via WebSocket to the React dashboard.

## Resilience

- **Disconnection:** Exponential backoff reconnection with stale-data indicator.
- **Model Drift:** Auto-flag for retraining when confidence drops below 60%.
- **Face Alignment Failure:** Bypasses inference and logs `unaligned_frame` to MLflow.
- **RAG Timeout:** Falls back to Vision Service score alone if context service exceeds 500ms.

## Docs

- [`FLOW.md`](./FLOW.md) — System flow and sequence diagrams
- [`PRD.md`](./PRD.md) — Product requirements
- [`TRD.md`](./TRD.md) — Technical requirements and module specs
- [`SCHEMA.md`](./SCHEMA.md) — Message and event schema reference
