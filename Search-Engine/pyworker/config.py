import os

# gRPC Server Configuration
GRPC_HOST = os.getenv("GRPC_HOST", "0.0.0.0")
GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))
GRPC_SEARCH_PORT = int(os.getenv("GRPC_SEARCH_PORT", "50052"))

# Embedding Model Configuration
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Alibaba-NLP/gme-Qwen2-VL-2B-Instruct")
EMBEDDING_DIMENSION = 1536  # gme-Qwen2-VL outputs 1536-dim vectors

OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "hpe-search-docs")
DEFAULT_LIMIT = int(os.getenv("DEFAULT_LIMIT", "10"))

# Cache Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB   = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# TTL settings (seconds) — mirrors ingestion pipeline .env.example
CACHE_TTL         = int(os.getenv("REDIS_TTL_DEFAULT", "300"))
CACHE_TTL_FILTERED = int(os.getenv("REDIS_TTL_FILTERED", "600"))
CACHE_TTL_POPULAR  = int(os.getenv("REDIS_TTL_POPULAR", "1800"))
CACHE_POPULAR_THRESHOLD = int(os.getenv("REDIS_POPULAR_THRESHOLD", "10"))