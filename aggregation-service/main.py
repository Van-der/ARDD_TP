import os
import time
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Optional
from collections import defaultdict, deque

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import mlflow
from aiokafka import AIOKafkaConsumer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
START_TIME = time.time()

# Config
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "test-key")
JWT_SECRET = os.getenv("JWT_SECRET", "ardd-tp-dev-secret-key-change-me!")
VISION_URL = os.getenv("VISION_URL", "http://vision-service:8001/infer")
RAG_URL = os.getenv("RAG_URL", "http://rag-agent:8002/audit")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_SECURITY_PROTOCOL = os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
KAFKA_SASL_USERNAME = os.getenv("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASSWORD = os.getenv("KAFKA_SASL_PASSWORD", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
RAG_TIMEOUT = 0.100  # 100ms

def _kafka_sasl_kwargs() -> dict:
    return {
        "security_protocol": "SASL_PLAINTEXT",
        "sasl_mechanism": "PLAIN",
        "sasl_plain_username": "admin",
        "sasl_plain_password": "admin-secret",
        "api_version": "auto",
    }

# Setup MLflow
try:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    if not os.getenv("TESTING"):
        mlflow.set_experiment("ardd_pipeline")
except Exception as e:
    logger.warning(f"MLflow setup failed: {e}")

# State
alert_counters: Dict[str, int] = defaultdict(int)
mlflow_buffer: deque = deque(maxlen=100)
MAX_STREAMS = 1000  # cap on unique stream_id keys to prevent unbounded memory growth

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

@asynccontextmanager
async def lifespan(app: FastAPI):
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

def process_labeled_result(result: dict, label: str):
    if label == "REAL":
        stream_id = result["stream_id"]
        confidence = 1.0 - result["deepfake_score"]
        drift_history[stream_id].append(confidence)  # deque(maxlen=100) auto-trims

async def start_labels_consumer():
    if os.getenv("TESTING"):
        return
    while True:
        try:
            consumer = AIOKafkaConsumer(
                os.getenv("KAFKA_TOPIC_LABELS", "labels"),
                bootstrap_servers=KAFKA_BOOTSTRAP,
                group_id="aggregation-labels-group",
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                **_kafka_sasl_kwargs()
            )
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
                        process_labeled_result(result, label)
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

async def start_frames_consumer():
    if os.getenv("TESTING"):
        return
    while True:
        try:
            consumer = AIOKafkaConsumer(
                "frames",
                bootstrap_servers=KAFKA_BOOTSTRAP,
                group_id="aggregation-pipeline-group",
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                **_kafka_sasl_kwargs()
            )
            await consumer.start()
            logger.info("Frames consumer started.")
            try:
                async for msg in consumer:
                    try:
                        payload = FramePayload(**msg.value)
                        await process_frame_payload(payload)
                    except Exception as e:
                        logger.error(f"Pipeline error processing frame: {e}")
            finally:
                await consumer.stop()
        except Exception as e:
            logger.error(f"Frames consumer crashed: {e}. Retrying in 5s...")
            await asyncio.sleep(5)

async def mlflow_flush_task():
    while True:
        if mlflow_buffer and not os.getenv("TESTING"):
            items_to_flush = list(mlflow_buffer)
            for t in items_to_flush:
                try:
                    with mlflow.start_run(nested=True):
                        metrics = {"latency_ms": t.get("latency_ms", 0)}
                        if "deepfake_score" in t:
                            metrics["deepfake_score"] = t["deepfake_score"]
                        if "temporal_score" in t:
                            metrics["temporal_score"] = t["temporal_score"]
                        mlflow.log_metrics(metrics)

                        params = {"stream_id": t.get("stream_id", "")}
                        if "frame_index" in t:
                            params["frame_index"] = t["frame_index"]
                        if "audit_verdict" in t:
                            params["audit_verdict"] = t["audit_verdict"]
                        if "temporal_verdict" in t:
                            params["temporal_verdict"] = t["temporal_verdict"]
                        if "drift_flag" in t:
                            params["drift_flag"] = t["drift_flag"]
                        mlflow.log_params(params)
                    mlflow_buffer.remove(t)
                except Exception as e:
                    logger.error(f"MLflow flush failed: {e}")
                    break
        await asyncio.sleep(10)

async def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    if api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    return api_key

@app.post("/auth/token")
async def login(req: TokenRequest):
    if not req.client_id or not req.client_secret:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = jwt.encode({"sub": req.client_id, "exp": time.time() + 3600}, JWT_SECRET, algorithm="HS256")
    return {"access_token": token, "expires_in": 3600}

@app.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket):
    # JWT passed via Sec-WebSocket-Protocol subprotocol — avoids token leakage in server access logs
    token = websocket.headers.get("sec-websocket-protocol", "").split(",")[0].strip()
    if not token:
        await websocket.close(code=1008)
        return
    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        await websocket.close(code=1008)
        return
    except jwt.InvalidTokenError:
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
    async with httpx.AsyncClient() as client:
        resp = await client.post(VISION_URL, json=payload, headers={"X-API-Key": INTERNAL_API_KEY}, timeout=5.0)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Vision Service Error")
        return resp.json()

