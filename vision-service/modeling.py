import numpy as np
import torch
import torch.nn as nn
import torchvision.models as models
from torchvision.models import EfficientNet_B4_Weights

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD  = [0.229, 0.224, 0.225]
FFT_BINS = 64


def compute_fft_features(img_gray: np.ndarray, n_bins: int = FFT_BINS) -> np.ndarray:
    """Compute a 64-dim radial FFT bin vector from a grayscale image array."""
    f = np.fft.fft2(img_gray.astype(np.float32))
    fshift = np.fft.fftshift(f)
    magnitude = np.abs(fshift)

    h, w = magnitude.shape
    cy, cx = h // 2, w // 2
    y_idx, x_idx = np.indices((h, w))
    r = np.sqrt((x_idx - cx) ** 2 + (y_idx - cy) ** 2).astype(np.float32)

    max_r = float(np.sqrt(cx ** 2 + cy ** 2))
    bin_edges = np.linspace(0.0, max_r, n_bins + 1)
    features = np.zeros(n_bins, dtype=np.float32)
    for i in range(n_bins):
        mask = (r >= bin_edges[i]) & (r < bin_edges[i + 1])
        if mask.any():
            features[i] = magnitude[mask].mean()

    max_val = features.max()
    if max_val > 0:
        features /= max_val
    return features


class SpatialBranch(nn.Module):
    def __init__(self):
        super().__init__()
        base = models.efficientnet_b4(weights=EfficientNet_B4_Weights.DEFAULT)
        base.classifier[1] = nn.Linear(base.classifier[1].in_features, 1)
        self.model = base
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.sigmoid(self.model(x))


class FftMlp(nn.Module):
    def __init__(self, input_dim: int = FFT_BINS):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 32),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)
