"""
backend/search/opensearch_query_builder.py
==========================================
Reference implementation of the hybrid BM25 + kNN query that the Go backend
replicates in opensearch.go.

Why this file exists
--------------------
Go's opensearch client speaks raw JSON.  This Python module is the single
source of truth for query structure — tested here, then translated to Go.
Prarthana's Go code should produce JSON identical to build_hybrid_query().

Owner: Search Database Administrator (Priyadarshini Sarja)
"""

from __future__ import annotations

import json
import logging
import os
from typing import List, Optional, Dict, Any

from dotenv import load_dotenv
from opensearchpy import OpenSearch, RequestsHttpConnection

load_dotenv()

log = logging.getLogger("obsidian.search")

OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
INDEX_NAME      = os.getenv("OPENSEARCH_INDEX", "obsidian-files")
KNN_K           = int(os.getenv("SEARCH_KNN_K", "50"))        # candidates from kNN
BM25_BOOST      = float(os.getenv("SEARCH_BM25_BOOST", "0.4"))
KNN_BOOST       = float(os.getenv("SEARCH_KNN_BOOST", "0.6"))
DEFAULT_SIZE    = int(os.getenv("SEARCH_DEFAULT_SIZE", "10"))


# ── Query builder ─────────────────────────────────────────────────────────────

def build_hybrid_query(
    query_text:   str,
    query_vector: List[float],
    *,
    extension:    Optional[str]       = None,
    bucket:       Optional[str]       = None,
    owner:        Optional[str]       = None,
    tags:         Optional[List[str]] = None,
    date_from:    Optional[str]       = None,   # ISO-8601
    date_to:      Optional[str]       = None,
    size:         int                 = DEFAULT_SIZE,
    from_:        int                 = 0,
) -> Dict[str, Any]:
    """
    Build the hybrid search request body.

    The normalization-processor attached as default pipeline on the index
    automatically combines BM25 and kNN scores using weights [BM25_BOOST, KNN_BOOST].

    Parameters
    ----------
    query_text    : Raw query string (for BM25 match)
    query_vector  : 384-dim embedding produced by the search worker
    extension     : Optional file type filter  (e.g. "pdf")
    bucket        : Optional MinIO bucket filter
    owner         : Optional owner filter
    tags          : Optional list of tags (must match at least one)
    date_from     : Filter docs uploaded after this date
    date_to       : Filter docs uploaded before this date
    size          : Number of results to return
    from_         : Offset for pagination
    """

    # ── Filters (applied as post-filter so they don't affect scoring) ─────────
    filters: List[dict] = []
    if extension:
        filters.append({"term": {"extension": extension.lower().lstrip(".")}})
    if bucket:
        filters.append({"term": {"bucket": bucket}})
    if owner:
        filters.append({"term": {"owner": owner}})
    if tags:
        filters.append({"terms": {"tags": tags}})
    if date_from or date_to:
        date_range: dict = {}
        if date_from:
            date_range["gte"] = date_from
        if date_to:
            date_range["lte"] = date_to
        filters.append({"range": {"uploaded_at": date_range}})

    filter_clause = filters if filters else [{"match_all": {}}]

    # ── BM25 sub-query ────────────────────────────────────────────────────────
    bm25_query: dict = {
        "bool": {
            "must": [
                {
                    "multi_match": {
                        "query":  query_text,
                        "fields": ["chunk_text^3", "filename^2", "tags"],
                        "type":   "best_fields",
                        "fuzziness": "AUTO",
                    }
                }
            ],
            "filter": filter_clause,
            "boost": BM25_BOOST,
        }
    }

    # ── kNN sub-query ─────────────────────────────────────────────────────────
    knn_query: dict = {
        "knn": {
            "embedding": {
                "vector": query_vector,
                "k":      KNN_K,
                "filter": {
                    "bool": {"must": filter_clause}
                },
                "boost": KNN_BOOST,
            }
        }
    }

    # ── Highlight config (for search-result snippets) ─────────────────────────
    highlight: dict = {
        "fields": {
            "chunk_text": {
                "fragment_size":       200,
                "number_of_fragments": 3,
                "no_match_size":       150,
            },
            "filename": {}
        },
        "pre_tags":  ["<mark>"],
        "post_tags": ["</mark>"],
    }

    # ── Aggregations (faceted counts for UI filters) ──────────────────────────
    aggs: dict = {
        "by_extension": {
            "terms": {"field": "extension", "size": 20}
        },
        "by_bucket": {
            "terms": {"field": "bucket", "size": 10}
        },
        "by_owner": {
            "terms": {"field": "owner", "size": 20}
        },
        "total_size": {
            "sum": {"field": "size_bytes"}
        },
    }

    # ── Assemble ──────────────────────────────────────────────────────────────
    body: Dict[str, Any] = {
        "size": size,
        "from": from_,
        "query": {
            "hybrid": {
                "queries": [bm25_query, knn_query]
            }
        },
        "highlight":    highlight,
        "aggregations": aggs,
        "_source": {
            "excludes": ["embedding"]   # never return the raw vector
        },
    }

    return body


