# HPE Search Engine — Go Gateway + PyWorker + Redis + OpenSearch

> **Stage 2 — Real-Time Search** | Roles 4 & 5

The search-side pipeline. Receives a natural-language query from the browser, checks Redis for a cached result, and if it misses, routes through the gRPC PyWorker to OpenSearch and caches the response.

---

## Containers

| Container | Port | What it does |
|-----------|------|--------------|
| `frontend` | `3000` | Next.js HPE-themed search UI |
| `go-gateway` | `8080` | REST entry point — `GET /search`, `GET /health` |
| `pyworker-2` | `50052` | gRPC — spaCy NLP → SentenceTransformer embed → OpenSearch |
| `opensearch` | `9200` | Hybrid BM25 + kNN search database |
| `opensearch-dashboards` | `5601` | Dev UI for inspecting the index |
| `redis` | `6379` | Search result cache (Steps 1 & 4 of search flow) |

---

## Quick Start

### 1. Configure

```bash
cd Search-Engine
cp .env.example .env
```

Key variables to review in `.env`:

```bash
# Must match the index created by the ingestion pipeline
OPENSEARCH_INDEX=obsidian-docs

# If OpenSearch is running on another machine (e.g. ingestion team's server):
OPENSEARCH_HOST=<remote_ip>

# Redis TTL tuning (seconds)
REDIS_TTL_DEFAULT=300
REDIS_TTL_POPULAR=1800
REDIS_POPULAR_THRESHOLD=10
```

### 2. Start

```bash
docker compose up --build
```

Background:

```bash
docker compose up --build -d
```

### 3. Search

Open **http://localhost:3000** — or hit the gateway directly:

```bash
curl "http://localhost:8080/search?q=quarterly+report+pdf&limit=5"
```

Check the `X-Cache` response header:
- `X-Cache: HIT` — result returned from Redis in < 1 ms
- `X-Cache: MISS` — full NLP + OpenSearch round-trip, result now cached

---

## Search Flow (Step by Step)

```
Browser
  │
  ▼
Frontend :3000
  │  GET /search?q=<query>
  ▼
Go Gateway :8080
  │
  ├─ Step 1: Redis lookup  ──── HIT ──▶ return immediately (X-Cache: HIT)
  │            :6379                     ↑
  │                                      │
  └─ MISS ──▶ gRPC ProcessQuery          │
                │                        │
                ▼                        │
         PyWorker-2 :50052               │
           │ a. spaCy NLP parse          │
           │ b. SentenceTransformer      │
           │    embed (384-dim)          │
           │ c. OpenSearch hybrid        │
           │    BM25 (0.4) + kNN (0.6)  │
           ▼                             │
         OpenSearch :9200                │
           │ Top-K results               │
           ▼                             │
         Go Gateway merger               │
           │ dedup + sort by score       │
           │                             │
  Step 4: Redis store ──────────────────┘  (X-Cache: MISS)
           │ TTL = default (300s) or
           │       popular (1800s if hits ≥ 10)
           ▼
         Frontend — render results
```

---

## Natural Language Query Examples

| Query | What PyWorker parses |
|-------|---------------------|
| `quarterly report pdf` | keywords: `quarterly report` + `type:pdf` |
| `images bigger than 10MB` | `type:image` + `size_gt:10MB` |
| `contracts from last week` | keywords: `contracts` + `date:last_week` |
| `invoices from May` | keywords: `invoices` + `month:may` |
| `marketing deck .pptx` | keywords: `marketing deck` + `extension:pptx` |

---

## OpenSearch Index

Index name: **`obsidian-docs`** (set via `OPENSEARCH_INDEX` env var).

The index is automatically created by the ingestion pipeline's `opensearch-init` container using the mapping at `../infrastructure/opensearch/index-mapping.json`.

To create it manually on a fresh local OpenSearch:

```bash
curl -X PUT http://localhost:9200/obsidian-docs \
  -H 'Content-Type: application/json' \
  -d @../infrastructure/opensearch/index-mapping.json
```

The index uses kNN plugin (HNSW, 384 dimensions) + standard BM25 for hybrid search.

---

## Integration with the Ingestion Pipeline

The search pipeline is **read-only** — it queries the `obsidian-docs` index but never writes to it.

| Shared resource | Ingestion side writes | Search side reads |
|-----------------|----------------------|------------------|
| OpenSearch `obsidian-docs` | `workers/ingestion/opensearch_client.py` bulk upsert | `pyworker/search_worker.py` hybrid query |
| Redis cache | — | `gateway/cache/redis.go` GET/SET |

To use the ingestion team's OpenSearch instead of running a local one:

1. Comment out `opensearch` and `opensearch-dashboards` services in `docker-compose.yml`
2. Set `OPENSEARCH_HOST=<ingestion_server_ip>` in `.env`

---

## Project Structure

```
Search-Engine/
├── gateway/                        # Go API Gateway (Role 4)
│   ├── cache/
│   │   └── redis.go                # Redis client — cache HIT/MISS/SET
│   ├── handlers/
│   │   └── search.go               # REST handler — Steps 1–4 of search flow
│   ├── grpcclient/
│   │   └── client.go               # gRPC client → PyWorker-2
│   ├── merger/
│   │   └── merger.go               # Top-K dedup + combined_score sort
│   ├── proto/                      # Generated Go gRPC stubs
│   ├── main.go                     # Server bootstrap (Redis + gRPC + HTTP)
│   ├── Dockerfile
│   └── go.mod
│
├── pyworker/                       # gRPC search worker (Role 5)
│   ├── search_worker.py            # gRPC servicer: NLP → embed → OpenSearch
│   ├── nlp_parser.py               # spaCy NLP — intent, keywords, filters
│   ├── embedding_service.py        # SentenceTransformer all-MiniLM-L6-v2
│   ├── config.py                   # All env-var config (OpenSearch + Redis)
│   ├── proto/                      # Generated Python gRPC stubs
│   └── Dockerfile
│
├── frontend/                       # Next.js search UI (Role 5)
│   └── app/
│       ├── page.js                 # Search page
│       └── api/search/             # Fallback direct-to-OpenSearch route
│
├── docker-compose.yml              # 6 services: OS + Redis + pyworker + gateway + UI
└── .env.example                    # All variables documented
```

---

## Health Check

```bash
curl http://localhost:8080/health
```

Response:

```json
{
  "pyworker": "reachable",
  "redis":    "reachable",
  "status":   "ok"
}
```

`redis: "unreachable"` is **non-fatal** — search still works, just without caching.

---

## Stopping

```bash
docker compose down

# Also remove volumes (OpenSearch data, Redis data)
docker compose down -v
```
