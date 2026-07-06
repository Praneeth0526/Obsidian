# HPE Object Search Engine — Go Gateway + PyWorker + Redis + OpenSearch

> **Stage 2 — Real-Time Search** | Roles 4 & 5

The search-side pipeline. Receives a natural-language query from the browser, checks Redis for a cached result, and if it misses, routes through the gRPC PyWorker to OpenSearch and caches the response.

---

## Architecture Overview

```
┌─────────────── SEARCH PIPELINE ─────────────────────────────────┐
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

## Containers

| Container | Port | What it does |
|-----------|------|--------------|
| `frontend` | `3000` | Next.js Object Search UI |
| `go-gateway` | `8080` | REST entry point — `GET /search`, `GET /health` |
| `pyworker-2` | `50052` | gRPC — spaCy NLP → SentenceTransformer embed → OpenSearch + T5 summarizer |
| `opensearch` | `9200` | Hybrid BM25 + kNN search database |
| `opensearch-dashboards` | `5601` | Dev UI for inspecting the index |
| `redis` | `6379` | Search result cache (Steps 1 & 4 of search flow) |

---

## Prerequisites

> **Target platforms:** WSL 2 (Ubuntu 22.04 on Windows) · RHEL 10 x86\_64 · Fedora 43 x86\_64

### Hardware Requirements (Search Pipeline only)

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 cores (x86\_64) | 4+ cores |
| RAM | 4 GB | 8 GB |
| Disk | 10 GB free | 20 GB free |

### WSL 2 (Windows 11 / Windows 10 22H2+)

#### 1. Enable WSL 2 + Ubuntu

```powershell
# Run in PowerShell (Administrator)
wsl --install -d Ubuntu-22.04
wsl --set-default-version 2
```

Restart Windows, then open the **Ubuntu 22.04** app and create your UNIX user.

#### 2. Docker Desktop (recommended for WSL)

Download and install **Docker Desktop for Windows** from [docs.docker.com/desktop/windows](https://docs.docker.com/desktop/windows/install/).

In Docker Desktop → **Settings → Resources → WSL Integration**, enable your Ubuntu distro.

```bash
# Verify inside WSL terminal
docker --version          # Docker version 24.x or higher
docker compose version    # Docker Compose version v2.20 or higher
```

#### 3. OpenSearch kernel setting (WSL)

WSL does not persist sysctl between reboots. Add to your shell profile **and** run manually each session:

```bash
# Apply now
sudo sysctl -w vm.max_map_count=262144

# Persist across WSL restarts — add to ~/.bashrc or ~/.zshrc
echo 'sudo sysctl -w vm.max_map_count=262144 > /dev/null 2>&1' >> ~/.bashrc
```

---

### RHEL 10 — x86\_64

> RHEL 10 ships with **DNF5** (replacing DNF4) and uses `dnf5 config-manager` syntax. All commands below reflect that.

#### 1. Docker Engine on RHEL 10

```bash
sudo dnf5 config-manager addrepo \
  --from-repofile=https://download.docker.com/linux/rhel/docker-ce.repo

sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER && newgrp docker

docker --version          # Docker version 26.x or higher
docker compose version    # Docker Compose version v2.27 or higher
```

#### 2. OpenSearch kernel setting (RHEL 10)

```bash
sudo sysctl -w vm.max_map_count=262144
echo "vm.max_map_count=262144" | sudo tee /etc/sysctl.d/99-opensearch.conf
sudo sysctl --system
```

---

### Fedora 43 — x86\_64

```bash
sudo dnf5 config-manager addrepo \
  --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo

sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER && newgrp docker

sudo sysctl -w vm.max_map_count=262144
echo "vm.max_map_count=262144" | sudo tee /etc/sysctl.d/99-opensearch.conf
sudo sysctl --system
```

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
OPENSEARCH_INDEX=hpe-search-docs

# If OpenSearch is running on another machine (e.g. ingestion team's server):
OPENSEARCH_HOST=<remote_ip>

# Redis TTL tuning (seconds)
REDIS_TTL_DEFAULT=300
REDIS_TTL_POPULAR=1800
REDIS_POPULAR_THRESHOLD=10
```

