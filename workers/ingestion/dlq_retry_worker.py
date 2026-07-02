"""
dlq_retry_worker.py — Dead Letter Queue retry consumer for the ingestion pipeline.

Reads failed events from ``file-upload-events-dlq``, waits for the appropriate
backoff interval, and re-publishes them to the main ``file-upload-events`` topic
so the ingestion worker can process them again.

Retry schedule (default):
    Attempt 1 → wait  60 s  (transient Tika / model-server hiccup)
    Attempt 2 → wait 300 s  (extended outage)
    Attempt 3 → wait 900 s  (last resort before permanent failure)

After ``MAX_RETRY_ATTEMPTS`` the event is written to a permanent-failures log
(``PERMANENT_FAILURE_LOG`` path) and no further re-queuing is performed.

Environment variables
---------------------
KAFKA_BOOTSTRAP_SERVERS   Kafka broker list (default: localhost:29092)
KAFKA_DLQ_TOPIC           Source topic (default: file-upload-events-dlq)
KAFKA_TOPIC               Destination topic (default: file-upload-events)
KAFKA_DLQ_RETRY_GROUP_ID  Consumer group for this worker (default: dlq-retry-worker)
MAX_RETRY_ATTEMPTS        Maximum re-queue attempts before permanent failure (default: 3)
RETRY_BACKOFF_SECONDS     Comma-separated backoff per attempt (default: 60,300,900)
PERMANENT_FAILURE_LOG     Path to append permanently-failed events (default: permanent_failures.jsonl)

Usage
-----
    python dlq_retry_worker.py

The worker runs as a separate process / container alongside the main ingestion
worker.  It shares no state with it — coordination happens entirely through
Kafka topics.
"""

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from confluent_kafka import Consumer, KafkaError, Producer

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
KAFKA_DLQ_TOPIC         = os.environ.get("KAFKA_DLQ_TOPIC", "file-upload-events-dlq")
KAFKA_TOPIC             = os.environ.get("KAFKA_TOPIC", "file-upload-events")
KAFKA_GROUP_ID          = os.environ.get("KAFKA_DLQ_RETRY_GROUP_ID", "dlq-retry-worker")

MAX_RETRY_ATTEMPTS = int(os.environ.get("MAX_RETRY_ATTEMPTS", "3"))

# Parse the per-attempt backoff schedule from a comma-separated env var.
# Each value is the number of seconds to sleep *before* re-publishing the event.
# Falls back to the last value if fewer entries are provided than attempts.
_raw_backoff = os.environ.get("RETRY_BACKOFF_SECONDS", "60,300,900")
_BACKOFF_SCHEDULE: List[int] = [int(s) for s in _raw_backoff.split(",") if s.strip()]
if not _BACKOFF_SCHEDULE:
    _BACKOFF_SCHEDULE = [60, 300, 900]

PERMANENT_FAILURE_LOG = Path(
    os.environ.get("PERMANENT_FAILURE_LOG", "permanent_failures.jsonl")
)


def _backoff_for_attempt(attempt: int) -> int:
    """
    Return the wait duration (seconds) before re-queuing a given attempt.

    ``attempt`` is 1-indexed (attempt 1 = first retry after the original failure).
    Uses the configured schedule and clamps to the last value for out-of-range attempts.
    """
    idx = max(0, min(attempt - 1, len(_BACKOFF_SCHEDULE) - 1))
    return _BACKOFF_SCHEDULE[idx]


