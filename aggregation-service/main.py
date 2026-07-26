import os
import re
import ssl
import time
import uuid
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, List, Optional
from urllib.parse import urlparse
from collections import defaultdict, deque

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import base64
import datetime
import aiobreaker
import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
import mlflow
import redis.asyncio as aioredis
from aiokafka import AIOKafkaConsumer
from aiokafka.abc import ConsumerRebalanceListener
from aiokafka.coordinator.assignors.sticky.sticky_assignor import StickyPartitionAssignor
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
START_TIME = time.time()

# Config
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "test-key")
JWT_SECRET = os.getenv("JWT_SECRET", "ardd-tp-dev-secret-key-change-me!")
# RBAC (M11) — hardcoded role pairs, not a persistent user store (decision #7).
ADMIN_CLIENT_ID = os.getenv("ADMIN_CLIENT_ID", "admin")
ADMIN_CLIENT_SECRET = os.getenv("ADMIN_CLIENT_SECRET", "admin-secret-change-me")
VIEWER_CLIENT_ID = os.getenv("VIEWER_CLIENT_ID", "viewer")
VIEWER_CLIENT_SECRET = os.getenv("VIEWER_CLIENT_SECRET", "viewer-secret-change-me")
VISION_URL = os.getenv("VISION_URL", "https://vision-service:8001/infer")
RAG_URL = os.getenv("RAG_URL", "https://rag-agent:8002/audit")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_SECURITY_PROTOCOL = os.getenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
KAFKA_SASL_USERNAME = os.getenv("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASSWORD = os.getenv("KAFKA_SASL_PASSWORD", "")
CA_CERT = os.getenv("CA_CERT", "/certs/ca.crt")
# mTLS (M10): vision-service/rag-agent/temporal-service require a client cert
# (CERT_REQUIRED) on inbound connections — aggregation-service presents its
# own server cert as its client identity for those outbound calls too.
CLIENT_CERT_FILE = os.getenv("CLIENT_CERT_FILE", "/certs/aggregation-service.crt")
CLIENT_KEY_FILE = os.getenv("CLIENT_KEY_FILE", "/certs/aggregation-service.key")


def _client_cert():
    if os.path.exists(CLIENT_CERT_FILE) and os.path.exists(CLIENT_KEY_FILE):
        return (CLIENT_CERT_FILE, CLIENT_KEY_FILE)
    return None
try:
    WEBHOOK_TARGETS: List[dict] = json.loads(os.getenv("WEBHOOK_TARGETS", "[]"))
    if not isinstance(WEBHOOK_TARGETS, list):
        raise ValueError("WEBHOOK_TARGETS must be a JSON array")
except (json.JSONDecodeError, ValueError) as e:
    logger.warning(f"WEBHOOK_TARGETS is not valid JSON — no webhook targets configured: {e}")
    WEBHOOK_TARGETS = []
RAG_TIMEOUT = float(os.getenv("RAG_TIMEOUT", "15.0"))  # seconds; tinyllama needs ~2-5s
ALERT_THRESHOLD = float(os.getenv("ALERT_THRESHOLD", "0.90"))
ALERT_WINDOW = int(os.getenv("ALERT_WINDOW", "5"))
PROFILE = os.getenv("PROFILE", "false").lower() == "true"
REDIS_URL = os.getenv("REDIS_URL", "")
REDIS_KEY_TTL_S = 3600  # 1h of inactivity clears Redis-backed counters/history
# Stream segment archival (M12) — local MinIO, S3-compatible, instead of real S3.
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin123")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "ardd-segments")

_minio = boto3.client(
    "s3",
    endpoint_url=MINIO_ENDPOINT,
    aws_access_key_id=MINIO_ACCESS_KEY,
    aws_secret_access_key=MINIO_SECRET_KEY,
    # boto3's default connect_timeout is ~60s — far too slow for a
    # fire-and-forget path that must never meaningfully block anything.
    # Short timeout + no retries so a down/unreachable MinIO fails fast.
    config=BotoConfig(connect_timeout=3, read_timeout=5, retries={"max_attempts": 1}),
)
_minio_bucket_ready = False


def _ensure_minio_bucket() -> None:
    """Idempotent — create the bucket on first use if it doesn't exist yet.
    Runs synchronously (called via asyncio.to_thread, boto3 has no async API)."""
    global _minio_bucket_ready
    if _minio_bucket_ready:
        return
    try:
        _minio.head_bucket(Bucket=MINIO_BUCKET)
    except ClientError:
        _minio.create_bucket(Bucket=MINIO_BUCKET)
    _minio_bucket_ready = True


def _upload_segment_sync(stream_id: str, frame_index: int, raw_bytes: bytes) -> None:
    _ensure_minio_bucket()
    key = f"{stream_id}/frame_{frame_index}.jpg"
    _minio.put_object(Bucket=MINIO_BUCKET, Key=key, Body=raw_bytes, ContentType="image/jpeg")


async def _archive_segment(stream_id: str, frame_index: int, payload_b64: str) -> None:
    """Fire-and-forget (asyncio.create_task, matching the webhook delivery
    pattern) — archival failure must never block the frame pipeline."""
    try:
        raw_bytes = base64.b64decode(payload_b64)
        await asyncio.to_thread(_upload_segment_sync, stream_id, frame_index, raw_bytes)
        logger.info(f"Archived alert-streak-start segment: {stream_id}/frame_{frame_index}.jpg")
    except Exception as e:
        logger.warning(f"MinIO archival failed for {stream_id}/frame_{frame_index}: {e}")


# Circuit breakers on the Vision/RAG calls: an open breaker fails fast instead
# of waiting out the full httpx timeout (5s vision / RAG_TIMEOUT for rag) on
# every frame during a partial outage. Using aiobreaker (not pybreaker) —
# pybreaker's call_async requires an undeclared `tornado` dependency and
# raises NameError without it; aiobreaker is asyncio-native with no extra deps.
_vision_breaker = aiobreaker.CircuitBreaker(
    fail_max=int(os.getenv("VISION_BREAKER_FAIL_MAX", "5")),
    timeout_duration=datetime.timedelta(seconds=int(os.getenv("VISION_BREAKER_RESET_S", "30"))),
    name="vision",
)
_rag_breaker = aiobreaker.CircuitBreaker(
    fail_max=int(os.getenv("RAG_BREAKER_FAIL_MAX", "5")),
    timeout_duration=datetime.timedelta(seconds=int(os.getenv("RAG_BREAKER_RESET_S", "30"))),
    name="rag",
)

def _kafka_sasl_kwargs() -> dict:
    kwargs = {
        "security_protocol": KAFKA_SECURITY_PROTOCOL,
        "sasl_mechanism": "PLAIN",
        "sasl_plain_username": KAFKA_SASL_USERNAME or "admin",
        "sasl_plain_password": KAFKA_SASL_PASSWORD or "admin-secret",
        "api_version": "auto",
    }
    if kwargs["security_protocol"] == "SASL_SSL":
        if os.path.exists(CA_CERT):
            kwargs["ssl_context"] = ssl.create_default_context(cafile=CA_CERT)
        else:
            logger.warning(f"CA_CERT '{CA_CERT}' not found — falling back to SASL_PLAINTEXT")
            kwargs["security_protocol"] = "SASL_PLAINTEXT"
    return kwargs

# Setup MLflow — one named parent run per service startup
_mlflow_run_id: Optional[str] = None
try:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    if not os.getenv("TESTING"):
        mlflow.set_experiment("ardd_pipeline")
        _session_name = f"session_{time.strftime('%Y%m%d_%H%M%S')}"
        _run = mlflow.start_run(run_name=_session_name)
        _mlflow_run_id = _run.info.run_id
        mlflow.set_tags({
            "service": "aggregation-service",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        logger.info(f"MLflow session run: {_session_name} (run_id={_mlflow_run_id})")
except Exception as e:
    logger.warning(f"MLflow setup failed: {e}")

# State
alert_counters: Dict[str, int] = defaultdict(int)
mlflow_buffer: deque = deque(maxlen=100)
MAX_STREAMS = 1000  # cap on unique stream_id keys to prevent unbounded memory growth
_latest_temporal: Dict[str, dict] = {}  # stream_id → {verdict, score, low_confidence}

# ── Security constants ────────────────────────────────────────────────────────
# stream_id: alphanumeric + underscore/hyphen/dot, 1–128 chars
_STREAM_ID_RE = re.compile(r'^[A-Za-z0-9_\-\.]{1,128}$')
# payload: base64 overhead ≈ 4/3 × 2 MB → reject before Vision call
_MAX_PAYLOAD_B64_BYTES = 2_796_032
# auth rate limiting: 20 attempts per 60 s per client IP
_AUTH_RATE_LIMIT = 20
_AUTH_RATE_WINDOW = 60  # seconds
_auth_attempts: Dict[str, list] = {}

# JWT refresh/revocation (SEC-4): short-lived access tokens + a longer-lived
# refresh token, with an in-memory revocation set (matches the rate-limiter's
# own in-memory-state pattern above — not persisted across restarts, same as
# every other in-process cache in this service).
ACCESS_TOKEN_TTL = int(os.getenv("ACCESS_TOKEN_TTL", "900"))       # 15 min
REFRESH_TOKEN_TTL = int(os.getenv("REFRESH_TOKEN_TTL", "86400"))   # 24 h
revoked_jtis: set = set()


def _is_rate_limited(client_ip: str) -> bool:
    """Return True if client_ip has exceeded _AUTH_RATE_LIMIT in the last window."""
    if os.getenv("TESTING"):
        return False
    now = time.time()
    bucket = _auth_attempts.setdefault(client_ip, [])
    # Evict timestamps outside the window
    _auth_attempts[client_ip] = [t for t in bucket if t > now - _AUTH_RATE_WINDOW]
    if len(_auth_attempts[client_ip]) >= _AUTH_RATE_LIMIT:
        return True
    _auth_attempts[client_ip].append(now)
    return False


def _valid_stream_id(stream_id: str) -> bool:
    """Validate stream_id format — prevents log injection and unbounded dict growth."""
    return bool(_STREAM_ID_RE.match(stream_id))


def _valid_webhook_url(url: str) -> bool:
    """Return True only for http(s) URLs with a non-empty host (SSRF guard)."""
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False

# Rolling deque per stream (maxlen=100 enforces the sliding window automatically)
drift_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
labels_buffer: Dict[str, dict] = {}
results_buffer: Dict[str, dict] = {}
MAX_LABELS_BUFFER = 500

def _evict_oldest(d: dict, cap: int) -> None:
    """Evict oldest half of keys when dict exceeds cap."""
    if len(d) > cap:
        to_drop = list(d.keys())[:len(d) // 2]
        for k in to_drop:
            del d[k]

# --- Redis-backed alert/drift state, with in-memory fallback ---
# Redis makes alert_counters/drift_history durable across restarts and safe
# to share across multiple aggregation-service replicas (M3's --scale
# testing). If REDIS_URL is unset, or Redis is unreachable, every helper
# below transparently falls back to the in-memory dicts above — Redis is
# never a hard dependency. Note: switching between Redis and the in-memory
# fallback mid-run does not reconcile state between the two; this is an
# accepted limitation for a fallback mechanism, not a distributed-state
# guarantee.
_redis: Optional["aioredis.Redis"] = None
_redis_was_available = False

async def _redis_client() -> Optional["aioredis.Redis"]:
    global _redis, _redis_was_available
    if not REDIS_URL:
        return None
    if _redis is None:
        _redis = aioredis.from_url(
            REDIS_URL, decode_responses=True,
            socket_connect_timeout=1, socket_timeout=1,
        )
    try:
        await _redis.ping()
        _redis_was_available = True
        return _redis
    except Exception:
        if _redis_was_available:
            logger.warning("Redis unavailable — falling back to in-memory alert/drift state.")
        _redis_was_available = False
        return None

async def _incr_alert_counter(stream_id: str) -> int:
    r = await _redis_client()
    if r is not None:
        try:
            key = f"alert_ctr:{stream_id}"
            val = await r.incr(key)
            await r.expire(key, REDIS_KEY_TTL_S)
            return val
        except Exception:
            pass
    alert_counters[stream_id] += 1
    _evict_oldest(alert_counters, MAX_STREAMS)
    return alert_counters[stream_id]

async def _reset_alert_counter(stream_id: str) -> None:
    r = await _redis_client()
    if r is not None:
        try:
            await r.set(f"alert_ctr:{stream_id}", 0, ex=REDIS_KEY_TTL_S)
            return
        except Exception:
            pass
    alert_counters[stream_id] = 0
    _evict_oldest(alert_counters, MAX_STREAMS)

async def _append_drift(stream_id: str, confidence: float) -> None:
    r = await _redis_client()
    if r is not None:
        try:
            key = f"drift:{stream_id}"
            await r.lpush(key, confidence)
            await r.ltrim(key, 0, 99)  # keep last 100 — matches deque(maxlen=100)
            await r.expire(key, REDIS_KEY_TTL_S)
            return
        except Exception:
            pass
    drift_history[stream_id].append(confidence)
    _evict_oldest(drift_history, MAX_STREAMS)

async def _drift_stats(stream_id: str) -> tuple[int, float]:
    """Returns (sample_count, avg_confidence) for the rolling drift window."""
    r = await _redis_client()
    if r is not None:
        try:
            vals = await r.lrange(f"drift:{stream_id}", 0, -1)
            vals = [float(v) for v in vals]
            return (len(vals), sum(vals) / len(vals)) if vals else (0, 0.0)
        except Exception:
            pass
    history = drift_history[stream_id]
    return (len(history), sum(history) / len(history)) if history else (0, 0.0)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections.copy():
            try:
                await connection.send_text(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()

# Schemas
class FramePayload(BaseModel):
    stream_id: str
    frame_index: int
    timestamp_ms: int
    payload: str

class TokenRequest(BaseModel):
    client_id: str
    client_secret: str

class RefreshRequest(BaseModel):
    refresh_token: str

class AggregatedResult(BaseModel):
    stream_id: str
    frame_index: int
    timestamp_ms: int
    deepfake_score: float
    audit_verdict: str
    matched_signature: Optional[str]
    alert: bool
    rag_used: bool
    latency_ms: int
    drift_flag: bool
    summary: str

class TemporalAuditResult(BaseModel):
    stream_id: str
    window_start_frame: int
    window_end_frame: int
    window_duration_s: float
    temporal_score: float
    temporal_verdict: str
    low_confidence_flag: bool
    frames_interpolated: int
    model_used: str
    latency_ms: int
    timestamp_ms: int

def _fuse_summary(speed_summary: str, speed_verdict: str, temporal: Optional[dict]) -> str:
    if temporal is None:
        return speed_summary + " Temporal sequence analysis not yet available."
    t_verdict = temporal["verdict"]
    t_score = temporal["score"]
    conf_note = " (low-confidence window — insufficient frames)" if temporal.get("low_confidence") else ""
    if speed_verdict == "FAIL" and t_verdict == "FAIL":
        return (speed_summary +
                f" Corroborated by 20-frame temporal sequence (score {t_score:.0%}).{conf_note}")
    if speed_verdict == "FAIL":
        return (speed_summary +
                f" Temporal sequence inconclusive ({t_verdict}, {t_score:.0%}) — treat as unconfirmed.{conf_note}")
    if t_verdict == "FAIL":
        return (f"Frame inconclusive at Speed Layer ({speed_summary.rstrip('.')}.)"
                f" Current 20-frame window shows deepfake pattern "
                f"(temporal score {t_score:.0%}).{conf_note}")
    return speed_summary + f" Temporal sequence also clear ({t_verdict}).{conf_note}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Security: warn loudly if default credentials are still in use
    if not os.getenv("TESTING"):
        if KAFKA_SASL_PASSWORD in ("", "admin-secret"):
            logger.warning("SECURITY WARNING: default Kafka SASL password detected — set KAFKA_SASL_USERNAME/PASSWORD before production deployment.")
        if JWT_SECRET == "ardd-tp-dev-secret-key-change-me!":
            logger.warning("SECURITY WARNING: default JWT_SECRET detected — set a strong secret before production deployment.")
        for _t in WEBHOOK_TARGETS:
            if not _valid_webhook_url(_t.get("url", "")):
                logger.warning(f"SECURITY WARNING: webhook target '{_t.get('url')}' is not a valid HTTP(S) URL — delivery to it will be blocked.")
    asyncio.create_task(mlflow_flush_task())
    asyncio.create_task(start_labels_consumer())
    asyncio.create_task(start_frames_consumer())
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# OpenTelemetry tracing (M8) — local Jaeger via otel-collector, no cloud APM.
_otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
if _otel_endpoint:
    _provider = TracerProvider(resource=Resource.create({"service.name": "aggregation-service"}))
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_otel_endpoint, insecure=True)))
    trace.set_tracer_provider(_provider)
    FastAPIInstrumentor.instrument_app(app)
tracer = trace.get_tracer(__name__)

async def process_labeled_result(result: dict, label: str):
    if label == "REAL":
        stream_id = result["stream_id"]
        confidence = 1.0 - result["deepfake_score"]
        await _append_drift(stream_id, confidence)

class RebalanceLogger(ConsumerRebalanceListener):
    """Logs partition revoke/assign events during a consumer-group rebalance.

    Note: aiokafka's rebalance protocol is always "eager" (stop-the-world) —
    unlike the Java client, it has no incremental/cooperative rebalance
    protocol to opt into. StickyPartitionAssignor (passed as
    partition_assignment_strategy below) minimizes partition churn across
    rebalances, which is the best available mitigation in aiokafka; this
    listener adds visibility into when a rebalance starts/ends rather than a
    hard pause-and-drain guarantee (offsets still auto-commit on the default
    5s interval regardless of in-flight per-frame tasks).
    """
    def __init__(self, name: str):
        self.name = name

    async def on_partitions_revoked(self, revoked):
        if revoked:
            logger.warning(f"[{self.name}] Rebalance: partitions revoked: {sorted(revoked)}")

    async def on_partitions_assigned(self, assigned):
        if assigned:
            logger.info(f"[{self.name}] Rebalance: partitions assigned: {sorted(assigned)}")

async def start_labels_consumer():
    if os.getenv("TESTING"):
        return
    while True:
        try:
            consumer = AIOKafkaConsumer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                group_id="aggregation-labels-group",
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                partition_assignment_strategy=(StickyPartitionAssignor,),
                **_kafka_sasl_kwargs()
            )
            consumer.subscribe(topics=[os.getenv("KAFKA_TOPIC_LABELS", "labels")],
                               listener=RebalanceLogger("aggregation-labels-group"))
            await consumer.start()
            logger.info("Labels consumer started.")
            try:
                async for msg in consumer:
                    label_data = msg.value
                    stream_id = label_data.get("stream_id")
                    frame_index = label_data.get("frame_index")
                    label = label_data.get("label")
                    if not stream_id or frame_index is None or not label:
                        continue
                    key = f"{stream_id}_{frame_index}"
                    if key in results_buffer:
                        result = results_buffer.pop(key)
                        await process_labeled_result(result, label)
                    else:
                        labels_buffer[key] = label_data
                        if len(labels_buffer) > MAX_LABELS_BUFFER:
                            oldest_key = next(iter(labels_buffer))
                            del labels_buffer[oldest_key]
            finally:
                await consumer.stop()
        except Exception as e:
            logger.error(f"Labels consumer crashed: {e}. Retrying in 5s...")
            await asyncio.sleep(5)

_stream_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

async def _process_frame_locked(payload: FramePayload) -> None:
    """Serialize processing per stream_id (preserves alert/drift ordering) while
    letting different streams run concurrently — a single slow RAG/LLM call for
    one stream must not stall every other stream's frames behind it."""
    try:
        async with _stream_locks[payload.stream_id]:
            await process_frame_payload(payload)
    except Exception as e:
        logger.error(f"Pipeline error processing frame: {e}")

async def start_frames_consumer():
    if os.getenv("TESTING"):
        return
    while True:
        try:
            consumer = AIOKafkaConsumer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                group_id="aggregation-pipeline-group",
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                partition_assignment_strategy=(StickyPartitionAssignor,),
                **_kafka_sasl_kwargs()
            )
            consumer.subscribe(topics=["frames"],
                               listener=RebalanceLogger("aggregation-pipeline-group"))
            await consumer.start()
            logger.info("Frames consumer started.")
            try:
                async for msg in consumer:
                    try:
                        data = msg.value
                        # Skip control/error events from ingest-gateway
                        # (e.g. gateway_fatal) — they are not FramePayloads
                        if "event" in data and "payload" not in data:
                            logger.debug(f"Skipping non-frame event: {data.get('event')}")
                            continue
                        payload = FramePayload(**data)
                        asyncio.create_task(_process_frame_locked(payload))
                    except Exception as e:
                        logger.debug(f"Pipeline skipped malformed message: {e}")
            finally:
                await consumer.stop()
        except Exception as e:
            logger.error(f"Frames consumer crashed: {e}. Retrying in 5s...")
            await asyncio.sleep(5)