### 2. Deploy to Minikube

The entire search engine and ingestion pipeline is deployed using the central Kubernetes deployment script:

```bash
cd ..
./k8s/deploy.sh
```

This will automatically build the custom images inside Minikube and spin up the frontend, gateway, pyworker, and infrastructure (OpenSearch, Redis).

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
  │            :6379
  │
  └─ MISS ──▶ gRPC ProcessQuery
                │
                ▼
         PyWorker-2 :50052
           │ a. spaCy NLP parse
           │    · exact dates → range filter
           │    · relative dates/type/size → filter clauses
           │ b. SentenceTransformer embed (384-dim)
           │ c. OpenSearch hybrid query:
           │    - kNN semantic search (HNSW cosine, k=50)
           │    - BM25 keyword search (fuzziness: AUTO)
           │    - match_all if filter-only (no keywords)
           │ d. Merge & Rank:
           │    - blend kNN (0.6) + BM25 (0.4) scores
           │    - dedup chunks — one result per file
           │    - combined_score >= 0.45 threshold
           │ e. Abstractive summarization:
           │    - google/flan-t5-small generates human summary
           │    - per top-result chunk_text from Tika
           ▼
         OpenSearch :9200 → Top-K results
           ▼
         Go Gateway → parse & format response
           │
  Step 4: Redis store (X-Cache: MISS)
           │ TTL = default (300s) or popular (1800s if hits ≥ 10)
           ▼
         Frontend — render results + compact AI Summary panel
```

---

## AI Summary Panel

When results are returned, the frontend renders a compact **AI Summary** panel on the right side with:

| Section | Content |
|---------|---------|
| **Document Summary** | Abstractive summary generated by `google/flan-t5-small` from the top result's extracted text |
| **File Info** | Filename, Bucket, Type, Size, Upload date in a 2×2 grid |
| **Score Badges** | Inline pills — Semantic (S), Keyword (K), Combined (★), match count, file type breakdown |

The T5 model is loaded once at `pyworker` startup and runs on CPU for low-latency inference.

---

## Natural Language Query Examples

| Query | What PyWorker parses |
|-------|---------------------|
| `quarterly report pdf` | keywords: `quarterly report` + `type:pdf` |
| `images bigger than 10MB` | `type:image` + `size_gt:10MB` |
| `contracts from last week` | keywords: `contracts` + `date:last_week` |
| `invoices from May` | keywords: `invoices` + `month:may` |
| `marketing deck .pptx` | keywords: `marketing deck` + `extension:pptx` |
| `files uploaded on May 15th` | `exact_date:may_15_<year>` → 24-hour range filter |
| `pdfs` | `type:pdf` filter → `match_all` (no keywords required) |
| `informaton tecnology act` | fuzzy BM25 corrects typos → matches IT Act PDF |

---

## OpenSearch Index

Index name: **`hpe-search-docs`** (set via `OPENSEARCH_INDEX` env var).

The index is automatically created by the ingestion pipeline's `opensearch-init` container using the mapping at `../infrastructure/opensearch/index-mapping.json`.

> **Important:** The `embedding` field **must** be mapped as `knn_vector` type (dimension: 384, engine: nmslib, space: cosinesimil) for semantic search to work. If you see `Field 'embedding' is not knn_vector type`, the index was auto-created with a plain `float` type. Fix it by dropping and recreating:

```bash
# Drop the incorrectly typed index
kubectl exec -n hpe-search deploy/opensearch -- curl -s -X DELETE localhost:9200/hpe-search-docs

