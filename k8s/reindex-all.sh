#!/bin/bash
set -euo pipefail

NAMESPACE="hpe-search"

echo "Wiping OpenSearch index 'hpe-search-docs'..."
kubectl exec -n "$NAMESPACE" deploy/opensearch -- curl -s -X DELETE localhost:9200/hpe-search-docs || true

echo "Recreating OpenSearch index 'hpe-search-docs' with correct mapping..."
kubectl delete job/opensearch-init -n "$NAMESPACE" --ignore-not-found
kubectl apply -f k8s/infrastructure/opensearch-init-job.yaml
echo "Waiting for opensearch-init job to recreate index..."
kubectl wait --for=condition=complete job/opensearch-init -n "$NAMESPACE" --timeout=120s

# Scale down ingestion worker
echo "Scaling down ingestion worker..."
kubectl scale deploy/ingestion-worker -n "$NAMESPACE" --replicas=0
kubectl wait --for=delete pod -l app=ingestion-worker -n "$NAMESPACE" --timeout=60s

# Reset offsets
echo "Resetting Kafka offsets for consumer group 'ingestion-worker' to earliest..."
kubectl exec -n "$NAMESPACE" kafka-0 -- /opt/kafka/bin/kafka-consumer-groups.sh \
  --bootstrap-server localhost:9092 \
  --group ingestion-worker \
  --topic file-upload-events \
  --reset-offsets --to-earliest --execute

# Scale up ingestion worker
echo "Scaling up ingestion worker..."
kubectl scale deploy/ingestion-worker -n "$NAMESPACE" --replicas=1

echo "[OK] Index recreation and offset reset complete."
