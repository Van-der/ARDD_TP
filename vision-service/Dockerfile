FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for OpenCV and PyTorch
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --default-timeout=100 --retries 5 --no-cache-dir -r requirements.txt

COPY . .

# mTLS (M10) — internal-only service, client cert required (--ssl-cert-reqs 2).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", \
     "--ssl-keyfile", "/certs/vision-service.key", \
     "--ssl-certfile", "/certs/vision-service.crt", \
     "--ssl-ca-certs", "/certs/ca.crt", "--ssl-cert-reqs", "2"]
