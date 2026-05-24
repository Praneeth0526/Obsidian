FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyworker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Download the spaCy English model (small, ~12 MB, CPU-only)
RUN python -m spacy download en_core_web_sm

COPY pyworker/ ./pyworker/

EXPOSE 50052

CMD ["python", "pyworker/search_worker.py"]
