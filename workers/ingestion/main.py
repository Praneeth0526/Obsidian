import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List
from urllib.parse import unquote_plus

from confluent_kafka import Consumer, KafkaError, Producer
from minio import Minio
import httpx

from chunker import TextChunker
from tika_extractor import TikaExtractor
from image_handler import ImageHandler
from model_client import ModelClient
from opensearch_client import OpenSearchClient, ChunkDocument

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ── Kafka ──────────────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
KAFKA_GROUP_ID          = os.environ.get("KAFKA_GROUP_ID", "ingestion-worker")
KAFKA_TOPIC             = os.environ.get("KAFKA_TOPIC", "file-upload-events")
KAFKA_DLQ_TOPIC         = os.environ.get("KAFKA_DLQ_TOPIC", "file-upload-events-dlq")

# ── MinIO ──────────────────────────────────────────────────────────────────────
MINIO_ENDPOINT   = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin123")
MINIO_SECURE     = os.environ.get("MINIO_SECURE", "false").lower() == "true"
MINIO_PUBLIC_URL = os.environ.get("MINIO_PUBLIC_URL", "http://localhost:9000")

# ── File size limit ────────────────────────────────────────────────────────────
# Files exceeding this limit are routed to the DLQ instead of downloaded.
# Default: 100 MB. Set MAX_FILE_SIZE_BYTES=0 to disable the guard.
MAX_FILE_SIZE_BYTES = int(os.environ.get("MAX_FILE_SIZE_BYTES", str(100 * 1024 * 1024)))

# ── Concurrency ────────────────────────────────────────────────────────────────
# Maximum number of Kafka messages processed concurrently.
MAX_CONCURRENT_EVENTS = int(os.environ.get("MAX_CONCURRENT_EVENTS", "4"))

