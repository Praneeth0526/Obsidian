"""
workers/ingestion/opensearch_client.py
=======================================
Industry-grade OpenSearch client for the HPE Search ingestion pipeline.

Responsibilities
----------------
  - Upsert document chunks (text + 384-dim embedding + metadata) into OpenSearch
  - Bulk-upsert for throughput efficiency
  - Idempotent: safe to call multiple times for the same object_key/chunk_index
  - Structured logging (JSON) for production observability
  - Retry with exponential back-off on transient failures
  - Health-check helper used by the ingestion worker at startup

Owner: Search Database Administrator (Priyadarshini Sarja)
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv
from opensearchpy import (
    OpenSearch,
    RequestsHttpConnection,
    helpers as os_helpers,
    ConnectionError as OSConnectionError,
    TransportError,
)
from opensearchpy.exceptions import NotFoundError

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts":      datetime.now(timezone.utc).isoformat(),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)

def _get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    return logger

log = _get_logger("hpe_search.opensearch_client")


# ── Configuration ─────────────────────────────────────────────────────────────

OPENSEARCH_HOST  = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT  = int(os.getenv("OPENSEARCH_PORT", "9200"))
INDEX_NAME       = os.getenv("OPENSEARCH_INDEX", "hpe-search-docs")
BULK_CHUNK_SIZE  = int(os.getenv("OPENSEARCH_BULK_CHUNK_SIZE", "200"))
MAX_RETRIES      = int(os.getenv("OPENSEARCH_MAX_RETRIES", "5"))
RETRY_BASE_DELAY = float(os.getenv("OPENSEARCH_RETRY_BASE_DELAY", "1.0"))  # seconds


# ── Document schema ───────────────────────────────────────────────────────────

@dataclass
class ChunkDocument:
    """
    One chunk of an ingested file.
    Mirrors the index mapping defined in infrastructure/opensearch/index-mapping.json
    """
    # Identity
    doc_id:       str   = field(default_factory=lambda: str(uuid.uuid4()))
    object_key:   str   = ""          # e.g. "my-bucket/reports/q1.pdf"
    bucket:       str   = ""
    filename:     str   = ""
    extension:    str   = ""
    mime_type:    str   = ""
    download_url: str   = ""

    # Ownership / access
    owner: str = ""

    # File-level metadata
    size_bytes:  int = 0
    uploaded_at: str = ""            # ISO-8601

    # Chunk-level
    chunk_index: int = 0
    chunk_total: int = 1
    chunk_text:  str = ""

    # AI fields
    embedding: List[float] = field(default_factory=list)
    language:  str = "en"
    tags:      List[str] = field(default_factory=list)

    # Operational
    indexed_at:        str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ingestion_status:  str = "ok"    # ok | error
    error_message:     str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # Remove empty embedding to avoid mapping errors during partial updates
        if not d.get("embedding"):
            del d["embedding"]
        return d

    def deterministic_id(self) -> str:
        """
        Stable document ID: <object_key>#<chunk_index>
        Guarantees idempotent upserts — re-indexing the same chunk overwrites cleanly.
        """
        safe_key = self.object_key.replace("/", "__")
        return f"{safe_key}#{self.chunk_index}"


# ── Client ────────────────────────────────────────────────────────────────────

class OpenSearchClient:
    """
    Thread-safe wrapper around the opensearch-py client.
    Instantiate once per worker process and reuse.
    """

    def __init__(
        self,
        host:  str = OPENSEARCH_HOST,
        port:  int = OPENSEARCH_PORT,
        index: str = INDEX_NAME,
    ) -> None:
        self.index = index
        self._client = OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_compress=True,
            use_ssl=False,
            verify_certs=False,
            connection_class=RequestsHttpConnection,
            timeout=30,
            max_retries=3,
            retry_on_timeout=True,
        )
        log.info("OpenSearch client initialised", extra={"host": host, "port": port, "index": index})

    # ── Public API ────────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Returns True when the cluster is green or yellow."""
        try:
            # Do not pass a timeout kwarg here — opensearch-py 2.8 + urllib3 2.x
            # cannot handle string timeouts (e.g. "10s") at the transport layer.
            # The 30s connection timeout set on the client is sufficient.
            status = self._client.cluster.health(wait_for_status="yellow")
            log.info("Cluster health", extra={"status": status.get("status")})
            return status.get("status") in ("green", "yellow")
        except Exception as exc:
            log.error("Health check failed: %s", exc)
            return False

    def upsert(self, doc: ChunkDocument) -> bool:
        """
        Upsert a single ChunkDocument.
        Uses script-based upsert for idempotency.
        Returns True on success.
        """
        doc_id = doc.deterministic_id()
        body = {
            "doc":            doc.to_dict(),
            "doc_as_upsert":  True,
        }
        return self._with_retry(
            lambda: self._client.update(index=self.index, id=doc_id, body=body, refresh="wait_for")
        )

    def bulk_upsert(self, docs: List[ChunkDocument]) -> dict:
        """
        Bulk-upsert a list of ChunkDocuments.
        Returns {"success": N, "failed": M, "errors": [...]}
        """
        if not docs:
            return {"success": 0, "failed": 0, "errors": []}

        actions = [
            {
                "_op_type": "update",
                "_index":   self.index,
                "_id":      doc.deterministic_id(),
                "doc":      doc.to_dict(),
                "doc_as_upsert": True,
            }
            for doc in docs
        ]

        success = failed = 0
        errors: list = []

        def _run_bulk():
            nonlocal success, failed, errors
            ok, errs = os_helpers.bulk(
                self._client,
                actions,
                chunk_size=BULK_CHUNK_SIZE,
                raise_on_error=False,
                raise_on_exception=False,
            )
            success = ok
            failed  = len(errs)
            errors  = errs

        self._with_retry(_run_bulk)

        if failed:
            log.warning("Bulk upsert partial failure", extra={"failed": failed, "sample": errors[:3]})
        else:
            log.info("Bulk upsert complete", extra={"success": success})

        return {"success": success, "failed": failed, "errors": errors}

    def delete_by_object_key(self, object_key: str) -> int:
        """
        Delete all chunks belonging to an object_key.
        Called when a file is deleted from MinIO.
        Returns number of deleted docs.
        """
        body = {"query": {"term": {"object_key": object_key}}}
        resp = self._with_retry(
            lambda: self._client.delete_by_query(
                index=self.index, body=body, refresh=True, conflicts="proceed"
            )
        )
        deleted = resp.get("deleted", 0) if isinstance(resp, dict) else 0
        log.info("Deleted chunks for object_key", extra={"object_key": object_key, "deleted": deleted})
        return deleted

    def get_index_stats(self) -> dict:
        """Returns doc count and store size for monitoring."""
        try:
            stats = self._client.indices.stats(index=self.index, metric="docs,store")
            idx   = stats["indices"][self.index]["primaries"]
            return {
                "doc_count":  idx["docs"]["count"],
                "size_bytes": idx["store"]["size_in_bytes"],
            }
        except NotFoundError:
            return {"doc_count": 0, "size_bytes": 0}
        except Exception as exc:
            log.error("Failed to fetch index stats: %s", exc)
            return {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _with_retry(self, fn, retries: int = MAX_RETRIES):
        """
        Execute fn() with exponential back-off on transient OpenSearch errors.
        """
        delay = RETRY_BASE_DELAY
        for attempt in range(1, retries + 1):
            try:
                return fn()
            except (OSConnectionError, TransportError) as exc:
                if attempt == retries:
                    log.error("All retries exhausted: %s", exc)
                    raise
                log.warning(
                    "Transient error (attempt %d/%d): %s — retrying in %.1fs",
                    attempt, retries, exc, delay,
                )
                time.sleep(delay)
                delay = min(delay * 2, 30)   # cap at 30 s


# ── Module-level singleton factory ────────────────────────────────────────────

_client: Optional[OpenSearchClient] = None

def get_client() -> OpenSearchClient:
    """Return the module-level singleton (creates it on first call)."""
    global _client
    if _client is None:
        _client = OpenSearchClient()
    return _client
