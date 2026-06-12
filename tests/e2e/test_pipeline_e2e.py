import pytest
import os
import time
import json
import base64
import requests
import websocket
from kafka import KafkaProducer

# Docker Compose Environment URLs
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
AGGREGATION_URL = os.getenv("AGGREGATION_URL", "http://localhost:8003")
WS_URL = os.getenv("WS_URL", "ws://localhost:8003/stream")
CLIENT_ID = "test_client"
CLIENT_SECRET = "test_secret"

def get_auth_token():
    """Fetch JWT token from Aggregation Service."""
    resp = requests.post(f"{AGGREGATION_URL}/auth/token", json={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET
    })
    resp.raise_for_status()
    return resp.json()["access_token"]

def test_e2e_kafka_to_websocket_latency():
    """
    E2E Test: Publish to Kafka -> Receive on WebSocket within 200ms.
    Requires the full Docker Compose stack to be running.
    """
    # 1. Connect to WebSocket
    token = get_auth_token()
    ws = websocket.WebSocket()
    ws.connect(f"{WS_URL}?token={token}")
    
    # 2. Connect to Kafka
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )
    
    # Create a dummy frame
    import cv2
    import numpy as np
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    _, buf = cv2.imencode('.jpg', img)
    b64_payload = base64.b64encode(buf.tobytes()).decode('utf-8')
    
    stream_id = f"e2e_test_{int(time.time())}"
    payload = {
        "stream_id": stream_id,
        "frame_index": 0,
        "timestamp_ms": int(time.time() * 1000),
        "payload": b64_payload
    }
    
    # 3. Publish and measure time
    start_time = time.time()
    producer.send("frames", payload)
    producer.flush()
    
    # 4. Wait for WebSocket message
    ws.settimeout(2.0)
    received_message = False
    
    while True:
        try:
            msg_str = ws.recv()
            msg = json.loads(msg_str)
            if msg.get("stream_id") == stream_id:
                received_message = True
                end_time = time.time()
                break
        except websocket.WebSocketTimeoutException:
            break
            
    ws.close()
    
    assert received_message is True, "Did not receive WebSocket event for published frame."
    
    # 5. Assert latency < 200ms
    latency_ms = (end_time - start_time) * 1000
    assert latency_ms <= 200, f"End-to-end latency exceeded 200ms SLA: took {latency_ms:.2f}ms"
