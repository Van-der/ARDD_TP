# API Specification

All internal services communicate over HTTP/REST. Schema field definitions are in `SCHEMA.md`. Security details are in `SECURITY.md`.

---

## 1. Authentication

### Internal Service-to-Service

All internal REST endpoints require an API key in the request header:

```
X-API-Key: <INTERNAL_API_KEY>
```

Missing or invalid key → `401 Unauthorized`.

### WebSocket

The WebSocket upgrade request requires a JWT bearer token:

```
Authorization: Bearer <token>
```

Tokens are obtained from `POST /auth/token` (§6). Missing, invalid, or expired token → `401 Unauthorized`.

---

## 2. Vision Service — `POST /infer`

Accepts a decoded frame and returns an inference result.

**Headers:** `X-API-Key`

**Request**
```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "timestamp_ms": "integer",
  "payload": "base64-encoded bytes (JPEG frame, max 2MB)"
}
```

**Response `200 OK`**
```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "deepfake_score": "float (0.0–1.0)",
  "aligned": "boolean",
  "latency_ms": "integer"
}
```

| Status | Meaning |
|---|---|
| `401` | Missing or invalid API key |
| `422` | Malformed payload, decode failure, or payload >2MB |
| `500` | Model inference error |
| `503` | OOM — retry failed; service temporarily unavailable |

---

## 3. RAG Context Agent — `POST /audit`

Accepts a vision result and returns a contextual audit verdict.

**Headers:** `X-API-Key`

**Request**
```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "deepfake_score": "float (0.0–1.0)"
}
```

**Response `200 OK`**
```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "audit_verdict": "string (PASS | FAIL | UNKNOWN)",
  "matched_signature": "string | null",
  "confidence": "float (0.0–1.0)"
}
```

| Status | Meaning |
|---|---|
| `401` | Missing or invalid API key |
| `422` | `deepfake_score` outside `[0.0, 1.0]` or missing fields |
| `503` | Ollama or vector store unavailable |
| `504` | Exceeded 150ms budget — Aggregation Service falls back to Vision-only |

---

## 4. Aggregation Service — `POST /aggregate`

Triggers the full Vision → RAG pipeline for a single frame. Primarily used for testing.

**Headers:** `X-API-Key`

**Request** — `FramePayload` (see `SCHEMA.md §1`).

**Response `200 OK`** — `AggregatedResult` (see `SCHEMA.md §4`).

| Status | Meaning |
|---|---|
| `401` | Missing or invalid API key |
| `502` | Vision Service returned an error; frame dropped |

---

## 5. Health Endpoints

All services expose a health check. No authentication required.

`GET /health`

**Response `200 OK`**
```json
{
  "status": "ok",
  "service": "string",
  "uptime_s": "integer"
}
```

**Response `503 Service Unavailable`** — service is up but a critical dependency (model, vector store, Kafka) is unreachable.

---

## 6. Auth — `POST /auth/token`

Issued by the Aggregation Service. Used by the React dashboard to obtain a JWT for WebSocket access.

**No authentication required on this endpoint.**

**Request**
```json
{
  "client_id": "string",
  "client_secret": "string"
}
```

**Response `200 OK`**
```json
{
  "access_token": "string (JWT)",
  "expires_in": 3600
}
```

| Status | Meaning |
|---|---|
| `401` | Invalid `client_id` or `client_secret` |

---

## 7. Kafka Message Contracts

| Topic | Producer | Consumer | Schema |
|---|---|---|---|
| `frames` | Ingest Gateway | Vision Service | `FramePayload` (`SCHEMA.md §1`) |

Kafka messages are not individually authenticated. Transport-level security (SASL_SSL) is used instead — see `SECURITY.md §3`.

---

## 8. WebSocket — `ws://<host>/stream`

**Headers:** `Authorization: Bearer <token>`

The Aggregation Service broadcasts `WebSocketPushEvent` (see `SCHEMA.md §6`) to all authenticated clients after each frame is processed.

- **Direction:** Server → Client only.
- **Reconnection:** Clients must re-authenticate and reconnect with exponential backoff. Server does not buffer missed events.
- **Token expiry:** Client must call `POST /auth/token` to refresh before the 1-hour expiry.
