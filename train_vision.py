#!/usr/bin/env python3
"""
train_vision.py — Phase 2.5: Train EfficientNet-B4 spatial branch + FFT MLP.

Training order:
    1. Trains SpatialBranch (EfficientNet-B4) and FftMlp simultaneously for N epochs.
    2. Fits a logistic regression fusion head on the val set.
    3. Evaluates on the test set.
    4. Saves state_dicts + fusion weights to checkpoints/.

Prerequisites:
    - Run extract_faces.py first to populate face_crops/real/ and face_crops/fake/
    - pip install scikit-learn (or: pip install -r requirements.txt)

Usage:
    python train_vision.py [--epochs 10] [--batch-size 16] [--workers 4]
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.cuda.amp import GradScaler, autocast
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score

sys.path.insert(0, str(Path(__file__).parent / "vision-service"))
from modeling import SpatialBranch, FftMlp, compute_fft_features, IMAGENET_MEAN, IMAGENET_STD

FACE_CROPS   = Path(__file__).parent / "face_crops"
MODEL_WEIGHTS = Path(__file__).parent / "model-weights"
MODEL_WEIGHTS.mkdir(exist_ok=True)

CROP_SIZE   = 380
TRAIN_FRAC  = 0.72   # 720 / 1000 (official FF++ split)
VAL_FRAC    = 0.14   # 140 / 1000


# ──────────────────────────────────────────────────────────────────────────────
# Dataset
# ──────────────────────────────────────────────────────────────────────────────

class FaceCropDataset(Dataset):
    def __init__(self, real_dirs: list, fake_dirs: list, spatial_transform=None):
        self.samples: list = []
        for d in real_dirs:
            self.samples.extend((p, 0) for p in sorted(d.glob("*.jpg")))
        for d in fake_dirs:
            self.samples.extend((p, 1) for p in sorted(d.glob("*.jpg")))
        self.spatial_transform = spatial_transform

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        path, label = self.samples[idx]

        img_bgr = cv2.imread(str(path))
        if img_bgr is None:
            # Return zeros on corrupt file rather than crashing the worker
            return (
                torch.zeros(3, CROP_SIZE, CROP_SIZE),
                torch.zeros(64, dtype=torch.float32),
                label,
            )

        img_gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        fft_feat = torch.from_numpy(compute_fft_features(img_gray))

        img_rgb  = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        tensor   = torch.from_numpy(img_rgb).permute(2, 0, 1).float() / 255.0
        if self.spatial_transform:
            tensor = self.spatial_transform(tensor)

        return tensor, fft_feat, label


def get_split_dirs(label: str) -> tuple:
    base = FACE_CROPS / label
    if not base.exists():
        print(f"[ERROR] {base} not found. Run extract_faces.py first.")
        sys.exit(1)
    dirs = sorted(d for d in base.iterdir() if d.is_dir())
    n       = len(dirs)
    n_train = int(n * TRAIN_FRAC)
    n_val   = int(n * VAL_FRAC)
    return dirs[:n_train], dirs[n_train:n_train + n_val], dirs[n_train + n_val:]


# ──────────────────────────────────────────────────────────────────────────────
# Loss: weighted BCE for 2:1 fake:real imbalance
# ──────────────────────────────────────────────────────────────────────────────

def weighted_bce(pred: torch.Tensor, target: torch.Tensor, fake_weight: float = 2.0) -> torch.Tensor:
    weights = torch.where(
        target.bool(),
        torch.full_like(pred, fake_weight),
        torch.ones_like(pred),
    )
    return F.binary_cross_entropy(pred, target.float(), weight=weights)


# ──────────────────────────────────────────────────────────────────────────────
# Train / eval loops
# ──────────────────────────────────────────────────────────────────────────────

def train_epoch(spatial: nn.Module, fft_mlp: nn.Module,
                loader: DataLoader, s_opt, f_opt,
                s_scaler: GradScaler, f_scaler: GradScaler,
                device: torch.device) -> tuple:
    spatial.train()
    fft_mlp.train()
    s_correct = f_correct = total = 0
    s_loss_sum = f_loss_sum = 0.0

    for batch_idx, (imgs, fft_feats, labels) in enumerate(loader):
        imgs      = imgs.to(device)
        fft_feats = fft_feats.to(device)
        labels    = labels.to(device)

        # Spatial branch step — forward in FP16, loss in FP32 (BCELoss is autocast-unsafe)
        s_opt.zero_grad(set_to_none=True)
        with autocast():
            s_pred = spatial(imgs).squeeze(1).float()
        s_loss = weighted_bce(s_pred, labels)
        s_scaler.scale(s_loss).backward()
        s_scaler.step(s_opt)
        s_scaler.update()

        # Free activation memory before FFT branch
        del imgs
        torch.cuda.empty_cache()

        # FFT branch step — same pattern
        f_opt.zero_grad(set_to_none=True)
        with autocast():
            f_pred = fft_mlp(fft_feats).squeeze(1).float()
        f_loss = weighted_bce(f_pred, labels)
        f_scaler.scale(f_loss).backward()
        f_scaler.step(f_opt)
        f_scaler.update()

        s_correct  += ((s_pred.detach().float() > 0.5).long() == labels).sum().item()
        f_correct  += ((f_pred.detach().float() > 0.5).long() == labels).sum().item()
        s_loss_sum += s_loss.item()
        f_loss_sum += f_loss.item()
        total      += labels.size(0)

        if (batch_idx + 1) % 100 == 0:
            print(f"    batch {batch_idx+1}/{len(loader)}  "
                  f"s_loss={s_loss.item():.4f}  f_loss={f_loss.item():.4f}", flush=True)

    return s_correct / total, f_correct / total


@torch.no_grad()
def evaluate(spatial: nn.Module, fft_mlp: nn.Module,
             loader: DataLoader, device: torch.device) -> tuple:
    spatial.eval()
    fft_mlp.eval()
    s_preds, f_preds, all_labels = [], [], []

    for imgs, fft_feats, labels in loader:
        imgs      = imgs.to(device)
        fft_feats = fft_feats.to(device)
        with autocast():
            s_out = spatial(imgs).squeeze(1)
            f_out = fft_mlp(fft_feats).squeeze(1)
        s_preds.append(s_out.float().cpu().numpy())
        f_preds.append(f_out.float().cpu().numpy())
        all_labels.append(labels.numpy())
        del imgs
        torch.cuda.empty_cache()

    s_preds    = np.concatenate(s_preds)
    f_preds    = np.concatenate(f_preds)
    all_labels = np.concatenate(all_labels)
    s_acc = ((s_preds > 0.5).astype(int) == all_labels).mean()
    f_acc = ((f_preds > 0.5).astype(int) == all_labels).mean()
    return s_acc, f_acc, s_preds, f_preds, all_labels


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Phase 2.5: Train vision speed layer")
    parser.add_argument("--epochs",     type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers",    type=int, default=4)
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")

    # --- Splits ---
    real_train, real_val, real_test = get_split_dirs("real")
    fake_train, fake_val, fake_test = get_split_dirs("fake")
    print(f"Split  train: {len(real_train)} real + {len(fake_train)} fake dirs")
    print(f"       val:   {len(real_val)} real + {len(fake_val)} fake dirs")
    print(f"       test:  {len(real_test)} real + {len(fake_test)} fake dirs")

    # --- Transforms ---
    train_tf = transforms.Compose([
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(brightness=0.2),
        transforms.RandomCrop(CROP_SIZE, padding=10, padding_mode="reflect"),
        transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
    ])
    val_tf = transforms.Compose([
        transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
    ])

    train_ds = FaceCropDataset(real_train, fake_train, spatial_transform=train_tf)
    val_ds   = FaceCropDataset(real_val,   fake_val,   spatial_transform=val_tf)
    test_ds  = FaceCropDataset(real_test,  fake_test,  spatial_transform=val_tf)

    print(f"Crops  train={len(train_ds):,}  val={len(val_ds):,}  test={len(test_ds):,}")

    loader_kw = dict(batch_size=args.batch_size, num_workers=args.workers, pin_memory=True)
    train_loader = DataLoader(train_ds, shuffle=True,  **loader_kw)
    val_loader   = DataLoader(val_ds,   shuffle=False, **loader_kw)
    test_loader  = DataLoader(test_ds,  shuffle=False, **loader_kw)

    # --- Models ---
    spatial = SpatialBranch().to(device)
    fft_mlp = FftMlp().to(device)

    # --- Optimisers + cosine schedulers + AMP scalers ---
    s_opt    = torch.optim.AdamW(spatial.parameters(), lr=1e-4, weight_decay=1e-4)
    f_opt    = torch.optim.AdamW(fft_mlp.parameters(), lr=1e-3, weight_decay=1e-4)
    s_sched  = torch.optim.lr_scheduler.CosineAnnealingLR(s_opt, T_max=args.epochs)
    f_sched  = torch.optim.lr_scheduler.CosineAnnealingLR(f_opt, T_max=args.epochs)
    s_scaler = GradScaler()
    f_scaler = GradScaler()

    # --- Training loop ---
    print(f"\nTraining for {args.epochs} epochs (AMP FP16, batch={args.batch_size})...")
    for epoch in range(1, args.epochs + 1):
        s_train_acc, f_train_acc = train_epoch(
            spatial, fft_mlp, train_loader, s_opt, f_opt, s_scaler, f_scaler, device
        )
        s_val_acc, f_val_acc, *_ = evaluate(spatial, fft_mlp, val_loader, device)
        s_sched.step()
        f_sched.step()
        print(
            f"Epoch {epoch:02d}/{args.epochs}  "
            f"spatial: train={s_train_acc:.3f} val={s_val_acc:.3f}  |  "
            f"fft_mlp: train={f_train_acc:.3f} val={f_val_acc:.3f}"
        )

    # --- Save branch weights ---
    torch.save(spatial.state_dict(), MODEL_WEIGHTS / "efficientnet_b4_ff++.pt")
    torch.save(fft_mlp.state_dict(), MODEL_WEIGHTS / "fft_mlp_ff++.pt")
    print(f"\nSaved spatial → {MODEL_WEIGHTS}/efficientnet_b4_ff++.pt")
    print(f"Saved fft_mlp → {MODEL_WEIGHTS}/fft_mlp_ff++.pt")

    # --- Fit logistic regression fusion on val set ---
    print("\nFitting fusion head on val set...")
    _, _, s_val, f_val, y_val = evaluate(spatial, fft_mlp, val_loader, device)
    X_val = np.column_stack([s_val, f_val])
    fusion = LogisticRegression(max_iter=1000)
    fusion.fit(X_val, y_val)

    # Save as [w_spatial, w_freq, intercept] — no sklearn dep at inference time
    fusion_params = np.array([
        fusion.coef_[0][0],
        fusion.coef_[0][1],
        fusion.intercept_[0],
    ], dtype=np.float32)
    np.save(MODEL_WEIGHTS / "fusion_alpha.npy", fusion_params)
    print(f"Fusion weights: spatial={fusion_params[0]:.4f}  "
          f"freq={fusion_params[1]:.4f}  bias={fusion_params[2]:.4f}")
    print(f"Saved fusion   → {MODEL_WEIGHTS}/fusion_alpha.npy")

    # --- Final test evaluation ---
    print("\nTest set evaluation...")
    _, _, s_test, f_test, y_test = evaluate(spatial, fft_mlp, test_loader, device)
    X_test = np.column_stack([s_test, f_test])

    s_acc    = accuracy_score(y_test, (s_test > 0.5).astype(int))
    f_acc    = accuracy_score(y_test, (f_test > 0.5).astype(int))
    fused    = torch.sigmoid(
        torch.tensor(fusion_params[0] * s_test + fusion_params[1] * f_test + fusion_params[2])
    ).numpy()
    fused_acc = accuracy_score(y_test, (fused > 0.5).astype(int))
    fused_auc = roc_auc_score(y_test, fused)

    print(f"  Spatial branch accuracy : {s_acc:.4f}")
    print(f"  FFT MLP accuracy        : {f_acc:.4f}")
    print(f"  Fused accuracy          : {fused_acc:.4f}")
    print(f"  Fused AUC               : {fused_auc:.4f}")
    print("\nDone. Weights saved to model-weights/ — commit with git add model-weights/")
    print("Rebuild the vision-service container to load trained weights:")
    print("  docker compose up -d --build vision-service")


if __name__ == "__main__":
    main()
