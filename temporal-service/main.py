import os
import time
import base64
import json
import logging
import asyncio
from collections import defaultdict, deque
from typing import Dict, Any

import torch
import torch.nn.functional as F
from torchvision import transforms
from PIL import Image
import io

import httpx
from fastapi import FastAPI, HTTPException, Depends, Request
from pydantic import BaseModel
from aiokafka import AIOKafkaConsumer

from modeling import DeepFakeDetector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "test-key")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC_FRAMES", "frames")
KAFKA_SECURITY_PROTOCOL = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
KAFKA_SASL_USERNAME = os.getenv("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASSWORD = os.getenv("KAFKA_SASL_PASSWORD", "")
AGGREGATION_URL = os.getenv("AGGREGATION_URL", "http://aggregation-service:8003")

def _kafka_sasl_kwargs() -> dict:
    if KAFKA_SECURITY_PROTOCOL == "SASL_PLAINTEXT":
        return {
            "security_protocol": "SASL_PLAINTEXT",
            "sasl_mechanism": "PLAIN",
            "sasl_plain_username": KAFKA_SASL_USERNAME,
            "sasl_plain_password": KAFKA_SASL_PASSWORD,
        }
    return {}
WEIGHTS_PATH = os.getenv("MODEL_WEIGHTS_PATH", "/app/weights/model_87_acc_20_frames_final_data.pt")
START_TIME = time.time()
TARGET_FRAMES = 20

# Initialization
app = FastAPI(title="ARDD-TP Temporal Service")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model_used = "random-fallback"

model = DeepFakeDetector()
try:
    if os.path.exists(WEIGHTS_PATH):
        model.load_state_dict(torch.load(WEIGHTS_PATH, map_location=device, weights_only=True))
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
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{AGGREGATION_URL}/temporal_audit", 
                json=payload, 
                headers={"X-API-Key": INTERNAL_API_KEY}, 
                timeout=5.0
            )
            resp.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send audit result to aggregation: {e}")

async def run_inference_and_flush(stream_id: str):
    buffer = stream_buffers[stream_id]
    n_frames = len(buffer)
    items = list(buffer)
    buffer.clear()

    frame_indices = [fi for fi, _ in items]
    frames = [t for _, t in items]
    window_start = frame_indices[0] if frame_indices else 0
    window_end = frame_indices[-1] if frame_indices else 0

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

    if n_frames < TARGET_FRAMES:
        pad_size = TARGET_FRAMES - n_frames
        zero_tensor = torch.zeros_like(frames[0])
        frames.extend([zero_tensor] * pad_size)

    batch = torch.stack(frames).unsqueeze(0).to(device)  # shape: (1, 20, 3, 112, 112)

    with torch.no_grad():
        _, logits = model(batch)
        probs = F.softmax(logits, dim=1)
        fake_prob = probs[0][0].item()

    verdict = "FAIL" if fake_prob > 0.5 else "PASS"
    latency_ms = int((time.time() - start_time) * 1000)

    logger.info(f"Inference complete: stream={stream_id} score={fake_prob:.3f}")

    payload = {
        "stream_id": stream_id,
        "window_start_frame": window_start,
        "window_end_frame": window_end,
        "window_duration_s": n_frames / 30.0,
        "temporal_score": fake_prob,
        "temporal_verdict": verdict,
        "low_confidence_flag": n_frames < TARGET_FRAMES,
        "frames_interpolated": 0,  # zero-pads are not interpolation; gap interpolation not yet implemented
        "model_used": model_used,
        "latency_ms": latency_ms,
        "timestamp_ms": int(time.time() * 1000)
    }

    await send_audit_result(payload)

async def frames_consumer_task():
    if os.getenv("TESTING"):
        return
    consumer = AIOKafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id="temporal-service-group",
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        **_kafka_sasl_kwargs()
    )
    await consumer.start()
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
                stream_buffers[stream_id].append((frame_index, tensor))
                
                if len(stream_buffers[stream_id]) == TARGET_FRAMES:
                    await run_inference_and_flush(stream_id)
            except Exception as e:
                logger.error(f"Error processing frame for {stream_id}: {e}")
    finally:
        await consumer.stop()

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(frames_consumer_task())

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
    if req.stream_id in stream_buffers:
        await run_inference_and_flush(req.stream_id)
    return {"status": "flushed", "stream_id": req.stream_id}