async def call_rag(stream_id: str, frame_index: int, score: float) -> dict:
    req = {
        "stream_id": stream_id,
        "frame_index": frame_index,
        "deepfake_score": score
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(RAG_URL, json=req, headers={"X-API-Key": INTERNAL_API_KEY}, timeout=RAG_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

async def process_frame_payload(payload: FramePayload) -> AggregatedResult:
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
    
    try:
        rag_res = await call_rag(payload.stream_id, payload.frame_index, deepfake_score)
        rag_used = True
        audit_verdict = rag_res.get("audit_verdict", "UNKNOWN")
        matched_signature = rag_res.get("matched_signature", None)
    except httpx.TimeoutException:
        logger.warning(f"RAG timeout exceeded {RAG_TIMEOUT}s")
    except Exception as e:
        logger.warning(f"RAG failure: {e}")

    final_score = deepfake_score
    if audit_verdict == "FAIL":
        final_score = deepfake_score * (1.0 + 0.15)
        
    final_score = min(max(final_score, 0.0), 1.0)
    
    if final_score > 0.90:
        alert_counters[payload.stream_id] += 1
    else:
        alert_counters[payload.stream_id] = 0
    _evict_oldest(alert_counters, MAX_STREAMS)

    alert = alert_counters[payload.stream_id] >= 5

    drift_flag = False
    history = drift_history[payload.stream_id]
    _evict_oldest(drift_history, MAX_STREAMS)
    if len(history) >= 100:  # rolling: evaluate continuously once window is full
        avg_confidence = sum(history) / len(history)
        if avg_confidence < 0.60:
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
        drift_flag=drift_flag
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
        process_labeled_result(result_data, label_data["label"])
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
        "timestamp_ms": payload.timestamp_ms
    }
    await manager.broadcast(json.dumps(ws_event))

    # Webhook alert delivery (3 attempts with backoff) — SCHEMA.md §8
    if alert and WEBHOOK_URL:
        webhook_payload = {
            "event": "DEEPFAKE_ALERT",
            "stream_id": payload.stream_id,
            "frame_index": payload.frame_index,
            "final_score": final_score,
            "audit_verdict": audit_verdict,
            "matched_signature": matched_signature,
            "consecutive_alert_frames": alert_counters[payload.stream_id],
            "timestamp_ms": payload.timestamp_ms
        }
        asyncio.create_task(_deliver_webhook(webhook_payload))

    return result


async def _deliver_webhook(payload: dict, max_attempts: int = 3) -> None:
    """Deliver webhook alert with 3-attempt exponential backoff."""
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient() as client:
                headers = {"Authorization": f"Bearer {WEBHOOK_TOKEN}"} if WEBHOOK_TOKEN else {}
                resp = await client.post(WEBHOOK_URL, json=payload, headers=headers, timeout=5.0)
                resp.raise_for_status()
                logger.info(f"Webhook delivered on attempt {attempt}")
                return
        except Exception as e:
            logger.warning(f"Webhook delivery attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                await asyncio.sleep(0.5 * (2 ** attempt))
    logger.error("Webhook delivery failed after 3 attempts")

@app.post("/temporal_audit", dependencies=[Depends(verify_api_key)])
async def temporal_audit(payload: TemporalAuditResult):
    ws_event = {
        "type": "temporal_audit",
        **payload.model_dump()
    }
    await manager.broadcast(json.dumps(ws_event))
    
    telemetry = {
        "stream_id": payload.stream_id,
        "temporal_score": payload.temporal_score,
        "temporal_verdict": payload.temporal_verdict,
        "latency_ms": payload.latency_ms
    }
    mlflow_buffer.append(telemetry)

    return {"status": "ok"}

@app.post("/aggregate", response_model=AggregatedResult, dependencies=[Depends(verify_api_key)])
async def aggregate(payload: FramePayload):
    return await process_frame_payload(payload)

@app.get("/health")
async def health():
    temporal_status = "unavailable"
    if not os.getenv("TESTING"):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://temporal-service:8004/health", timeout=2.0)
                if resp.status_code == 200:
                    temporal_status = "ok"
        except Exception:
            pass
    return {
        "status": "ok", 
        "service": "aggregation-service", 
        "uptime_s": int(time.time() - START_TIME),
        "temporal_service_status": temporal_status
    }
