# Implementation Plan — Role 5 (NLP Parser fix) + Role 4 (Go Gateway, your part)

## Scope Clarification

| Work Item | Owner | Included Here |
|-----------|-------|---------------|
| PyWorker-2 gRPC server | You (Role 5) | ✅ |
| NLP Parser (open-source swap) | You (Role 5) | ✅ |
| Go API Gateway REST server | You (Role 4 partial) | ✅ |
| gRPC client → PyWorker-2 | You (Role 4 partial) | ✅ |
| Top-K result merging & response | You (Role 4 partial) | ✅ |
| Redis caching layer | Role 3 member | ❌ Excluded |
| OpenSearch indexes (kNN + BM25) | Role 3 member | ❌ Excluded |

---

## Part A — Replace OpenAI NLP Parser with Open-Source (spaCy)

### Why spaCy?
- **Fully offline** — no API key, no external calls
- **Production-grade** — used in enterprise NLP pipelines
- **Fast** — `en_core_web_sm` runs entirely on CPU, sub-10ms per query
- **Rich linguistic features** — NER, POS tagging, dependency parsing all in one
- **Aligns perfectly** with our existing rule-based fallback (keeps the same interface)

### What Changes

| File | Change |
|------|--------|
| `pyworker/nlp_parser.py` | Replace `_init_llm()` + OpenAI chain with spaCy pipeline |
| `pyworker/requirements.txt` | Remove `langchain-openai`, add `spacy` |
| `pyworker/Dockerfile` | Add `python -m spacy download en_core_web_sm` |

### New NLP Parser Design

The new `NLPQueryParser` will use **spaCy's `en_core_web_sm`** model for:

1. **Tokenization + POS tagging** — identify nouns (likely file names / topics), adjectives
2. **Named Entity Recognition (NER)** — detect dates (`DATE`, `TIME`), sizes, organizations
3. **Lemmatization** — normalize keywords (`reports` → `report`)
4. **Intent text extraction** — after filters stripped, remaining NOUN/PROPN/ADJ tokens form the semantic search string

The rule-based filter extraction (date, size, type patterns) stays **exactly as-is** since it's solid regex logic — spaCy just replaces the LLM layer for intent/keyword extraction.

**Parser interface stays identical** → `parse(query) → (intent_text, keywords, filters)` — zero changes needed in `search_worker.py`.

---

## Part B — Go API Gateway (Role 4, your portion)

### What You Own
```
Go Gateway:
  ├── REST endpoint: POST /search?q=...
  ├── gRPC client → PyWorker-2 (port 50052)
  ├── Receive SearchQueryResponse from PyWorker-2
  ├── Merge / rank Top-K results
  └── Return JSON response to Frontend
  
NOT yours:
  └── Redis cache check/store (Role 3's addition)
```

### New Directory Structure
```
Search-Engine/
└── gateway/               ← NEW
    ├── main.go
    ├── go.mod
    ├── go.sum
    ├── handlers/
    │   └── search.go      ← REST handler
    ├── grpcclient/
    │   └── client.go      ← gRPC client to PyWorker-2
    ├── merger/
    │   └── merger.go      ← Top-K result merging logic
    ├── proto/
    │   └── search.proto   ← copy from pyworker/proto (shared contract)
    └── Dockerfile
```

> **Note**: The `.proto` file is shared. Role 3's Redis middleware will slot in cleanly between the REST handler and the gRPC call — the handler will check Redis first (their code), then call gRPC on miss (your code). Design for this interface.

### REST API Contract

```
GET /search?q=<query>&limit=<n>
```

**Response:**
```json
{
  "results": [
    {
      "id": "abc123",
      "object_name": "quarterly-report.pdf",
      "bucket": "hpe-objects",
      "size_bytes": 204800,
      "content_type": "application/pdf",
      "extension": "pdf",
      "last_modified": "2026-05-07T10:30:00Z",
      "combined_score": 0.92,
      "keyword_score": 0.87,
      "semantic_score": 0.0
    }
  ],
  "total_hits": 1,
  "intent_text": "quarterly report",
  "extracted_keywords": ["quarterly", "report"],
  "applied_filters": ["type:pdf"]
}
```

**Other endpoints:**
```
GET /health    → { "status": "ok", "pyworker": "reachable" }
GET /stats     → (future, can stub for now)
```

### gRPC Client Design

- Connect to `PYWORKER_HOST:PYWORKER_PORT` (env vars)
- Reuse a single gRPC connection (connection pooling)
- Set a configurable timeout (default: 10s)
- On timeout/error → return 503

### Top-K Merging Logic

Since PyWorker-2 returns pre-ranked results (combined_score), the Go merger will:
1. Sort results by `combined_score` descending
2. Deduplicate by `id`
3. Trim to `limit` (default 10, max 50)
4. (Future hook) If Role 3 adds a Redis cache here, the merger result is what gets cached

### docker-compose.yml Additions

```yaml
gateway:
  build:
    context: .
    dockerfile: gateway/Dockerfile
  container_name: role4-gateway
  ports:
    - "8080:8080"
  environment:
    PYWORKER_HOST: pyworker
    PYWORKER_PORT: 50052
    GATEWAY_PORT: 8080
  depends_on:
    - pyworker
  restart: unless-stopped
```

Frontend's `NEXT_PUBLIC_API_BASE` will be updated to point to `http://gateway:8080`.

---

## Implementation Order

```
Step 1  →  Replace NLP parser (spaCy)          [pyworker/nlp_parser.py]
Step 2  →  Update pyworker requirements         [pyworker/requirements.txt]
Step 3  →  Update pyworker Dockerfile           [pyworker/Dockerfile]
Step 4  →  Scaffold Go gateway module           [gateway/go.mod, main.go]
Step 5  →  Copy + compile proto for Go          [gateway/proto/]
Step 6  →  Build gRPC client                   [gateway/grpcclient/client.go]
Step 7  →  Build REST handler                  [gateway/handlers/search.go]
Step 8  →  Build merger                        [gateway/merger/merger.go]
Step 9  →  Gateway Dockerfile                  [gateway/Dockerfile]
Step 10 →  Update docker-compose.yml           [add gateway service]
Step 11 →  Update frontend env                 [point to gateway:8080]
```

---

## Interface for Role 3 (Redis) Integration

Role 3 will insert their Redis middleware **before the gRPC call** in `handlers/search.go`. 

The handler will be structured so Role 3 can add:
```go
// Role 3 inserts here:
// cached, result := redis.Get(queryHash)
// if cached { return result }
// ... (your gRPC call below)
// Role 3 also adds:
// redis.Set(queryHash, result, TTL)
```

This is documented clearly in code comments so integration is seamless.
