import os
import ssl
import time
import base64
import json
import pickle
import logging
import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional

import torch
import torch.nn.functional as F
from torchvision import transforms
from PIL import Image
import io

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Depends, Request
from pydantic import BaseModel
from aiokafka import AIOKafkaConsumer
from aiokafka.abc import ConsumerRebalanceListener
from aiokafka.coordinator.assignors.sticky.sticky_assignor import StickyPartitionAssignor

from modeling import DeepFakeDetector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "test-key")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC_FRAMES", "frames")
KAFKA_SECURITY_PROTOCOL = os.getenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
KAFKA_SASL_USERNAME = os.getenv("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASSWORD = os.getenv("KAFKA_SASL_PASSWORD", "")
CA_CERT = os.getenv("CA_CERT", "/certs/ca.crt")
AGGREGATION_URL = os.getenv("AGGREGATION_URL", "https://aggregation-service:8003")
# mTLS (M10): aggregation-service is CERT_OPTIONAL, not CERT_REQUIRED, so this
# isn't strictly required — presented anyway to match the full-mesh intent.
CLIENT_CERT_FILE = os.getenv("CLIENT_CERT_FILE", "/certs/temporal-service.crt")
CLIENT_KEY_FILE = os.getenv("CLIENT_KEY_FILE", "/certs/temporal-service.key")


def _client_cert():
    if os.path.exists(CLIENT_CERT_FILE) and os.path.exists(CLIENT_KEY_FILE):
        return (CLIENT_CERT_FILE, CLIENT_KEY_FILE)
    return None

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
WEIGHTS_PATH = os.getenv("MODEL_WEIGHTS_PATH", "/app/weights/model_87_acc_20_frames_final_data.pt")
START_TIME = time.time()
TARGET_FRAMES = 20
REDIS_URL = os.getenv("REDIS_URL", "")
REDIS_KEY_TTL_S = 300  # a partial window uncompleted for 5min indicates a dead stream

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(frames_consumer_task())
    yield

# Initialization
app = FastAPI(title="ARDD-TP Temporal Service", lifespan=lifespan)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model_used = "random-fallback"

model = DeepFakeDetector()
try:
    if os.path.exists(WEIGHTS_PATH):
        raw_sd = torch.load(WEIGHTS_PATH, map_location=device, weights_only=True)
        # Checkpoint uses "model.*" keys; our class uses "backbone.*" — remap on load
        remapped = {
            k.replace("model.", "backbone.", 1) if k.startswith("model.") else k: v
            for k, v in raw_sd.items()
        }
        missing, unexpected = model.load_state_dict(remapped, strict=False)
        if missing:
            logger.warning(f"Weights loaded with {len(missing)} unexpected missing keys: {missing}")
        model_used = "resnext50-lstm-v1"
        logger.info(f"Loaded weights from {WEIGHTS_PATH}")
    else:
        logger.warning(f"Weights file not found at {WEIGHTS_PATH}. Using random fallback.")
except Exception as e:
    logger.error(f"Failed to load weights: {e}. Using random fallback.")

model.to(device)
model.eval()

# Transform
preprocess = transforms.Compose([
    transforms.Resize((112, 112)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])

# State
stream_buffers: Dict[str, deque] = defaultdict(lambda: deque(maxlen=TARGET_FRAMES))

# --- Redis-backed shared buffer, with process-local fallback ---
# Makes the 20-frame tumbling window shared across multiple temporal-service
# replicas (M3's --scale testing): whichever replica pushes the frame that
# completes a window "wins" the flush atomically via the Lua script below, so
# two replicas never double-run inference on the same window. If REDIS_URL is
# unset, or Redis is unreachable, falls back to the process-local
# `stream_buffers` deque exactly as before — Redis is never a hard dependency.
# Known limitation: /health and /batch_status report `stream_buffers` sizes
# only, so they under-report buffer fill levels while Redis is the active
# backing store (observability gap, not a correctness issue).
_redis: Optional["aioredis.Redis"] = None
_redis_was_available = False

# KEYS[1]=buffer key, ARGV[1]=pickled (frame_index, tensor) blob,
# ARGV[2]=target frame count, ARGV[3]=TTL seconds.
# Atomically pushes the frame and, only once the window is exactly full,
# claims (reads + deletes) the whole buffer in the same round trip so no
# other replica can also claim it.
_FLUSH_AND_CLAIM_SCRIPT = """
redis.call('RPUSH', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[3])
local len = redis.call('LLEN', KEYS[1])
if len >= tonumber(ARGV[2]) then
    local items = redis.call('LRANGE', KEYS[1], 0, -1)
    redis.call('DEL', KEYS[1])
    return items
else
    return nil
end
"""

async def _redis_client() -> Optional["aioredis.Redis"]:
    global _redis, _redis_was_available
    if not REDIS_URL:
        return None
    if _redis is None:
        _redis = aioredis.from_url(
            REDIS_URL, decode_responses=False,  # binary mode — buffer values are pickled tensors
            socket_connect_timeout=1, socket_timeout=1,
        )
    try:
        await _redis.ping()
        _redis_was_available = True
        return _redis
    except Exception:
        if _redis_was_available:
            logger.warning("Redis unavailable — falling back to process-local stream buffers.")
        _redis_was_available = False
        return None

async def _push_frame_and_maybe_flush(stream_id: str, frame_index: int, tensor: torch.Tensor) -> Optional[list]:
    """Push a frame into the stream's window; returns the full list of
    (frame_index, tensor) items if the window just completed (caller should
    run inference), else None."""
    r = await _redis_client()
    if r is not None:
        try:
            key = f"temporal:buf:{stream_id}"
            blob = pickle.dumps((frame_index, tensor))
            raw = await r.eval(_FLUSH_AND_CLAIM_SCRIPT, 1, key, blob, TARGET_FRAMES, REDIS_KEY_TTL_S)
            if raw is None:
                return None
            return [pickle.loads(item) for item in raw]
        except Exception:
            pass
    buffer = stream_buffers[stream_id]
    buffer.append((frame_index, tensor))
    if len(buffer) == TARGET_FRAMES:
        items = list(buffer)
        buffer.clear()
        return items
    return None

async def _pop_all_frames(stream_id: str) -> Optional[list]:
    """Manual/early flush (POST /flush): pop whatever is currently buffered
    for stream_id, partial or full. Returns None if the stream has never been
    touched (flush is then a no-op, matching prior behavior)."""
    r = await _redis_client()
    if r is not None:
        try:
            key = f"temporal:buf:{stream_id}"
            if not await r.exists(key):
                return None
            raw = await r.lrange(key, 0, -1)
            await r.delete(key)
            return [pickle.loads(item) for item in raw]
        except Exception:
            pass
    if stream_id not in stream_buffers:
        return None
    buffer = stream_buffers[stream_id]
    items = list(buffer)
    buffer.clear()
    return items

class FlushRequest(BaseModel):
    stream_id: str

async def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    if api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    return api_key

def process_frame(payload_b64: str) -> torch.Tensor:
    image_data = base64.b64decode(payload_b64)
    image = Image.open(io.BytesIO(image_data)).convert('RGB')
    tensor = preprocess(image)
    return tensor

async def send_audit_result(payload: dict):
    try:
        async with httpx.AsyncClient(verify=CA_CERT if os.path.exists(CA_CERT) else True, cert=_client_cert()) as client:
            resp = await client.post(
                f"{AGGREGATION_URL}/temporal_audit",
                json=payload, 
                headers={"X-API-Key": INTERNAL_API_KEY}, 
                timeout=5.0
            )
            resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send audit result to aggregation: {e}")

async def run_inference_and_flush(stream_id: str, items: list):
    n_frames = len(items)

    window_start = items[0][0] if items else 0
    window_end = items[-1][0] if items else 0

    if n_frames < 6:
        logger.warning(f"Flush aborted for {stream_id}, insufficient frames ({n_frames})")
        payload = {
            "stream_id": stream_id,
            "window_start_frame": window_start,
            "window_end_frame": window_end,
            "window_duration_s": n_frames / 30.0,
            "temporal_score": 0.5,
            "temporal_verdict": "UNKNOWN",
            "low_confidence_flag": True,
            "frames_interpolated": 0,
            "model_used": model_used,
            "latency_ms": 0,
            "timestamp_ms": int(time.time() * 1000)
        }
        await send_audit_result(payload)
        return

    start_time = time.time()

    # Sort by frame_index and interpolate tensors across any gaps
    items_sorted = sorted(items, key=lambda x: x[0])
    filled: list = []
    interpolated_count = 0
    for i in range(len(items_sorted) - 1):
        filled.append(items_sorted[i])
        idx_a, t_a = items_sorted[i]
        idx_b, t_b = items_sorted[i + 1]
        for k in range(1, idx_b - idx_a):
            alpha = k / (idx_b - idx_a)
            filled.append((idx_a + k, (1 - alpha) * t_a + alpha * t_b))
            interpolated_count += 1
    filled.append(items_sorted[-1])

    frames = [t for _, t in filled]
    n_filled = len(frames)

    if n_filled < TARGET_FRAMES:
        pad_size = TARGET_FRAMES - n_filled
        zero_tensor = torch.zeros_like(frames[0])
        frames.extend([zero_tensor] * pad_size)

    batch = torch.stack(frames[:TARGET_FRAMES]).unsqueeze(0).to(device)  # (1, 20, 3, 112, 112)

    with torch.no_grad():
        _, logits = model(batch)
        probs = F.softmax(logits, dim=1)
        fake_prob = probs[0][0].item()

    verdict = "FAIL" if fake_prob > 0.5 else "PASS"
    latency_ms = int((time.time() - start_time) * 1000)

    logger.info(f"Inference complete: stream={stream_id} score={fake_prob:.3f} interpolated={interpolated_count}")

    payload = {
        "stream_id": stream_id,
        "window_start_frame": window_start,
        "window_end_frame": window_end,
        "window_duration_s": n_frames / 30.0,
        "temporal_score": fake_prob,
        "temporal_verdict": verdict,
        "low_confidence_flag": n_frames < TARGET_FRAMES,
        "frames_interpolated": interpolated_count,
        "model_used": model_used,
        "latency_ms": latency_ms,
        "timestamp_ms": int(time.time() * 1000)
    }

    await send_audit_result(payload)

class RebalanceLogger(ConsumerRebalanceListener):
    """Logs partition revoke/assign events during a consumer-group rebalance.

    Note: aiokafka has no incremental/cooperative rebalance protocol (unlike
    the Java client) — StickyPartitionAssignor minimizes partition churn
    across rebalances, the best available mitigation here. Per-stream buffers
    in `stream_buffers` are process-local, so a stream whose partition moves
    to a different replica mid-window will under-fill; this fails safe via
    the existing N<20 zero-pad / N<6 UNKNOWN fallback (see M4 for the
    Redis-backed fix that removes this limitation).
    """
    def __init__(self, name: str):
        self.name = name

    async def on_partitions_revoked(self, revoked):
        if revoked:
            logger.warning(f"[{self.name}] Rebalance: partitions revoked: {sorted(revoked)}")

    async def on_partitions_assigned(self, assigned):
        if assigned:
            logger.info(f"[{self.name}] Rebalance: partitions assigned: {sorted(assigned)}")

async def frames_consumer_task():
    if os.getenv("TESTING"):
        return
    while True:
        try:
            consumer = AIOKafkaConsumer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                group_id="temporal-service-group",
                auto_offset_reset="latest",
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                partition_assignment_strategy=(StickyPartitionAssignor,),
                **_kafka_sasl_kwargs()
            )
            consumer.subscribe(topics=[KAFKA_TOPIC],
                               listener=RebalanceLogger("temporal-service-group"))
            await consumer.start()
            logger.info("Temporal frames consumer started.")
            try:
                async for msg in consumer:
                    data = msg.value
                    stream_id = data.get("stream_id")
                    frame_index = data.get("frame_index", 0)
                    payload_b64 = data.get("payload")
                    if not stream_id or not payload_b64:
                        continue
                    try:
                        tensor = process_frame(payload_b64)
                        items = await _push_frame_and_maybe_flush(stream_id, frame_index, tensor)
                        if items is not None:
                            await run_inference_and_flush(stream_id, items)
                    except Exception as e:
                        logger.error(f"Error processing frame for {stream_id}: {e}")
            finally:
                await consumer.stop()
        except Exception as e:
            logger.error(f"Temporal frames consumer crashed: {e}. Retrying in 5s...")
            await asyncio.sleep(5)

@app.get("/health")
async def health():
    return {
        "status": "ok", 
        "service": "temporal-service", 
        "uptime_s": int(time.time() - START_TIME),
        "buffer_sizes": {k: len(v) for k, v in stream_buffers.items()}
    }

@app.get("/batch_status", dependencies=[Depends(verify_api_key)])
async def batch_status():
    return [{"stream_id": k, "buffer_size": len(v), "target": TARGET_FRAMES} for k, v in stream_buffers.items()]

@app.post("/flush", dependencies=[Depends(verify_api_key)])
async def flush_buffer(req: FlushRequest):
    items = await _pop_all_frames(req.stream_id)
    if items is not None:
        await run_inference_and_flush(req.stream_id, items)
    return {"status": "flushed", "stream_id": req.stream_id}
