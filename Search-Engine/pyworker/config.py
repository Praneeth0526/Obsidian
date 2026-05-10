import os

# gRPC Server Configuration
GRPC_HOST = os.getenv("GRPC_HOST", "0.0.0.0")
GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))
GRPC_SEARCH_PORT = int(os.getenv("GRPC_SEARCH_PORT", "50052"))

# Embedding Model Configuration
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBEDDING_DIMENSION = 384  # all-MiniLM-L6-v2 outputs 384-dim vectors

OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_INDEX = os.getenv("OPENSEARCH_INDEX", "object-storage-index")
DEFAULT_LIMIT = int(os.getenv("DEFAULT_LIMIT", "10"))

# Cache Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
CACHE_TTL = 300  # 5 minutes