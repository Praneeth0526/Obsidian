import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List
from urllib.parse import unquote_plus

from confluent_kafka import Consumer, KafkaError
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

# ── MinIO ──────────────────────────────────────────────────────────────────────
MINIO_ENDPOINT   = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin123")
MINIO_SECURE     = os.environ.get("MINIO_SECURE", "false").lower() == "true"
MINIO_PUBLIC_URL = os.environ.get("MINIO_PUBLIC_URL", "http://localhost:9000")

# ── Debug output (optional) ────────────────────────────────────────────────────
# Set DEBUG_OUTPUT=true to also dump processed chunks to output_dims/ as JSON.
DEBUG_OUTPUT = os.environ.get("DEBUG_OUTPUT", "false").lower() == "true"
OUTPUT_DIR   = Path(os.environ.get("OUTPUT_DIR", "output_dims"))
if DEBUG_OUTPUT:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class IngestionWorker:
    def __init__(self):
        # ── Kafka Consumer ─────────────────────────────────────────────────────
        self.consumer = Consumer({
            'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
            'group.id': KAFKA_GROUP_ID,
            'auto.offset.reset': 'earliest'
        })
        self.consumer.subscribe([KAFKA_TOPIC])

        # ── MinIO Client ───────────────────────────────────────────────────────
        self.minio_client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE
        )

        # ── Processing Components ──────────────────────────────────────────────
        self.tika_extractor = TikaExtractor(tika_url=os.environ.get("TIKA_URL", "http://localhost:9998"))
        self.text_chunker   = TextChunker()
        self.image_handler  = ImageHandler()
        self.model_client   = ModelClient()  # Uses MODEL_SERVER_URL env, defaults to http://localhost:8000

        # ── OpenSearch Storage ─────────────────────────────────────────────────
        self.os_client = OpenSearchClient()
        if not self.os_client.health_check():
            logger.warning("OpenSearch health check failed at startup — will retry on first write")
        else:
            logger.info("OpenSearch cluster is healthy")

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

            logger.info(f"Processing s3 object: bucket={bucket}, key={key}, content_type={content_type}")

            try:
                # 1. Download file from MinIO
                response   = self.minio_client.get_object(bucket, key)
                file_bytes = response.read()
                response.close()
                response.release_conn()

                # 2. Route based on content-type
                is_image   = content_type.startswith("image/")
                uploaded_at = datetime.now(timezone.utc).isoformat()
                filename   = key.split("/")[-1]
                extension  = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

                if is_image:
                    logger.info(f"Handling as image: {key}")
                    image_result = await asyncio.to_thread(self.image_handler.process, file_bytes, content_type)
                    vector       = await self.model_client.embed_image(image_result.processed_bytes, "image/jpeg")

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
                        chunk_text   = "",          # images have no text chunk
                        embedding    = vector,
                    )
                    await asyncio.to_thread(self.os_client.upsert, doc)
                    logger.info(f"Indexed image: {object_key}")

                    if DEBUG_OUTPUT:
                        self._debug_dump(bucket, key, {
                            "type": "image", "bucket": bucket, "key": key,
                            "vector": vector, "metadata": image_result.metadata,
                        })

                else:
                    logger.info(f"Handling as document/text: {key}")
                    tika_result = await self.tika_extractor.extract(file_bytes, content_type)

                    if not tika_result.text.strip():
                        logger.warning(f"No text extracted from {key}. Skipping.")
                        continue

                    chunks  = self.text_chunker.chunk(tika_result.text)
                    vectors = await self.model_client.embed_texts([c.text for c in chunks])

                    docs: List[ChunkDocument] = []
                    for chunk, vector in zip(chunks, vectors):
                        docs.append(ChunkDocument(
                            object_key   = object_key,
                            bucket       = bucket,
                            filename     = filename,
                            extension    = extension,
                            mime_type    = content_type,
                            download_url = download_url,
                            size_bytes   = size_bytes,
                            uploaded_at  = uploaded_at,
                            chunk_index  = chunk.chunk_index,
                            chunk_total  = len(chunks),
                            chunk_text   = chunk.text,
                            embedding    = vector,
                        ))

                    result = await asyncio.to_thread(self.os_client.bulk_upsert, docs)
                    logger.info(
                        f"Indexed {result['success']}/{len(docs)} chunks for {object_key}"
                        + (f" ({result['failed']} failed)" if result['failed'] else "")
                    )

                    if DEBUG_OUTPUT:
                        self._debug_dump(bucket, key, {
                            "type": "document", "bucket": bucket, "key": key,
                            "metadata": tika_result.metadata,
                            "chunks": [
                                {"chunk_index": c.chunk_index, "text": c.chunk_text, "vector": c.embedding}
                                for c in docs
                            ],
                        })

            except Exception as e:
                logger.error(f"Error processing {key}: {e}", exc_info=True)
                # In a production system, publish to the DLQ here

    def _debug_dump(self, bucket: str, key: str, data: Dict[str, Any]) -> None:
        """Optionally write processed output to output_dims/ for local debugging."""
        safe_key  = key.replace("/", "_")
        out_file  = OUTPUT_DIR / f"{bucket}_{safe_key}.json"
        with open(out_file, "w") as f:
            json.dump(data, f, indent=2)
        logger.debug(f"Debug dump written to {out_file}")

    async def run(self):
        logger.info("Starting ingestion worker loop...")
        try:
            while True:
                # Use asyncio.to_thread for blocking Kafka poll
                msg = await asyncio.to_thread(self.consumer.poll, 1.0)

                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    logger.error(f"Kafka error: {msg.error()}")
                    continue

                # Process message
                try:
                    event_data = json.loads(msg.value().decode('utf-8'))
                    await self.process_event(event_data)
                except json.JSONDecodeError:
                    logger.error("Failed to decode Kafka message as JSON")
                except Exception as e:
                    logger.error(f"Unexpected error processing Kafka message: {e}", exc_info=True)
        finally:
            self.consumer.close()


if __name__ == "__main__":
    worker = IngestionWorker()
    asyncio.run(worker.run())
