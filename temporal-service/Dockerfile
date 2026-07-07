FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --default-timeout=100 --no-cache-dir -r requirements.txt

# Pre-download ResNeXt50 backbone at build time so the container never fetches it at runtime
RUN python3 -c "from torchvision.models.resnet import ResNeXt50_32X4D_Weights; import torchvision; torchvision.models.resnext50_32x4d(weights=ResNeXt50_32X4D_Weights.DEFAULT)"

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8004"]
