FROM python:3.11-slim

WORKDIR /app

# System deps for confluent-kafka and httpx
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    librdkafka-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from root requirements.txt
# (Docker build context is the repo root, so requirements.txt is at .)
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# Copy worker source
COPY workers/ingestion/ .

CMD ["python", "main.py"]
