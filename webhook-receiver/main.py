import logging
import time

from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
START_TIME = time.time()

received: list = []
MAX_RECEIVED = 100


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.json()
    logger.info(f"Received webhook: {body}")
    received.append(body)
    if len(received) > MAX_RECEIVED:
        received.pop(0)
    return {"status": "received"}


@app.get("/received")
async def list_received():
    """Debug endpoint for the demo — inspect what's arrived so far."""
    return {"count": len(received), "events": received}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "webhook-receiver", "uptime_s": int(time.time() - START_TIME)}
