FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --default-timeout=100 --retries 5 --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8003

# mTLS (M10) — this is the one service the browser dashboard and host-side
# scripts hit directly, so client certs are optional (--ssl-cert-reqs 1), not
# required: verified if presented (other backend services do present one),
# but a plain HTTPS browser connection is still accepted.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003", \
     "--ssl-keyfile", "/certs/aggregation-service.key", \
     "--ssl-certfile", "/certs/aggregation-service.crt", \
     "--ssl-ca-certs", "/certs/ca.crt", "--ssl-cert-reqs", "1"]
