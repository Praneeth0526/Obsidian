# Graph Report - .  (2026-06-12)

## Corpus Check
- Corpus is ~40,452 words - fits in a single context window. You may not need a graph.

## Summary
- 553 nodes · 778 edges · 30 communities detected
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 106 edges (avg confidence: 0.66)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]

## God Nodes (most connected - your core abstractions)
1. `PipelineTestRunner` - 28 edges
2. `TestFile` - 26 edges
3. `ChunkDocument` - 18 edges
4. `OpenSearchClient` - 18 edges
5. `RedisCache` - 17 edges
6. `SearchResult` - 16 edges
7. `SearchWorkerServicer` - 16 edges
8. `TikaExtractor` - 16 edges
9. `NLPQueryParser` - 14 edges
10. `TestRedisCacheIntegration` - 13 edges

## Surprising Connections (you probably didn't know these)
- `test_same_inputs_produce_same_key()` --calls--> `build_cache_key()`  [INFERRED]
  tests/test_opensearch.py → backend/cache/redis_cache.py
- `test_different_queries_produce_different_keys()` --calls--> `build_cache_key()`  [INFERRED]
  tests/test_opensearch.py → backend/cache/redis_cache.py
- `test_filter_changes_key()` --calls--> `build_cache_key()`  [INFERRED]
  tests/test_opensearch.py → backend/cache/redis_cache.py
- `test_tags_order_does_not_matter()` --calls--> `build_cache_key()`  [INFERRED]
  tests/test_opensearch.py → backend/cache/redis_cache.py
- `test_query_case_insensitive()` --calls--> `build_cache_key()`  [INFERRED]
  tests/test_opensearch.py → backend/cache/redis_cache.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.05
Nodes (43): build_cache_key(), Deterministic cache key from query + all filter parameters.     Matches the hash, ChunkDocument, OpenSearchClient, Stable document ID: <object_key>#<chunk_index>         Guarantees idempotent ups, Thread-safe wrapper around the opensearch-py client.     Instantiate once per wo, Returns True when the cluster is green or yellow., Upsert a single ChunkDocument.         Uses script-based upsert for idempotency. (+35 more)

