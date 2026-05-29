"""
Dead Letter Queue (DLQ) Consumer
Consumes failed messages from file-upload-events-dlq topic
and logs them for inspection and reprocessing.
"""

import json
import logging
from datetime import datetime
from kafka import KafkaConsumer

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Kafka config
BOOTSTRAP_SERVERS = ['my-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092']
DLQ_TOPIC = 'file-upload-events-dlq'
GROUP_ID = 'dlq-consumer-group'

def process_failed_message(message):
    """Log and inspect a failed message from the DLQ."""
    try:
        event = message.value
        logger.error(f"=== FAILED MESSAGE ===")
        logger.error(f"Topic     : {message.topic}")
        logger.error(f"Partition : {message.partition}")
        logger.error(f"Offset    : {message.offset}")
        logger.error(f"Timestamp : {datetime.fromtimestamp(message.timestamp/1000)}")
        logger.error(f"Key       : {message.key}")
        logger.error(f"Value     : {json.dumps(event, indent=2)}")
        logger.error(f"=====================")
    except Exception as e:
        logger.error(f"Error processing DLQ message: {e}")

def main():
    logger.info("Starting DLQ Consumer...")
    logger.info(f"Listening on topic: {DLQ_TOPIC}")

    consumer = KafkaConsumer(
        DLQ_TOPIC,
        bootstrap_servers=BOOTSTRAP_SERVERS,
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='earliest',
        enable_auto_commit=True,
        group_id=GROUP_ID
    )

    logger.info("DLQ Consumer started. Waiting for failed messages...")

    for message in consumer:
        process_failed_message(message)

if __name__ == '__main__':
    main()