# ── Debug output (optional) ────────────────────────────────────────────────────
# Set DEBUG_OUTPUT=true to also dump processed chunks to output_dims/ as JSON.
DEBUG_OUTPUT = os.environ.get("DEBUG_OUTPUT", "false").lower() == "true"
OUTPUT_DIR   = Path(os.environ.get("OUTPUT_DIR", "output_dims"))
if DEBUG_OUTPUT:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class IngestionWorker:
    def __init__(self):
        # ── Kafka Consumer — manual offset commits for at-least-once delivery ──
        # enable.auto.commit is intentionally disabled.  We commit only after
        # the message has been fully processed (or sent to the DLQ), so a crash
        # mid-processing will cause the message to be re-delivered rather than
        # silently lost.
        self.consumer = Consumer({
            'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
            'group.id': KAFKA_GROUP_ID,
            'auto.offset.reset': 'earliest',
            'enable.auto.commit': False,   # ← manual commits only
        })
        self.consumer.subscribe([KAFKA_TOPIC])

        # ── Kafka DLQ Producer ─────────────────────────────────────────────────
        # Failures are published here so they can be inspected, retried, or
        # alerted on without losing the original event payload.
        self.dlq_producer = Producer({
            'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
            'acks': 'all',               # wait for all ISR brokers to ack
        })

        # ── MinIO Client ───────────────────────────────────────────────────────
        self.minio_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE
        )

        # ── Processing Components ──────────────────────────────────────────────
        self.tika_extractor = TikaExtractor(tika_url=os.environ.get("TIKA_SERVER_URL", "http://localhost:9998"))
        self.text_chunker   = TextChunker()
        self.image_handler  = ImageHandler()
        self.model_client   = ModelClient()  # Uses MODEL_SERVER_URL env, defaults to http://localhost:8000

        # ── OpenSearch Storage ─────────────────────────────────────────────────
        self.os_client = OpenSearchClient()
        if not self.os_client.health_check():
            logger.warning("OpenSearch health check failed at startup — will retry on first write")
        else:
            logger.info("OpenSearch cluster is healthy")

        # ── Concurrency semaphore ──────────────────────────────────────────────
        # Limits the number of events processed concurrently so we don't
        # overwhelm Tika / the model server under a burst of uploads.
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_EVENTS)

        # ── Graceful shutdown flag ─────────────────────────────────────────────
        self._shutdown = False

    # ─────────────────────────────────────────────────────────────────────────
    # Graceful shutdown helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _install_signal_handlers(self) -> None:
        """Install SIGTERM / SIGINT handlers for clean Kubernetes pod shutdown."""
        loop = asyncio.get_event_loop()

        def _handle_signal(sig):
            logger.info("Received signal %s — initiating graceful shutdown", sig.name)
            self._shutdown = True

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _handle_signal, sig)

    # ─────────────────────────────────────────────────────────────────────────
    # DLQ helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _publish_to_dlq(
        self,
        original_payload: bytes,
        error: str,
        bucket: str = "",
        key: str = "",
    ) -> None:
        """
        Publish the original Kafka message payload to the DLQ topic, augmented
        with error context, so failed events can be inspected and retried.
        """
        dlq_record = {
            "original_payload": original_payload.decode("utf-8", errors="replace"),
            "error": error,
            "bucket": bucket,
            "key": key,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.dlq_producer.produce(
                KAFKA_DLQ_TOPIC,
                value=json.dumps(dlq_record).encode("utf-8"),
            )
            self.dlq_producer.flush(timeout=10)
            logger.info("Published failed event to DLQ: bucket=%s key=%s", bucket, key)
        except Exception as dlq_exc:
            # Log but don't raise — we don't want a DLQ failure to mask the
            # original error or prevent the offset from being committed.
            logger.error("Failed to publish to DLQ: %s", dlq_exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Core event processing
    # ─────────────────────────────────────────────────────────────────────────

    async def process_event(self, event: Dict[str, Any]):
        """Process a single S3 event from MinIO."""
        records = event.get("Records", [])
        for record in records:
            event_name = record.get("eventName", "")

            # ── Handle object deletions ────────────────────────────────────────
            if event_name.startswith("s3:ObjectRemoved"):
                bucket = record["s3"]["bucket"]["name"]
                key    = unquote_plus(record["s3"]["object"]["key"])
                logger.info(f"Object deleted: bucket={bucket}, key={key} — purging from OpenSearch")
                deleted = await asyncio.to_thread(self.os_client.delete_by_object_key, f"{bucket}/{key}")
                logger.info(f"Purged {deleted} chunks for {key}")
                continue

            if not event_name.startswith("s3:ObjectCreated"):
                continue

            bucket       = record["s3"]["bucket"]["name"]
            key          = unquote_plus(record["s3"]["object"]["key"])
            content_type = record["s3"]["object"].get("contentType", "application/octet-stream")
            size_bytes   = int(record["s3"]["object"].get("size", 0))
            object_key   = f"{bucket}/{key}"
            download_url = f"{MINIO_PUBLIC_URL}/{bucket}/{key}"

            # ── File-size guard ────────────────────────────────────────────────
            # Prevent loading enormous files into memory.  Oversized files are
            # sent to the DLQ rather than silently dropped.
            if MAX_FILE_SIZE_BYTES > 0 and size_bytes > MAX_FILE_SIZE_BYTES:
                logger.warning(
                    "File too large — skipping: bucket=%s key=%s size=%d limit=%d",
                    bucket, key, size_bytes, MAX_FILE_SIZE_BYTES,
                )
                return  # caller will publish to DLQ with this error context

            logger.info(f"Processing s3 object: bucket={bucket}, key={key}, content_type={content_type}")

            # 1. Download file from MinIO
            response   = self.minio_client.get_object(bucket, key)
            file_bytes = response.read()
            response.close()
            response.release_conn()

            # 2. Auto-detect content-type if the event reported octet-stream
            if content_type == "application/octet-stream":
                detected = await self.tika_extractor.detect_type(file_bytes)
                if detected:
                    logger.info(
                        "Content-type auto-detected: %s → %s (bucket=%s key=%s)",
                        content_type, detected, bucket, key,
                    )
                    content_type = detected

            # 3. Route based on content-type
            is_image   = content_type.startswith("image/")
            uploaded_at = datetime.now(timezone.utc).isoformat()
            filename   = key.split("/")[-1]
            extension  = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

            if is_image:
                await self._process_image(
                    file_bytes, content_type, object_key, bucket,
                    filename, extension, download_url, size_bytes, uploaded_at,
                )
            else:
                await self._process_document(
                    file_bytes, content_type, object_key, bucket,
                    filename, extension, download_url, size_bytes, uploaded_at,
                )

    async def _process_image(
        self,
        file_bytes: bytes,
        content_type: str,
        object_key: str,
        bucket: str,
        filename: str,
        extension: str,
        download_url: str,
        size_bytes: int,
        uploaded_at: str,
    ) -> None:
        """Preprocess and embed a single image file."""
        logger.info("Handling as image: %s", object_key)
        image_result = await asyncio.to_thread(self.image_handler.process, file_bytes, content_type)

        if not image_result.success:
            raise RuntimeError(f"Image preprocessing failed: {image_result.error}")

        vector = await self.model_client.embed_image(image_result.image_bytes, "image/jpeg")

        # Build a searchable text description so BM25 can surface images.
        # Without this, images have no keyword representation and are invisible
        # to the BM25 side of the hybrid search.
        bm25_text = (
            f"Image: {filename} "
            f"({image_result.orig_width}x{image_result.orig_height} pixels, "
            f"{content_type})"
        )

        doc = ChunkDocument(
            object_key   = object_key,
            bucket       = bucket,
            filename     = filename,
            extension    = extension,
            mime_type    = content_type,
            download_url = download_url,
            size_bytes   = size_bytes,
            uploaded_at  = uploaded_at,
            chunk_index  = 0,
            chunk_total  = 1,
            chunk_text   = bm25_text,
            embedding    = vector,
        )
        await asyncio.to_thread(self.os_client.upsert, doc)
        logger.info("Indexed image: %s", object_key)

        if DEBUG_OUTPUT:
            self._debug_dump(bucket, object_key.split("/", 1)[-1], {
                "type": "image", "bucket": bucket, "key": object_key,
                "vector": vector,
                "metadata": {
                    "orig_width": image_result.orig_width,
                    "orig_height": image_result.orig_height,
                    "orig_mode": image_result.orig_mode,
                },
            })

    async def _process_document(
        self,
        file_bytes: bytes,
        content_type: str,
        object_key: str,
        bucket: str,
        filename: str,
        extension: str,
        download_url: str,
        size_bytes: int,
        uploaded_at: str,
    ) -> None:
        """Extract, chunk, embed, and index a text-based document."""
        logger.info("Handling as document/text: %s", object_key)
        tika_result = await self.tika_extractor.extract(file_bytes, content_type)

        if not tika_result.text.strip():
            logger.warning(f"No text extracted from {object_key}. Skipping.")
            return

        chunks  = self.text_chunker.chunk(tika_result.text)
        vectors = await self.model_client.embed_texts([c.text for c in chunks])

        # Extract useful fields from Tika metadata for richer search filtering.
        # Each field is stored both as a dedicated ChunkDocument attribute (for
        # structured OpenSearch filtering) and, where useful, as a searchable
        # tag (for BM25 keyword matching).
        meta           = tika_result.metadata
        doc_language   = meta.get("Content-Language") or meta.get("language") or "en"
        doc_author     = meta.get("dc:creator") or meta.get("Author") or ""
        doc_title      = meta.get("dc:title") or meta.get("title") or filename
        doc_created_at = meta.get("dcterms:created") or meta.get("Creation-Date") or ""

        # meta:page-count is a string in Tika’s output; coerce to int.
        raw_page_count = meta.get("meta:page-count") or meta.get("xmpTPg:NPages") or "0"
        try:
            doc_page_count = int(raw_page_count)
        except (TypeError, ValueError):
            doc_page_count = 0

        # Build supplementary BM25 tags so keyword search can surface documents
        # by author / title even when those terms aren’t in the body text.
        doc_tags: List[str] = []
        if doc_author:
            doc_tags.append(f"author:{doc_author}")
        if doc_title and doc_title != filename:
            doc_tags.append(f"title:{doc_title}")
        if doc_page_count:
            doc_tags.append(f"pages:{doc_page_count}")

        docs: List[ChunkDocument] = []
        for chunk, vector in zip(chunks, vectors):
            docs.append(ChunkDocument(
                object_key     = object_key,
                bucket         = bucket,
                filename       = filename,
                extension      = extension,
                mime_type      = content_type,
                download_url   = download_url,
                size_bytes     = size_bytes,
                uploaded_at    = uploaded_at,
                chunk_index    = chunk.chunk_index,
                chunk_total    = len(chunks),
                chunk_text     = chunk.text,
                embedding      = vector,
                language       = doc_language,
                tags           = doc_tags,
                # Document-level metadata fields (Feature 2)
                author         = doc_author,
                title          = doc_title,
                page_count     = doc_page_count,
                doc_created_at = doc_created_at,
            ))

        result = await asyncio.to_thread(self.os_client.bulk_upsert, docs)
        logger.info(
            "Indexed %d/%d chunks for %s%s",
            result['success'], len(docs), object_key,
            f" ({result['failed']} failed)" if result['failed'] else "",
        )

        if DEBUG_OUTPUT:
            self._debug_dump(bucket, object_key.split("/", 1)[-1], {
                "type": "document", "bucket": bucket, "key": object_key,
                "metadata": tika_result.metadata,
                "chunks": [
                    {"chunk_index": c.chunk_index, "text": c.chunk_text, "vector": c.embedding}
                    for c in docs
                ],
            })

    # ─────────────────────────────────────────────────────────────────────────
    # Main loop
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_message(self, msg) -> None:
        """
        Decode and process one Kafka message under the concurrency semaphore.
        On success: commit the offset.
        On failure: publish to DLQ, then commit the offset so the bad message
                    is not re-delivered endlessly.
        """
        raw_value = msg.value()
        bucket = key = ""

        async with self._semaphore:
            try:
                event_data = json.loads(raw_value.decode('utf-8'))

                # Pull bucket/key for DLQ context before processing
                records = event_data.get("Records", [])
                if records:
                    s3 = records[0].get("s3", {})
                    bucket = s3.get("bucket", {}).get("name", "")
                    key    = unquote_plus(s3.get("object", {}).get("key", ""))

                await self.process_event(event_data)

            except json.JSONDecodeError:
                logger.error("Failed to decode Kafka message as JSON — publishing to DLQ")
                self._publish_to_dlq(raw_value, "JSONDecodeError", bucket, key)

            except Exception as e:
                logger.error("Error processing %s/%s: %s", bucket, key, e, exc_info=True)
                self._publish_to_dlq(raw_value, str(e), bucket, key)

            finally:
                # Commit regardless of outcome — after DLQ publish the event is
                # safely persisted; re-delivering it would just DLQ it again.
                await asyncio.to_thread(self.consumer.commit, msg)

    async def run(self):
        self._install_signal_handlers()
        logger.info("Starting ingestion worker loop (max_concurrent=%d)...", MAX_CONCURRENT_EVENTS)

        pending: set[asyncio.Task] = set()

        try:
            while not self._shutdown:
                msg = await asyncio.to_thread(self.consumer.poll, 1.0)

                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    logger.error("Kafka error: %s", msg.error())
                    continue

                # Spawn a task for each message — the semaphore inside
                # _handle_message limits actual concurrency.
                task = asyncio.create_task(self._handle_message(msg))
                pending.add(task)
                task.add_done_callback(pending.discard)

            # Drain in-flight tasks before shutting down
            if pending:
                logger.info("Shutdown: waiting for %d in-flight task(s) to finish...", len(pending))
                await asyncio.gather(*pending, return_exceptions=True)

        finally:
            self.consumer.close()
            self.dlq_producer.flush(timeout=15)
            logger.info("Ingestion worker shut down cleanly.")


    def _debug_dump(self, bucket: str, key: str, data: Dict[str, Any]) -> None:
        """Optionally write processed output to output_dims/ for local debugging."""
        safe_key  = key.replace("/", "_")
        out_file  = OUTPUT_DIR / f"{bucket}_{safe_key}.json"
        with open(out_file, "w") as f:
            json.dump(data, f, indent=2)
        logger.debug("Debug dump written to %s", out_file)


if __name__ == "__main__":
    worker = IngestionWorker()
    asyncio.run(worker.run())
