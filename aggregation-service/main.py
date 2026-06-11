import os
import time
import json
import asyncio
import logging
import threading
from typing import Dict, List, Optional
from collections import defaultdict

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

import mlflow
from kafka import KafkaConsumer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "test-key")
JWT_SECRET = os.getenv("JWT_SECRET", "super-secret-key")
VISION_URL = os.getenv("VISION_URL", "http://vision-service:8001/infer")
RAG_URL = os.getenv("RAG_URL", "http://rag-agent:8002/audit")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
RAG_TIMEOUT = 0.100  # 100ms

# Setup MLflow
try:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    if not os.getenv("TESTING"):
        mlflow.set_experiment("ardd_pipeline")
except Exception as e:
    logger.warning(f"MLflow setup failed: {e}")

# State
alert_counters: Dict[str, int] = defaultdict(int)
mlflow_buffer: List[dict] = []
MAX_BUFFER = 100

drift_history: Dict[str, List[float]] = defaultdict(list)
labels_buffer: Dict[str, dict] = {}
results_buffer: Dict[str, dict] = {}
MAX_LABELS_BUFFER = 500

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

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

app = FastAPI()

def process_labeled_result(result: dict, label: str):
    if label == "REAL":
        stream_id = result["stream_id"]
        confidence = 1.0 - result["deepfake_score"]
        drift_history[stream_id].append(confidence)
        if len(drift_history[stream_id]) > 100:
            drift_history[stream_id].pop(0)

def start_kafka_consumer():
    if os.getenv("TESTING"):
        return
    try:
        consumer = KafkaConsumer(
            os.getenv("KAFKA_TOPIC_LABELS", "labels"),
            bootstrap_servers=KAFKA_BOOTSTRAP,
            value_deserializer=lambda m: json.loads(m.decode('utf-8'))
        )
        for msg in consumer:
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
    except Exception as e:
        logger.error(f"Kafka consumer error: {e}")

async def mlflow_flush_task():
    while True:
        if mlflow_buffer and not os.getenv("TESTING"):
            items_to_flush = list(mlflow_buffer)
            for t in items_to_flush:
                try:
                    with mlflow.start_run(nested=True):
                        mlflow.log_metrics({
                            "deepfake_score": t.get("deepfake_score", 0.0),
                            "temporal_score": t.get("temporal_score", 0.0),
                            "latency_ms": t.get("latency_ms", 0)
                        })
                        mlflow.log_params({
                            "stream_id": t.get("stream_id", ""),
                            "frame_index": t.get("frame_index", -1),
                            "audit_verdict": t.get("audit_verdict", ""),
                            "temporal_verdict": t.get("temporal_verdict", ""),
                            "drift_flag": t.get("drift_flag", False)
                        })
                    mlflow_buffer.remove(t)
                except Exception as e:
                    logger.error(f"MLflow flush failed: {e}")
                    break
        await asyncio.sleep(10)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(mlflow_flush_task())
    threading.Thread(target=start_kafka_consumer, daemon=True).start()

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
async def websocket_endpoint(websocket: WebSocket, token: str = None):
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

    await manager.connect(websocket)
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

@app.post("/aggregate", response_model=AggregatedResult, dependencies=[Depends(verify_api_key)])
async def aggregate(payload: FramePayload):
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
        
    alert = alert_counters[payload.stream_id] >= 5

    drift_flag = False
    if len(drift_history[payload.stream_id]) == 100:
        avg_confidence = sum(drift_history[payload.stream_id]) / 100.0
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
        "latency_ms": latency_ms,
        "drift_flag": drift_flag
    }
    
    mlflow_buffer.append(telemetry)
    if len(mlflow_buffer) > MAX_BUFFER:
        mlflow_buffer.pop(0)
        
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
    
    return result

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
    if len(mlflow_buffer) > MAX_BUFFER:
        mlflow_buffer.pop(0)
    
    return {"status": "ok"}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "aggregation-service", "uptime_s": 0}
