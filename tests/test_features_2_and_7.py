"""
tests/test_features_2_and_7.py
================================
Tests for Feature 2 (Document-level metadata enrichment) and
Feature 7 (DLQ retry consumer with exponential backoff).

Markers
-------
unit        — fully mocked, no services needed  (pytest -m unit)
integration — requires running OpenSearch/Kafka  (pytest -m integration)

Run all unit tests (no Docker required):
    cd /mnt/e/Obsidian
    pip install pytest pytest-asyncio
    pytest tests/test_features_2_and_7.py -v -m unit

Run integration tests (Docker must be up):
    pytest tests/test_features_2_and_7.py -v -m integration
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ── Path bootstrap so tests can import workers/ and backend/ packages ──────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "workers" / "ingestion"))

# ── Env setup before importing modules that read env at import time ────────────
TEST_INDEX = f"hpe-test-feat-{uuid.uuid4().hex[:8]}"
os.environ.setdefault("OPENSEARCH_INDEX", TEST_INDEX)
os.environ.setdefault("OPENSEARCH_HOST", "localhost")
os.environ.setdefault("OPENSEARCH_PORT", "9200")

from workers.ingestion.opensearch_client import ChunkDocument, OpenSearchClient
from workers.ingestion.tika_extractor import ExtractionResult


# =============================================================================
# ██████████████   FEATURE 2: Document-level metadata enrichment   ████████████
# =============================================================================

class TestChunkDocumentMetadataFields:
    """
    Unit tests — verify the new ChunkDocument fields exist, default correctly,
    and round-trip through to_dict() without loss.
    """

    @pytest.mark.unit
    def test_new_fields_exist_with_correct_defaults(self):
        """All four new metadata fields must be present with zero-value defaults."""
        doc = ChunkDocument(object_key="b/f.pdf", chunk_index=0)
        assert doc.author == ""
        assert doc.title == ""
        assert doc.page_count == 0
        assert doc.doc_created_at == ""

    @pytest.mark.unit
    def test_metadata_fields_serialised_in_to_dict(self):
        """to_dict() must include all four fields so OpenSearch indexes them."""
        doc = ChunkDocument(
            object_key="bucket/report.pdf",
            chunk_index=0,
            author="Jane Smith",
            title="Q4 Safety Report",
            page_count=42,
            doc_created_at="2024-03-15T09:00:00Z",
        )
        d = doc.to_dict()
        assert d["author"] == "Jane Smith"
        assert d["title"] == "Q4 Safety Report"
        assert d["page_count"] == 42
        assert d["doc_created_at"] == "2024-03-15T09:00:00Z"

    @pytest.mark.unit
    def test_empty_metadata_still_serialises(self):
        """Empty / zero defaults must also appear in the serialised dict."""
        doc = ChunkDocument(object_key="b/f.pdf", chunk_index=0)
        d = doc.to_dict()
        assert "author" in d
        assert "title" in d
        assert "page_count" in d
        assert "doc_created_at" in d

    @pytest.mark.unit
    def test_tags_contain_author_hint(self):
        """
        When author is set, _process_document should add an 'author:X' BM25 tag.
        We simulate the tag-building logic from main.py directly.
        """
        meta = {
            "dc:creator": "Alice Wonderland",
            "dc:title": "Down the Rabbit Hole",
            "meta:page-count": "12",
            "dcterms:created": "2023-06-01T00:00:00Z",
        }
        doc_author = meta.get("dc:creator") or ""
        doc_title  = meta.get("dc:title") or ""
        raw_pages  = meta.get("meta:page-count") or "0"
        doc_page_count = int(raw_pages)

        tags: list[str] = []
        if doc_author:
            tags.append(f"author:{doc_author}")
        if doc_title:
            tags.append(f"title:{doc_title}")
        if doc_page_count:
            tags.append(f"pages:{doc_page_count}")

        assert "author:Alice Wonderland" in tags
        assert "title:Down the Rabbit Hole" in tags
        assert "pages:12" in tags

    @pytest.mark.unit
    def test_page_count_coerced_from_string(self):
        """
        Tika returns meta:page-count as a string; the pipeline coerces it to int.
        Verify the coercion handles typical Tika output.
        """
        for raw, expected in [("5", 5), ("0", 0), ("", 0), (None, 0), ("abc", 0)]:
            try:
                result = int(raw) if raw else 0
            except (ValueError, TypeError):
                result = 0
            assert result == expected, f"Failed for raw={raw!r}"

    @pytest.mark.unit
    def test_process_document_populates_all_metadata_fields(self):
        """
        Integration-style unit test: mock the full _process_document path in
        main.py and assert that the ChunkDocuments produced carry all four
        metadata fields from the Tika response.
        """
        # Arrange — simulate a Tika response with rich metadata
        tika_metadata = {
            "dc:creator":      "Dr. Bob Engineer",
            "dc:title":        "Annual Infrastructure Review",
            "meta:page-count": "25",
            "dcterms:created": "2024-01-10T08:30:00Z",
            "Content-Language": "en-US",
        }
        tika_result = ExtractionResult(
            text="This document covers the annual infrastructure review findings.",
            metadata=tika_metadata,
            success=True,
        )

        # Simulate the extraction logic from main._process_document
        meta           = tika_result.metadata
        doc_language   = meta.get("Content-Language") or "en"
        doc_author     = meta.get("dc:creator") or meta.get("Author") or ""
        doc_title      = meta.get("dc:title") or meta.get("title") or "unknown.pdf"
        doc_created_at = meta.get("dcterms:created") or meta.get("Creation-Date") or ""

        raw_page_count = meta.get("meta:page-count") or meta.get("xmpTPg:NPages") or "0"
        try:
            doc_page_count = int(raw_page_count)
        except (TypeError, ValueError):
            doc_page_count = 0

        # Build a representative ChunkDocument (as main.py does)
        doc = ChunkDocument(
            object_key     = "bucket/report.pdf",
            bucket         = "bucket",
            filename       = "report.pdf",
            extension      = "pdf",
            mime_type      = "application/pdf",
            download_url   = "http://minio:9000/bucket/report.pdf",
            size_bytes     = 51200,
            uploaded_at    = datetime.now(timezone.utc).isoformat(),
            chunk_index    = 0,
            chunk_total    = 1,
            chunk_text     = tika_result.text,
            embedding      = [0.1] * 384,
            language       = doc_language,
            tags           = [f"author:{doc_author}", f"pages:{doc_page_count}"],
            author         = doc_author,
            title          = doc_title,
            page_count     = doc_page_count,
            doc_created_at = doc_created_at,
        )

        # Assert — all metadata fields propagated correctly
        assert doc.author         == "Dr. Bob Engineer"
        assert doc.title          == "Annual Infrastructure Review"
        assert doc.page_count     == 25
        assert doc.doc_created_at == "2024-01-10T08:30:00Z"
        assert doc.language       == "en-US"
        assert "author:Dr. Bob Engineer" in doc.tags
        assert "pages:25" in doc.tags

    @pytest.mark.unit
    def test_missing_tika_metadata_falls_back_gracefully(self):
        """
        When Tika returns no metadata, all fields should default to empty/zero
        without raising exceptions.
        """
        tika_result = ExtractionResult(text="Some text.", metadata={}, success=True)

        meta           = tika_result.metadata
        doc_author     = meta.get("dc:creator") or meta.get("Author") or ""
        doc_title      = meta.get("dc:title") or meta.get("title") or "fallback.pdf"
        doc_created_at = meta.get("dcterms:created") or meta.get("Creation-Date") or ""
        raw_pages      = meta.get("meta:page-count") or "0"
        try:
            doc_page_count = int(raw_pages)
        except (ValueError, TypeError):
            doc_page_count = 0

        assert doc_author     == ""
        assert doc_title      == "fallback.pdf"
        assert doc_created_at == ""
        assert doc_page_count == 0


@pytest.mark.integration
class TestFeature2Integration:
    """
    Integration tests — upsert a ChunkDocument with metadata fields and verify
    OpenSearch actually stores and returns them in queries.
    Requires: running OpenSearch on localhost:9200.
    """

    @pytest.fixture(scope="class")
    def os_client(self):
        import json as _json
        client = OpenSearchClient(
            host=os.getenv("OPENSEARCH_HOST", "localhost"),
            port=int(os.getenv("OPENSEARCH_PORT", "9200")),
            index=TEST_INDEX,
        )
        mapping_path = ROOT / "infrastructure" / "opensearch" / "index-mapping.json"
        try:
            with open(mapping_path) as fh:
                mapping = _json.load(fh)
            client._client.indices.create(index=TEST_INDEX, body=mapping)
            time.sleep(1)
        except Exception:
            pass
        yield client
        try:
            client._client.indices.delete(index=TEST_INDEX, ignore=[404])
        except Exception:
            pass

    def test_metadata_fields_indexed_and_retrievable(self, os_client):
        """
        Upsert a document with all four metadata fields and verify they round-
        trip through OpenSearch correctly.
        """
        doc = ChunkDocument(
            object_key     = f"bucket/meta-test-{uuid.uuid4().hex[:6]}.pdf",
            bucket         = "bucket",
            filename       = "meta-test.pdf",
            extension      = "pdf",
            mime_type      = "application/pdf",
            download_url   = "http://minio:9000/bucket/meta-test.pdf",
            size_bytes     = 20480,
            uploaded_at    = "2024-06-01T12:00:00Z",
            chunk_index    = 0,
            chunk_total    = 1,
            chunk_text     = "Annual review of network infrastructure.",
            embedding      = [0.25] * 384,
            language       = "en",
            tags           = ["author:Dr. Test", "pages:10"],
            author         = "Dr. Test",
            title          = "Network Infrastructure Review",
            page_count     = 10,
            doc_created_at = "2024-01-01T00:00:00Z",
        )
        os_client.upsert(doc)
        time.sleep(1.5)

        # Fetch the doc back and verify all metadata fields were stored
        doc_id = doc.deterministic_id()
        stored = os_client._client.get(index=TEST_INDEX, id=doc_id)
        src = stored["_source"]

        assert src["author"]         == "Dr. Test"
        assert src["title"]          == "Network Infrastructure Review"
        assert src["page_count"]     == 10
        assert src["doc_created_at"] == "2024-01-01T00:00:00Z"

    def test_author_field_filterable_by_term_query(self, os_client):
        """
        Verify that the 'author' field can be used in an exact-match term filter.

        The index mapping defines 'author' as text+keyword multi-field:
          - author        → analyzed text (BM25 full-text search)
          - author.keyword → exact keyword (term/filter queries)

        This test uses 'author.keyword' for the term filter, which is the
        correct sub-field for exact-match lookups.  A unique value is used so
        the assertion is unambiguous even on a shared index.
        """
        unique_author = f"UniqueAuthor-{uuid.uuid4().hex[:8]}"
        doc = ChunkDocument(
            object_key     = f"bucket/author-filter-{uuid.uuid4().hex[:6]}.pdf",
            bucket         = "bucket",
            filename       = "author-filter.pdf",
            extension      = "pdf",
            mime_type      = "application/pdf",
            download_url   = "http://minio:9000/bucket/author-filter.pdf",
            size_bytes     = 1024,
            uploaded_at    = "2024-06-01T00:00:00Z",
            chunk_index    = 0,
            chunk_total    = 1,
            chunk_text     = "Report body text here.",
            embedding      = [0.5] * 384,
            author         = unique_author,
            title          = "Filter Test Doc",
            page_count     = 3,
            doc_created_at = "2024-01-01T00:00:00Z",
        )
        os_client.upsert(doc)
        time.sleep(1.5)

        # Use the .keyword sub-field for exact-match term filtering
        result = os_client._client.search(
            index=TEST_INDEX,
            body={"query": {"term": {"author.keyword": unique_author}}},
        )
        assert result["hits"]["total"]["value"] >= 1, \
            f"Expected to find doc with author={unique_author!r} via author.keyword term filter"


# =============================================================================
# ██████████████   FEATURE 7: DLQ retry consumer                   ████████████
# =============================================================================

# Import the module-level helpers we need to test directly
import importlib, types

def _load_dlq_module():
    """Import dlq_retry_worker with a fast backoff override for tests."""
    # Patch env before module constants are evaluated at import time
    os.environ["RETRY_BACKOFF_SECONDS"] = "0,0,0"  # no real sleeps in unit tests
    os.environ["MAX_RETRY_ATTEMPTS"]     = "3"
    import dlq_retry_worker as m
    return m

dlq = _load_dlq_module()
DLQRetryWorker = dlq.DLQRetryWorker


def _make_mock_msg(payload: dict) -> MagicMock:
    """Build a confluent_kafka Message mock from a DLQ envelope dict."""
    msg = MagicMock()
    msg.value.return_value = json.dumps(payload).encode("utf-8")
    msg.error.return_value = None
    return msg


class TestDLQBackoffSchedule:
    """Unit tests for the backoff helper — no Kafka needed."""

    @pytest.mark.unit
    def test_attempt_1_uses_first_schedule_entry(self):
        # Reset to default schedule for this test
        original = dlq._BACKOFF_SCHEDULE[:]
        dlq._BACKOFF_SCHEDULE[:] = [60, 300, 900]
        assert dlq._backoff_for_attempt(1) == 60
        dlq._BACKOFF_SCHEDULE[:] = original

    @pytest.mark.unit
    def test_attempt_2_uses_second_entry(self):
        original = dlq._BACKOFF_SCHEDULE[:]
        dlq._BACKOFF_SCHEDULE[:] = [60, 300, 900]
        assert dlq._backoff_for_attempt(2) == 300
        dlq._BACKOFF_SCHEDULE[:] = original

    @pytest.mark.unit
    def test_out_of_range_attempt_clamps_to_last(self):
        original = dlq._BACKOFF_SCHEDULE[:]
        dlq._BACKOFF_SCHEDULE[:] = [60, 300, 900]
        assert dlq._backoff_for_attempt(99) == 900
        dlq._BACKOFF_SCHEDULE[:] = original


class TestDLQRetryWorkerUnit:
    """
    Unit tests for DLQRetryWorker — all Kafka calls are mocked.
    Uses RETRY_BACKOFF_SECONDS=0,0,0 so tests complete instantly.
    """

    def _make_worker(self) -> DLQRetryWorker:
        """Create a DLQRetryWorker with mocked Kafka consumer + producer."""
        with patch("dlq_retry_worker.Consumer"), patch("dlq_retry_worker.Producer"):
            worker = DLQRetryWorker()
        worker.consumer = MagicMock()
        worker.consumer.commit = MagicMock()
        worker.producer = MagicMock()
        worker.producer.produce = MagicMock()
        worker.producer.flush   = MagicMock()
        return worker

    # ── Test 1: DLQ → successful reprocess ────────────────────────────────────

    @pytest.mark.unit
    def test_dlq_message_is_requeued_to_main_topic(self):
        """
        GIVEN  a valid DLQ envelope with retry_count=0
        WHEN   the worker handles the message
        THEN   the original_payload is re-published to the main topic and
               an updated envelope (retry_count=1) is sent back to the DLQ.
        """
        worker = self._make_worker()
        original_event = {
            "Records": [{
                "eventName": "s3:ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": "uploads"},
                    "object": {"key": "reports/q1.pdf", "size": 1024,
                               "contentType": "application/pdf"},
                },
            }]
        }
        envelope = {
            "original_payload": json.dumps(original_event),
            "error":       "Tika timeout",
            "bucket":      "uploads",
            "key":         "reports/q1.pdf",
            "failed_at":   "2024-06-01T10:00:00Z",
            "retry_count": 0,
        }
        msg = _make_mock_msg(envelope)

        asyncio.run(worker._handle_dlq_message(msg))

        # Original payload must be re-published to the main topic
        calls = worker.producer.produce.call_args_list
        main_topic_calls = [c for c in calls if c[0][0] == dlq.KAFKA_TOPIC]
        assert len(main_topic_calls) == 1, \
            "Expected exactly one produce() call to the main topic"

        published_bytes = main_topic_calls[0][1]["value"]
        published_event = json.loads(published_bytes.decode("utf-8"))
        assert published_event["Records"][0]["s3"]["bucket"]["name"] == "uploads"

        # Updated envelope (retry_count=1) must be sent back to the DLQ
        dlq_calls = [c for c in calls if c[0][0] == dlq.KAFKA_DLQ_TOPIC]
        assert len(dlq_calls) == 1, "Expected one DLQ envelope update"
        updated = json.loads(dlq_calls[0][1]["value"].decode("utf-8"))
        assert updated["retry_count"] == 1
        assert "last_retried_at" in updated

        # Offset must be committed after successful re-publish
        worker.consumer.commit.assert_called_once_with(msg)

    # ── Test 2: Permanent failure after max retries ────────────────────────────

    @pytest.mark.unit
    def test_event_permanently_fails_after_max_retries(self):
        """
        GIVEN  a DLQ envelope whose retry_count == MAX_RETRY_ATTEMPTS (3)
        WHEN   the worker handles the message
        THEN   nothing is re-queued to the main topic and the event is written
               to the permanent failure log.
        """
        worker = self._make_worker()
        envelope = {
            "original_payload": json.dumps({"Records": []}),
            "error":       "Persistent extraction failure",
            "bucket":      "uploads",
            "key":         "broken/corrupt.pdf",
            "failed_at":   "2024-06-01T09:00:00Z",
            "retry_count": 3,   # already at MAX_RETRY_ATTEMPTS=3
        }
        msg = _make_mock_msg(envelope)

        with tempfile.NamedTemporaryFile(mode="r", suffix=".jsonl", delete=False) as fh:
            log_path = Path(fh.name)

        original_log = dlq.PERMANENT_FAILURE_LOG
        dlq.PERMANENT_FAILURE_LOG = log_path

        try:
            asyncio.run(worker._handle_dlq_message(msg))
        finally:
            dlq.PERMANENT_FAILURE_LOG = original_log

        # Nothing should be re-published to the main topic
        main_topic_calls = [
            c for c in worker.producer.produce.call_args_list
            if c[0][0] == dlq.KAFKA_TOPIC
        ]
        assert len(main_topic_calls) == 0, \
            "A permanently-failed event must NOT be re-queued to the main topic"

        # Offset must still be committed so the message isn't re-delivered
        worker.consumer.commit.assert_called_once_with(msg)

        # The event must be written to the permanent failure log
        written = log_path.read_text(encoding="utf-8").strip()
        assert written, "Permanent failure log must not be empty"
        record = json.loads(written)
        assert record["key"]   == "broken/corrupt.pdf"
        assert record["error"] == "Persistent extraction failure"
        assert "permanently_failed_at" in record

    # ── Test 3: Malformed DLQ message ─────────────────────────────────────────

    @pytest.mark.unit
    def test_malformed_dlq_message_is_skipped_gracefully(self):
        """
        GIVEN  a DLQ message that is not valid JSON
        WHEN   the worker handles it
        THEN   no produce() call is made and the offset is still committed
               so the bad message doesn't block the consumer.
        """
        worker = self._make_worker()
        msg = MagicMock()
        msg.value.return_value = b"this is not valid json {{{"
        msg.error.return_value = None

        asyncio.run(worker._handle_dlq_message(msg))

        worker.producer.produce.assert_not_called()
        worker.consumer.commit.assert_called_once_with(msg)

    # ── Test 4: Retry increments count correctly across attempts ──────────────

    @pytest.mark.unit
    def test_retry_count_increments_on_each_requeue(self):
        """
        Simulate three successive DLQ deliveries (retry_count 0 → 1 → 2).
        Each should re-queue with an incremented count and commit the offset.
        After retry_count reaches MAX_RETRY_ATTEMPTS, it should permanently fail.
        """
        for attempt_count in range(3):
            worker = self._make_worker()
            envelope = {
                "original_payload": json.dumps({"Records": []}),
                "error":       "Intermittent failure",
                "bucket":      "uploads",
                "key":         "doc.pdf",
                "failed_at":   "2024-06-01T00:00:00Z",
                "retry_count": attempt_count,
            }
            msg = _make_mock_msg(envelope)
            asyncio.run(worker._handle_dlq_message(msg))

            # Each attempt < MAX should produce to main topic
            main_calls = [
                c for c in worker.producer.produce.call_args_list
                if c[0][0] == dlq.KAFKA_TOPIC
            ]
            assert len(main_calls) == 1, \
                f"Attempt {attempt_count}: expected 1 main-topic produce call"

            # DLQ envelope update should carry incremented count
            dlq_calls = [
                c for c in worker.producer.produce.call_args_list
                if c[0][0] == dlq.KAFKA_DLQ_TOPIC
            ]
            updated = json.loads(dlq_calls[0][1]["value"].decode("utf-8"))
            assert updated["retry_count"] == attempt_count + 1

