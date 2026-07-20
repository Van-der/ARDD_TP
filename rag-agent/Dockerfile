FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --default-timeout=100 --retries 5 --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8002

# mTLS (M10) — internal-only service, client cert required (--ssl-cert-reqs 2).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", \
     "--ssl-keyfile", "/certs/rag-agent.key", \
     "--ssl-certfile", "/certs/rag-agent.crt", \
     "--ssl-ca-certs", "/certs/ca.crt", "--ssl-cert-reqs", "2"]