class DLQRetryWorker:
    """
    Kafka consumer that replays DLQ events to the main topic with backoff.

    The DLQ message envelope written by the ingestion worker looks like::

        {
            "original_payload": "<JSON string of the original Kafka event>",
            "error": "some error message",
            "bucket": "my-bucket",
            "key": "path/to/file.pdf",
            "failed_at": "2024-01-01T00:00:00+00:00",
            "retry_count": 0   # incremented by this worker on each re-queue
        }

    On each DLQ message:
      1. Parse the envelope and read ``retry_count`` (defaults to 0).
      2. If ``retry_count >= MAX_RETRY_ATTEMPTS``:  permanently fail.
      3. Otherwise: sleep for the configured backoff, increment ``retry_count``,
         and re-publish the *original_payload* to the main topic.  The updated
         envelope (with the new count) is also re-published to the DLQ so that
         if the retry itself fails, the envelope already reflects the attempt.

    Manual offset commits are used so a crash during the backoff sleep does not
    silently drop the retry.
    """

    def __init__(self) -> None:
        # ── Consumer — reads from the DLQ ──────────────────────────────────────
        self.consumer = Consumer({
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "group.id": KAFKA_GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,  # manual commits only
        })
        self.consumer.subscribe([KAFKA_DLQ_TOPIC])

        # ── Producer — re-publishes to the main topic (and DLQ for state) ──────
        self.producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
            "acks": "all",
        })

        # ── Graceful shutdown flag ─────────────────────────────────────────────
        self._shutdown = False

        logger.info(
            "DLQ retry worker initialised — source=%s dest=%s max_attempts=%d schedule=%s",
            KAFKA_DLQ_TOPIC,
            KAFKA_TOPIC,
            MAX_RETRY_ATTEMPTS,
            _BACKOFF_SCHEDULE,
        )

    # ── Signal handling ───────────────────────────────────────────────────────

    def _install_signal_handlers(self) -> None:
        """Install SIGTERM / SIGINT handlers for clean shutdown."""
        loop = asyncio.get_event_loop()

        def _handle(sig):
            logger.info("Received %s — shutting down DLQ retry worker", sig.name)
            self._shutdown = True

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _handle, sig)

    # ── Permanent failure sink ────────────────────────────────────────────────

    def _record_permanent_failure(self, envelope: dict) -> None:
        """
        Append an event that has exhausted all retries to the permanent-failure
        log file.  This provides an audit trail without re-queuing the event.
        """
        record = {
            **envelope,
            "permanently_failed_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with PERMANENT_FAILURE_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            logger.warning(
                "Permanently failed — bucket=%s key=%s after %d attempt(s)",
                envelope.get("bucket", "?"),
                envelope.get("key", "?"),
                envelope.get("retry_count", 0),
            )
        except OSError as exc:
            logger.error("Could not write to permanent failure log: %s", exc)

    # ── Core message handler ──────────────────────────────────────────────────

    async def _handle_dlq_message(self, msg) -> None:
        """
        Process one DLQ message:
          - Parse the envelope.
          - If retries exhausted -> permanently fail.
          - Otherwise -> sleep (backoff), re-publish original payload to main topic,
            update the envelope on the DLQ, commit the offset.
        """
        raw_value = msg.value()

        try:
            envelope = json.loads(raw_value.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.error("Malformed DLQ message — skipping: %s", exc)
            await asyncio.to_thread(self.consumer.commit, msg)
            return

        retry_count = int(envelope.get("retry_count", 0))
        bucket      = envelope.get("bucket", "?")
        key         = envelope.get("key", "?")
        error       = envelope.get("error", "unknown")

        logger.info(
            "DLQ message received — bucket=%s key=%s retry_count=%d error=%r",
            bucket, key, retry_count, error,
        )

        # ── Check if permanently failed ────────────────────────────────────────
        if retry_count >= MAX_RETRY_ATTEMPTS:
            logger.warning(
                "Max retries (%d) reached for bucket=%s key=%s — permanently failing",
                MAX_RETRY_ATTEMPTS, bucket, key,
            )
            self._record_permanent_failure(envelope)
            await asyncio.to_thread(self.consumer.commit, msg)
            return

        # ── Compute backoff and wait ───────────────────────────────────────────
        wait_seconds = _backoff_for_attempt(retry_count + 1)
        logger.info(
            "Waiting %ds before retry %d/%d for bucket=%s key=%s",
            wait_seconds, retry_count + 1, MAX_RETRY_ATTEMPTS, bucket, key,
        )
        # Sleep in small increments so a shutdown signal is honoured promptly.
        elapsed = 0
        while elapsed < wait_seconds and not self._shutdown:
            await asyncio.sleep(min(5, wait_seconds - elapsed))
            elapsed += 5

        if self._shutdown:
            # Don't commit — message will be re-delivered after restart.
            logger.info(
                "Shutdown during backoff — not committing offset for %s/%s",
                bucket, key,
            )
            return

        # ── Re-publish original payload to the main topic ─────────────────────
        original_payload_str = envelope.get("original_payload", "")
        if not original_payload_str:
            logger.error(
                "DLQ envelope missing original_payload — permanently failing bucket=%s key=%s",
                bucket, key,
            )
            self._record_permanent_failure(envelope)
            await asyncio.to_thread(self.consumer.commit, msg)
            return

        try:
            original_bytes = original_payload_str.encode("utf-8")
            self.producer.produce(KAFKA_TOPIC, value=original_bytes)
            self.producer.flush(timeout=10)
            logger.info(
                "Re-queued to main topic (attempt %d/%d) — bucket=%s key=%s",
                retry_count + 1, MAX_RETRY_ATTEMPTS, bucket, key,
            )
        except Exception as produce_exc:
            logger.error(
                "Failed to re-publish to main topic — bucket=%s key=%s: %s",
                bucket, key, produce_exc,
            )
            # Don't commit — leave the DLQ message for the next poll cycle.
            return

        # ── Update the retry envelope on the DLQ ─────────────────────────────
        # Publishing the updated envelope back to the DLQ means that if this
        # retry also fails, the ingestion worker's next DLQ publish will carry
        # the incremented count automatically (the DLQ consumer group advances
        # past the old message so it won't be processed twice).
        updated_envelope = {
            **envelope,
            "retry_count":      retry_count + 1,
            "last_retried_at":  datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.producer.produce(
                KAFKA_DLQ_TOPIC,
                value=json.dumps(updated_envelope).encode("utf-8"),
            )
            self.producer.flush(timeout=10)
        except Exception as dlq_exc:
            # Non-fatal — the main-topic re-publish already succeeded.
            logger.warning(
                "Could not update DLQ envelope for %s/%s: %s",
                bucket, key, dlq_exc,
            )

        # ── Commit offset after successful re-publish ──────────────────────────
        await asyncio.to_thread(self.consumer.commit, msg)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Poll the DLQ and dispatch messages to _handle_dlq_message."""
        self._install_signal_handlers()
        logger.info("DLQ retry worker started — polling %s", KAFKA_DLQ_TOPIC)

        try:
            while not self._shutdown:
                msg = await asyncio.to_thread(self.consumer.poll, 2.0)

                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    logger.error("Kafka error: %s", msg.error())
                    continue

                # Process one message at a time — backoff sleeps make
                # parallelism here counter-productive.
                await self._handle_dlq_message(msg)

        finally:
            self.consumer.close()
            self.producer.flush(timeout=15)
            logger.info("DLQ retry worker shut down cleanly.")


if __name__ == "__main__":
    worker = DLQRetryWorker()
    asyncio.run(worker.run())
