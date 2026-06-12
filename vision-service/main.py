import os
import base64
import time
import cv2
import numpy as np
import torch
import torchvision.models as models
import torch.nn as nn
from fastapi import FastAPI, HTTPException, Depends, Request
from pydantic import BaseModel
from facenet_pytorch import MTCNN

# Initialize FastAPI
app = FastAPI()

# Config
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "test-key")
START_TIME = time.time()

# Setup Device & MTCNN
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
mtcnn = MTCNN(keep_all=False, device=device)

# Dummy EfficientNet-B4 logic (pre-trained ImageNet model as a placeholder instead of random weights)
class SpatialBranch(nn.Module):
    def __init__(self):
        super().__init__()
        # Load pre-trained ImageNet weights so features aren't completely random noise
        self.model = models.efficientnet_b4(weights=models.EfficientNet_B4_Weights.DEFAULT)
        # Modify the classification head for binary output
        self.model.classifier[1] = nn.Linear(self.model.classifier[1].in_features, 1)
        self.sigmoid = nn.Sigmoid()
        
    def forward(self, x):
        return self.sigmoid(self.model(x))

spatial_branch = SpatialBranch().to(device)
spatial_branch.eval()

# Load weights from disk if they exist (for actual trained deepfake models)
MODEL_WEIGHTS_PATH = os.getenv("MODEL_WEIGHTS_PATH", "/app/weights/efficientnet_b4.pth")
if os.path.exists(MODEL_WEIGHTS_PATH):
    try:
        spatial_branch.load_state_dict(torch.load(MODEL_WEIGHTS_PATH, map_location=device))
        print(f"Loaded trained weights from {MODEL_WEIGHTS_PATH}")
    except Exception as e:
        print(f"Failed to load weights from {MODEL_WEIGHTS_PATH}, falling back to ImageNet: {e}")
else:
    print("No trained weights found at path. Using base ImageNet weights for testing.")

# Helper for FFT frequency branch
def extract_frequency_score(img_gray: np.ndarray) -> float:
    f = np.fft.fft2(img_gray)
    fshift = np.fft.fftshift(f)
    magnitude_spectrum = 20 * np.log(np.abs(fshift) + 1e-8)
    # Simple deterministic heuristic that maps to (0, 1) range for testing
    mean_freq = np.mean(magnitude_spectrum)
    return float(torch.sigmoid(torch.tensor((mean_freq - 200.0) / 10.0)).item())

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
    
    # Check payload size strictly against the 2MB raw-bytes limit.
    # Decode first (fast, raises binascii.Error on invalid base64) then measure.
    try:
        raw_bytes = base64.b64decode(payload.payload)
    except Exception:
        raise HTTPException(status_code=422, detail="Malformed payload: invalid base64")
    if len(raw_bytes) > 2 * 1024 * 1024:  # exactly 2 MiB
        raise HTTPException(status_code=422, detail="Payload exceeds 2MB limit")
        
    try:
        # raw_bytes already decoded above; reuse it
        np_arr = np.frombuffer(raw_bytes, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        
        if img is None:
            raise ValueError("Could not decode image")
            
        # Convert BGR to RGB for MTCNN and models
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        # MTCNN Alignment
        boxes, probs = mtcnn.detect(img_rgb)
        
        aligned = False
        deepfake_score = 0.5
        
        if boxes is not None and len(boxes) > 0:
            # Face found
            aligned = True
            
            # Crop face for spatial branch
            box = boxes[0]
            x1, y1, x2, y2 = [int(b) for b in box]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(img_rgb.shape[1], x2), min(img_rgb.shape[0], y2)
            face_crop = img_rgb[y1:y2, x1:x2]
            
            # Preprocess crop to torch tensor (B, C, H, W)
            if face_crop.size == 0:
                raise ValueError("Empty face crop after alignment")
                
            # EfficientNet-B4 canonical input is 380×380
            face_crop_resized = cv2.resize(face_crop, (380, 380))
            tensor_crop = torch.from_numpy(face_crop_resized).permute(2, 0, 1).float().unsqueeze(0) / 255.0
            tensor_crop = tensor_crop.to(device)
            
            with torch.no_grad():
                spatial_score = spatial_branch(tensor_crop).item()
                
            img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            frequency_score = extract_frequency_score(img_gray)
            
            deepfake_score = 0.6 * spatial_score + 0.4 * frequency_score
            
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Malformed payload or decode failure: {str(e)}")

    latency_ms = int((time.time() - start_time) * 1000)
    
    return VisionResult(
        stream_id=payload.stream_id,
        frame_index=payload.frame_index,
        deepfake_score=deepfake_score,
        aligned=aligned,
        latency_ms=latency_ms
    )

@app.get("/health")
async def health():
    # Check critical dependency readiness
    try:
        _ = spatial_branch  # model loaded
        _ = mtcnn           # MTCNN loaded
    except Exception:
        raise HTTPException(status_code=503, detail="Model not ready")
    uptime_s = int(time.time() - START_TIME)
    return {"status": "ok", "service": "vision-service", "uptime_s": uptime_s}
