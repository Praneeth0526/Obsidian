#!/bin/bash
# MinIO Setup Script
# Creates the uploads bucket and configures event notifications to Kafka

set -e

echo "=== MinIO Setup Script ==="

# Wait for MinIO to be ready
echo "Waiting for MinIO to be ready..."
until mc alias set localminio http://localhost:9000 minioadmin minioadmin123 2>/dev/null; do
  echo "MinIO not ready yet, retrying in 5s..."
  sleep 5
done
echo "MinIO is ready!"

# Create uploads bucket
echo "Creating uploads bucket..."
mc mb localminio/uploads --ignore-existing
echo "Bucket 'uploads' created!"

# Configure Kafka notification target
echo "Configuring Kafka notification target..."
mc admin config set localminio notify_kafka:1 \
  brokers="my-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092" \
  topic="file-upload-events"

# Restart MinIO to apply config
echo "Restarting MinIO to apply Kafka config..."
kubectl rollout restart deployment/minio
sleep 10

# Wait for MinIO to come back
until mc alias set localminio http://localhost:9000 minioadmin minioadmin123 2>/dev/null; do
  echo "Waiting for MinIO restart..."
  sleep 5
done

# Add event notification on uploads bucket
echo "Adding s3:ObjectCreated event notification..."
mc event add localminio/uploads arn:minio:sqs::1:kafka --event put

# Verify
echo "Verifying event configuration..."
mc event list localminio/uploads

echo "=== MinIO Setup Complete! ==="
