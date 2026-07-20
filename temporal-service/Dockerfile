FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --default-timeout=100 --retries 5 --no-cache-dir -r requirements.txt

# Pre-download ResNeXt50 backbone at build time so the container never fetches it at runtime
RUN python3 -c "from torchvision.models.resnet import ResNeXt50_32X4D_Weights; import torchvision; torchvision.models.resnext50_32x4d(weights=ResNeXt50_32X4D_Weights.DEFAULT)"

COPY . .

# mTLS (M10) — internal-only service, client cert required (--ssl-cert-reqs 2).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004", \
     "--ssl-keyfile", "/certs/temporal-service.key", \
     "--ssl-certfile", "/certs/temporal-service.crt", \
     "--ssl-ca-certs", "/certs/ca.crt", "--ssl-cert-reqs", "2"]
