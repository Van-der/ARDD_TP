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

**Correction (2026-07-12):** this section previously claimed MLflow "is not exposed outside the Docker network" — that was false. `docker-compose.yml` published `mlflow`'s port `5000:5000` directly to the host, and MLflow OSS does not enforce `MLFLOW_TRACKING_TOKEN` (or any bearer token) natively, so that port was genuinely unauthenticated.

**Fixed (SEC-3, 2026-07-12):** `mlflow`'s host port mapping was removed; a new `mlflow-proxy` service (`nginx:alpine` + `openssl`-generated htpasswd, `mlflow-proxy/`) now publishes `5000:5000` instead, enforcing HTTP basic auth (`MLFLOW_PROXY_USER`/`MLFLOW_PROXY_PASSWORD`) in front of MLflow. Internal service-to-service traffic (aggregation-service → `http://mlflow:5000`) still bypasses the proxy and talks to MLflow directly on the trusted `ardd_net` bridge network — only host/browser access is gated.

### 2.4 Rate Limiting

`POST /auth/token` is rate-limited per client IP to **20 requests per 60-second window** (in-memory, resets on restart). Requests exceeding the limit receive `429 Too Many Requests`. Disabled in test environments via the `TESTING` env var.

### 2.5 JWT Refresh & Revocation (SEC-4, 2026-07-12)

Access tokens are short-lived (`ACCESS_TOKEN_TTL`, default **900s / 15 min**, down from the original 3600s) to shrink the exposure window of a leaked token. `POST /auth/token` now also returns a longer-lived `refresh_token` (`REFRESH_TOKEN_TTL`, default **86400s / 24h**). `POST /auth/refresh` exchanges a valid, unrevoked refresh token for a new access/refresh pair — the used refresh token is immediately revoked (rotation), so it can't be replayed. `POST /auth/revoke` lets a client self-revoke its current bearer token (e.g. on logout). Revocation is tracked via an in-memory `revoked_jtis` set (each token carries a unique `jti` claim) — matches this service's existing in-memory-state pattern (rate limiter, alert counters); not persisted across restarts, same tradeoff as those. `require_role()` and the WebSocket handshake both check `jti` against `revoked_jtis` in addition to signature/expiry/role.

---

## 3. Transport Security

