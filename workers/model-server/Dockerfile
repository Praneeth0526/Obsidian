FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from root requirements.txt
# (Docker build context is the repo root)
COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# Copy model-server source
COPY workers/model-server/ .

EXPOSE 8000

CMD ["python", "main.py"]