### Community 1 - "Community 1"
Cohesion: 0.07
Nodes (55): _build_docx(), _build_image(), _build_pdf(), _build_pptx(), generate_docx_large(), generate_docx_medium(), generate_docx_small(), generate_edge_corrupt_pdf() (+47 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (36): ChunkResult, chunker.py — LangChain-based text chunking for the ingestion pipeline.  Splits e, Container for a single text chunk with positional metadata., Wraps LangChain's ``RecursiveCharacterTextSplitter`` with configurable     param, Split *text* into overlapping chunks with positional metadata.          Args:, TextChunker, analyse_chunk(), ChunkAnalysis (+28 more)

### Community 3 - "Community 3"
Cohesion: 0.08
Nodes (25): main(), PipelineTestRunner, _print_health(), _print_result_line(), test_e2e_full_pipeline.py — Comprehensive E2E test runner for the ingestion pipe, Verify all required services are reachable. Returns True if all healthy., Best-effort Kafka check: verify MinIO has Kafka event notifications configured., Upload a test file to MinIO. Returns the upload timestamp. (+17 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (21): EmbeddingService, Embedding Service - Generates vector embeddings using SentenceTransformers, Service for generating text embeddings using SentenceTransformers., Singleton pattern to ensure model is loaded only once., Initialize the embedding service., Encode text into a vector embedding.          Args:             text: The text t, Get the dimension of the embedding vectors., gRPC Worker Server - Hybrid Search Worker Handles query parsing, vectorization, (+13 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (24): BaseModel, ImageEmbedder, image_embedder.py — nomic-embed-vision wrapper for the Model Server.  Loads ``no, Embed a single preprocessed image into a 384-dim float vector.          The inpu, Wraps ``nomic-embed-vision-v1.5`` (CLIP-compatible) for image embedding.      Pa, Load the vision model and its image processor into memory.          Call this on, embed_image(), embed_text() (+16 more)

### Community 6 - "Community 6"
Cohesion: 0.06
Nodes (3): SearchQueryRequest, SearchQueryResponse, SearchResult

### Community 7 - "Community 7"
Cohesion: 0.1
Nodes (17): cached_search(), get_cache(), _get_default_cache(), _get_logger(), _hit_count_key(), _JsonFormatter, backend/cache/redis_cache.py ============================= Industry-grade Redis, Thread-safe Redis cache for search results.      Usage pattern (search layer) (+9 more)

### Community 8 - "Community 8"
Cohesion: 0.09
Nodes (18): Exception, _batchify(), EmbeddingError, ModelClient, model_client.py — HTTP client for the shared Model Server.  Sends text chunks (o, Embed a list of text strings into 384-dim vectors.          The list is split in, Embed a single preprocessed image into a 384-dim vector.          Args:, Return True if the model-server's /health endpoint responds 200. (+10 more)

### Community 9 - "Community 9"
Cohesion: 0.11
Nodes (17): build_opensearch_doc_document(), build_opensearch_doc_image(), _format_bytes(), test_report_generator.py — Report generation for E2E pipeline QA.  Produces thre, Build a single OpenSearch document for a text chunk.      Matches the schema in, Build a single OpenSearch document for an image.      Matches the schema in infr, Generates JSON, markdown, and OpenSearch document outputs., Generate the structured JSON test report. (+9 more)

### Community 10 - "Community 10"
Cohesion: 0.12
Nodes (12): ExtractionResult, tika_extractor.py — Text and metadata extraction via Apache Tika Server.  Sends, Extract text (and optionally metadata) from raw file bytes.          Args:, Auto-detect the MIME type of file bytes via Tika.          Useful when the Kafka, Return True if the Tika server is reachable., PUT /tika — returns plain text., PUT /meta — returns JSON metadata., Send a PUT request to a Tika endpoint with retry logic.          Args: (+4 more)

### Community 11 - "Community 11"
Cohesion: 0.14
Nodes (12): _failure(), ImageHandler, ImageResult, is_image_supported(), image_handler.py — Image preprocessing pipeline for the ingestion worker.  Valid, Validate and preprocess raw image bytes.          The returned ``ImageResult.ima, Return True if *content_type* is a supported image MIME type., Container for the output of ``ImageHandler.process()``.      Attributes: (+4 more)

### Community 12 - "Community 12"
Cohesion: 0.18
Nodes (8): _load_spacy(), NLPQueryParser, NLP Query Parser - Extracts intent, keywords, and filters from natural language, Use spaCy for linguistic analysis, then layer regex filters on top., Parses natural language queries to extract filters, intent text, and keywords., Remove date/size/type pattern text before passing to spaCy., Parse a natural language query.          Args:             query: The raw user q, Convert a canonical date filter key to a datetime range.

### Community 13 - "Community 13"
Cohesion: 0.13
Nodes (8): Client, New(), NewSearchWorkerClient(), _SearchWorker_ProcessQuery_Handler(), SearchWorkerClient, SearchWorkerServer, UnimplementedSearchWorkerServer, UnsafeSearchWorkerServer

### Community 14 - "Community 14"
Cohesion: 0.21
Nodes (7): Client, getEnv(), Key(), New(), parseInt(), monthToIndex(), parseNaturalQuery()

### Community 15 - "Community 15"
Cohesion: 0.2
Nodes (9): corsMiddleware(), getEnv(), main(), NewSearchHandler(), writeJSON(), SearchHandler, Merge(), Result (+1 more)

### Community 16 - "Community 16"
Cohesion: 0.16
Nodes (9): object, Missing associated documentation comment in .proto file., Constructor.          Args:             channel: A grpc.Channel., Missing associated documentation comment in .proto file., ProcessQuery handles cache miss - parses query, vectorizes, and performs hybrid, Missing associated documentation comment in .proto file., SearchWorker, SearchWorkerServicer (+1 more)

### Community 17 - "Community 17"
Cohesion: 0.5
Nodes (4): main(), process_failed_message(), Dead Letter Queue (DLQ) Consumer Consumes failed messages from file-upload-event, Log and inspect a failed message from the DLQ.

### Community 20 - "Community 20"
Cohesion: 1.0
Nodes (1): main.py — Entrypoint for the Model Server.  Boots the FastAPI application (defin

### Community 27 - "Community 27"
Cohesion: 1.0
Nodes (1): Load spaCy en_core_web_sm model. Returns None if not available.

### Community 31 - "Community 31"
Cohesion: 1.0
Nodes (1): Return a failed ``ImageResult`` with a descriptive error.

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (1): Print a compact one-line result for a test case.

### Community 33 - "Community 33"
Cohesion: 1.0
Nodes (1): Convert a TestCaseResult to a JSON-serializable dict.

### Community 34 - "Community 34"
Cohesion: 1.0
Nodes (1): Return a status icon for a test result.

### Community 35 - "Community 35"
Cohesion: 1.0
Nodes (1): Human-readable byte size.

### Community 36 - "Community 36"
Cohesion: 1.0
Nodes (1): Return True if Tika produced no usable text.

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (1): ``True`` after ``load()`` has completed successfully.

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (1): Output vector dimension (384).  Fixed regardless of model state.

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (1): ``True`` after ``load()`` has completed successfully.

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (1): Output vector dimension (384).  Fixed regardless of model state.

## Knowledge Gaps
- **181 isolated node(s):** `backend/cache/redis_cache.py ============================= Industry-grade Redis`, `Deterministic cache key from query + all filter parameters.     Matches the hash`, `Thread-safe Redis cache for search results.      Usage pattern (search layer)`, `Returns cached search results dict, or None on miss/error.         Also incremen`, `Store search results in Redis.          TTL selection:           - Explicit ttl` (+176 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 20`** (2 nodes): `main.py — Entrypoint for the Model Server.  Boots the FastAPI application (defin`, `main.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 27`** (1 nodes): `Load spaCy en_core_web_sm model. Returns None if not available.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 31`** (1 nodes): `Return a failed ``ImageResult`` with a descriptive error.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (1 nodes): `Print a compact one-line result for a test case.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 33`** (1 nodes): `Convert a TestCaseResult to a JSON-serializable dict.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 34`** (1 nodes): `Return a status icon for a test result.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 35`** (1 nodes): `Human-readable byte size.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 36`** (1 nodes): `Return True if Tika produced no usable text.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 37`** (1 nodes): ```True`` after ``load()`` has completed successfully.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `Output vector dimension (384).  Fixed regardless of model state.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): ```True`` after ``load()`` has completed successfully.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `Output vector dimension (384).  Fixed regardless of model state.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.