async def mlflow_flush_task():
    while True:
        if mlflow_buffer and not os.getenv("TESTING") and _mlflow_run_id:
            items_to_flush = list(mlflow_buffer)
            try:
                for t in items_to_flush:
                    if "frame_index" in t:
                        # Speed-layer frame result
                        mlflow.log_metrics({
                            "deepfake_score": t.get("deepfake_score", 0.0),
                            "speed_latency_ms": t.get("latency_ms", 0),
                        }, step=t["frame_index"])
                    else:
                        # Temporal batch result — use window_end_frame as step
                        mlflow.log_metrics({
                            "temporal_score": t.get("temporal_score", 0.0),
                            "temporal_latency_ms": t.get("latency_ms", 0),
                        }, step=t.get("window_end_frame", 0))
                mlflow_buffer.clear()
            except Exception as e:
                logger.error(f"MLflow flush failed: {e}")
        await asyncio.sleep(10)

async def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    if api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    return api_key

def _role_for_credentials(client_id: str, client_secret: str) -> Optional[str]:
    """RBAC (M11): hardcoded admin/viewer role pairs, not a persistent user store."""
    if client_id == ADMIN_CLIENT_ID and client_secret == ADMIN_CLIENT_SECRET:
        return "admin"
    if client_id == VIEWER_CLIENT_ID and client_secret == VIEWER_CLIENT_SECRET:
        return "viewer"
    return None


