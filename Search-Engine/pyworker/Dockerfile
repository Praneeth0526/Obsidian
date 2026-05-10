FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyworker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY pyworker/ ./pyworker/

EXPOSE 50052

CMD ["python", "pyworker/search_worker.py"]
