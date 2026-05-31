# Technical Requirements Document

**Project:** ARDD-TP
**Version:** 1.0.0

## 1. Tech Stack

| Component | Technology |
|---|---|
| Message Broker | Apache Kafka |
| Vision Service | PyTorch, FastAPI (EfficientNet + FFT dual-branch) |
| RAG / Context Service | LangChain, FAISS/ChromaDB, Ollama/Mistral |
| MLOps & Telemetry | MLflow |
| Frontend | React, TypeScript, Zustand, WebSockets |
| Infrastructure | Docker, Docker Compose |

## 2. Performance & SLA

- **Latency:** End-to-end processing per frame must not exceed 200ms.
- **Throughput:** Must handle concurrent input from at least 3 Kafka topics.
- **Uptime:** All microservices must implement automatic restart policies via Docker Compose.

## 3. Module Specifications

- **Kafka Consumer:** Reads binary payloads, decodes to OpenCV tensors, forwards to Vision Service.
- **FastAPI Vision Node:** Exposes a POST endpoint; accepts tensors, runs MTCNN alignment, executes EfficientNet-FFT model.
- **LangChain Auditor:** Retrieves metadata via semantic search; applies strict refusal-based guardrails for unidentifiable inputs.

## 4. Conditional Branching & Future States

- **Face Alignment Failure:** If MTCNN fails to detect a face, Vision Service bypasses inference, logs an `unaligned_frame` flag to MLflow, and returns a neutral baseline score.
- **Service Timeout:** If the RAG service exceeds 500ms, the final audit resolves using only the Vision Service score to maintain SLA compliance.
- **Future State:** Transition from REST to gRPC for Kafka Consumer ↔ Vision Service communication to reduce serialization overhead.
