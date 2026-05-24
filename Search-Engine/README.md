# HPE Search Engine — Role 4 + Role 5 Stack

> **Stage 2 — Real-Time Search** as defined in `Architecture/Flow.png`

This repo contains the **search-side** of the HPE Campus Connect Search Engine:

| Container | Flow.png Label | What it does |
|-----------|---------------|--------------|
| `go-gateway` | Go Gateway | REST API entry point (`GET /search`, `GET /health`) |
| `pyworker-2` | PyWorker-2 (Searcher) | gRPC server — spaCy NLP parse → embed → OpenSearch hybrid query |
| `opensearch` | OpenSearch | kNN + BM25 single-cluster search database |
| `opensearch-dashboards` | — | Dev UI for inspecting the index |
| `frontend` | Frontend | Next.js search UI |

> **OpenSearch** is shared with Role 3. In a fully integrated deployment, point `OPENSEARCH_HOST` to Role 3's server instead of running the local container.

---

## Prerequisites

- Docker Engine + Docker Compose v2
- ~4 GB RAM (OpenSearch needs at least 1 GB heap)

---

## Quick Start

### 1. Clone and configure

```bash
cd Search-Engine
cp .env.example .env
```

Edit `.env` and fill in the values for your environment:

```bash
# If Role 3's OpenSearch is on a different server, set its IP here:
OPENSEARCH_HOST=<Role3_SERVER_IP>   # default: localhost (uses local container)
OPENSEARCH_PORT=9200
OPENSEARCH_INDEX=object-storage-index

# Embedding model (cached via HuggingFace)
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Leave blank — frontend routes through go-gateway automatically
NEXT_PUBLIC_API_BASE=
```

### 2. Build and start

```bash
docker compose up --build
```

To run in the background:

```bash
docker compose up --build -d
```

### 3. Open the UI

```
http://localhost:3000
```

---

## Service Ports

| Service | URL / Address | Description |
|---------|--------------|-------------|
| **Frontend** | http://localhost:3000 | Search UI |
| **Go Gateway** | http://localhost:8080 | REST API (`GET /search?q=`, `GET /health`) |
| **PyWorker-2** | `localhost:50052` | gRPC endpoint |
| **OpenSearch** | http://localhost:9200 | Search database |
| **OpenSearch Dashboards** | http://localhost:5601 | Index inspection UI |

---

## How a Search Works (Stage 2 Flow)

```
Browser → Frontend (3000)
       → Go Gateway (8080)   [REST]
       → PyWorker-2 (50052)  [gRPC]  ← spaCy NLP + SentenceTransformer embed
       → OpenSearch (9200)           ← kNN + BM25 hybrid query
       → Go Gateway                  ← merge Top-K results
       → Frontend                    ← render results
```

Redis caching (Role 3) slots in between Go Gateway ↔ PyWorker-2 — cache hit returns instantly, cache miss goes through the full gRPC path.

---

## Natural Language Query Examples

The spaCy NLP parser (PyWorker-2) understands:

| Query | What it does |
|-------|-------------|
| `quarterly report pdf` | keyword search + `type:pdf` filter |
| `images bigger than 10MB` | `type:image` + `size:size_gt:10MB` filter |
| `contracts from last week` | keyword search + `date:last_week` filter |
| `invoices from May` | keyword search + `month:may` filter |
| `marketing deck .pptx` | keyword search + `extension:pptx` filter |

---

## Integration with Other Roles

### Using Role 3's external OpenSearch (recommended for full demo)

Set `OPENSEARCH_HOST` to Role 3's server IP in `.env`:

```bash
OPENSEARCH_HOST=<Role3_SERVER_IP>
```

Then you can remove the local `opensearch` and `opensearch-dashboards` services from `docker-compose.yml` since you'll use theirs.

### Role 3 Redis caching integration

Role 3 adds the Redis cache middleware in `gateway/handlers/search.go`.
Integration points are marked with `// [ROLE-3 HOOK]` comments in that file.

---

## OpenSearch Index

The search worker expects an index named `object-storage-index` (set via `OPENSEARCH_INDEX` env var).

If running a fresh local OpenSearch and need to create the index manually:

```bash
curl -X PUT http://localhost:9200/object-storage-index \
  -H 'Content-Type: application/json' \
  -d '{
    "mappings": {
      "properties": {
        "object_name":   { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
        "bucket":        { "type": "keyword" },
        "size_bytes":    { "type": "long" },
        "content_type":  { "type": "keyword" },
        "last_modified": { "type": "date" },
        "etag":          { "type": "keyword" },
        "extension":     { "type": "keyword" },
        "indexed_at":    { "type": "date" }
      }
    }
  }'
```

---

## Stopping the Stack

```bash
docker compose down
```

To also remove the OpenSearch data volume:

```bash
docker compose down -v
```

---

## Project Structure

```
Search-Engine/
├── gateway/                   # Go API Gateway (Role 4)
│   ├── main.go
│   ├── handlers/search.go     # REST handler (ROLE-3 HOOK inside)
│   ├── grpcclient/client.go   # gRPC client → PyWorker-2
│   ├── merger/merger.go       # Top-K dedup + sort
│   └── proto/                 # Shared .proto contract
├── pyworker/                  # PyWorker-2 gRPC server (Role 5)
│   ├── search_worker.py       # gRPC servicer
│   ├── nlp_parser.py          # spaCy NLP parser
│   ├── embedding_service.py   # SentenceTransformer (all-MiniLM-L6-v2)
│   └── proto/                 # Generated Python gRPC stubs
├── frontend/                  # Next.js search UI (Role 5)
│   └── app/
│       ├── page.js            # Search page
│       └── api/search/        # Fallback direct-to-OpenSearch route
├── Architecture/
│   └── Flow.png               # System architecture diagram
└── docker-compose.yml         # All 5 containers
```
