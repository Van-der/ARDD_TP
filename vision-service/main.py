import os
import base64
import pickle
import time
import logging
import cv2
import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from facenet_pytorch import MTCNN
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from modeling import SpatialBranch, FftMlp, compute_fft_features, IMAGENET_MEAN, IMAGENET_STD

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROFILE = os.getenv("PROFILE", "false").lower() == "true"

# OpenTelemetry tracing (M8) — local Jaeger via otel-collector, no cloud APM.
# Converts M0's throwaway PROFILE logging into permanent, queryable traces.
_otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
if _otel_endpoint:
    _provider = TracerProvider(resource=Resource.create({"service.name": "vision-service"}))
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_otel_endpoint, insecure=True)))
    trace.set_tracer_provider(_provider)
    FastAPIInstrumentor.instrument_app(app)
tracer = trace.get_tracer(__name__)

INTERNAL_API_KEY  = os.getenv("INTERNAL_API_KEY", "test-key")
SPATIAL_WEIGHTS   = os.getenv("MODEL_WEIGHTS_PATH", "/app/weights/efficientnet_b4_ff++.pt")
FFT_MLP_WEIGHTS   = os.getenv("FFT_MLP_WEIGHTS_PATH", "/app/weights/fft_mlp_ff++.pt")
FUSION_WEIGHTS    = os.getenv("FUSION_WEIGHTS_PATH", "/app/weights/fusion_alpha.npy")
CALIBRATION_PATH  = os.getenv("CALIBRATION_PATH", "/app/weights/calibration.pkl")
START_TIME        = time.time()

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
mtcnn  = MTCNN(keep_all=False, device=device)

# ImageNet normalisation constants as tensors (C, 1, 1) for broadcasting
_MEAN = torch.tensor(IMAGENET_MEAN, device=device).view(3, 1, 1)
_STD  = torch.tensor(IMAGENET_STD,  device=device).view(3, 1, 1)

# ── Spatial branch ────────────────────────────────────────────────────────────
spatial_branch = SpatialBranch().to(device)
spatial_branch_trained = False
if os.path.exists(SPATIAL_WEIGHTS):
    try:
        spatial_branch.load_state_dict(
            torch.load(SPATIAL_WEIGHTS, map_location=device, weights_only=True)
        )
        spatial_branch_trained = True
        print(f"Loaded spatial weights from {SPATIAL_WEIGHTS}")
    except Exception as e:
        print(f"Failed to load spatial weights: {e}. Using ImageNet baseline.")
else:
    print(f"No spatial weights at {SPATIAL_WEIGHTS}. Using ImageNet baseline.")
spatial_branch.eval()

# ── FFT MLP branch ────────────────────────────────────────────────────────────
fft_mlp = FftMlp().to(device)
fft_mlp_trained = False
if os.path.exists(FFT_MLP_WEIGHTS):
    try:
        fft_mlp.load_state_dict(
            torch.load(FFT_MLP_WEIGHTS, map_location=device, weights_only=True)
        )
        fft_mlp_trained = True
        print(f"Loaded FFT MLP weights from {FFT_MLP_WEIGHTS}")
    except Exception as e:
        print(f"Failed to load FFT MLP weights: {e}. Using heuristic fallback.")
else:
    print(f"No FFT MLP weights at {FFT_MLP_WEIGHTS}. Using heuristic fallback.")
fft_mlp.eval()

# ── Fusion ────────────────────────────────────────────────────────────────────
# fusion_params = [w_spatial, w_freq, bias]; score = sigmoid(dot)
fusion_params: np.ndarray | None = None
if os.path.exists(FUSION_WEIGHTS):
    try:
        fusion_params = np.load(FUSION_WEIGHTS).astype(np.float32)
        print(f"Loaded fusion weights from {FUSION_WEIGHTS}")
    except Exception as e:
        print(f"Failed to load fusion weights: {e}. Defaulting to 0.6/0.4 blend.")
else:
    print(f"No fusion weights at {FUSION_WEIGHTS}. Defaulting to 0.6/0.4 blend.")


# ── Confidence calibration (M13) ───────────────────────────────────────────────
# Isotonic regression fit on the same val split used for the fusion logistic
# regression (see fit_calibration.py) — reshapes the fused score's mapping to
# empirical accuracy without changing its rank order (AUC-preserving).
calibrator = None
calibration_trained = False
if os.path.exists(CALIBRATION_PATH):
    try:
        with open(CALIBRATION_PATH, "rb") as f:
            calibrator = pickle.load(f)
        calibration_trained = True
        print(f"Loaded calibration from {CALIBRATION_PATH}")
    except Exception as e:
        print(f"Failed to load calibration: {e}. Scores will be uncalibrated.")
else:
    print(f"No calibration at {CALIBRATION_PATH}. Scores will be uncalibrated.")


def calibrate(score: float) -> float:
    if calibrator is not None:
        return float(calibrator.predict([score])[0])
    return score


