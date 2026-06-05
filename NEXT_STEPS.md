# Next Steps After .env Update

Now that you've updated your `.env` files, here's what to do next:

## 1. Verify Environment Variables

Check that all required variables are set:

```bash
# Check .env file exists
ls -la .env

# Verify key variables are set
grep -E "INTERNAL_API_KEY|JWT_SECRET|MLFLOW_TRACKING_TOKEN" .env
```

## 2. Prepare Test Dataset

Run the dataset preparation script:

```bash
python prepare_test_dataset.py
```

This creates:
- `test_dataset/` with real/fake samples
- Test payloads for integration tests
- Metadata for benchmarks

## 3. Start the Infrastructure

Start the Docker Compose stack:

```bash
# Start all services in detached mode
docker compose up -d

# Check service status
docker compose ps

# View logs
docker compose logs -f
```

## 4. Verify Services are Healthy

Wait 30 seconds for services to start, then test:

```bash
# Test health endpoints
curl -f http://localhost:8001/health  # Vision Service
curl -f http://localhost:8002/health  # RAG Agent
curl -f http://localhost:8003/health  # Aggregation Service
curl -f http://localhost:5000         # MLflow

# Test Kafka
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list
```

## 5. Run Initial Tests

```bash
# Run infrastructure test
python test_infrastructure.py

# Test ingest gateway (if you have a video source)
# Or use the test dataset
cd ingest-gateway
python -c "import main; print('Ingest gateway imports OK')"
```

## 6. Next Phase (Step 3 - Vision Service)

Once infrastructure is running, proceed to Phase 1, Step 3:

1. Create `vision-service/` directory
2. Implement FastAPI app with `POST /infer`
3. Add MTCNN face alignment
4. Implement EfficientNet + FFT branches
5. Test with the prepared dataset

## Troubleshooting

If services fail to start:

```bash
# Check specific service logs
docker compose logs vision-service
docker compose logs kafka

# Check for port conflicts
netstat -tulpn | grep :8001
netstat -tulpn | grep :9092

# Restart services
docker compose down
docker compose up -d
```

## Environment Variable Reference

Make sure these are set in your `.env`:

- `INTERNAL_API_KEY` - Service-to-service authentication
- `JWT_SECRET` - WebSocket JWT signing
- `MLFLOW_TRACKING_TOKEN` - MLflow authentication
- `RTSP_SOURCE` - Video source URL (or use test mode)
- Optional: `WEBHOOK_URL`, `WEBHOOK_TOKEN` for alerts