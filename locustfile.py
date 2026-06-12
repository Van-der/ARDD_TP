from locust import HttpUser, task, between
import time
import base64
import os

# Generate a small 10x10 mock image in base64 to avoid network overhead for the payload itself
# (In a real load test, you might want to use a real 224x224 frame)
MOCK_PAYLOAD = base64.b64encode(b"\x00" * 300).decode("utf-8")
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
