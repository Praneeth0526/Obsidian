# HPE Object Search Engine — End-to-End Technical Architecture

## 1. Executive Summary

The HPE Object Search Engine is a production-grade, hybrid enterprise search platform designed for extreme scale and accuracy. It processes multimodal documents (PDFs, presentations, images, text) through a Kafka-driven ingestion pipeline, generating highly optimized vector embeddings using the state-of-the-art **Jina CLIP v2** model. 

At query time, the system leverages a **Go API Gateway** and a **Python gRPC Search Worker** to translate natural language user queries into robust OpenSearch hybrid operations (kNN Semantic + BM25 Lexical + Timezone-Aware Filtering). Finally, a **Flan-T5** model provides instant, abstractive AI summarization of the top result, which is aggressively cached via Redis.

The entire infrastructure is containerized and managed via Kubernetes (Minikube) or Docker Compose, ensuring scalable, fault-tolerant execution.

---

## 2. Infrastructure Components

The core data layer consists of several highly-available subsystems:

- **MinIO (S3-Compatible):** The primary object storage for uploaded files. It supports multiple Role-Based Access Control (RBAC) buckets (`admin-uploads`, `manager-uploads`, `user-uploads`) and acts as the event trigger for the ingestion pipeline.
- **Kafka (3-Node Cluster) & Zookeeper:** The message broker handling `s3:ObjectCreated` events, guaranteeing ordered, fault-tolerant message delivery to the ingestion workers.
- **OpenSearch:** The central document database mapped with a `knn_vector` field (512-dim, HNSW engine, cosine similarity space). It natively executes arithmetic-mean score blending via a `hybrid-search-pipeline`.
- **Redis:** An in-memory key-value store acting as an ultra-fast query cache (Step 1 and 4 of the search flow), significantly reducing latency for popular search queries.
- **Apache Tika:** A dedicated container for extracting raw text, metadata, and structural features from highly complex document formats (PDFs, Word, PPT).

---

## 3. Ingestion Pipeline (Asynchronous Document Processing)

The ingestion flow is entirely event-driven, ensuring massive throughput without blocking user operations.

### Flow Diagram:
```text
MinIO [Upload] ──(S3 Event)──▶ Kafka [Topic: file-upload-events]
                                        │
                         Ingestion Worker (Python Consumer)
                                        │
           ┌────────────────────────────┼────────────────────────────┐
           ▼                            ▼                            ▼
      Apache Tika                 Text Chunker                Jina CLIP v2
   (Text Extraction)           (Sliding Window)             (Local Inference)
           │                            │                            │
           └────────────────────────────┴────────────────────────────┘
                                        ▼
                                  OpenSearch
                    (Idempotent Bulk Upsert of chunk_docs)
```

### Key Mechanisms:
1. **Dynamic Bucket Handling:** The worker dynamically extracts the source bucket (`admin-uploads`, `manager-uploads`, etc.) from the Kafka payload, persisting this metadata to OpenSearch for downstream RBAC filtering.
2. **Chunking & Embeddings:** Raw text is sliced using a sliding-window chunker to preserve contextual overlap. Each chunk (or raw image byte-stream) is passed directly to an in-memory **Jina CLIP v2** model to generate a rich 512-dimensional vector.
3. **Idempotency:** OpenSearch document IDs are deterministically generated using the format `SHA256(object_key) + chunk_index`, ensuring safe retries without index duplication.

---

## 4. Search Pipeline (Real-Time Retrieval & RAG)

The search pipeline focuses on ultra-low latency, combining the speed of a Go Gateway with the computational density of Python-based Machine Learning.

### Flow Diagram:
```text
Frontend (Next.js) ──(REST GET)──▶ Go API Gateway
                                        │
                                      Redis (Cache Check)
                                     /     \
                              (HIT) /       \ (MISS)
                                   /         \
                         Return Cached      gRPC Stream
                                               ▼
                                      Python Search Worker
                                               │
           ┌───────────────────────────────────┼───────────────────────────────────┐
           ▼                                   ▼                                   ▼
       spaCy NLP                          Jina CLIP v2                        OpenSearch
(Intent & Filter Parsing)            (Query Vectorization)            (kNN + BM25 Hybrid Query)
           │                                   │                                   │
           └───────────────────────────────────┴───────────────────────────────────┘
                                               ▼
                                         Result Merger
                           (Deduplication & Threshold Filtering)
                                               ▼
                                         Flan-T5-Small
                                  (Abstractive AI Summarization)
                                               ▼
                                    Return to Go Gateway
                                     (Set Redis Cache)
```

### Key Mechanisms:
1. **Go API Gateway:** Intercepts REST queries. It maintains a persistent Redis connection for immediate cache retrieval (sub-1ms latency on cache hits). On a cache miss, it routes the query via gRPC to the PyWorker.
2. **spaCy NLP Intent Parsing:** Uses lexical rules and regex to map colloquial terms (e.g., "powerpoints", "pics") to hard OpenSearch extensions (`.pptx`, `.jpg`).
3. **Timezone-Aware Filtering:** Converts conversational relative dates ("last week", "today", "july 7th") into precise, timezone-adjusted UTC boundary filters to align exactly with stored ingestion timestamps.
4. **Native OpenSearch Hybrid Query:** 
   - Executes a semantic `kNN` search against the 512-dim vector field.
   - Executes a lexical `BM25` search against the chunk text (with `fuzziness: AUTO` to handle typos).
   - Applies strict `filter` clauses based on bucket RBAC, parsed dates, and file extensions.
   - Offloads the scoring arithmetic (typically 0.6 kNN / 0.4 BM25) entirely to OpenSearch's internal `hybrid-search-pipeline` for maximum C++ speed.
5. **AI Summarization:** A lightweight, CPU-optimized Flan-T5 model generates a concise, human-readable summary of the highest-ranked chunk, directly attached to the final gRPC response payload.

---

## 5. Security & RBAC (Role-Based Access Control)

The system enforces multi-tenant security implicitly through MinIO and OpenSearch:
- **Bucket-Level Isolation:** Distinct MinIO buckets isolate uploads based on user roles (`admin`, `manager`, `user`).
- **Metadata Persistence:** The ingestion pipeline automatically records the originating bucket inside the OpenSearch `hpe-search-docs` index.
- **Query-Time Enforcement:** When a user queries the Go Gateway, the gateway inherently injects the user's role-based bucket list as a strict OpenSearch `terms` filter, ensuring users can only semantically match against documents they are explicitly authorized to view.

---

## 6. Deployment & Resiliency

- **Minikube / Kubernetes Native:** All components are defined via robust Kubernetes deployments (`k8s/`).
- **Initialization Lifecycle:** Dedicated `init-jobs` (`minio-init-job`, `kafka-init-job`) run prior to the core services, guaranteeing that buckets, Kafka topics, and OpenSearch index schemas (`knn_vector`) are correctly configured before workers begin consuming.
- **Hardware Acceleration:** K8s Pod resource limits are configured to strictly funnel available GPU hardware passthrough (e.g., `nvidia.com/gpu: 1`) to the PyWorker, achieving sub-100ms vectorization and inference latency during real-time queries.
