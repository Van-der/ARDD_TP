# Schema Reference

> **Field naming conventions used consistently across all schemas:**
> - `stream_id` — identifies the originating stream
> - `frame_index` — zero-based frame sequence number
> - `timestamp_ms` — Unix epoch milliseconds
> - `latency_ms` — processing duration in milliseconds
> - `deepfake_score` — float 0.0–1.0 from Vision Service
> - `audit_verdict` — string `PASS | FAIL | UNKNOWN` from RAG / Aggregation

---

## 1. Kafka Frame Payload

Published by the Ingest Gateway to the `frames` Kafka topic.

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "timestamp_ms": "integer",
  "payload": "base64-encoded bytes (JPEG frame)"
}
```

## 2. Vision Service Result

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
  "audit_verdict": "string (PASS | FAIL | UNKNOWN)",
  "matched_signature": "string | null",
  "confidence": "float (0.0–1.0)"
}
```

## 4. Aggregated Result

Produced by the Aggregation Service by merging Vision Result and RAG Audit Verdict. This is the canonical payload consumed by MLflow and the WebSocket broadcaster.

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "timestamp_ms": "integer",
  "deepfake_score": "float (0.0–1.0)",
  "audit_verdict": "string (PASS | FAIL | UNKNOWN)",
  "matched_signature": "string | null",
  "alert": "boolean",
  "rag_used": "boolean",
  "latency_ms": "integer",
  "drift_flag": "boolean"
}
```

> `rag_used: false` when the RAG service timed out and the verdict was resolved from Vision score alone.

## 5. MLflow Telemetry Log

Logged per frame from the `AggregatedResult`.

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

## 6. WebSocket Push Event

Broadcast to the React dashboard from the `AggregatedResult`.

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "deepfake_score": "float",
  "audit_verdict": "string",
  "alert": "boolean",
  "timestamp_ms": "integer"
}
```

---

## 7. Ground Truth Label

Published to the Kafka `labels` topic by an operator or auto-labelling service. Used by the Aggregation Service to feed the MLflow drift monitor.

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "label": "string (REAL | FAKE)",
  "labelled_by": "string (OPERATOR | AUTO_LABEL_SERVICE)",
  "timestamp_ms": "integer"
}
```

> Only `label: "REAL"` frames are used for drift detection. See `TRD.md §4.4`.

---

## 8. Webhook Alert Payload

POSTed by the Aggregation Service to `WEBHOOK_URL` when `alert: true` is set on an `AggregatedResult`.

```json
{
  "event": "DEEPFAKE_ALERT",
  "stream_id": "string",
  "frame_index": "integer",
  "final_score": "float (0.0–1.0)",
  "audit_verdict": "string (PASS | FAIL | UNKNOWN)",
  "matched_signature": "string | null",
  "consecutive_alert_frames": "integer",
  "timestamp_ms": "integer"
}
```

**Headers sent with the request:**
```
Content-Type: application/json
Authorization: Bearer <WEBHOOK_TOKEN>
```

> Delivery is retried up to 3 times with exponential backoff. Failed deliveries are logged as `webhook_delivery_failed` — see `ERROR_HANDLING.md §5`.

---

## 9. Threat Signature Database

The RAG Context Agent queries a vector store (FAISS/ChromaDB) backed by a structured threat signature registry. Each signature represents a known synthetic identity or deepfake artefact pattern.

### 9.1 Threat Signature Record

Stored in ChromaDB / FAISS as a document with metadata. The `embedding` is generated from `description` + `artefact_tags` at index time.

```json
{
  "signature_id": "string (UUID)",
  "label": "string — human-readable name, e.g. 'FaceSwap-v2-GAN'",
  "description": "string — natural language description used for embedding",
  "artefact_tags": ["string"] ,
  "source": "string (MANUAL | AUTO_DETECTED | IMPORTED)",
  "severity": "string (LOW | MEDIUM | HIGH | CRITICAL)",
  "created_at": "integer (Unix epoch ms)",
  "updated_at": "integer (Unix epoch ms)",
  "active": "boolean"
}
```

### 9.2 Signature Match Result

Returned by the vector store query before the LLM verdict step.

```json
{
  "signature_id": "string",
  "label": "string",
  "similarity_score": "float (0.0–1.0)",
  "severity": "string"
}
```

> A match is considered relevant when `similarity_score >= 0.75`. Below this threshold the RAG agent treats the result as no match and returns `audit_verdict: "UNKNOWN"`.

### 9.3 Artefact Tag Vocabulary

Standardised tags used in `artefact_tags` to enable consistent retrieval:

| Tag | Description |
|---|---|
| `spectral_anomaly` | Irregular frequency-domain pattern (FFT artefact) |
| `texture_inconsistency` | Skin texture mismatch between face and background |
| `boundary_blending` | Visible blending artefact at face boundary |
| `eye_reflection_mismatch` | Asymmetric or absent corneal reflections |
| `temporal_flicker` | Frame-to-frame identity instability |
| `compression_artefact` | Re-compression pattern from GAN post-processing |
