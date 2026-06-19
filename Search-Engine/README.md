# HPE Search Engine — Go Gateway + PyWorker + Redis + OpenSearch

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
| `frontend` | `3000` | Next.js HPE-themed search UI |
| `go-gateway` | `8080` | REST entry point — `GET /search`, `GET /health` |
| `pyworker-2` | `50052` | gRPC — spaCy NLP → SentenceTransformer embed → OpenSearch |
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

> **Note:** Docker CE is not in the default RHEL repos. Add Docker's official RHEL repository.

```bash
# Add Docker CE repo (DNF5 syntax)
sudo dnf5 config-manager addrepo \
  --from-repofile=https://download.docker.com/linux/rhel/docker-ce.repo

# Install Docker CE + Compose plugin
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Enable and start the Docker daemon
sudo systemctl enable --now docker

# Allow your user to run Docker without sudo
sudo usermod -aG docker $USER && newgrp docker

# Verify
docker --version          # Docker version 26.x or higher
docker compose version    # Docker Compose version v2.27 or higher
```

> **SELinux note (RHEL 10):** SELinux is enforcing by default. If containers fail to read bind-mounted paths:
> ```bash
> # Option A — label the host path (preferred for production)
> chcon -Rt container_file_t /path/to/your/data
>
> # Option B — set permissive temporarily (testing only)
> sudo setenforce 0
> ```

#### 2. OpenSearch kernel setting (RHEL 10)

```bash
# Apply immediately (no reboot needed)
sudo sysctl -w vm.max_map_count=262144

# Persist across reboots via sysctl.d drop-in
echo "vm.max_map_count=262144" | sudo tee /etc/sysctl.d/99-opensearch.conf
sudo sysctl --system
```

---

### Fedora 43 — x86\_64

> Fedora 43 ships with **DNF5** by default. The syntax differs slightly from older DNF4 commands.

#### 1. Docker Engine on Fedora 43

```bash
# Add Docker CE repo for Fedora (DNF5 syntax)
sudo dnf5 config-manager addrepo \
  --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo

# Install Docker CE + Compose plugin
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Enable and start Docker
sudo systemctl enable --now docker

# Allow non-root use
sudo usermod -aG docker $USER && newgrp docker

# Verify
docker --version
docker compose version
```

> **SELinux note (Fedora 43):** Fedora enforces SELinux. Apply the `container_file_t` label to any directories you bind-mount:
> ```bash
> chcon -Rt container_file_t /path/to/your/data
> ```

#### 2. OpenSearch kernel setting (Fedora 43)

```bash
# Apply immediately
sudo sysctl -w vm.max_map_count=262144

# Persist across reboots
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
           │    · exact dates → range    │
           │    · relative dates/type/   │
           │      size → filter clauses  │
           │ b. SentenceTransformer      │
           │    embed (384-dim)          │
           │ c. OpenSearch query:        │
           │    - kNN semantic match     │
           │    - BM25 keyword match     │
           │    - match_all if filters   │
           │ d. Merge & Rank:            │
           │    - blend kNN/BM25 scores  │
           │    - dedup chunks by file   │
           │    - combined_score >= 0.55 │
           ▼                             │
         OpenSearch :9200                │
           │ Top-K results               │
           ▼                             │
         Go Gateway                      │
           │ parse & format response     │
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
| `files uploaded on May 15th` | `exact_date:may_15_<year>` → 24-hour range filter |
| `pdfs` | `type:pdf` filter → `match_all` (no keywords required) |

---

## OpenSearch Index

Index name: **`hpe-search-docs`** (set via `OPENSEARCH_INDEX` env var).

The index is automatically created by the ingestion pipeline's `opensearch-init` container using the mapping at `../infrastructure/opensearch/index-mapping.json`.

To create it manually on a fresh local OpenSearch:

```bash
curl -X PUT http://localhost:9200/hpe-search-docs \
  -H 'Content-Type: application/json' \
  -d @../infrastructure/opensearch/index-mapping.json
```

The index uses kNN plugin (HNSW, 384 dimensions) + standard BM25 for hybrid search.

---

## Integration with the Ingestion Pipeline

The search pipeline is **read-only** — it queries the `hpe-search-docs` index but never writes to it.

| Shared resource | Ingestion side writes | Search side reads |
|-----------------|----------------------|------------------|
| OpenSearch `hpe-search-docs` | `workers/ingestion/opensearch_client.py` bulk upsert | `pyworker/search_worker.py` hybrid query |
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
