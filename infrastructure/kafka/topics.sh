#!/bin/bash
# Kafka Topics Setup Script
# Creates all required topics with correct partition and replication settings

set -e

KAFKA_POD="my-cluster-dual-role-0"
KAFKA_NAMESPACE="kafka"
BOOTSTRAP="localhost:9092"

echo "=== Kafka Topics Setup ==="

# Create file-upload-events topic
echo "Creating file-upload-events topic..."
kubectl exec -it $KAFKA_POD -n $KAFKA_NAMESPACE -- \
  bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --create \
  --if-not-exists \
  --topic file-upload-events \
  --partitions 3 \
  --replication-factor 3

# Create dead-letter-queue topic
echo "Creating dead-letter-queue topic..."
kubectl exec -it $KAFKA_POD -n $KAFKA_NAMESPACE -- \
  bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --create \
  --if-not-exists \
  --topic file-upload-events-dlq \
  --partitions 3 \
  --replication-factor 3

# List all topics to verify
echo "Verifying topics..."
kubectl exec -it $KAFKA_POD -n $KAFKA_NAMESPACE -- \
  bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --list

echo "=== Kafka Topics Setup Complete! ==="
