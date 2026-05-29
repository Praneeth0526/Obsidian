# HPE Enterprise Search Engine

A production-grade, hybrid (BM25 + kNN) enterprise search pipeline built on OpenSearch, Redis, Kafka, and MinIO. The system is split into two co-operating pipelines:

| Pipeline | Directory | What it does |
|----------|-----------|--------------|
| **Ingestion** | `workers/`, `infrastructure/`, `docker-compose.yml` | File upload event → Kafka → Tika extract → chunk → embed → index into OpenSearch |
| **Search** | `Search-Engine/` | REST query → Go Gateway → gRPC PyWorker → OpenSearch hybrid query → Redis cache |

---

## Architecture Overview

```
┌─────────────── INGESTION PIPELINE ───────────────────────────────┐
│                                                                    │
│  MinIO (S3)  ──PUT event──▶  Kafka (3-node)  ──▶  Ingestion      │
│  :9000/:9001                 :29092               Worker          │
│                                                    │              │
│                              Model Server  ◀───────┤  (embed)    │
│                              :8000 (text)           │              │
│                              :8001 (image)          ▼              │
│                                              OpenSearch            │
│                              Tika  ◀──────── (extract)  :9200    │
│                              :9998                  │              │
│                                              Redis Cache           │
└──────────────────────────────────────────── :6379 ───────────────┘
                                                     │
┌─────────────── SEARCH PIPELINE ──────────────────▼──────────────┐
│                                                                    │
│  Frontend (Next.js)  ──▶  Go Gateway  ──▶  PyWorker-2 (gRPC)    │
│  :3000                     :8080            :50052                │
│                              │                │                    │
│                           Redis            OpenSearch             │
│                         (cache HIT)      (kNN + BM25)            │
│                           :6379             :9200                 │
└───────────────────────────────────────────────────────────────────┘
```

---

## Repository Structure

```
HPE/
├── Search-Engine/                  # Search pipeline (Roles 4 & 5)
│   ├── gateway/                    # Go API Gateway
│   │   ├── cache/redis.go          # Redis cache — Steps 1 & 4 of search flow
│   │   ├── handlers/search.go      # REST handler (HIT/MISS logic)
│   │   ├── grpcclient/client.go    # gRPC client → PyWorker-2
│   │   ├── merger/merger.go        # Top-K dedup + score sort
│   │   ├── proto/                  # Generated gRPC stubs (Go)
│   │   ├── main.go
│   │   ├── Dockerfile
│   │   └── go.mod
│   ├── pyworker/                   # PyWorker-2 gRPC server (NLP + embed + search)
│   │   ├── search_worker.py        # gRPC servicer
│   │   ├── nlp_parser.py           # spaCy NLP parser
│   │   ├── embedding_service.py    # SentenceTransformer (all-MiniLM-L6-v2)
│   │   ├── config.py               # All env-var config
│   │   ├── proto/                  # Generated gRPC stubs (Python)
│   │   └── Dockerfile
│   ├── frontend/                   # Next.js HPE-themed search UI
│   ├── docker-compose.yml          # Search-side stack (OpenSearch + Redis + gateway + UI)
│   └── .env.example
│
├── workers/
│   ├── ingestion/                  # Ingestion worker (Kafka consumer)
│   │   ├── main.py                 # Entry point — Kafka → process → index
│   │   ├── tika_extractor.py       # Apache Tika text & metadata extraction
│   │   ├── chunker.py              # Sliding-window text chunker
│   │   ├── image_handler.py        # Image pipeline (resize → embed)
│   │   ├── model_client.py         # HTTP client → model-server
│   │   └── opensearch_client.py    # Bulk upsert into OpenSearch
│   └── model-server/               # FastAPI embedding server
│       ├── server.py               # /embed/text  /embed/image  endpoints
│       ├── text_embedder.py        # SentenceTransformer (384-dim)
│       ├── image_embedder.py       # CLIP / torchvision image embedder
│       ├── main.py                 # Uvicorn entry
│       └── load-balancer/nginx.conf
│
├── backend/
│   ├── cache/redis_cache.py        # Python Redis cache layer (search results)
│   └── search/opensearch_query_builder.py  # Hybrid BM25+kNN query reference
│
├── infrastructure/
│   ├── opensearch/index-mapping.json  # kNN-enabled index schema (obsidian-docs)
│   ├── kafka/                         # Kafka KRaft cluster configs + topic scripts
│   ├── minio/                         # MinIO bucket + event-notification setup
│   └── startup.sh                     # One-shot bootstrap script
│
├── tests/
│   └── test_opensearch.py          # OpenSearch integration tests
│
├── docker-compose.yml              # Unified full-stack compose — starts everything in one command
├── requirements.txt                # Python dependencies for ML (model-server + pyworker)
├── pytest.ini
└── .env.example                    # All environment variables documented
```

---

## Quick Start