def parse_search_response(raw: dict) -> dict:
    """
    Transform the raw OpenSearch response into a clean structure
    that the Go backend returns to the frontend.
    """
    hits_raw = raw.get("hits", {})
    total    = hits_raw.get("total", {}).get("value", 0)
    hits     = hits_raw.get("hits", [])

    results = []
    for h in hits:
        src  = h.get("_source", {})
        hl   = h.get("highlight", {})
        results.append({
            "doc_id":       h.get("_id"),
            "score":        h.get("_score"),
            "filename":     src.get("filename"),
            "extension":    src.get("extension"),
            "mime_type":    src.get("mime_type"),
            "bucket":       src.get("bucket"),
            "owner":        src.get("owner"),
            "size_bytes":   src.get("size_bytes"),
            "uploaded_at":  src.get("uploaded_at"),
            "download_url": src.get("download_url"),
            "chunk_index":  src.get("chunk_index"),
            "chunk_total":  src.get("chunk_total"),
            "snippet":      hl.get("chunk_text", [src.get("chunk_text", "")[:200]])[0],
            "tags":         src.get("tags", []),
        })

    # Deduplicate by object_key — keep highest-scoring chunk per file
    seen: dict = {}
    deduped = []
    for r in results:
        key = f"{r['bucket']}/{r['filename']}"
        if key not in seen or r["score"] > seen[key]["score"]:
            seen[key] = r
    deduped = list(seen.values())
    deduped.sort(key=lambda x: x["score"], reverse=True)

    # Aggregations
    aggs_raw = raw.get("aggregations", {})
    facets = {
        "extensions": [
            {"key": b["key"], "count": b["doc_count"]}
            for b in aggs_raw.get("by_extension", {}).get("buckets", [])
        ],
        "buckets": [
            {"key": b["key"], "count": b["doc_count"]}
            for b in aggs_raw.get("by_bucket", {}).get("buckets", [])
        ],
        "owners": [
            {"key": b["key"], "count": b["doc_count"]}
            for b in aggs_raw.get("by_owner", {}).get("buckets", [])
        ],
        "total_size_bytes": int(aggs_raw.get("total_size", {}).get("value", 0)),
    }

    return {
        "total":   total,
        "results": deduped,
        "facets":  facets,
    }


# ── Thin search client (used in integration tests / CLI) ──────────────────────

class OpenSearchSearchClient:
    """
    Minimal search-side client.
    The Go backend uses its own HTTP client; this is for Python-side testing.
    """

    def __init__(self) -> None:
        self._os = OpenSearch(
            hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
            http_compress=True,
            use_ssl=False,
            verify_certs=False,
            connection_class=RequestsHttpConnection,
            timeout=10,
        )

    def search(
        self,
        query_text:   str,
        query_vector: List[float],
        **filters,
    ) -> dict:
        body = build_hybrid_query(query_text, query_vector, **filters)
        raw  = self._os.search(index=INDEX_NAME, body=body)
        return parse_search_response(raw)

    def suggest(self, prefix: str, size: int = 5) -> List[str]:
        """Autocomplete on filename.suggest field."""
        body = {
            "suggest": {
                "filename-suggest": {
                    "prefix": prefix,
                    "completion": {"field": "filename.suggest", "size": size},
                }
            }
        }
        raw  = self._os.search(index=INDEX_NAME, body=body)
        opts = raw.get("suggest", {}).get("filename-suggest", [{}])[0].get("options", [])
        return [o["_source"]["filename"] for o in opts]


# ── CLI for quick manual testing ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "fire safety protocol"
    # Use a zero vector for CLI testing (no embedding server available)
    dummy_vector = [0.0] * 384

    body = build_hybrid_query(query, dummy_vector, size=5)
    print("=== Query body ===")
    print(json.dumps(body, indent=2))
