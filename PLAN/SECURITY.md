# Security Specifications

---

## 1. Threat Model

| Asset | Threat | Mitigation |
|---|---|---|
| Frame payloads in Kafka | Interception / tampering | TLS on Kafka broker (SASL_SSL); internal network only |
| Internal REST APIs | Unauthorized access | Shared secret (API key) on all inter-service calls |
| WebSocket endpoint | Unauthorized dashboard access | JWT bearer token on WebSocket upgrade handshake |
| MLflow server | Unauthorized metric writes | API key auth; MLflow not exposed outside Docker network |
| Threat signature DB | Poisoning / unauthorized writes | Write access restricted to admin role; reads are read-only |
| Container images | Supply chain attack | Pin all base image digests; no `latest` tags in production |
| Secrets (API keys, tokens) | Leakage via env or logs | Injected via Docker secrets / env file excluded from VCS |

---

## 2. Authentication

See `API_SPEC.md §6` for per-endpoint auth details.

### 2.1 Internal Service-to-Service (API Key)

All internal REST calls (Aggregation → Vision, Aggregation → RAG) use a shared **API key** passed in the `X-API-Key` header. The key is injected at runtime via environment variable `INTERNAL_API_KEY`.

- Requests missing or presenting an invalid key receive `401 Unauthorized`.
- The key is rotated per deployment environment (dev / staging / prod).

### 2.2 WebSocket Dashboard (JWT)

The React dashboard authenticates the WebSocket upgrade request by passing a **JWT bearer token** as the `Sec-WebSocket-Protocol` subprotocol value. (Standard HTTP `Authorization` headers are not forwarded on WebSocket upgrades in most browsers.)

- Token issued by the Aggregation Service on `POST /auth/token` (see `API_SPEC.md §6`).
- Server calls `await websocket.accept(subprotocol=token)` after validation; any request with an invalid token is rejected with `1008 Policy Violation` before the WebSocket handshake completes.
- Token expiry: **1 hour**. Client must re-authenticate on expiry.
- Algorithm: **HS256**. Secret injected via `JWT_SECRET` environment variable (minimum 32 bytes enforced at startup).

### 2.3 MLflow

MLflow is accessible only within the `ardd_net` Docker network. No external port is exposed. The Aggregation Service authenticates with a static `MLFLOW_TRACKING_TOKEN` env var.

> **Note:** MLflow OSS does not enforce bearer tokens natively. Production deployments should place MLflow behind an authenticated reverse proxy (nginx + htpasswd or OAuth2 proxy). See `SEC-3` in `TaskTo.md`.

### 2.4 Rate Limiting

`POST /auth/token` is rate-limited per client IP to **20 requests per 60-second window** (in-memory, resets on restart). Requests exceeding the limit receive `429 Too Many Requests`. Disabled in test environments via the `TESTING` env var.

---

## 3. Transport Security

| Link | Protocol | Encryption |
|---|---|---|
| Video source → Ingest Gateway | RTSP / HTTP | TLS required in production; plaintext allowed in dev |
| Ingest Gateway → Kafka | SASL_PLAINTEXT | SASL PLAIN credentials enforced; plaintext channel within Docker bridge (SASL_SSL upgrade deferred to Phase 5) |
| Aggregation Service → Kafka | SASL_PLAINTEXT | Same as above |
| Temporal Service → Kafka | SASL_PLAINTEXT | Same as above |
| Vision / RAG / Aggregation (internal) | HTTP | Plaintext within Docker bridge network (TLS in Phase 5) |
| Aggregation → React Dashboard | WebSocket (ws://) | Plaintext in dev; WSS with TLS deferred to Phase 5 |
| Aggregation → MLflow | HTTP | Plaintext within Docker bridge network |
| Aggregation → Webhook | HTTPS | TLS required; SSRF guard enforced (scheme must be http/https with non-empty host) |

---

## 4. Input Validation

- **Ingest Gateway:** Validates frame dimensions and JPEG magic bytes before publishing to Kafka.
- **Vision Service:** Validates base64 payload decodes to a valid JPEG; rejects malformed requests with `422`.
- **RAG Agent:** Validates `deepfake_score` is a float in `[0.0, 1.0]`; rejects malformed requests with `422`.
- **Aggregation Service (enforced at `/aggregate`):**
  - `stream_id` must match `^[A-Za-z0-9_\-\.]{1,128}$`; mismatches rejected with `422`.
  - `payload` base64 string must be ≤ 2,796,032 bytes (approx. 2 MB decoded); oversized frames rejected with `422`.
  - Webhook `WEBHOOK_URL` is validated on startup: scheme must be `http` or `https` with a non-empty host (SSRF guard).
- **Aggregation Service (enforced at `/auth/token`):** Rate-limited to 20 req / 60 s per client IP (see §2.4).

---

## 5. Secrets Management

All secrets are injected via environment variables. No secrets are hardcoded or committed to VCS.

| Secret | Env Var | Used By |
|---|---|---|
| Internal API key | `INTERNAL_API_KEY` | All internal services |
| JWT signing secret | `JWT_SECRET` | Aggregation Service |
| MLflow tracking token | `MLFLOW_TRACKING_TOKEN` | Aggregation Service |
| Kafka SASL credentials | `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD` | Ingest Gateway, Aggregation Service, Temporal Service |
| Webhook URL + token | `WEBHOOK_URL`, `WEBHOOK_TOKEN` | Aggregation Service |

A `.env.example` file documents all required variables with placeholder values. The `.env` file is listed in `.gitignore`.

---

## 6. Logging & Audit

- Secrets and frame payloads are **never** written to logs.
- All `401` / `403` / `422` / `429` / `500` responses are logged with `stream_id`, `frame_index`, and timestamp.
- MLflow logs are append-only; no log entry is modified or deleted after write.

---

## 7. Security Controls — Enforcement Status

| Control | Status | Phase Completed | Deferred Item |
|---|---|---|---|
| Internal API key auth (X-API-Key) | ✅ Enforced | Phase 1 | — |
| JWT HS256 (≥ 32-byte secret enforced at startup) | ✅ Enforced | Phase 2 | — |
| WebSocket JWT via Sec-WebSocket-Protocol | ✅ Enforced | Phase 2 | — |
| Kafka SASL_PLAINTEXT (all three consumers) | ✅ Enforced | Phase 2 | Upgrade to SASL_SSL in Phase 5 (SEC-1) |
| stream_id regex validation | ✅ Enforced | Phase 2 | — |
| Frame payload size guard (≤ 2 MB) | ✅ Enforced | Phase 2 | — |
| Webhook SSRF guard (scheme + netloc check) | ✅ Enforced | Phase 2 | — |
| /auth/token rate limiting (20 req/60 s per IP) | ✅ Enforced | Phase 2 | — |
| Startup warnings for default credentials | ✅ Enforced | Phase 2 | — |
| Kafka SASL_SSL (TLS) | ⏳ Deferred | — | Phase 5 (SEC-1) |
| WebSocket WSS / TLS | ⏳ Deferred | — | Phase 5 (SEC-2) |
| MLflow behind authenticated proxy | ⏳ Deferred | — | Phase 5 (SEC-3) |
| JWT short-lived tokens + refresh / revocation | ⏳ Deferred | — | Phase 5 (SEC-4) |