def fuse(spatial_score: float, freq_score: float) -> float:
    if fusion_params is not None:
        logit = (fusion_params[0] * spatial_score
                 + fusion_params[1] * freq_score
                 + fusion_params[2])
        return calibrate(float(torch.sigmoid(torch.tensor(logit)).item()))
    return 0.6 * spatial_score + 0.4 * freq_score


def heuristic_fft_score(img_gray: np.ndarray) -> float:
    """Fallback heuristic used when FftMlp weights are not available."""
    f = np.fft.fft2(img_gray)
    fshift = np.fft.fftshift(f)
    magnitude_spectrum = 20 * np.log(np.abs(fshift) + 1e-8)
    mean_freq = float(np.mean(magnitude_spectrum))
    return float(torch.sigmoid(torch.tensor((mean_freq - 200.0) / 10.0)).item())


def extract_frequency_score(img_gray: np.ndarray) -> float:
    """Return frequency-domain fake probability for a grayscale face crop."""
    if fft_mlp_trained:
        fft_feat = torch.from_numpy(compute_fft_features(img_gray)).unsqueeze(0).to(device)
        with torch.no_grad():
            return fft_mlp(fft_feat).item()
    return heuristic_fft_score(img_gray)


# ── Request / response models ─────────────────────────────────────────────────

class FramePayload(BaseModel):
    stream_id: str
    frame_index: int
    timestamp_ms: int
    payload: str


class VisionResult(BaseModel):
    stream_id: str
    frame_index: int
    deepfake_score: float
    aligned: bool
    latency_ms: int


async def verify_api_key(request: Request):
    api_key = request.headers.get("X-API-Key")
    if api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    return api_key


@app.post("/infer", response_model=VisionResult, dependencies=[Depends(verify_api_key)])
async def infer(payload: FramePayload):
    start_time = time.time()
    t_start = time.perf_counter()

    try:
        raw_bytes = base64.b64decode(payload.payload)
    except Exception:
        raise HTTPException(status_code=422, detail="Malformed payload: invalid base64")
    if len(raw_bytes) > 2 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Payload exceeds 2MB limit")

    try:
        np_arr  = np.frombuffer(raw_bytes, np.uint8)
        img_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if img_bgr is None:
            raise ValueError("Could not decode image")

        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        t_decode = time.perf_counter()
        with tracer.start_as_current_span("mtcnn_detect"):
            boxes, _ = mtcnn.detect(img_rgb)
        t_mtcnn = time.perf_counter()

        aligned       = False
        deepfake_score = 0.5
        t_spatial = t_freq = t_mtcnn

        if boxes is not None and len(boxes) > 0:
            aligned = True
            x1, y1, x2, y2 = [int(b) for b in boxes[0]]
            x1 = max(0, x1);  y1 = max(0, y1)
            x2 = min(img_rgb.shape[1], x2);  y2 = min(img_rgb.shape[0], y2)
            face_crop = img_rgb[y1:y2, x1:x2]
            if face_crop.size == 0:
                raise ValueError("Empty face crop")

            face_380 = cv2.resize(face_crop, (380, 380))

            # Spatial branch — with ImageNet normalisation
            tensor = (torch.from_numpy(face_380)
                      .permute(2, 0, 1).float().unsqueeze(0) / 255.0).to(device)
            tensor = (tensor - _MEAN) / _STD
            with tracer.start_as_current_span("spatial_branch"):
                with torch.no_grad():
                    spatial_score = spatial_branch(tensor).item()
            t_spatial = time.perf_counter()

            # FFT branch — computed on face crop, not full frame
            face_gray = cv2.cvtColor(face_380, cv2.COLOR_RGB2GRAY)
            with tracer.start_as_current_span("freq_branch"):
                freq_score = extract_frequency_score(face_gray)
            t_freq = time.perf_counter()

            deepfake_score = fuse(spatial_score, freq_score)

    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Decode failure: {e}")

    latency_ms = int((time.time() - start_time) * 1000)

    if PROFILE:
        logger.info(
            "PROFILE stream=%s frame=%d decode_ms=%.1f mtcnn_ms=%.1f spatial_ms=%.1f freq_ms=%.1f total_ms=%d",
            payload.stream_id, payload.frame_index,
            (t_decode - t_start) * 1000,
            (t_mtcnn - t_decode) * 1000,
            (t_spatial - t_mtcnn) * 1000,
            (t_freq - t_spatial) * 1000,
            latency_ms,
        )

    return VisionResult(
        stream_id=payload.stream_id,
        frame_index=payload.frame_index,
        deepfake_score=deepfake_score,
        aligned=aligned,
        latency_ms=latency_ms,
    )


@app.get("/health")
async def health():
    try:
        _ = spatial_branch
        _ = mtcnn
    except Exception:
        raise HTTPException(status_code=503, detail="Model not ready")
    return {
        "status": "ok",
        "service": "vision-service",
        "uptime_s": int(time.time() - START_TIME),
        "spatial_trained": spatial_branch_trained,
        "fft_mlp_trained": fft_mlp_trained,
        "fusion_trained": fusion_params is not None,
        "calibration_trained": calibration_trained,
    }
