#!/usr/bin/env python3
"""
Prepare test dataset for ARDD-TP.
This script creates a minimal test dataset with labelled real/fake frame samples.
"""

import os
import json
import cv2
import numpy as np
from pathlib import Path

def create_test_dataset(output_dir="test_dataset"):
    """Create a minimal test dataset for unit tests and benchmarks."""
    os.makedirs(output_dir, exist_ok=True)
    
    # Create subdirectories
    real_dir = os.path.join(output_dir, "real")
    fake_dir = os.path.join(output_dir, "fake")
    os.makedirs(real_dir, exist_ok=True)
    os.makedirs(fake_dir, exist_ok=True)
    
    # Create metadata file
    metadata = {
        "description": "ARDD-TP test dataset for unit tests and accuracy benchmarks",
        "version": "1.0.0",
        "samples": []
    }
    
    # Generate synthetic test images
    for i in range(10):  # 10 real, 10 fake samples
        # Real sample - simple face-like pattern
        real_img = np.zeros((224, 224, 3), dtype=np.uint8)
        # Draw a simple face (circles for eyes, line for mouth)
        cv2.circle(real_img, (80, 80), 20, (255, 255, 255), -1)  # left eye
        cv2.circle(real_img, (144, 80), 20, (255, 255, 255), -1)  # right eye
        cv2.ellipse(real_img, (112, 140), (40, 20), 0, 0, 180, (255, 255, 255), 2)  # mouth
        
        real_path = os.path.join(real_dir, f"real_{i:03d}.jpg")
        cv2.imwrite(real_path, real_img)
        
        metadata["samples"].append({
            "path": real_path,
            "label": "REAL",
            "stream_id": f"test_stream_real",
            "frame_index": i
        })
        
        # Fake sample - distorted face pattern
        fake_img = real_img.copy()
        # Add some distortion to simulate GAN artefacts
        noise = np.random.randint(0, 50, (224, 224, 3), dtype=np.uint8)
        fake_img = cv2.addWeighted(fake_img, 0.7, noise, 0.3, 0)
        # Add spectral-like pattern (simulating FFT artefacts)
        for y in range(0, 224, 20):
            cv2.line(fake_img, (0, y), (224, y), (100, 100, 100), 1)
        
        fake_path = os.path.join(fake_dir, f"fake_{i:03d}.jpg")
        cv2.imwrite(fake_path, fake_img)
        
        metadata["samples"].append({
            "path": fake_path,
            "label": "FAKE",
            "stream_id": f"test_stream_fake",
            "frame_index": i
        })
    
    # Save metadata
    metadata_path = os.path.join(output_dir, "metadata.json")
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    print(f"Test dataset created in {output_dir}/")
    print(f"- {len(metadata['samples'])} total samples")
    print(f"- {len([s for s in metadata['samples'] if s['label'] == 'REAL'])} REAL samples")
    print(f"- {len([s for s in metadata['samples'] if s['label'] == 'FAKE'])} FAKE samples")
    print(f"- Metadata: {metadata_path}")
    
    # Create a simple test payload for integration tests
    test_payload_dir = os.path.join(output_dir, "test_payloads")
    os.makedirs(test_payload_dir, exist_ok=True)
    
    # Create a sample FramePayload JSON for testing
    sample_img = cv2.imread(real_path)
    _, buffer = cv2.imencode('.jpg', sample_img)
    jpeg_bytes = buffer.tobytes()
    
    import base64
    sample_payload = {
        "stream_id": "test_stream_001",
        "frame_index": 0,
        "timestamp_ms": 1234567890,
        "payload": base64.b64encode(jpeg_bytes).decode('utf-8')
    }
    
    payload_path = os.path.join(test_payload_dir, "frame_payload.json")
    with open(payload_path, 'w') as f:
        json.dump(sample_payload, f, indent=2)
    
    print(f"\nTest payload created: {payload_path}")
    
    return output_dir

if __name__ == "__main__":
    create_test_dataset()