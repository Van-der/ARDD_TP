from locust import HttpUser, task, between
import time
import base64
import os
import cv2
import numpy as np

# A real encoded JPEG is required — vision-service's cv2.imdecode() rejects
# raw non-JPEG bytes with a 422, which previously made every request fail.
_mock_frame = np.zeros((224, 224, 3), dtype=np.uint8)
_, _mock_jpeg = cv2.imencode(".jpg", _mock_frame)
MOCK_PAYLOAD = base64.b64encode(_mock_jpeg.tobytes()).decode("utf-8")
API_KEY = os.getenv("INTERNAL_API_KEY", "test-key")

class DeepfakeLoadTest(HttpUser):
    # Wait time between tasks: ~33ms corresponds to 30 FPS for a single user
    wait_time = between(0.033, 0.033)

    def on_start(self):
        self.frame_idx = 0

    @task
    def send_frame(self):
        payload = {
            "stream_id": "locust_load_stream",
            "frame_index": self.frame_idx,
            "timestamp_ms": int(time.time() * 1000),
            "payload": MOCK_PAYLOAD
        }
        
        # Test the Aggregation Service's synchronous HTTP endpoint
        with self.client.post(
            "/aggregate", 
            json=payload, 
            headers={"X-API-Key": API_KEY},
            catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed with status {response.status_code}: {response.text}")
                
        self.frame_idx += 1
