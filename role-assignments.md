# Role Assignments (Architecture-Based)

Date: 2026-05-09
Source: Architecture/Flow.pdf and Project-dessc.txt

## Architecture Summary

- Stage 1 (Background Ingestion)
  - User uploads file to MinIO.
  - s3:ObjectCreated triggers Apache Kafka event.
  - PyWorker-1 consumes event, extracts text with Apache Tika.
  - LangChain chunks text, SentenceTransformers embeds chunks.
  - Dual-index upsert to Qdrant (vector) and Meilisearch/OpenSearch (keyword).
- Stage 2 (Real-Time Search)
  - User submits query to Go API Gateway.
  - Gateway checks Redis cache by query hash.
  - Cache hit returns results instantly.
  - Cache miss calls PyWorker-2 via gRPC.
  - PyWorker-2 parses query with LangChain, generates vector with SentenceTransformers.
  - Hybrid search: vector to Qdrant, keywords to Meilisearch/OpenSearch.
  - Go backend merges Top-K, stores in Redis with TTL, returns snippets and URLs to frontend.

## Role Assignments (5 Roles)

1. Infrastructure & Messaging Engineer
   - Own MinIO setup and s3:ObjectCreated event wiring.
   - Deploy Kafka, configure topics/retention, and implement the event forwarder to PyWorker-1.
   - Handle DLQ for ingestion failures and container networking/volumes.

2. AI Ingestion Specialist (PyWorker-1)
   - Build PyWorker-1 ingestion pipeline.
   - Use Apache Tika for text extraction, LangChain for chunking.
   - Generate embeddings with all-MiniLM-L2-v2 and hand off to Role 3 for indexing.

3. Search Database Administrator
   - Deploy and manage Qdrant collections and Meilisearch/OpenSearch indexes.
   - Implement dual-index upsert interface for Role 2.
   - Tune relevance, manage index health and filter configuration.

4. Backend Orchestration & Caching Engineer (Go)
   - Build Go API Gateway and Redis cache flow.
   - On cache miss, call PyWorker-2 over gRPC.
   - Merge Top-K results, cache with TTL, return snippets and URLs to frontend.

5. Real-Time Search NLP Engineer (PyWorker-2)
   - Build PyWorker-2 gRPC server and shared .proto contract.
   - Parse queries with LangChain, generate vectors with all-MiniLM-L2-v2.
   - Execute hybrid search (Qdrant + Meilisearch/OpenSearch) and return ranked results.