def _issue_tokens(client_id: str, role: str) -> dict:
    """Issue a short-lived access token + longer-lived refresh token (SEC-4),
    each with its own `jti` so either can be individually revoked."""
    now = time.time()
    access_token = jwt.encode(
        {"sub": client_id, "role": role, "type": "access", "jti": str(uuid.uuid4()), "exp": now + ACCESS_TOKEN_TTL},
        JWT_SECRET, algorithm="HS256",
    )
    refresh_token = jwt.encode(
        {"sub": client_id, "role": role, "type": "refresh", "jti": str(uuid.uuid4()), "exp": now + REFRESH_TOKEN_TTL},
        JWT_SECRET, algorithm="HS256",
    )
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer", "expires_in": ACCESS_TOKEN_TTL}


@app.post("/auth/token")
async def login(req: TokenRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Too many auth requests — try again later.")
    role = _role_for_credentials(req.client_id, req.client_secret)
    if role is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return _issue_tokens(req.client_id, role)


@app.post("/auth/refresh")
async def refresh(req: RefreshRequest):
    """Exchange a valid, unrevoked refresh token for a new access/refresh
    pair. The used refresh token is revoked (rotation) so it can't be replayed."""
    try:
        payload = jwt.decode(req.refresh_token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")
    if payload.get("jti") in revoked_jtis:
        raise HTTPException(status_code=401, detail="Refresh token revoked")
    revoked_jtis.add(payload["jti"])
    return _issue_tokens(payload["sub"], payload["role"])


@app.post("/auth/revoke")
async def revoke(request: Request):
    """Self-service revocation of the caller's own bearer token (e.g. on
    logout) — adds its `jti` to the in-memory revocation set checked by
    require_role() and the WebSocket handshake below."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header[len("Bearer "):]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if "jti" in payload:
        revoked_jtis.add(payload["jti"])
    return {"revoked": True}


def require_role(role: str):
    """FastAPI dependency factory gating an endpoint to a JWT role claim.
    401 if the bearer token is missing/invalid/expired/revoked (not authenticated);
    403 if it's valid but the wrong role (authenticated, not authorized)."""
    async def _check(request: Request) -> dict:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = auth_header[len("Bearer "):]
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")
        if payload.get("jti") in revoked_jtis:
            raise HTTPException(status_code=401, detail="Token revoked")
        if payload.get("role") != role:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return payload
    return _check

@app.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket):
    # JWT passed via Sec-WebSocket-Protocol subprotocol — avoids token leakage in server access logs
    token = websocket.headers.get("sec-websocket-protocol", "").split(",")[0].strip()
    if not token:
        await websocket.close(code=1008)
        return
    try:
        _ws_payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        await websocket.close(code=1008)
        return
    except jwt.InvalidTokenError:
        await websocket.close(code=1008)
        return
    if _ws_payload.get("jti") in revoked_jtis:
        await websocket.close(code=1008)
        return

    await websocket.accept(subprotocol=token)
    manager.active_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

async def call_vision(payload: dict) -> dict:
    async def _do_call() -> dict:
        with tracer.start_as_current_span("call_vision"):
            t0 = time.perf_counter()
            async with httpx.AsyncClient(verify=CA_CERT if os.path.exists(CA_CERT) else True, cert=_client_cert()) as client:
                resp = await client.post(VISION_URL, json=payload, headers={"X-API-Key": INTERNAL_API_KEY}, timeout=5.0)
                round_trip_ms = (time.perf_counter() - t0) * 1000
                if resp.status_code != 200:
                    raise HTTPException(status_code=502, detail="Vision Service Error")
                result = resp.json()
                if PROFILE:
                    server_latency_ms = result.get("latency_ms", 0)
                    logger.info(
                        "PROFILE vision stream=%s frame=%s round_trip_ms=%.1f server_latency_ms=%d overhead_ms=%.1f",
                        payload.get("stream_id"), payload.get("frame_index"),
                        round_trip_ms, server_latency_ms, round_trip_ms - server_latency_ms,
                    )
                return result
    return await _vision_breaker.call_async(_do_call)

async def call_rag(stream_id: str, frame_index: int, score: float) -> dict:
    async def _do_call() -> dict:
        with tracer.start_as_current_span("call_rag"):
            req = {
                "stream_id": stream_id,
                "frame_index": frame_index,
                "deepfake_score": score
            }
            t0 = time.perf_counter()
            async with httpx.AsyncClient(verify=CA_CERT if os.path.exists(CA_CERT) else True, cert=_client_cert()) as client:
                resp = await client.post(RAG_URL, json=req, headers={"X-API-Key": INTERNAL_API_KEY}, timeout=RAG_TIMEOUT)
                resp.raise_for_status()
                result = resp.json()
                if PROFILE:
                    round_trip_ms = (time.perf_counter() - t0) * 1000
                    logger.info(
                        "PROFILE rag stream=%s frame=%s round_trip_ms=%.1f",
                        stream_id, frame_index, round_trip_ms,
                    )
                return result
    return await _rag_breaker.call_async(_do_call)

async def process_frame_payload(payload: FramePayload) -> AggregatedResult:
    # Security: validate stream_id format (prevents log injection + unbounded dict growth)
    if not _valid_stream_id(payload.stream_id):
        raise HTTPException(status_code=422, detail="stream_id may only contain A–Z a–z 0–9 _ - . (max 128 chars)")
    # Security: reject oversized payloads before the Vision Service call
    if len(payload.payload) > _MAX_PAYLOAD_B64_BYTES:
        raise HTTPException(status_code=422, detail="Payload exceeds 2 MB limit")

    start_time = time.time()

    try:
        vision_res = await call_vision(payload.model_dump())
    except Exception as e:
        logger.error(f"Vision failure: {e}")
        err_msg = json.dumps({"event": "pipeline_error", "stream_id": payload.stream_id})
        await manager.broadcast(err_msg)
        raise HTTPException(status_code=502, detail="Vision Service returned an error; frame dropped")

    deepfake_score = vision_res.get("deepfake_score", 0.5)
    
    rag_used = False
    audit_verdict = "UNKNOWN"
    matched_signature = None
    speed_summary = "RAG agent unavailable — verdict inconclusive."

    try:
        rag_res = await call_rag(payload.stream_id, payload.frame_index, deepfake_score)
        rag_used = True
        audit_verdict = rag_res.get("audit_verdict", "UNKNOWN")
        matched_signature = rag_res.get("matched_signature", None)
        speed_summary = rag_res.get("summary", "No summary returned by RAG agent.")
    except httpx.TimeoutException:
        logger.warning(f"RAG timeout exceeded {RAG_TIMEOUT}s")
    except Exception as e:
        logger.warning(f"RAG failure: {e}")

    fused_summary = _fuse_summary(speed_summary, audit_verdict, _latest_temporal.get(payload.stream_id))

    final_score = deepfake_score
    if audit_verdict == "FAIL":
        final_score = deepfake_score * (1.0 + 0.15)
        
    final_score = min(max(final_score, 0.0), 1.0)
    
    if final_score > ALERT_THRESHOLD:
        consecutive_alerts = await _incr_alert_counter(payload.stream_id)
    else:
        await _reset_alert_counter(payload.stream_id)
        consecutive_alerts = 0

    alert = consecutive_alerts >= ALERT_WINDOW

    # Stream segment archival (M12): fires exactly once per alert streak, on
    # the frame that crosses the threshold — not on every subsequent alerted
    # frame, and not again until the streak resets and re-triggers.
    if consecutive_alerts == ALERT_WINDOW:
        asyncio.create_task(_archive_segment(payload.stream_id, payload.frame_index, payload.payload))

    drift_flag = False
    sample_count, avg_confidence = await _drift_stats(payload.stream_id)
    if sample_count >= 100 and avg_confidence < 0.60:  # rolling: evaluate continuously once window is full
        drift_flag = True

    latency_ms = int((time.time() - start_time) * 1000)
    
    result = AggregatedResult(
        stream_id=payload.stream_id,
        frame_index=payload.frame_index,
        timestamp_ms=payload.timestamp_ms,
        deepfake_score=final_score,
        audit_verdict=audit_verdict,
        matched_signature=matched_signature,
        alert=alert,
        rag_used=rag_used,
        latency_ms=latency_ms,
        drift_flag=drift_flag,
        summary=fused_summary,
    )
    
    telemetry = {
        "stream_id": payload.stream_id,
        "frame_index": payload.frame_index,
        "deepfake_score": final_score,
        "audit_verdict": audit_verdict,
        "rag_used": rag_used,
        "latency_ms": latency_ms,
        "drift_flag": drift_flag
    }
    
    mlflow_buffer.append(telemetry)

    # Check if we already have a label for this frame
    key = f"{payload.stream_id}_{payload.frame_index}"
    result_data = {
        "stream_id": payload.stream_id,
        "frame_index": payload.frame_index,
        "deepfake_score": final_score
    }
    
    if key in labels_buffer:
        label_data = labels_buffer.pop(key)
        await process_labeled_result(result_data, label_data["label"])
    else:
        results_buffer[key] = result_data
        if len(results_buffer) > MAX_LABELS_BUFFER:
            oldest_key = next(iter(results_buffer))
            del results_buffer[oldest_key]
    
    ws_event = {
        "stream_id": payload.stream_id,
        "frame_index": payload.frame_index,
        "deepfake_score": final_score,
        "audit_verdict": audit_verdict,
        "alert": alert,
        "timestamp_ms": payload.timestamp_ms,
        "matched_signature": matched_signature,
        "summary": fused_summary,
    }
    await manager.broadcast(json.dumps(ws_event))

    # Webhook alert delivery (3 attempts with backoff, fanned out to all
    # configured targets) — SCHEMA.md §8
    if alert and WEBHOOK_TARGETS:
        webhook_payload = {
            "event": "DEEPFAKE_ALERT",
            "stream_id": payload.stream_id,
            "frame_index": payload.frame_index,
            "final_score": final_score,
            "audit_verdict": audit_verdict,
            "matched_signature": matched_signature,
            "consecutive_alert_frames": consecutive_alerts,
            "timestamp_ms": payload.timestamp_ms
        }
        asyncio.create_task(_deliver_webhook_fanout(webhook_payload))

    return result


def _format_for_target(payload: dict, fmt: str) -> dict:
    """Reshape the alert payload for a target's expected webhook schema."""
    if fmt == "slack":
        return {
            "text": (
                f":rotating_light: *DEEPFAKE_ALERT* on stream `{payload['stream_id']}` "
                f"(frame {payload['frame_index']})\n"
                f"score={payload['final_score']:.2f} verdict={payload['audit_verdict']} "
                f"signature={payload['matched_signature']} "
                f"consecutive_frames={payload['consecutive_alert_frames']}"
            )
        }
    return payload


async def _deliver_webhook_fanout(payload: dict) -> None:
    """Fan out webhook delivery to every configured target; one bad target
    (invalid URL, unreachable, non-2xx) must not block or cancel the others."""
    await asyncio.gather(
        *(_deliver_webhook(target, payload) for target in WEBHOOK_TARGETS),
        return_exceptions=True,
    )


async def _deliver_webhook(target: dict, payload: dict, max_attempts: int = 3) -> None:
    """Deliver one webhook alert to one target with 3-attempt exponential backoff."""
    url = target.get("url", "")
    token = target.get("token", "")
    fmt = target.get("format", "generic")
    # Security: SSRF guard — only deliver to validated HTTP(S) URLs
    if not _valid_webhook_url(url):
        logger.error(f"Webhook delivery blocked: target URL '{url}' is not a valid HTTP(S) URL")
        return
    body = _format_for_target(payload, fmt)
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient() as client:
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                resp = await client.post(url, json=body, headers=headers, timeout=5.0)
                resp.raise_for_status()
                logger.info(f"Webhook delivered to {url} on attempt {attempt}")
                return
        except Exception as e:
            logger.warning(f"Webhook delivery to {url} attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                await asyncio.sleep(0.5 * (2 ** attempt))
    logger.error(f"Webhook delivery to {url} failed after {max_attempts} attempts")

@app.post("/temporal_audit", dependencies=[Depends(verify_api_key)])
async def temporal_audit(payload: TemporalAuditResult):
    _latest_temporal[payload.stream_id] = {
        "verdict": payload.temporal_verdict,
        "score": payload.temporal_score,
        "low_confidence": payload.low_confidence_flag,
    }
    ws_event = {
        "type": "temporal_audit",
        **payload.model_dump()
    }
    await manager.broadcast(json.dumps(ws_event))
    
    telemetry = {
        "stream_id": payload.stream_id,
        "window_end_frame": payload.window_end_frame,
        "temporal_score": payload.temporal_score,
        "temporal_verdict": payload.temporal_verdict,
        "latency_ms": payload.latency_ms,
    }
    mlflow_buffer.append(telemetry)

    return {"status": "ok"}

@app.post("/aggregate", response_model=AggregatedResult, dependencies=[Depends(verify_api_key)])
async def aggregate(payload: FramePayload):
    return await process_frame_payload(payload)


class ResetBreakerRequest(BaseModel):
    target: str  # "vision" | "rag" | "both"


@app.post("/admin/reset_breaker", dependencies=[Depends(require_role("admin"))])
async def reset_breaker(req: ResetBreakerRequest):
    """Admin-only (M11): manually close an open circuit breaker instead of
    waiting out VISION_BREAKER_RESET_S/RAG_BREAKER_RESET_S."""
    if req.target not in ("vision", "rag", "both"):
        raise HTTPException(status_code=422, detail="target must be 'vision', 'rag', or 'both'")
    reset = []
    if req.target in ("vision", "both"):
        _vision_breaker.close()
        reset.append("vision")
    if req.target in ("rag", "both"):
        _rag_breaker.close()
        reset.append("rag")
    return {"status": "ok", "reset": reset}

@app.get("/health")
async def health():
    temporal_status = "unavailable"
    if not os.getenv("TESTING"):
        try:
            async with httpx.AsyncClient(verify=CA_CERT if os.path.exists(CA_CERT) else True, cert=_client_cert()) as client:
                resp = await client.get("https://temporal-service:8004/health", timeout=2.0)
                if resp.status_code == 200:
                    temporal_status = "ok"
        except Exception:
            pass
    return {
        "status": "ok",
        "service": "aggregation-service",
        "uptime_s": int(time.time() - START_TIME),
        "temporal_service_status": temporal_status,
        "vision_circuit_state": _vision_breaker.current_state.name.lower(),
        "rag_circuit_state": _rag_breaker.current_state.name.lower(),
    }