```bash
# 1. Configure
cp .env.example .env
# Edit .env only if you need to change defaults (OpenSearch host, MinIO creds, etc.)

# 2. Start everything
docker compose up --build -d
```

That's it. All 14 services — Kafka, MinIO, Tika, OpenSearch, Redis, ingestion worker, model server, PyWorker-2, Go Gateway, and the frontend — start together with proper dependency ordering.

Open **http://localhost:3000** when the stack is up.

## Service Ports

All services run from the single root `docker-compose.yml`.

| Service | Port(s) | Description |
|---------|---------|-------------|
| **Frontend** | `3000` | Next.js search UI |
| **Go Gateway** | `8080` | REST API (`GET /search`, `GET /health`) |
| **PyWorker-2** | `50052` | gRPC — NLP parse → embed → OpenSearch |
| **Model Server** | `8000` | FastAPI text + image embedding |
| **OpenSearch** | `9200` | Hybrid BM25 + kNN search database |
| **OpenSearch Dashboards** | `5601` | Index inspection UI |
| **Redis** | `6379` | Search result cache |
| **Apache Tika** | `9998` | Text & metadata extraction |
| **MinIO S3 API** | `9000` | Object upload endpoint |
| **MinIO Console** | `9001` | MinIO web UI |
| **Kafka broker 1** | `29092` | External Kafka listener |
| **Kafka broker 2** | `29093` | External Kafka listener |
| **Kafka broker 3** | `29094` | External Kafka listener |

---

## Environment Variables

Copy `.env.example` → `.env` and adjust as needed.

```bash
# OpenSearch — must match the index created by ingestion pipeline
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_INDEX=obsidian-docs

# Search tuning
SEARCH_KNN_K=50
SEARCH_BM25_BOOST=0.4
SEARCH_KNN_BOOST=0.6

# Redis cache (Go Gateway Steps 1 & 4)
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_TTL_DEFAULT=300         # seconds — default query TTL
REDIS_TTL_POPULAR=1800        # seconds — TTL for frequently hit queries
REDIS_POPULAR_THRESHOLD=10    # hit count to qualify as "popular"

# Ingestion tuning
OPENSEARCH_BULK_CHUNK_SIZE=200
OPENSEARCH_MAX_RETRIES=5

# Kafka
KAFKA_BOOTSTRAP_SERVERS=localhost:29092
KAFKA_TOPIC=file-upload-events

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin123
```

---

## OpenSearch Index

The pipeline uses an index named **`obsidian-docs`** with kNN + BM25 hybrid mapping.

The index is created automatically on startup by the `opensearch-init` service in the ingestion `docker-compose.yml`. The mapping lives in `infrastructure/opensearch/index-mapping.json`.

To create it manually:

```bash
curl -X PUT http://localhost:9200/obsidian-docs \
  -H 'Content-Type: application/json' \
  -d @infrastructure/opensearch/index-mapping.json
```

---

## How the Search Pipeline Works

```
1. Browser → Frontend (port 3000)
2. Frontend → Go Gateway GET /search?q=<query>   (port 8080)
3. Go Gateway → Redis: cache lookup by SHA-256(query)
   └─ HIT  → return cached JSON immediately  [X-Cache: HIT]
   └─ MISS → continue ↓
4. Go Gateway → PyWorker-2 gRPC ProcessQuery
5. PyWorker-2:
   a. spaCy NLP parse → intent text + keywords + filters
      - Exact calendar dates (e.g. "May 15th") → 24-hour range filter
      - Relative dates (last week / month / year / yesterday / today)
      - File type, extension, and size filters
   b. SentenceTransformer embed query → 384-dim vector
   c. OpenSearch query:
      - Keywords present → multi_match across filename, chunk_text, mime_type, etc.
      - Filter-only query (no keywords) → match_all + filter clauses
6. PyWorker-2 returns ranked proto results to Go Gateway
7. Go Gateway merger: dedup by ID, sort by combined_score desc, trim to limit
8. Go Gateway → Redis: store result with TTL    [X-Cache: MISS]
9. Go Gateway → Frontend → render results
```

---

## How the Ingestion Pipeline Works

```
1. Client uploads file to MinIO bucket "uploads"
2. MinIO publishes s3:ObjectCreated event to Kafka topic "file-upload-events"
3. Ingestion Worker (Python) consumes Kafka message:
   a. Download file bytes from MinIO
   b. Apache Tika: extract text + metadata
   c. Route by content type:
      - Image  → Model Server /embed/image → 384-dim vector → single chunk doc
      - Text   → TextChunker (sliding window) → Model Server /embed/text → N chunk docs
   d. OpenSearch bulk upsert (object_key/chunk_index = document ID, idempotent)
```

---

## Natural Language Query Examples

