import time
import json
import base64
import random
import cv2
import numpy as np
from kafka import KafkaProducer

def generate_frame_b64(is_fake=False):
    """Generate a dummy image. Red tint if fake, green if real."""
    img = np.zeros((224, 224, 3), dtype=np.uint8)
    if is_fake:
        img[:, :, 2] = 200  # Red
    else:
        img[:, :, 1] = 200  # Green
        
    _, buf = cv2.imencode('.jpg', img)
    return base64.b64encode(buf.tobytes()).decode('utf-8')

def main():
    print("Connecting to Kafka...")
    producer = KafkaProducer(
        bootstrap_servers='localhost:9092',
        security_protocol='SASL_PLAINTEXT',
        sasl_mechanism='PLAIN',
        sasl_plain_username='admin',
        sasl_plain_password='admin-secret',
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )
    
    stream_id = "live_demo_stream"
    frame_index = 0
    print(f"Starting simulation for {stream_id}...")
    print("Open your browser to http://localhost:3000 to see the dashboard.")
    print("Press Ctrl+C to stop.")
    
    try:
        while True:
            # Create a sudden "deepfake attack" for 10 frames every 50 frames
            # so the user can see the Alert Banner trigger
            is_fake_attack = (frame_index % 50 >= 40)
            
            payload = {
                "stream_id": stream_id,
                "frame_index": frame_index,
                "timestamp_ms": int(time.time() * 1000),
                "payload": generate_frame_b64(is_fake=is_fake_attack)
            }
            
            producer.send("frames", payload)
            
            if is_fake_attack:
                print(f"[{frame_index}] Sent FAKE frame (Watch the dashboard alert spike!)")
            else:
                print(f"[{frame_index}] Sent normal frame")
                
            frame_index += 1
            time.sleep(0.1)  # 10 FPS
            
    except KeyboardInterrupt:
        print("\nStopping simulation.")
    finally:
        producer.close()

if __name__ == "__main__":
    main()
