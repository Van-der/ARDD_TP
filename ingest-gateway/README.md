# Ingest Gateway

The entry point for video streams into ARDD-TP. Decodes RTSP/HTTP video, extracts frames, and publishes them to Kafka.

## Features

- RTSP/HTTP video stream decoding via OpenCV
- Frame extraction at configurable FPS (default: 30 FPS)
- JPEG encoding of frames
- Kafka publishing to `frames` topic
- Dynamic FPS downsampling (30 → 5 FPS) on downstream lag detection

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker address | `kafka:9092` |
| `KAFKA_TOPIC_FRAMES` | Kafka topic for frame payloads | `frames` |
| `RTSP_SOURCE` | RTSP/HTTP video source URL | `rtsp://localhost:8554/stream` |
| `FPS_TARGET` | Target frames per second | `30` |
| `FPS_DOWNSAMPLE_THRESHOLD` | Lag threshold (ms) to trigger downsampling | `500` |

## Frame Payload Schema

Published to Kafka `frames` topic:

```json
{
  "stream_id": "string",
  "frame_index": "integer",
  "timestamp_ms": "integer",
  "payload": "base64-encoded bytes (JPEG frame)"
}
```

## Usage

1. Set up environment variables in `.env` file
2. Build and run with Docker Compose:
   ```bash
   docker compose up ingest-gateway
   ```
3. The gateway will connect to the video source and start publishing frames to Kafka

## Testing

To test with a local RTSP stream:
```bash
# Start a test RTSP server (requires ffmpeg)
ffmpeg -f lavfi -i testsrc=size=640x480:rate=30 -c:v libx264 -f rtsp rtsp://localhost:8554/stream

# Run the ingest gateway
python main.py
```