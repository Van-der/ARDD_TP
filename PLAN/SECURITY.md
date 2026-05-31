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

The React dashboard authenticates the WebSocket upgrade request with a **JWT bearer token** in the `Authorization` header.

- Token issued by the Aggregation Service on `POST /auth/token` (see `API_SPEC.md §6`).
- Token expiry: **1 hour**. Client must re-authenticate on expiry.
- Algorithm: **HS256**. Secret injected via `JWT_SECRET` environment variable.

### 2.3 MLflow

MLflow is accessible only within the `ardd_net` Docker network. No external port is exposed. The Aggregation Service authenticates with a static `MLFLOW_TRACKING_TOKEN` env var.

---

## 3. Transport Security

| Link | Protocol | Encryption |
|---|---|---|
| Video source → Ingest Gateway | RTSP / HTTP | TLS required in production; plaintext allowed in dev |
| Ingest Gateway → Kafka | SASL_SSL | TLS + SASL PLAIN credentials |
| Vision / RAG / Aggregation (internal) | HTTP | Plaintext within Docker bridge network (TLS in Phase 4) |
| Aggregation → React Dashboard | WebSocket | WSS (TLS) required in production |
| Aggregation → MLflow | HTTP | Plaintext within Docker bridge network |
| Aggregation → Webhook | HTTPS | TLS required; certificate validation enforced |

---

## 4. Input Validation

- **Ingest Gateway:** Validates frame dimensions and JPEG magic bytes before publishing to Kafka.
- **Vision Service:** Validates base64 payload decodes to a valid JPEG; rejects oversized payloads (max **2MB** per frame).
- **RAG Agent:** Validates `deepfake_score` is a float in `[0.0, 1.0]`; rejects malformed requests with `422`.
- **Aggregation Service:** Validates all upstream responses against expected schema before merging.

---

## 5. Secrets Management

All secrets are injected via environment variables. No secrets are hardcoded or committed to VCS.

| Secret | Env Var | Used By |
|---|---|---|
| Internal API key | `INTERNAL_API_KEY` | All internal services |
| JWT signing secret | `JWT_SECRET` | Aggregation Service |
| MLflow tracking token | `MLFLOW_TRACKING_TOKEN` | Aggregation Service |
| Kafka SASL credentials | `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD` | Ingest Gateway, Vision Service |
| Webhook URL + token | `WEBHOOK_URL`, `WEBHOOK_TOKEN` | Aggregation Service |

A `.env.example` file documents all required variables with placeholder values. The `.env` file is listed in `.gitignore`.

---

## 6. Logging & Audit

- Secrets and frame payloads are **never** written to logs.
- All `401` / `403` / `422` / `500` responses are logged with `stream_id`, `frame_index`, and timestamp.
- MLflow logs are append-only; no log entry is modified or deleted after write.
