import cv2
import time
import json
import base64
import logging
from kafka import KafkaProducer
from typing import Optional
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class IngestGateway:
    def __init__(self):
        self.kafka_bootstrap = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')
        self.kafka_topic = os.getenv('KAFKA_TOPIC_FRAMES', 'frames')
        self.rtsp_source = os.getenv('RTSP_SOURCE', 'rtsp://localhost:8554/stream')
        self.fps_target = int(os.getenv('FPS_TARGET', '30'))
        self.fps_downsample_threshold = int(os.getenv('FPS_DOWNSAMPLE_THRESHOLD', '500'))
        
        self.producer = KafkaProducer(
            bootstrap_servers=self.kafka_bootstrap,
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        
        self.cap: Optional[cv2.VideoCapture] = None
        self.stream_id = f"stream_{int(time.time())}"
        self.frame_index = 0
        self.current_fps = self.fps_target
        self.downsample_active = False
        
    def connect_to_stream(self) -> bool:
        """Connect to RTSP/HTTP video stream."""
        try:
            self.cap = cv2.VideoCapture(self.rtsp_source)
            if not self.cap.isOpened():
                logger.error(f"Failed to open video source: {self.rtsp_source}")
                return False
            logger.info(f"Connected to video source: {self.rtsp_source}")
            return True
        except Exception as e:
            logger.error(f"Error connecting to stream: {e}")
            return False
    
    def extract_and_publish_frame(self) -> bool:
        """Extract a frame, encode as JPEG, and publish to Kafka."""
        if not self.cap or not self.cap.isOpened():
            return False
        
        ret, frame = self.cap.read()
        if not ret:
            logger.warning("Failed to read frame from stream")
            return False
        
        # Encode frame as JPEG
        _, buffer = cv2.imencode('.jpg', frame)
        jpeg_bytes = buffer.tobytes()
        
        # Create frame payload
        payload = {
            "stream_id": self.stream_id,
            "frame_index": self.frame_index,
            "timestamp_ms": int(time.time() * 1000),
            "payload": base64.b64encode(jpeg_bytes).decode('utf-8')
        }
        
        # Publish to Kafka
        try:
            self.producer.send(self.kafka_topic, payload)
            self.frame_index += 1
            logger.debug(f"Published frame {self.frame_index} to Kafka")
            return True
        except Exception as e:
            logger.error(f"Failed to publish frame to Kafka: {e}")
            return False
    
    def check_and_adjust_fps(self) -> None:
        """Check downstream lag and adjust FPS if needed."""
        # TODO: Implement actual lag detection from Kafka consumer metrics
        # For now, placeholder logic
        if self.downsample_active:
            self.current_fps = 5
        else:
            self.current_fps = self.fps_target
    
    def run(self):
        """Main loop for frame extraction and publishing."""
        if not self.connect_to_stream():
            logger.error("Failed to connect to video stream. Exiting.")
            return
        
        logger.info(f"Starting ingest gateway for stream: {self.stream_id}")
        
        try:
            while True:
                start_time = time.time()
                
                # Check and adjust FPS based on downstream lag
                self.check_and_adjust_fps()
                
                # Extract and publish frame
                success = self.extract_and_publish_frame()
                
                if not success:
                    logger.warning("Frame extraction/publish failed, attempting to reconnect...")
                    time.sleep(1)
                    if not self.connect_to_stream():
                        logger.error("Reconnection failed. Exiting.")
                        break
                    continue
                
                # Maintain target FPS
                elapsed = time.time() - start_time
                target_interval = 1.0 / self.current_fps
                if elapsed < target_interval:
                    time.sleep(target_interval - elapsed)
                
        except KeyboardInterrupt:
            logger.info("Shutting down ingest gateway...")
        finally:
            if self.cap:
                self.cap.release()
            self.producer.close()
            logger.info("Ingest gateway stopped.")

if __name__ == "__main__":
    gateway = IngestGateway()
    gateway.run()