# Schema Reference

## 1. Kafka Frame Payload

Published by the ingest gateway to the frame topic.

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "timestamp_ms": "integer",
  "payload": "base64-encoded bytes (JPEG frame)"
}
```

## 2. Vision Service Response

Returned by the FastAPI Vision Node after inference.

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "deepfake_score": "float (0.0–1.0)",
  "aligned": "boolean",
  "latency_ms": "integer"
}
```

## 3. RAG Audit Verdict

Returned by the LangChain Auditor after contextual verification.

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "verdict": "string (PASS | FAIL | UNKNOWN)",
  "matched_signature": "string | null",
  "confidence": "float (0.0–1.0)"
}
```

## 4. MLflow Telemetry Log

Logged per frame after aggregation.

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "deepfake_score": "float",
  "audit_verdict": "string",
  "latency_ms": "integer",
  "drift_flag": "boolean"
}
```

## 5. WebSocket Push Event

Broadcasted to the React dashboard after aggregation.

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "deepfake_score": "float",
  "verdict": "string",
  "alert": "boolean",
  "timestamp_ms": "integer"
}
```