| Link | Protocol | Encryption |
|---|---|---|
| Video source → Ingest Gateway | RTSP / HTTP | TLS required in production; plaintext allowed in dev |
| Ingest Gateway / Aggregation / Temporal → Kafka | SASL_SSL (M10) | TLS transport (local CA, PEM keystore/truststore) + existing SASL PLAIN credentials. Not mTLS — a Kafka client cert would be redundant with SASL auth; `ssl.client.auth` is unset (defaults to none). |
| Vision / RAG / Temporal (internal) | HTTPS, mutual TLS (M10) | `--ssl-cert-reqs 2` (CERT_REQUIRED) — server presents a cert signed by the local CA, and rejects any client that doesn't present one too. Verified live: a request with CA trust but no client cert fails the TLS handshake (`ConnectionError`/`SSLError`), not a 401. |
| Aggregation Service (internal + browser-facing) | HTTPS, server-auth TLS (M10) | `--ssl-cert-reqs 1` (CERT_OPTIONAL) — the one service the React dashboard and host-side scripts hit directly, so a client cert is validated if presented (other backend services do present one) but never required, so the browser doesn't need one. |
| Aggregation → React Dashboard | WebSocket (wss://, M10) | TLS via the same aggregation-service cert; browser must trust the local CA once (see `scripts/gen_certs.sh`'s printed instructions) or it hits a cert warning. |
| Aggregation → MLflow | HTTP (unchanged) | Plaintext within Docker bridge network. Deliberately excluded from M10's mTLS mesh: `mlflow server`'s CLI has no TLS flags at all — wrapping it needs a reverse-proxy sidecar, disproportionate for internal-only experiment tracking that never handles frame data. |
| RAG Agent → Ollama | HTTP (unchanged) | Plaintext within Docker bridge network. Also excluded from the mTLS mesh — Ollama is a third-party model server, not a project-owned service. |
| Aggregation → Webhook targets | HTTP/HTTPS (per target) | TLS required only if the target URL is https; SSRF guard enforced (scheme must be http/https with non-empty host). Deliberately excluded from mTLS — `WEBHOOK_TARGETS` can point at arbitrary external endpoints (e.g. a real Slack webhook), which by definition can't present our local CA's client cert. |

**Certificates (M10):** `scripts/gen_certs.sh` generates a local self-signed root CA (`certs/ca.crt`, `certs/ca.key` — gitignored) plus a 2048-bit RSA leaf cert per mesh member (10-year validity; this is a local dev CA, not a production rotation policy), with `basicConstraints`/`keyUsage`/`extendedKeyUsage` extensions set explicitly — omitting these caused a real bug during verification where Python's `ssl` module (unlike `curl`) rejected the CA for strict RFC 5280 non-compliance. **Rotation is restart-based, not hot-reload**: re-run `gen_certs.sh` then `docker-compose restart <service>` (or `up -d` for services whose env also changed) — uvicorn and the Kafka broker only read cert files at process startup, matching this project's existing "no live config reload" pattern elsewhere. Browsers don't trust the local CA by default; `gen_certs.sh` prints one-time OS/browser trust-store import instructions.

---

## 4. Input Validation

- **Ingest Gateway:** Validates frame dimensions and JPEG magic bytes before publishing to Kafka.
- **Vision Service:** Validates base64 payload decodes to a valid JPEG; rejects malformed requests with `422`.
- **RAG Agent:** Validates `deepfake_score` is a float in `[0.0, 1.0]`; rejects malformed requests with `422`.
- **Aggregation Service (enforced at `/aggregate`):**
  - `stream_id` must match `^[A-Za-z0-9_\-\.]{1,128}$`; mismatches rejected with `422`.
  - `payload` base64 string must be ≤ 2,796,032 bytes (approx. 2 MB decoded); oversized frames rejected with `422`.
  - Each `WEBHOOK_TARGETS` entry's `url` is validated on startup and again at delivery time: scheme must be `http` or `https` with a non-empty host (SSRF guard). An invalid target is skipped, not fatal to the others.
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
| Webhook fan-out targets (URL + per-target token) | `WEBHOOK_TARGETS` (JSON array) | Aggregation Service |
| RBAC role-pair credentials (M11) | `ADMIN_CLIENT_ID`, `ADMIN_CLIENT_SECRET`, `VIEWER_CLIENT_ID`, `VIEWER_CLIENT_SECRET` | Aggregation Service |
| Which role pair the frontend logs in as | `FRONTEND_CLIENT_ID`, `FRONTEND_CLIENT_SECRET` (defaults to the viewer pair) | Frontend |
| MLflow proxy basic-auth credentials (SEC-3) | `MLFLOW_PROXY_USER`, `MLFLOW_PROXY_PASSWORD` | mlflow-proxy |

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
| Kafka SASL_PLAINTEXT (all three consumers) | ✅ Enforced | Phase 2 | Upgraded to SASL_SSL — see below (M10) |
| stream_id regex validation | ✅ Enforced | Phase 2 | — |
| Frame payload size guard (≤ 2 MB) | ✅ Enforced | Phase 2 | — |
| Webhook SSRF guard (scheme + netloc check) | ✅ Enforced | Phase 2 | — |
| /auth/token rate limiting (20 req/60 s per IP) | ✅ Enforced | Phase 2 | — |
| Startup warnings for default credentials | ✅ Enforced | Phase 2 | — |
| Real credential lookup on /auth/token (was: any non-empty string) | ✅ Enforced | M11 (2026-07-11) | — |
| RBAC — role claim (admin/viewer) + `require_role` on admin endpoints | ✅ Enforced | M11 (2026-07-11) | — |
| Kafka SASL_SSL (TLS) | ✅ Enforced | M10 (2026-07-11) | — |
| mTLS on Vision/RAG/Temporal (CERT_REQUIRED) | ✅ Enforced | M10 (2026-07-11) | — |
| TLS on Aggregation Service (CERT_OPTIONAL, browser-facing) | ✅ Enforced | M10 (2026-07-11) | — |
| WebSocket WSS / TLS | ✅ Enforced | M10 (2026-07-11) | — |
| mTLS on MLflow / Ollama / webhook targets | ⏳ Not planned | — | Excluded by design — see §3 notes (no TLS CLI support / third-party / arbitrary external endpoint) |
| MLflow behind authenticated proxy | ✅ Enforced | SEC-3 (2026-07-12) | — |
| JWT short-lived tokens + refresh / revocation | ✅ Enforced | SEC-4 (2026-07-12) | — |
