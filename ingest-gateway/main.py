import cv2
import time
import json
import base64
import logging
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from typing import Optional
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_KAFKA_SECURITY_PROTOCOL = os.getenv('KAFKA_SECURITY_PROTOCOL', 'PLAINTEXT')
_KAFKA_SASL_USERNAME = os.getenv('KAFKA_SASL_USERNAME', '')
_KAFKA_SASL_PASSWORD = os.getenv('KAFKA_SASL_PASSWORD', '')

def _kafka_sasl_kwargs() -> dict:
    if _KAFKA_SECURITY_PROTOCOL == 'SASL_PLAINTEXT':
        return {
            'security_protocol': 'SASL_PLAINTEXT',
            'sasl_mechanism': 'PLAIN',
            'sasl_plain_username': _KAFKA_SASL_USERNAME,
            'sasl_plain_password': _KAFKA_SASL_PASSWORD,
        }
    return {}

class IngestGateway:
    def __init__(self):
        self.kafka_bootstrap = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')
        self.kafka_topic = os.getenv('KAFKA_TOPIC_FRAMES', 'frames')
        self.rtsp_source = os.getenv('RTSP_SOURCE', 'rtsp://localhost:8554/stream')
        self.fps_target = int(os.getenv('FPS_TARGET', '30'))
        self.fps_downsample_threshold = int(os.getenv('FPS_DOWNSAMPLE_THRESHOLD', '500'))
        self.consumer_group = os.getenv('KAFKA_CONSUMER_GROUP', 'vision-service-group')
        
        self.producer = KafkaProducer(
            bootstrap_servers=self.kafka_bootstrap,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            **_kafka_sasl_kwargs()
        )
        
        self.cap: Optional[cv2.VideoCapture] = None
        self.stream_id = f"stream_{int(time.time())}"
        self.frame_index = 0
        self.current_fps = self.fps_target
        self.downsample_active = False

        # Error tracking
        self.decode_error = 0
        self.consecutive_failures = 0
        self.MAX_CONSECUTIVE_FAILURES = 5

    def connect_to_stream(self) -> bool:
        """Connect to RTSP/HTTP video stream with exponential backoff (max 5 attempts)."""
        for attempt in range(1, self.MAX_CONSECUTIVE_FAILURES + 1):
            try:
                self.cap = cv2.VideoCapture(self.rtsp_source)
                if self.cap.isOpened():
                    logger.info(f"Connected to video source: {self.rtsp_source} (attempt {attempt})")
                    self.consecutive_failures = 0
                    return True
                logger.warning(f"Could not open stream (attempt {attempt})")
            except Exception as e:
                logger.error(f"Error connecting to stream (attempt {attempt}): {e}")

            backoff = min(2 ** attempt, 30)
            logger.info(f"Retrying in {backoff}s...")
            time.sleep(backoff)

        # All retries exhausted — emit gateway_fatal
        logger.critical("Gateway fatal: stream unreachable after 5 attempts. Emitting stream_error.")
        try:
            self.producer.send(self.kafka_topic, {
                "event": "gateway_fatal",
                "stream_id": self.stream_id,
                "timestamp_ms": int(time.time() * 1000),
                "reason": "stream_unreachable_after_5_attempts"
            })
            self.producer.flush()
        except Exception:
            pass
        return False

    def extract_and_publish_frame(self) -> bool:
        """Extract a frame, encode as JPEG, and publish to Kafka with retry (3 attempts)."""
        if not self.cap or not self.cap.isOpened():
            return False

        ret, frame = self.cap.read()
        if not ret:
            logger.warning("Failed to read frame from stream")
            return False

        # Encode frame as JPEG
        success, buffer = cv2.imencode('.jpg', frame)
        if not success or buffer is None:
            logger.warning("Failed to encode frame as JPEG")
            self.decode_error += 1
            logger.info(f"decode_error count: {self.decode_error}")
            return False

        jpeg_bytes = buffer.tobytes()

        # Validate decoded bytes (catch corrupt JPEGs)
        check = cv2.imdecode(
            __import__('numpy').frombuffer(jpeg_bytes, __import__('numpy').uint8),
            cv2.IMREAD_COLOR
        )
        if check is None:
            logger.warning("Corrupt JPEG detected — dropping frame")
            self.decode_error += 1
            logger.info(f"decode_error count: {self.decode_error}")
            return False

        payload = {
            "stream_id": self.stream_id,
            "frame_index": self.frame_index,
            "timestamp_ms": int(time.time() * 1000),
            "payload": base64.b64encode(jpeg_bytes).decode('utf-8')
        }

        # Kafka publish with 3-attempt retry and exponential backoff
        for attempt in range(1, 4):
            try:
                self.producer.send(self.kafka_topic, payload)
                self.frame_index += 1
                logger.debug(f"Published frame {self.frame_index} to Kafka")
                return True
            except Exception as e:
                logger.warning(f"Kafka publish failed (attempt {attempt}/3): {e}")
                if attempt < 3:
                    time.sleep(0.1 * (2 ** attempt))  # 0.2s, 0.4s

        logger.error("kafka_publish_error: all 3 Kafka publish attempts failed — dropping frame")
        return False

    def check_and_adjust_fps(self) -> None:
        """Check downstream Kafka consumer lag and downsample FPS if lag > threshold."""
        if os.getenv("TESTING"):
            # In test mode, respect the flag directly without hitting Kafka Admin API
            self.current_fps = 5 if self.downsample_active else self.fps_target
            return

        try:
            admin = KafkaAdminClient(bootstrap_servers=self.kafka_bootstrap, **_kafka_sasl_kwargs())
            # Fetch consumer group offsets and partition offsets to compute lag
            consumer_offsets = admin.list_consumer_group_offsets(self.consumer_group)
            admin.close()

            total_lag = 0
            for tp, offset_meta in consumer_offsets.items():
                # Get end offset for this partition
                from kafka import KafkaConsumer as _KC
                tmp = _KC(bootstrap_servers=self.kafka_bootstrap, **_kafka_sasl_kwargs())
                end = tmp.end_offsets([tp]).get(tp, 0)
                tmp.close()
                lag = max(0, end - offset_meta.offset)
                total_lag += lag

            if total_lag > self.fps_downsample_threshold:
                if not self.downsample_active:
                    logger.warning(f"Kafka lag={total_lag} > threshold={self.fps_downsample_threshold} — downsampling to 5 FPS")
                    self.downsample_active = True
            else:
                if self.downsample_active:
                    logger.info(f"Kafka lag={total_lag} recovered — restoring {self.fps_target} FPS")
                    self.downsample_active = False

        except Exception as e:
            logger.warning(f"Could not read Kafka lag: {e} — keeping current FPS")

        self.current_fps = 5 if self.downsample_active else self.fps_target

    def run(self):
        """Main loop for frame extraction and publishing."""
        if not self.connect_to_stream():
            logger.error("Failed to connect to video stream after retries. Exiting.")
            return

        logger.info(f"Starting ingest gateway for stream: {self.stream_id}")

        try:
            while True:
                start_time = time.time()

                self.check_and_adjust_fps()

                success = self.extract_and_publish_frame()

                if not success:
                    self.consecutive_failures += 1
                    logger.warning(
                        f"Frame failure #{self.consecutive_failures}, attempting reconnect..."
                    )
                    if not self.connect_to_stream():
                        logger.critical("Could not reconnect after failures. Exiting.")
                        break
                    continue
                else:
                    self.consecutive_failures = 0

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