# HPE Enterprise Search Engine

A production-grade, hybrid (BM25 + kNN) enterprise search pipeline built on OpenSearch, Redis, Kafka, and MinIO — deployable via **Docker Compose** (local dev) or **Minikube / Kubernetes** (server).

| Pipeline | Directory | What it does |
|----------|-----------|--------------|
| **Ingestion** | `workers/`, `infrastructure/`, `docker-compose.yml` | File upload → Kafka → Tika extract → chunk → embed → OpenSearch |
| **Search** | `Search-Engine/` | REST query → Go Gateway → gRPC PyWorker → OpenSearch hybrid query → Redis cache |

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
   - [Common Requirements](#common-requirements)
   - [Docker Compose deployment](#docker-compose-deployment)
   - [Minikube deployment](#minikube-deployment)
3. [Repository Structure](#repository-structure)
4. [Quick Start — Docker Compose](#quick-start--docker-compose)
5. [Quick Start — Minikube](#quick-start--minikube)
6. [Service Ports](#service-ports)
7. [Environment Variables](#environment-variables)
8. [OpenSearch Index](#opensearch-index)
9. [How the Pipelines Work](#how-the-search-pipeline-works)
10. [Uploading Files](#uploading-files-with-aws-cli)
11. [Debugging](#debugging)
12. [Running Tests](#running-tests)

---

## Architecture Overview

```
┌─────────────── INGESTION PIPELINE ───────────────────────────────┐
│                                                                    │
│  MinIO (S3)  ──PUT event──▶  Kafka (3-node)  ──▶  Ingestion      │
│  :9000/:9001                 :29092               Worker          │
│                                                    │              │
│                              Model Server  ◀───────┤  (embed)    │
│                              :8000 (text+image)     │              │
│                                                     ▼              │
│                              Tika  ◀──────── OpenSearch :9200    │
│                              :9998                  │              │
└──────────────────────────────────────────── Redis :6379 ─────────┘
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

## Prerequisites

> **Target platforms:** WSL 2 (Ubuntu 22.04 on Windows) · RHEL 10 x86\_64 · Fedora 43 x86\_64

### Hardware Requirements

| Resource | Docker Compose (min) | Minikube (min) | Recommended |
|----------|---------------------|----------------|-------------|
| CPU | 4 cores (x86\_64) | 4 cores (x86\_64) | 6+ cores |
| RAM | 8 GB | 12 GB | 16 GB |
| Disk | 25 GB free | 45 GB free | 60 GB free |

---

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

> **Alternative (Docker Engine inside WSL, no Desktop):**
> ```bash
> sudo apt-get update
> sudo apt-get install -y ca-certificates curl gnupg
> sudo install -m 0755 -d /etc/apt/keyrings
> curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
>   | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
> echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] \
>   https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
>   | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
> sudo apt-get update
> sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
> sudo usermod -aG docker $USER && newgrp docker
> ```

#### 3. OpenSearch kernel setting (WSL)

WSL does not persist sysctl between reboots. Add to your shell profile **and** run manually each session:

```bash
# Apply now
sudo sysctl -w vm.max_map_count=262144

# Persist across WSL restarts — add to ~/.bashrc or ~/.zshrc
echo 'sudo sysctl -w vm.max_map_count=262144 > /dev/null 2>&1' >> ~/.bashrc
```

> Alternatively, create `/etc/wsl.conf` on the Windows side to auto-apply on WSL start:
> ```ini
> # C:\Users\<you>\.wslconfig
> [wsl2]
> kernelCommandLine=sysctl.vm.max_map_count=262144
> ```

#### 4. Minikube on WSL 2

```bash
# Install minikube (x86_64)
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube && rm minikube-linux-amd64

# Install kubectl (x86_64)
curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install kubectl /usr/local/bin/kubectl && rm kubectl

# Verify
minikube version    # v1.32.x or higher
kubectl version --client

# Minikube uses Docker as its driver inside WSL — no extra setup needed
minikube config set driver docker
```

#### 5. Git & AWS CLI (WSL)

```bash
sudo apt-get install -y git awscli
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
> RHEL 10 uses the `container_file_t` label (replaces `svirt_sandbox_file_t` from older releases).

#### 2. OpenSearch kernel setting (RHEL 10)

```bash
# Apply immediately (no reboot needed)
sudo sysctl -w vm.max_map_count=262144

# Persist across reboots via sysctl.d drop-in
echo "vm.max_map_count=262144" | sudo tee /etc/sysctl.d/99-opensearch.conf
sudo sysctl --system

# Confirm
sysctl vm.max_map_count   # should print: vm.max_map_count = 262144
```

#### 3. Firewall (RHEL 10 — only needed for Minikube NodePorts)

```bash
# Open the NodePorts used by k8s/ manifests
sudo firewall-cmd --add-port=30080/tcp --permanent   # Go Gateway
sudo firewall-cmd --add-port=30300/tcp --permanent   # Frontend
sudo firewall-cmd --add-port=30601/tcp --permanent   # OpenSearch Dashboards
sudo firewall-cmd --add-port=30900/tcp --permanent   # MinIO S3 API
sudo firewall-cmd --add-port=30901/tcp --permanent   # MinIO Console
sudo firewall-cmd --reload

# Verify
sudo firewall-cmd --list-ports
```

#### 4. Minikube on RHEL 10 x86\_64

```bash
# Install minikube
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube && rm minikube-linux-amd64

# Install kubectl
curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install kubectl /usr/local/bin/kubectl && rm kubectl

# Verify
minikube version    # v1.34.x or higher
kubectl version --client

# Use Docker as the Minikube driver
minikube config set driver docker
```

#### 5. Git & AWS CLI (RHEL 10)

```bash
sudo dnf install -y git unzip

# AWS CLI v2 — install from the official bundle (not in DNF)
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install
rm -rf awscliv2.zip aws/
aws --version
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

#### 3. Firewall (Fedora 43 — only needed for Minikube NodePorts)

```bash
sudo firewall-cmd --add-port=30080/tcp --permanent   # Go Gateway
sudo firewall-cmd --add-port=30300/tcp --permanent   # Frontend
sudo firewall-cmd --add-port=30601/tcp --permanent   # OpenSearch Dashboards
sudo firewall-cmd --add-port=30900/tcp --permanent   # MinIO S3 API
sudo firewall-cmd --add-port=30901/tcp --permanent   # MinIO Console
sudo firewall-cmd --reload
```

#### 4. Minikube on Fedora 43 x86\_64

```bash
# Install minikube
curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64
sudo install minikube-linux-amd64 /usr/local/bin/minikube && rm minikube-linux-amd64

# Install kubectl
curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install kubectl /usr/local/bin/kubectl && rm kubectl

# Verify
minikube version
kubectl version --client

# Use Docker driver
minikube config set driver docker
```

#### 5. Git & AWS CLI (Fedora 43)

```bash
sudo dnf install -y git unzip

# AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip awscliv2.zip && sudo ./aws/install
rm -rf awscliv2.zip aws/
aws --version
```

---

### Quick prerequisite check (all platforms)

Run this snippet inside your terminal to verify everything is in place before deploying:

```bash
echo "=== Prerequisite Check ===" && \
  docker --version && \
  docker compose version && \
  (command -v minikube && minikube version || echo "minikube: not installed (only needed for k8s deploy)") && \
  (command -v kubectl  && kubectl version --client 2>/dev/null || echo "kubectl: not installed") && \
  echo "vm.max_map_count=$(sysctl -n vm.max_map_count) (need >= 262144)" && \
  echo "=== All checks done ==="
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
│   ├── docker-compose.yml          # Search-side stack (standalone)
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
│       ├── image_embedder.py       # nomic-embed-vision-v1.5 image embedder
│       ├── main.py                 # Uvicorn entry
│       ├── requirements.txt        # Model-server-specific Python deps
│       └── load-balancer/nginx.conf
│
├── backend/
│   ├── cache/redis_cache.py        # Python Redis cache layer (search results)
│   └── search/opensearch_query_builder.py  # Hybrid BM25+kNN query reference
│
├── infrastructure/
│   ├── opensearch/index-mapping.json  # kNN-enabled index schema (hpe-search-docs)
│   ├── kafka/                         # Kafka topic scripts
│   ├── minio/                         # MinIO bucket + event-notification config
│   └── startup.sh                     # One-shot bootstrap script
│
├── k8s/                            # Kubernetes / Minikube manifests
│   ├── 00-namespace.yaml
│   ├── 01-configmap.yaml
│   ├── 02-pvcs.yaml
│   ├── infrastructure/             # Kafka, MinIO, Tika, OpenSearch, Redis
│   ├── ingestion/                  # model-server, ingestion-worker
│   ├── search/                     # pyworker, go-gateway, frontend
│   └── deploy.sh                   # One-shot deploy script for Minikube
│
├── tests/
│   └── test_opensearch.py          # OpenSearch + Redis integration tests
│
├── docker-compose.yml              # Unified full-stack compose (14 services)
├── requirements.txt                # Python dependencies (ingestion + backend)
├── pytest.ini
└── .env.example                    # All environment variables documented
```

---

## Quick Start — Docker Compose

```bash
# 1. Clone the repo
git clone <repo-url> && cd HPE

# 2. Apply kernel setting for OpenSearch (Linux only)
sudo sysctl -w vm.max_map_count=262144

# 3. Configure environment
cp .env.example .env
# Edit .env only if you need non-default values (host, credentials, etc.)

# 4. Start all 14 services
docker compose up --build -d

# 5. Follow startup logs (wait ~2 min for all services to be healthy)
docker compose logs -f opensearch ingestion-worker model-server
```

Open **http://localhost:3000** once the stack is healthy.

---

## Quick Start — Minikube

The `k8s/deploy.sh` script handles everything — Minikube startup, image builds, manifest apply, and init job sequencing.

```bash
# 1. Clone the repo
git clone <repo-url> && cd HPE

# 2. Run the deploy script (takes ~5–10 min on first run — downloads models)
chmod +x k8s/deploy.sh
./k8s/deploy.sh

# Full reset (wipes namespace and redeploys from scratch):
./k8s/deploy.sh --reset
```

After deployment, the script prints access URLs:

```
Frontend:              http://<minikube-ip>:30300
Go Gateway (API):      http://<minikube-ip>:30080
MinIO Console:         http://<minikube-ip>:30901
OpenSearch Dashboards: http://<minikube-ip>:30601
```

Get your Minikube IP with:
```bash
minikube ip
```

### Common Minikube commands

```bash
# Watch all pods
kubectl get pods -n hpe-search -w

# Tail a service's logs
kubectl logs -n hpe-search deploy/model-server -f
kubectl logs -n hpe-search deploy/ingestion-worker -f

# Open a service in the browser
minikube service frontend -n hpe-search
minikube service go-gateway -n hpe-search

# Stop Minikube (preserves data)
minikube stop

# Delete entire cluster
minikube delete
```

---

## Service Ports

### Docker Compose

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

### Minikube (NodePort)

All services are reachable at `http://$(minikube ip):<NodePort>` — no port-forwarding needed.

| Service | NodePort | Description |
|---------|----------|-------------|
| **Frontend** | `30300` | Next.js search UI |
| **Go Gateway** | `30080` | REST API (`GET /search`, `GET /health`) |
| **MinIO S3 API** | `30900` | S3-compatible upload endpoint |
| **MinIO Console** | `30901` | MinIO web UI |
| **OpenSearch Dashboards** | `30601` | Index inspection UI |

---

## Environment Variables

Copy `.env.example` → `.env` and adjust as needed.

```bash
# OpenSearch — must match the index created by the ingestion pipeline
OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_INDEX=hpe-search-docs

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

The pipeline uses an index named **`hpe-search-docs`** with kNN + BM25 hybrid mapping.

The index is created automatically on startup by the `opensearch-init` service. The mapping lives in `infrastructure/opensearch/index-mapping.json`.

To create it manually:

```bash
curl -X PUT http://localhost:9200/hpe-search-docs \
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
      - kNN semantic search using the embedded vector
      - BM25 keyword search using multi_match
      - Filter-only query (no keywords) → match_all + filter clauses
   d. Merge & Rank:
      - Blend kNN and BM25 scores (configurable boosts)
      - Deduplicate chunks (keep highest scoring chunk per file)
      - Drop irrelevant matches (combined_score < 0.55 threshold)
6. PyWorker-2 returns ranked proto results to Go Gateway
7. Go Gateway caches and returns the final results to the UI
8. Go Gateway → Redis: store result with TTL    [X-Cache: MISS]
9. Go Gateway → Frontend → render results
```

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

## Uploading Files

### Docker Compose

MinIO is exposed on `localhost:9000`. Configure AWS CLI once:

```bash
aws configure set aws_access_key_id     minioadmin
aws configure set aws_secret_access_key minioadmin123
aws configure set default.region        us-east-1
```

```bash
# Upload a file
aws s3 cp ./file.pdf s3://uploads/file.pdf --endpoint-url http://localhost:9000

# Upload a folder
aws s3 cp ./folder/ s3://uploads/ --recursive --endpoint-url http://localhost:9000

# List bucket contents
aws s3 ls s3://uploads/ --endpoint-url http://localhost:9000 --recursive
```

### Minikube

MinIO's S3 API is exposed on **NodePort 30900** — no port-forwarding needed. Use the provided script:

```bash
# Upload a single file (bucket defaults to "uploads")
./infrastructure/upload.sh ./yourfile.pdf

# Upload an entire folder
./infrastructure/upload.sh ./your-folder/

# Upload to a specific bucket
./infrastructure/upload.sh ./yourfile.pdf my-bucket
```

Or call AWS CLI directly:

```bash
MINIO_URL="http://$(minikube ip):30900"

# Upload a file
aws s3 cp ./file.pdf s3://uploads/file.pdf --endpoint-url $MINIO_URL

# Upload a folder
aws s3 cp ./folder/ s3://uploads/ --recursive --endpoint-url $MINIO_URL

# List bucket contents
aws s3 ls s3://uploads/ --endpoint-url $MINIO_URL --recursive
```

> **How it triggers ingestion:** Every upload PUT fires a MinIO event → Kafka `file-upload-events` → Ingestion Worker → Tika extract → chunk → embed → OpenSearch. Files become searchable within seconds.

---

## Debugging

### Docker Compose

```bash
# OpenSearch health
curl -s http://localhost:9200/_cluster/health | python3 -m json.tool
curl -s http://localhost:9200/hpe-search-docs/_count | python3 -m json.tool

# Redis cache
docker exec hpe-search-redis redis-cli FLUSHDB   # flush stale cache
docker exec hpe-search-redis redis-cli DBSIZE

# Service logs
docker logs hpe-search-ingestion-worker -f
docker logs hpe-search-go-gateway -f
docker logs hpe-search-pyworker-2 -f

# Kafka topics
docker exec hpe-search-kafka-1 /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka1:9092 --list

# MinIO — list files
aws s3 ls s3://uploads/ --endpoint-url http://localhost:9000 --recursive
# Web console: http://localhost:9001  (minioadmin / minioadmin123)
```

### Minikube

```bash
MINIO_URL="http://$(minikube ip):30900"

# Pod status
kubectl get pods -n hpe-search

# OpenSearch health (via NodePort)
curl -s "http://$(minikube ip):30601"   # OpenSearch Dashboards UI
# or port-forward for raw API access:
kubectl port-forward svc/opensearch 9200:9200 -n hpe-search &
curl -s http://localhost:9200/hpe-search-docs/_count | python3 -m json.tool
kill %1

# Redis cache — flush stale results after a deploy
kubectl exec deploy/redis -n hpe-search -- redis-cli FLUSHALL

# Service logs
kubectl logs -n hpe-search deploy/ingestion-worker -f
kubectl logs -n hpe-search deploy/go-gateway -f
kubectl logs -n hpe-search deploy/pyworker -f
kubectl logs -n hpe-search deploy/model-server -f

# Kafka topics
kubectl exec kafka-0 -n hpe-search -- /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 --list

# Watch Kafka upload events in real-time
kubectl exec kafka-0 -n hpe-search -- /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 --topic file-upload-events --from-beginning

# MinIO — list files (direct NodePort, no port-forward)
aws s3 ls s3://uploads/ --endpoint-url $MINIO_URL --recursive
# Web console: http://$(minikube ip):30901  (minioadmin / minioadmin123)
```

---

## Stopping

### Docker Compose

```bash
# Stop containers (preserves volumes)
docker compose down

# Stop and remove all data volumes
docker compose down -v
```

### Minikube

```bash
# Stop cluster (preserves data)
minikube stop

# Delete cluster and all data
minikube delete
```

---

## Running Tests

```bash
# Install Python dependencies
pip install -r requirements.txt

# Unit tests only (no running services needed)
pytest tests/test_opensearch.py -v -m unit

# Integration tests (requires OpenSearch + Redis running on localhost)
pytest tests/test_opensearch.py -v -m integration

# Full E2E pipeline test
pytest workers/ingestion/test_e2e_full_pipeline.py -v
```
