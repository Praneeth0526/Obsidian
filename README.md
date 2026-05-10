# HPE Search (Role 5 Stack)

This workspace is trimmed to Role 5 (gRPC search worker) plus OpenSearch and a simple HPE-branded frontend. By default the frontend proxies search requests directly to OpenSearch via a built-in `/api/search` route. You can point it to an external Go gateway if needed.

## What Runs

- OpenSearch (search backend)
- OpenSearch Dashboards (optional UI)
- PyWorker (Role 5 gRPC search worker)
- Frontend (Next.js, HPE theme)

## Prerequisites

- Docker Engine with Docker Compose v2
- Optional: a Go API Gateway reachable from the frontend (not included here)

## Quick Start

```bash
cd Search-Engine
cp .env.example .env
```

Edit `.env` if you want to use an external Go gateway instead of the built-in proxy:

```
NEXT_PUBLIC_API_BASE=http://YOUR_GO_GATEWAY_HOST:PORT
```

Start the stack (separate containers with Role 5 names):

```bash
docker compose -p role5 -f docker-compose.yml up --build --force-recreate
```

## Service Ports

- Frontend: http://localhost:3000
- OpenSearch: http://localhost:9200
- OpenSearch Dashboards: http://localhost:5601
- gRPC Worker: 0.0.0.0:50052

## Notes

- The frontend uses `/api/search` by default.
- If `NEXT_PUBLIC_API_BASE` is set, it calls `GET /search?q=...` on that gateway instead.
- The gRPC worker talks to OpenSearch directly and returns ranked results.
- If you only want the gRPC worker and OpenSearch, you can comment out the `frontend` service in `docker-compose.yml`.

## Natural Language Search (Basic)

The frontend API route supports lightweight natural language parsing for demos. Examples:

- "retrieve me pdfs uploaded on may 7th"
- "show images from may 7"

Current parsing support:

- File types: pdf, images, documents (via content type)
- Date: "Month Day" (uses current year, filters `last_modified`)

Implementation lives in [Search-Engine/frontend/app/api/search/route.js](Search-Engine/frontend/app/api/search/route.js).

## Index Requirement

The search route expects an OpenSearch index named `object-storage-index`. If it does not exist, the API returns 502.

Create the index if needed:

```bash
curl -X PUT http://localhost:9200/object-storage-index -H 'Content-Type: application/json' -d '{"mappings":{"properties":{"object_name":{"type":"text","fields":{"keyword":{"type":"keyword"}}},"bucket":{"type":"keyword"},"size_bytes":{"type":"long"},"content_type":{"type":"keyword"},"last_modified":{"type":"date"},"etag":{"type":"keyword"},"extension":{"type":"keyword"},"indexed_at":{"type":"date"}}}}'
```

## Stop

```bash
docker compose -f docker-compose.yml down
```
