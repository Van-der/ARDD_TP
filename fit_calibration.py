#!/usr/bin/env python3
"""
fit_calibration.py — M13: fit a confidence calibration layer on top of the
already-trained (and already-deployed) fusion scores.

Does NOT retrain the spatial/FFT branches or refit the fusion logistic
regression — it loads the existing weights exactly as vision-service does,
runs one no_grad forward pass over the val split (the same 72/14/14 split
train_vision.py already uses), and fits an isotonic regression mapping
fused score -> calibrated probability. This ties calibration to the weights
actually running in production, which a fresh retrain would not (retraining
introduces randomness and would produce different weights).

Usage:
    python fit_calibration.py
"""
import pickle
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader
from torchvision import transforms
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, str(Path(__file__).parent / "vision-service"))
from modeling import SpatialBranch, FftMlp, IMAGENET_MEAN, IMAGENET_STD

from train_vision import FaceCropDataset, get_split_dirs, evaluate, CROP_SIZE

MODEL_WEIGHTS = Path(__file__).parent / "model-weights"


def fuse(spatial_score: np.ndarray, freq_score: np.ndarray, fusion_params: np.ndarray) -> np.ndarray:
    logit = fusion_params[0] * spatial_score + fusion_params[1] * freq_score + fusion_params[2]
    return torch.sigmoid(torch.from_numpy(logit)).numpy()


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    real_train, real_val, real_test = get_split_dirs("real")
    fake_train, fake_val, fake_test = get_split_dirs("fake")

    val_tf = transforms.Compose([
        transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
    ])
    val_ds = FaceCropDataset(real_val, fake_val, spatial_transform=val_tf)
    val_loader = DataLoader(val_ds, batch_size=8, shuffle=False, num_workers=4, pin_memory=True)
    print(f"Val crops: {len(val_ds):,}")

    spatial = SpatialBranch().to(device)
    fft_mlp = FftMlp().to(device)
    spatial.load_state_dict(torch.load(MODEL_WEIGHTS / "efficientnet_b4_ff++.pt", map_location=device, weights_only=True))
    fft_mlp.load_state_dict(torch.load(MODEL_WEIGHTS / "fft_mlp_ff++.pt", map_location=device, weights_only=True))

    fusion_params = np.load(MODEL_WEIGHTS / "fusion_alpha.npy").astype(np.float32)

    print("Running val set forward pass (no training)...")
    _, _, s_val, f_val, y_val = evaluate(spatial, fft_mlp, val_loader, device)
    fused_val = fuse(s_val, f_val, fusion_params)

    np.save(MODEL_WEIGHTS / "calibration_val_scores.npy", fused_val)
    np.save(MODEL_WEIGHTS / "calibration_val_labels.npy", y_val)
    print(f"Saved calibration_val_scores.npy / calibration_val_labels.npy ({len(fused_val)} samples)")

    calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    calibrator.fit(fused_val, y_val)

    with open(MODEL_WEIGHTS / "calibration.pkl", "wb") as f:
        pickle.dump(calibrator, f)
    print(f"Saved calibration.pkl")

    # Sanity: calibration must be monotonic in raw fused score.
    probe = np.linspace(0.0, 1.0, 21)
    calibrated_probe = calibrator.predict(probe)
    assert np.all(np.diff(calibrated_probe) >= 0), "Calibrator is not monotonic — this should be impossible for IsotonicRegression"
    print("Monotonicity check passed.")


if __name__ == "__main__":
    main()
