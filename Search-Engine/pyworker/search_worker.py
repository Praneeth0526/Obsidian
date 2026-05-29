"""
gRPC Worker Server - Hybrid Search Worker
Handles query parsing, vectorization, semantic + keyword search, and ranking
"""
import os
import sys
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
        Process a search query and return ranked hybrid results.

        Args:
            request: SearchQueryRequest with query string and limit
            context: gRPC context

        Returns:
            SearchQueryResponse with ranked results
        """
        query = request.query or ""
        limit = request.limit if request.limit > 0 else DEFAULT_LIMIT

        print(f"[*] Processing search query: {query}")

        parse_result = self.query_chain.invoke(query)
        intent_text = parse_result["intent_text"]
        keywords = parse_result["keywords"]
        filters = parse_result["filters"]

        # Embedding is retained for future vector search in OpenSearch if enabled
        _ = self.embedding_service.encode(intent_text)

        keyword_results = self._keyword_search(query, keywords, filters, limit)

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
            for item in keyword_results
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

    def _semantic_search(self, vector: List[float], limit: int) -> List[Dict[str, object]]:
        """Placeholder for future vector search if OpenSearch kNN is enabled."""
        return []

    def _keyword_search(
        self,
        query: str,
        keywords: List[str],
        filters: List[str],
        limit: int,
    ) -> List[Dict[str, object]]:
        """Query OpenSearch for keyword results."""
        query_text = " ".join(keywords) if keywords else query.strip()
        if not query_text:
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

        body = {
            "size": limit,
            "query": {
                "bool": {
                    "must": [
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
                    ],
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
                clauses.append({"prefix": {"content_type": file_type}})
            elif filter_str.startswith("size:"):
                range_clause = self._size_filter_to_range(filter_str)
                if range_clause:
                    clauses.append(range_clause)
            elif filter_str.startswith("date:"):
                range_clause = self._date_filter_to_range(filter_str)
                if range_clause:
                    clauses.append(range_clause)
            elif filter_str.startswith("month:"):
                # Month filtering requires indexed month data; skip for now.
                continue
        return clauses


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
        return {"range": {"last_modified": {"gte": start.isoformat(), "lte": end.isoformat()}}}

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