# Recreate with correct knn_vector mapping
kubectl exec -n hpe-search deploy/opensearch -- curl -s -X PUT localhost:9200/hpe-search-docs \
  -H "Content-Type: application/json" \
  -d "$(cat infrastructure/opensearch/index-mapping.json)"

# Re-upload files to trigger re-ingestion
aws s3 cp ./yourfile.pdf s3://uploads/ --endpoint-url http://$(minikube ip):30900
```

To create it manually on a fresh local OpenSearch:

```bash
curl -X PUT http://localhost:9200/hpe-search-docs \
  -H 'Content-Type: application/json' \
  -d @../infrastructure/opensearch/index-mapping.json
```

---

## Search Relevance Tuning

| Parameter | Value | Location | Effect |
|-----------|-------|----------|--------|
| `SEARCH_KNN_BOOST` | `0.6` | `01-configmap.yaml` / `.env` | Weight of semantic score |
| `SEARCH_BM25_BOOST` | `0.4` | `01-configmap.yaml` / `.env` | Weight of keyword score |
| `SEARCH_KNN_K` | `50` | `01-configmap.yaml` / `.env` | Number of kNN candidates |
| `combined_score` threshold | `0.45` | `search_worker.py` | Minimum score to surface a result |
| BM25 `fuzziness` | `AUTO` | `search_worker.py` | Typo tolerance for keyword matching |
| BM25 `prefix_length` | `2` | `search_worker.py` | First 2 chars must match exactly |

---

## Integration with the Ingestion Pipeline

The search pipeline is **read-only** — it queries the `hpe-search-docs` index but never writes to it.

| Shared resource | Ingestion side writes | Search side reads |
|-----------------|----------------------|------------------|
| OpenSearch `hpe-search-docs` | `workers/ingestion/opensearch_client.py` bulk upsert | `pyworker/search_worker.py` hybrid query |
| Redis cache | — | `gateway/cache/redis.go` GET/SET |

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
│   │   └── merger.go               # Top-K dedup + combined_score sort + Snippet propagation
│   ├── proto/                      # Generated Go gRPC stubs
│   │   └── search.proto            # Includes snippet field + go_package option
│   ├── main.go                     # Server bootstrap (Redis + gRPC + HTTP)
│   ├── Dockerfile
│   └── go.mod
│
├── pyworker/                       # gRPC search worker (Role 5)
│   ├── search_worker.py            # gRPC servicer: NLP → embed → OpenSearch → T5 summarize
│   ├── nlp_parser.py               # spaCy NLP — intent, keywords, filters
│   ├── embedding_service.py        # SentenceTransformer all-MiniLM-L6-v2 (384-dim)
│   ├── config.py                   # All env-var config (OpenSearch + Redis)
│   ├── requirements.txt            # Includes sentencepiece for T5 tokenizer
│   ├── proto/                      # Generated Python gRPC stubs
│   └── Dockerfile
│
├── frontend/                       # Next.js search UI (Role 5)
│   └── app/
│       ├── page.js                 # Search page + compact AI Summary panel
│       ├── globals.css             # Design system + compact summary layout styles
│       └── api/
│           └── download/           # MinIO presigned URL proxy
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

## Debugging

```bash
# Watch pyworker logs (NLP parsing, kNN/BM25 scores, T5 summarization)
kubectl logs -n hpe-search deploy/pyworker -f

# Watch go-gateway logs (cache hits/misses, request routing)
kubectl logs -n hpe-search deploy/go-gateway -f

# Flush Redis cache after a code change
kubectl exec -n hpe-search deploy/redis -- redis-cli FLUSHALL

# Check OpenSearch document count
kubectl exec -n hpe-search deploy/opensearch -- curl -s localhost:9200/hpe-search-docs/_count?pretty

# Test search API directly
curl "http://$(minikube ip):30080/search?q=information+technology+act" | python3 -m json.tool
```

---

## Stopping

```bash
minikube stop
```

To completely wipe the deployment and start fresh:

```bash
./k8s/deploy.sh --reset
```
