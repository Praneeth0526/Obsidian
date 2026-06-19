# HPE Search Engine - Project Documentation

## Project Overview

This is an HPE CPP Project — a search engine that lets you upload files to a storage server and search through them by name, type, and size. Built for the HPE Campus Connect Program.

### Current Architecture

The system has two main components:

1. **Indexer** - Watches MinIO storage and indexes file metadata into OpenSearch
2. **API** - FastAPI backend that provides search functionality and serves a web UI

## Architecture Diagram

See `/home/praneeth08/HPE/Architecture/Flow.pdf` for the full architecture diagram showing:

- **Frontend**: React Web Application
- **API Layer**: FastAPI REST API (port 8000)
- **Search Engine**: Elasticsearch/OpenSearch (port 9200)
- **Storage**: MinIO S3-compatible object storage (port 9000)
- **Data Processing**: Python-based processors
  - Ingestion Pipeline
  - Search Indexing Pipeline
  - Semantic Search Pipeline (with embeddings)
- **Cache**: Redis
- **Metadata Storage**: MongoDB
- **Authentication**: OAuth/JWT

### Simplified Current Implementation

The current codebase implements a simplified version:

- **Storage**: MinIO (S3-compatible)
- **Search Database**: OpenSearch
- **API**: FastAPI
- **Indexer**: Python polling-based

## Technology Stack

| Tool | Purpose | Port |
|------|---------|------|
| MinIO | Object storage (files) | 9000, Console: 9001 |
| OpenSearch | Search database | 9200 |
| OpenSearch Dashboards | Search UI | 5600 |
| FastAPI | Search API | 8000 |
| Python | Indexer service | - |

## Key Files

```
HPE/
├── api/
│   └── api.py              # FastAPI search backend
├── indexer/
│   └── indexer.py          # MinIO to OpenSearch indexer
├── Search-Engine/
│   ├── indexer/            # Polling-based indexer
│   ├── api/                # FastAPI backend
│   └── README.md           # Project documentation
├── Architecture/
│   └── Flow.pdf            # Full architecture diagram
└── CLAUDE.md               # This file
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Opens the search UI |
| `GET /search?q=filename` | Search files by name |
| `GET /search?extension=pdf` | Filter by file type |
| `GET /search?bucket=hpe-objects` | Filter by bucket |
| `GET /stats` | Total files, size, types breakdown |
| `GET /health` | Check if OpenSearch is reachable |
| `GET /docs` | Auto-generated API documentation |

## Index Schema

The OpenSearch index `object-storage-index` has the following fields:

| Field | Type | Description |
|-------|------|-------------|
| object_name | text | File name with keyword sub-field |
| bucket | keyword | Bucket name |
| size_bytes | long | File size in bytes |
| content_type | keyword | MIME type |
| last_modified | date | File modification date |
| etag | keyword | File ETag |
| extension | keyword | File extension |
| indexed_at | date | Indexing timestamp |

## Development Guidelines

### Adding New Features

1. **API Changes**: Modify `api/api.py` - add new endpoints following existing patterns
2. **Indexer Changes**: Modify `indexer/indexer.py` - the indexer polls MinIO every 10 seconds
3. **New Components**: Reference the full architecture in `Architecture/Flow.pdf` for context

### Testing

- Start services with `podman-compose up -d`
- Check API at `http://SERVER_IP:8000`
- Check health: `GET /health`
- View logs: `journalctl --user -f -u hpe-indexer` or `hpe-api`

### Configuration

Environment variables (set in `.env`):
- `SERVER_IP` - Server IP address
- `MINIO_ACCESS` - MinIO access key
- `MINIO_SECRET` - MinIO secret key
- `MINIO_BUCKET` - Bucket name (default: hpe-objects)
- `OPENSEARCH_HOST` - OpenSearch host
- `OPENSEARCH_PORT` - OpenSearch port

## Future Enhancements (From Architecture)

The full architecture in Flow.pdf includes features not yet implemented:

- Semantic search with embeddings
- Redis caching layer
- MongoDB for metadata storage
- OAuth/JWT authentication
- Real-time file processing pipelines
- More sophisticated ingestion workflows

When implementing these, reference the architecture diagram for component relationships.