| Query | What PyWorker extracts |
|-------|------------------------|
| `quarterly report pdf` | keywords: `quarterly report` + `type:pdf` filter |
| `images bigger than 10MB` | `type:image` + `size_gt:10MB` filter |
| `contracts from last week` | keywords: `contracts` + `date:last_week` filter |
| `invoices from May` | keywords: `invoices` + `month:may` filter |
| `marketing deck .pptx` | keywords: `marketing deck` + `extension:pptx` filter |
| `files uploaded on May 15th` | `exact_date:may_15_<year>` filter → 24-hour range query |
| `pdfs` | `type:pdf` filter → `match_all` (no keywords needed) |

---

## Uploading Files with AWS CLI

MinIO exposes an S3-compatible API on `http://localhost:9000`. Point the AWS CLI at it using `--endpoint-url`.

### 1. One-time configuration

```bash
aws configure set aws_access_key_id     minioadmin
aws configure set aws_secret_access_key minioadmin123
aws configure set default.region        us-east-1
```

> The region value is ignored by MinIO but required by the AWS CLI — any string works.

### 2. Upload a single file

```bash
aws s3 cp /path/to/your/file.pdf s3://uploads/file.pdf \
  --endpoint-url http://localhost:9000
```

### 3. Upload an entire folder

```bash
aws s3 cp /path/to/folder/ s3://uploads/ \
  --recursive \
  --endpoint-url http://localhost:9000
```

### 4. Upload with a subfolder prefix (recommended for organisation)

```bash
aws s3 cp /path/to/folder/ s3://uploads/my-project/ \
  --recursive \
  --endpoint-url http://localhost:9000
```

### 5. Useful commands

```bash
# List all files in the bucket
aws s3 ls s3://uploads/ --endpoint-url http://localhost:9000 --recursive

# Delete a file
aws s3 rm s3://uploads/file.pdf --endpoint-url http://localhost:9000

# Sync a local folder (only uploads changed/new files)
aws s3 sync /path/to/folder/ s3://uploads/ \
  --endpoint-url http://localhost:9000
```

> **How it triggers ingestion:** Every `s3 cp` / `s3 sync` PUT fires a MinIO event → Kafka `file-upload-events` topic → Ingestion Worker downloads the file, extracts text (Tika), chunks it, embeds it (Model Server), and indexes it into OpenSearch. The file becomes searchable within seconds.

---

## Debugging

### OpenSearch

```bash
# Check cluster health
curl -s http://localhost:9200/_cluster/health | python3 -m json.tool

# Count indexed documents
curl -s http://localhost:9200/obsidian-docs/_count | python3 -m json.tool

# View latest 5 documents
curl -s "http://localhost:9200/obsidian-docs/_search?pretty&size=5"

# Search for a term
curl -s "http://localhost:9200/obsidian-docs/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{"query": {"match": {"chunk_text": "your search term"}}}'

# List all indices
curl -s http://localhost:9200/_cat/indices?v
```

### Redis

```bash
# List all cached search keys
docker exec obsidian-redis redis-cli KEYS "search:*"

# Count cached entries
docker exec obsidian-redis redis-cli DBSIZE

# View a cached result (replace <key> with one from KEYS above)
docker exec obsidian-redis redis-cli GET <key>

# Cache hit/miss stats
docker exec obsidian-redis redis-cli INFO stats | grep -E "keyspace_hits|keyspace_misses|used_memory_human"

# Flush cache (force all misses — useful during testing)
docker exec obsidian-redis redis-cli FLUSHDB
```

### Container health & logs

```bash
# Check all container statuses at a glance
docker ps --format "table {{.Names}}\t{{.Status}}"

# Tail logs for a specific service
docker logs obsidian-ingestion-worker -f
docker logs obsidian-tika -f
docker logs obsidian-minio -f
docker logs obsidian-opensearch -f
docker logs obsidian-go-gateway -f
docker logs obsidian-pyworker-2 -f

# Inspect health check detail for a container
docker inspect obsidian-tika --format '{{json .State.Health}}' | python3 -m json.tool
```

### Kafka

```bash
# List topics
docker exec obsidian-kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka1:9092 --list

# Watch upload events in real-time
docker exec obsidian-kafka-1 /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka1:9092 --topic file-upload-events --from-beginning

# Check consumer lag (how far behind the ingestion worker is)
docker exec obsidian-kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server kafka1:9092 --describe --group ingestion-worker
```

### MinIO

```bash
# List all uploaded files
aws s3 ls s3://uploads/ --endpoint-url http://localhost:9000 --recursive

# Web console — login: minioadmin / minioadmin123
http://localhost:9001
```

---

## Stopping

```bash
docker compose down

# Also remove all volumes (OpenSearch data, MinIO data, Redis data)
docker compose down -v
```

---

## Running Tests

```bash
# Install dependencies
pip install -r requirements.txt

# OpenSearch integration tests (requires OpenSearch running on localhost:9200)
pytest tests/test_opensearch.py -v

# Full E2E pipeline test
pytest workers/ingestion/test_e2e_full_pipeline.py -v
```
