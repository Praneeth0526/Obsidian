"""
gRPC Worker Server - Hybrid Search Worker
Handles query parsing, vectorization, semantic + keyword search, and ranking
"""
import os
import sys
import time
from concurrent import futures
from typing import Dict, List, Tuple

import grpc
from opensearchpy import OpenSearch
from langchain_core.runnables import RunnableLambda

# Add parent and proto directories to path for imports
base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, base_dir)
sys.path.insert(0, os.path.join(base_dir, "proto"))

from config import (
    GRPC_HOST,
    GRPC_SEARCH_PORT,
    OPENSEARCH_HOST,
    OPENSEARCH_PORT,
    OPENSEARCH_INDEX,
    DEFAULT_LIMIT,
)
from nlp_parser import NLPQueryParser
from embedding_service import EmbeddingService

# Import generated gRPC code
import proto.search_pb2 as search_pb2
import proto.search_pb2_grpc as search_pb2_grpc


class SearchWorkerServicer(search_pb2_grpc.SearchWorkerServicer):
    """gRPC servicer for hybrid search."""

    def __init__(self):
        self.parser = NLPQueryParser()
        self.embedding_service = EmbeddingService()
        self.opensearch = OpenSearch(
            hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
            use_ssl=False,
            verify_certs=False,
        )
        self.query_chain = RunnableLambda(self._parse_query)
        print("[+] Search Worker Servicer initialized")

    def ProcessQuery(self, request, context):
        """
        Process a search query and return ranked hybrid kNN + BM25 results.
        """
        query = request.query or ""
        limit = request.limit if request.limit > 0 else DEFAULT_LIMIT

        print(f"[*] Processing search query: {query}")
        t0 = time.time()

        parse_result = self.query_chain.invoke(query)
        t_parse = time.time() - t0

        intent_text = parse_result["intent_text"]
        keywords = parse_result["keywords"]
        filters = parse_result["filters"]

        # Encode the intent text into a vector embedding for semantic search
        t1 = time.time()
        query_vector = self.embedding_service.encode(intent_text)
        t_embed = time.time() - t1

        t2 = time.time()
        results = self._hybrid_search(query, keywords, filters, query_vector, intent_text, limit)
        t_search = time.time() - t2
        
        print(f"[METRICS] Parse: {t_parse:.3f}s | Embed: {t_embed:.3f}s | Search: {t_search:.3f}s | Total: {(time.time() - t0):.3f}s")

        response_results = [
            search_pb2.SearchResult(
                id=item.get("id", ""),
                object_name=item.get("object_name", ""),
                bucket=item.get("bucket", ""),
                size_bytes=item.get("size_bytes", 0),
                content_type=item.get("content_type", ""),
                extension=item.get("extension", ""),
                last_modified=item.get("last_modified", ""),
                semantic_score=item.get("semantic_score", 0.0),
                keyword_score=item.get("keyword_score", 0.0),
                combined_score=item.get("combined_score", 0.0),
                highlights=item.get("highlights", {}),
            )
            for item in results
        ]

        return search_pb2.SearchQueryResponse(
            results=response_results,
            total_hits=len(response_results),
            intent_text=intent_text,
            extracted_keywords=keywords,
            applied_filters=filters,
        )

    def _parse_query(self, query: str) -> Dict[str, List[str]]:
        """Parse query into intent, keywords, and filters using LangChain + rules."""
        intent_text, keywords, filters = self.parser.parse(query)
        return {
            "intent_text": intent_text,
            "keywords": keywords,
            "filters": filters,
        }

    def _hybrid_search(
        self,
        raw_query: str,
        keywords: List[str],
        filters: List[str],
        query_vector: List[float],
        intent_text: str,
        limit: int,
    ) -> List[Dict[str, object]]:
        """Run kNN semantic search and BM25 keyword search, merge and re-rank."""
        knn_boost = float(os.environ.get("SEARCH_KNN_BOOST", "0.6"))
        bm25_boost = float(os.environ.get("SEARCH_BM25_BOOST", "0.4"))
        knn_k = int(os.environ.get("SEARCH_KNN_K", "50"))

        filter_clauses = self._build_filter_clauses(filters)
        query_text = " ".join(keywords)

        # ── kNN semantic search ──────────────────────────────────────────────
        knn_hits: Dict[str, Dict] = {}
        use_knn = bool(query_vector) and bool(intent_text.strip())
        if use_knn:
            knn_body = {
                "size": knn_k,
                "query": {
                    "knn": {
                        "embedding": {
                            "vector": query_vector,
                            "k": knn_k,
                        }
                    }
                },
                "_source": True,
            }
            if filter_clauses:
                knn_body["post_filter"] = {"bool": {"filter": filter_clauses}}
            try:
                resp = self.opensearch.search(index=OPENSEARCH_INDEX, body=knn_body)
                for hit in resp.get("hits", {}).get("hits", []):
                    doc_id = hit["_id"]
                    knn_hits[doc_id] = {
                        "hit": hit,
                        "knn_score": float(hit.get("_score") or 0.0),
                    }
            except Exception as exc:
                print(f"[!] kNN search failed: {exc}")

        # ── BM25 keyword search (collapsed per file) ─────────────────────────
        # We collapse on filename.keyword so each unique file appears once,
        # regardless of how many chunks it has — a single large PDF won't
        # flood the results and push smaller files off the list.
        bm25_hits: Dict[str, Dict] = {}
        if query_text:
            must_clause = [{
                "multi_match": {
                    "query": query_text,
                    "fields": ["filename^3", "object_key^2", "chunk_text",
                               "extension", "bucket", "mime_type"],
                }
            }]
        else:
            must_clause = [{"match_all": {}}]
        bm25_body = {
            "size": max(limit * 2, 50),
            "query": {"bool": {"must": must_clause, "filter": filter_clauses}},
            "collapse": {"field": "filename.keyword"},  # one hit per unique file
        }
        try:
            resp = self.opensearch.search(index=OPENSEARCH_INDEX, body=bm25_body)
            for hit in resp.get("hits", {}).get("hits", []):
                doc_id = hit["_id"]
                bm25_hits[doc_id] = {
                    "hit": hit,
                    "bm25_score": float(hit.get("_score") or 0.0),
                }
        except Exception as exc:
            print(f"[!] BM25 search failed: {exc}")

        # ── Normalise scores ─────────────────────────────────────────────────
        def _normalize(scores: List[float]) -> List[float]:
            if not scores: return scores
            max_s = max(scores) or 1.0
            return [s / max_s for s in scores]

        knn_ids   = list(knn_hits.keys())
        bm25_ids  = list(bm25_hits.keys())
        knn_norms  = _normalize([knn_hits[i]["knn_score"]  for i in knn_ids])
        bm25_norms = _normalize([bm25_hits[i]["bm25_score"] for i in bm25_ids])
        knn_norm_map  = dict(zip(knn_ids,  knn_norms))
        bm25_norm_map = dict(zip(bm25_ids, bm25_norms))

        # ── Merge and rank ───────────────────────────────────────────────────
        all_ids = set(knn_ids) | set(bm25_ids)
        merged: List[Dict] = []
        for doc_id in all_ids:
            knn_entry  = knn_hits.get(doc_id,  {})
            bm25_entry = bm25_hits.get(doc_id, {})
            hit = knn_entry.get("hit") or bm25_entry.get("hit")
            knn_s  = knn_norm_map.get(doc_id,  0.0)
            bm25_s = bm25_norm_map.get(doc_id, 0.0)
            combined = knn_boost * knn_s + bm25_boost * bm25_s
            source = hit.get("_source", {})
            merged.append({
                "id": doc_id,
                "object_key": source.get("object_key", doc_id),
                "semantic_score": round(knn_s,  4),
                "keyword_score":  round(bm25_s, 4),
                "combined_score": round(combined, 4),
                "object_name": source.get("filename", source.get("object_key", "")),
                "bucket":       source.get("bucket", ""),
                "size_bytes":   int(source.get("size_bytes", 0) or 0),
                "content_type": source.get("mime_type", ""),
                "extension":    source.get("extension", ""),
                "last_modified": source.get("uploaded_at", ""),
                "highlights":   {},
            })

        # Sort by combined score DESC, then by filename ASC as a stable tiebreaker
        # so equal-scored files (e.g. pure filter queries returning 0.4 each) all surface.
        merged.sort(key=lambda x: (-x["combined_score"], x.get("object_name", "")))

        # When query has intent text, filter out irrelevant matches below threshold.
        # When filter-only (no intent), show all matching docs.
        if intent_text.strip():
            merged = [item for item in merged if item["combined_score"] >= 0.55]

        seen_files: set = set()
        deduplicated: List[Dict] = []
        for item in merged:
            # Use filename as the dedup key so each unique file appears once
            file_key = item.get("object_name") or item.get("object_key", item["id"])
            if file_key not in seen_files:
                seen_files.add(file_key)
                deduplicated.append(item)

        return deduplicated[:limit]



    def _keyword_search(
        self,
        query: str,
        keywords: List[str],
        filters: List[str],
        limit: int,
    ) -> List[Dict[str, object]]:
        """Query OpenSearch for keyword results."""
        query_text = " ".join(keywords) if keywords else query.strip()
        
        # If no query text AND no filters, return empty
        if not query_text and not filters:
            return []

        return self._keyword_search_opensearch(query_text, filters, limit)

    def _keyword_search_opensearch(
        self,
        query_text: str,
        filters: List[str],
        limit: int,
    ) -> List[Dict[str, object]]:
        """Query OpenSearch for keyword results."""
        filter_clauses = self._build_filter_clauses(filters)

        if query_text:
            must_clause = [
                {
                    "multi_match": {
                        "query": query_text,
                        "fields": [
                            "filename^3",
                            "object_key^2",
                            "chunk_text",
                            "extension",
                            "bucket",
                            "mime_type",
                        ],
                    }
                }
            ]
        else:
            must_clause = [{"match_all": {}}]

        body = {
            "size": limit,
            "query": {
                "bool": {
                    "must": must_clause,
                    "filter": filter_clauses,
                }
            },
        }

        try:
            print(f"[*] Executing OpenSearch query: {body}")
            response = self.opensearch.search(index=OPENSEARCH_INDEX, body=body)
            print(f"[*] OpenSearch response hits: {response.get('hits', {}).get('total', {})}")
        except Exception as exc:
            print(f"[!] OpenSearch search failed: {exc}")
            return []

        hits = response.get("hits", {}).get("hits", [])
        results = []
        for hit in hits:
            source = hit.get("_source", {})
            results.append(
                {
                    "id": hit.get("_id", ""),
                    "semantic_score": 0.0,
                    "keyword_score": float(hit.get("_score", 0.0) or 0.0),
                    "combined_score": float(hit.get("_score", 0.0) or 0.0),
                    "object_name": source.get("filename", source.get("object_key", "")),
                    "bucket": source.get("bucket", ""),
                    "size_bytes": int(source.get("size_bytes", 0) or 0),
                    "content_type": source.get("mime_type", ""),
                    "extension": source.get("extension", ""),
                    "last_modified": source.get("uploaded_at", ""),
                    "highlights": {},
                }
            )
        return results

    def _build_filter_clauses(self, filters: List[str]) -> List[Dict[str, object]]:
        """Translate parsed filter strings to OpenSearch filter clauses."""
        clauses: List[Dict[str, object]] = []
        for filter_str in filters:
            if filter_str.startswith("extension:"):
                extension = filter_str.split(":", 1)[1]
                clauses.append({"term": {"extension": extension}})
            elif filter_str.startswith("type:"):
                file_type = filter_str.split(":", 1)[1]
                if file_type == "pdf":
                    clauses.append({"term": {"extension": "pdf"}})
                elif file_type == "document":
                    clauses.append({"prefix": {"mime_type": "application/"}})
                else:
                    clauses.append({"prefix": {"mime_type": file_type}})
            elif filter_str.startswith("size:"):
                range_clause = self._size_filter_to_range(filter_str)
                if range_clause:
                    clauses.append(range_clause)
            elif filter_str.startswith("date:"):
                range_clause = self._date_filter_to_range(filter_str)
                if range_clause:
                    clauses.append(range_clause)
            elif filter_str.startswith("exact_date:"):
                range_clause = self._exact_date_to_range(filter_str)
                if range_clause:
                    clauses.append(range_clause)
            elif filter_str.startswith("month:"):
                # Month filtering requires indexed month data; skip for now.
                continue
        return clauses

    def _exact_date_to_range(self, filter_str: str) -> Dict[str, object]:
        parts = filter_str.split(":", 1)[1].split("_")
        if len(parts) != 3: return {}
        month_str, day_str, year_str = parts

        month_map = {
            "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
            "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
            "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
            "nov": 11, "november": 11, "dec": 12, "december": 12,
        }
        month = month_map.get(month_str.lower(), 1)
        try:
            day  = int(day_str)
            year = int(year_str)
        except ValueError:
            return {"match_none": {}}

        # Use timezone-aware UTC datetimes so the range matches the +00:00
        # timestamps stored by the ingestion worker.
        from datetime import datetime, timedelta, timezone
        try:
            start = datetime(year, month, day, tzinfo=timezone.utc)
            end   = start + timedelta(days=1)
            # OpenSearch expects RFC-3339 with offset, e.g. "2026-06-04T00:00:00+00:00"
            return {
                "range": {
                    "uploaded_at": {
                        "gte": start.isoformat(),
                        "lt":  end.isoformat(),
                    }
                }
            }
        except ValueError:
            return {"match_none": {}}

    def _size_filter_to_range(self, filter_str: str) -> Dict[str, object]:
        """Convert size filter to OpenSearch range clause."""
        parts = filter_str.split(":")
        if len(parts) < 3:
            return {}
        _, op, raw_value = parts[0], parts[1], parts[2]
        size_bytes = self._parse_size_bytes(raw_value)
        if size_bytes is None:
            return {}

        if op == "size_gt":
            return {"range": {"size_bytes": {"gt": size_bytes}}}
        if op == "size_lt":
            return {"range": {"size_bytes": {"lt": size_bytes}}}
        return {}

    def _date_filter_to_range(self, filter_str: str) -> Dict[str, object]:
        """Convert date filter to OpenSearch range clause."""
        date_key = filter_str.split(":", 1)[1]
        start, end = self.parser.get_date_range(date_key)
        return {"range": {"uploaded_at": {"gte": start.isoformat(), "lte": end.isoformat()}}}

    def _parse_size_bytes(self, raw_value: str) -> int:
        """Parse size string like 10MB into bytes."""
        raw = raw_value.strip().upper()
        units = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
        for unit, factor in units.items():
            if raw.endswith(unit):
                try:
                    value = float(raw[: -len(unit)].strip())
                except ValueError:
                    return None
                return int(value * factor)
        try:
            return int(raw)
        except ValueError:
            return None



def serve():
    """Start the gRPC server."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    search_pb2_grpc.add_SearchWorkerServicer_to_server(
        SearchWorkerServicer(),
        server,
    )

    address = f"{GRPC_HOST}:{GRPC_SEARCH_PORT}"
    server.add_insecure_port(address)

    server.start()
    print(f"[*] Search Worker started on {address}")
    print("[*] Ready to process hybrid search queries...")

    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print("\n[!] Shutting down...")
        server.stop(0)


if __name__ == "__main__":
    serve()
