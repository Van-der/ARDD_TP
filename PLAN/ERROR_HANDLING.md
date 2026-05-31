# Error Handling

All failure scenarios, their detection points, responses, and logging behaviour.

---

## 1. Ingest Gateway

| Scenario | Detection | Response | Logged |
|---|---|---|---|
| Stream source unreachable | Connection timeout on RTSP/HTTP | Retry with exponential backoff (max 5 attempts); emit `stream_error` alert | Yes — MLflow |
| Frame decode failure | OpenCV/FFmpeg exception | Drop frame, increment `decode_error` counter | Yes — MLflow |
| Throughput overload | Downstream Kafka lag detected | Downsample FPS from 30 → 5 | Yes — MLflow |
| Kafka publish failure | Producer `send()` exception | Retry up to 3 times; drop frame and log `kafka_publish_error` if all retries fail | Yes — MLflow |
| Stream source reconnect loop | >5 consecutive backoff failures | Halt gateway, emit `gateway_fatal` alert, require manual restart | Yes — MLflow |

---

## 2. Kafka Broker

| Scenario | Detection | Response | Logged |
|---|---|---|---|
| Broker unavailable | Producer connection refused | Ingest Gateway retries with backoff; Vision Service pauses consumption | Yes — service logs |
| Message deserialization error | Consumer decode exception | Drop message, log `kafka_deserialize_error` | Yes — MLflow |
| Consumer lag exceeds threshold | Lag monitor (>500 pending messages) | Ingest Gateway activates FPS downsampling; alert emitted | Yes — MLflow |
| Topic partition unavailable | Consumer assignment error | Consumer pauses, retries partition assignment after 5s | Yes — service logs |

---

## 3. Vision Service

| Scenario | Detection | Response | Logged |
|---|---|---|---|
| MTCNN face alignment failure | No face detected in frame | Bypass inference; return `aligned: false`, `deepfake_score: 0.5` (neutral baseline) | Yes — MLflow `unaligned_frame` flag |
| Malformed payload | Decode exception on base64/JPEG | Return `422 Unprocessable Entity` | Yes — service logs |
| Model inference error | PyTorch runtime exception | Return `500 Internal Server Error`; Aggregation Service drops frame | Yes — MLflow |
| Out-of-memory (OOM) | CUDA/CPU OOM exception | Release tensor cache, retry once at half batch size; return `503` if retry fails | Yes — MLflow |
| Model weights missing / corrupt | File load exception at startup | Service fails to start; Docker Compose restart policy triggers reload | Yes — service logs |
| GPU unavailable | CUDA device not found | Fall back to CPU inference; log `cpu_fallback` warning | Yes — MLflow |

---

## 4. RAG Context Agent

| Scenario | Detection | Response | Logged |
|---|---|---|---|
| Timeout (>150ms) | Aggregation Service deadline exceeded | Aggregation resolves with Vision score only; `audit_verdict: "UNKNOWN"`, `rag_used: false` | Yes — MLflow |
| No matching signature | FAISS/ChromaDB returns empty result | Return `audit_verdict: "UNKNOWN"`, `matched_signature: null` | No |
| LLM (Ollama) unavailable | HTTP connection error | Return `503 Service Unavailable`; treated as timeout by Aggregation Service | Yes — service logs |
| Unidentifiable input | Guardrail triggered | Return `audit_verdict: "UNKNOWN"`, `confidence: 0.0` | Yes — MLflow |
| Vector store unavailable | FAISS/ChromaDB connection error | Return `503`; treated as timeout by Aggregation Service | Yes — service logs |
| LLM returns malformed response | JSON parse error on LLM output | Return `audit_verdict: "UNKNOWN"`, `confidence: 0.0` | Yes — service logs |

---

## 5. Aggregation Service

| Scenario | Detection | Response | Logged |
|---|---|---|---|
| Vision Service unavailable | HTTP connection error | Drop frame; emit `pipeline_error` to WebSocket | Yes — MLflow |
| Vision Service returns 500 | HTTP 500 response | Drop frame; emit `pipeline_error` to WebSocket | Yes — MLflow |
| RAG timeout | Deadline exceeded (150ms) | Resolve with Vision score only (`rag_used: false`) | Yes — MLflow |
| Consecutive high-score frames (≥5, score >0.90) | Rolling window counter | Trigger webhook alert; archive stream segment | Yes — MLflow `alert: true` |
| Drift detected | MLflow moving average <60% confidence | Flag model weights for retraining | Yes — MLflow `drift_flag: true` |
| MLflow unavailable | HTTP connection error on log call | Buffer up to 100 telemetry entries in memory; retry flush every 10s | Yes — local buffer |
| WebSocket broadcast failure | Send exception on active connection | Remove dead connection from pool; continue broadcasting to remaining clients | No |
| Webhook delivery failure | HTTP error on alert POST | Retry up to 3 times with backoff; log `webhook_delivery_failed` | Yes — service logs |

---

## 6. WebSocket / Frontend

| Scenario | Detection | Response | Logged |
|---|---|---|---|
| WebSocket disconnect | Connection close event | Zustand freezes last known state; exponential backoff reconnection | No |
| Stale data | Reconnection in progress | Display "Stale Data" warning banner in UI | No |
| Reconnection failure (max retries) | Backoff exhausted | Display persistent error state; require manual refresh | No |
| Received malformed event | JSON parse error on incoming message | Discard message; log warning to browser console | No |
