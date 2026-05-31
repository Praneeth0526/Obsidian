FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy model-server source first (build context is repo root)
COPY workers/model-server/ .

# Install only the model-server's own dependencies.
# --timeout 300 / --retries 5 guard against slow networks when
# downloading large ML wheels (torch, transformers, timm).
RUN pip install --no-cache-dir --timeout 300 --retries 5 -r requirements.txt

EXPOSE 8000

CMD ["python", "main.py"]
