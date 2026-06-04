"""
tests/test_opensearch.py
=========================
Integration + unit tests for the OpenSearch layer.

Run with:
    pytest tests/test_opensearch.py -v
    pytest tests/test_opensearch.py -v -m unit        # fast, no network
    pytest tests/test_opensearch.py -v -m integration  # needs running OS

Owner: Search Database Administrator (Priyadarshini Sarja)
"""

from __future__ import annotations

import os
import time
import uuid
import pytest

# ── Allow running without a live cluster (unit tests only) ────────────────────
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
TEST_INDEX      = f"hpe-search-test-{uuid.uuid4().hex[:8]}"

os.environ["OPENSEARCH_INDEX"] = TEST_INDEX


from backend.cache.redis_cache import (
    RedisCache,
    build_cache_key,
    cached_search,
)
from workers.ingestion.opensearch_client import (
    ChunkDocument,
    OpenSearchClient,
)
from backend.search.opensearch_query_builder import (
    build_hybrid_query,
    parse_search_response,
    OpenSearchSearchClient,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def os_client():
    import json
    import time
    from pathlib import Path

    client = OpenSearchClient(
        host=OPENSEARCH_HOST,
        port=OPENSEARCH_PORT,
        index=TEST_INDEX,
    )

    # Create the test index with the production mapping so that:
    #   - object_key is indexed as keyword (required for term queries)
    #   - embedding is indexed as knn_vector (required for hybrid search)
    # Without this, OpenSearch auto-creates the index with dynamic mapping,
    # turning object_key into a text field and making term queries return 0.
    mapping_path = Path(__file__).parent.parent / "infrastructure" / "opensearch" / "index-mapping.json"
    with open(mapping_path) as fh:
        mapping = json.load(fh)
    try:
        client._client.indices.create(index=TEST_INDEX, body=mapping)
        time.sleep(1)   # allow the index to become ready
    except Exception:
        pass  # index may already exist from a previous aborted run

    yield client

    # Teardown: remove test index
    try:
        client._client.indices.delete(index=TEST_INDEX, ignore=[404])
    except Exception:
        pass


@pytest.fixture(scope="session")
def sample_docs():
    base_vector = [0.1] * 384
    docs = [
        ChunkDocument(
            object_key="bucket1/reports/fire_safety.pdf",
            bucket="bucket1",
            filename="fire_safety.pdf",
            extension="pdf",
            mime_type="application/pdf",
            download_url="http://minio:9000/bucket1/reports/fire_safety.pdf",
            owner="alice",
            size_bytes=204800,
            uploaded_at="2024-11-01T10:00:00Z",
            chunk_index=0,
            chunk_total=3,
            chunk_text="Fire safety protocols require regular drills and extinguisher checks.",
            embedding=base_vector,
            tags=["safety", "compliance"],
        ),
        ChunkDocument(
            object_key="bucket1/reports/fire_safety.pdf",
            bucket="bucket1",
            filename="fire_safety.pdf",
            extension="pdf",
            mime_type="application/pdf",
            download_url="http://minio:9000/bucket1/reports/fire_safety.pdf",
            owner="alice",
            size_bytes=204800,
            uploaded_at="2024-11-01T10:00:00Z",
            chunk_index=1,
            chunk_total=3,
            chunk_text="Emergency exits must be clearly marked and unobstructed at all times.",
            embedding=[0.2] * 384,
            tags=["safety", "compliance"],
        ),
        ChunkDocument(
            object_key="bucket2/images/site_photo.jpg",
            bucket="bucket2",
            filename="site_photo.jpg",
            extension="jpg",
            mime_type="image/jpeg",
            download_url="http://minio:9000/bucket2/images/site_photo.jpg",
            owner="bob",
            size_bytes=512000,
            uploaded_at="2024-12-15T08:30:00Z",
            chunk_index=0,
            chunk_total=1,
            chunk_text="Site photograph taken during Q4 inspection.",
            embedding=[0.3] * 384,
            tags=["inspection", "photo"],
        ),
    ]
    return docs


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests (no network needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestChunkDocument:
    @pytest.mark.unit
    def test_deterministic_id_stable(self):
        doc = ChunkDocument(object_key="bucket/file.pdf", chunk_index=2)
        assert doc.deterministic_id() == "bucket__file.pdf#2"

    @pytest.mark.unit
    def test_deterministic_id_unique_per_chunk(self):
        doc0 = ChunkDocument(object_key="bucket/file.pdf", chunk_index=0)
        doc1 = ChunkDocument(object_key="bucket/file.pdf", chunk_index=1)
        assert doc0.deterministic_id() != doc1.deterministic_id()

    @pytest.mark.unit
    def test_to_dict_excludes_empty_embedding(self):
        doc = ChunkDocument(object_key="x", chunk_index=0, embedding=[])
        d = doc.to_dict()
        assert "embedding" not in d

    @pytest.mark.unit
    def test_to_dict_includes_embedding_when_present(self):
        vec = [0.1] * 384
        doc = ChunkDocument(object_key="x", chunk_index=0, embedding=vec)
        d = doc.to_dict()
        assert "embedding" in d
        assert len(d["embedding"]) == 384


class TestQueryBuilder:
    @pytest.mark.unit
    def test_build_hybrid_query_structure(self):
        body = build_hybrid_query("fire safety", [0.0] * 384)
        assert "query" in body
        assert "hybrid" in body["query"]
        queries = body["query"]["hybrid"]["queries"]
        assert len(queries) == 2           # BM25 + kNN

    @pytest.mark.unit
    def test_extension_filter_applied(self):
        body = build_hybrid_query("fire", [0.0] * 384, extension="pdf")
        body_str = str(body)
        assert "pdf" in body_str

    @pytest.mark.unit
    def test_embedding_excluded_from_source(self):
        body = build_hybrid_query("test", [0.0] * 384)
        assert "embedding" in body["_source"]["excludes"]

    @pytest.mark.unit
    def test_aggregations_present(self):
        body = build_hybrid_query("test", [0.0] * 384)
        assert "aggregations" in body
        assert "by_extension" in body["aggregations"]

    @pytest.mark.unit
    def test_parse_response_deduplicates(self):
        fake_response = {
            "hits": {
                "total": {"value": 2},
                "hits": [
                    {
                        "_id":     "bucket__file.pdf#0",
                        "_score":  0.9,
                        "_source": {
                            "filename": "file.pdf",
                            "bucket": "bucket",
                            "extension": "pdf",
                            "chunk_index": 0,
                            "chunk_total": 2,
                            "chunk_text": "First chunk content here.",
                        },
                        "highlight": {},
                    },
                    {
                        "_id":     "bucket__file.pdf#1",
                        "_score":  0.7,
                        "_source": {
                            "filename": "file.pdf",
                            "bucket": "bucket",
                            "extension": "pdf",
                            "chunk_index": 1,
                            "chunk_total": 2,
                            "chunk_text": "Second chunk content here.",
                        },
                        "highlight": {},
                    },
                ],
            },
            "aggregations": {},
        }
        parsed = parse_search_response(fake_response)
        # Both chunks are from the same file — only one result after dedup
        assert len(parsed["results"]) == 1
        assert parsed["results"][0]["score"] == 0.9   # kept highest score


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests (require running OpenSearch)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestOpenSearchClientIntegration:

    def test_health_check(self, os_client):
        assert os_client.health_check() is True

    def test_index_created_on_setup(self, os_client):
        # The test index is created by the fixture; verify it exists
        exists = os_client._client.indices.exists(index=TEST_INDEX)
        assert exists

    def test_upsert_single_doc(self, os_client, sample_docs):
        doc = sample_docs[0]
        result = os_client.upsert(doc)
        assert result is not None

    def test_bulk_upsert(self, os_client, sample_docs):
        report = os_client.bulk_upsert(sample_docs)
        assert report["failed"] == 0
        assert report["success"] == len(sample_docs)

    def test_upsert_idempotent(self, os_client, sample_docs):
        doc = sample_docs[0]
        os_client.upsert(doc)
        os_client.upsert(doc)   # second call must not raise or create duplicate
        time.sleep(1)
        count = os_client._client.count(
            index=TEST_INDEX,
            body={"query": {"term": {"object_key": doc.object_key}}}
        )
        # sample_docs contains 2 chunks for fire_safety.pdf (chunk_index 0 and 1);
        # chunk_total=3 is a metadata field, not the number inserted.
        # Assert against the actual number of chunks in sample_docs for this file.
        expected = len([d for d in sample_docs if d.object_key == doc.object_key])
        assert count["count"] == expected

    def test_get_index_stats(self, os_client, sample_docs):
        os_client.bulk_upsert(sample_docs)
        time.sleep(1)
        stats = os_client.get_index_stats()
        assert "doc_count" in stats
        assert stats["doc_count"] >= len(sample_docs)

# ─────────────────────────────────────────────────────────────────────────────
# Redis cache — unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCacheKey:
    @pytest.mark.unit
    def test_same_inputs_produce_same_key(self):
        k1 = build_cache_key("fire safety", extension="pdf")
        k2 = build_cache_key("fire safety", extension="pdf")
        assert k1 == k2

    @pytest.mark.unit
    def test_different_queries_produce_different_keys(self):
        k1 = build_cache_key("fire safety")
        k2 = build_cache_key("flood protocol")
        assert k1 != k2

    @pytest.mark.unit
    def test_filter_changes_key(self):
        k1 = build_cache_key("report")
        k2 = build_cache_key("report", extension="pdf")
        assert k1 != k2

    @pytest.mark.unit
    def test_tags_order_does_not_matter(self):
        k1 = build_cache_key("report", tags=["safety", "compliance"])
        k2 = build_cache_key("report", tags=["compliance", "safety"])
        assert k1 == k2

    @pytest.mark.unit
    def test_query_case_insensitive(self):
        k1 = build_cache_key("Fire Safety")
        k2 = build_cache_key("fire safety")
        assert k1 == k2


@pytest.mark.integration
class TestRedisCacheIntegration:

    @pytest.fixture(scope="class")
    def cache(self):
        return RedisCache()

    def test_health_check(self, cache):
        assert cache.health_check() is True

    def test_set_and_get(self, cache):
        key     = build_cache_key(f"test-{uuid.uuid4().hex}")
        payload = {"total": 5, "results": [{"filename": "test.pdf"}], "facets": {}}
        cache.set(key, payload)
        result = cache.get(key)
        assert result is not None
        assert result["total"] == 5

    def test_cache_miss_returns_none(self, cache):
        key = build_cache_key(f"nonexistent-{uuid.uuid4().hex}")
        assert cache.get(key) is None

    def test_invalidate(self, cache):
        key = build_cache_key(f"to-delete-{uuid.uuid4().hex}")
        cache.set(key, {"total": 1, "results": [], "facets": {}})
        assert cache.get(key) is not None
        cache.invalidate(key)
        assert cache.get(key) is None

    def test_hit_count_increments(self, cache):
        key     = build_cache_key(f"popular-{uuid.uuid4().hex}")
        payload = {"total": 3, "results": [], "facets": {}}
        cache.set(key, payload)
        for _ in range(3):
            r = cache.get(key)
        assert r["_cache"]["hit_count"] == 3

    def test_cached_search_returns_on_second_call(self, cache):
        call_count = {"n": 0}

        def fake_search(qt, qv, **kwargs):
            call_count["n"] += 1
            return {"total": 1, "results": [{"filename": "doc.pdf"}], "facets": {}}

        query = f"cache-wrap-test-{uuid.uuid4().hex}"
        vec   = [0.0] * 384

        r1 = cached_search(query, vec, fake_search, cache=cache)
        r2 = cached_search(query, vec, fake_search, cache=cache)

        assert call_count["n"] == 1          # search_fn only called once
        assert r2["_cache"]["hit"] is True   # second call is a cache hit

    def test_get_stats_returns_counts(self, cache):
        stats = cache.get_stats()
        assert "hits" in stats
        assert "misses" in stats
        assert "hit_rate_pct" in stats

    def test_delete_by_object_key(self, os_client, sample_docs):
        os_client.bulk_upsert(sample_docs)
        time.sleep(1)
        deleted = os_client.delete_by_object_key(sample_docs[2].object_key)
        assert deleted >= 1
        time.sleep(1)
        count = os_client._client.count(
            index=TEST_INDEX,
            body={"query": {"term": {"object_key": sample_docs[2].object_key}}}
        )
        assert count["count"] == 0
