#!/bin/bash
# Check mapping, delete+recreate if wrong, and reset Kafka offsets

NAMESPACE="hpe-search"

echo "Checking OpenSearch mapping for 'embedding' field..."
MAPPING=$(kubectl exec -n "$NAMESPACE" deploy/opensearch -- curl -s localhost:9200/hpe-search-docs/_mapping)

if echo "$MAPPING" | grep -q '"embedding":{"type":"knn_vector"'; then
  echo "[OK] Index mapping is correct (knn_vector)."
else
  echo "[!] Index mapping is INCORRECT (likely float). Recreating index..."
  
  # 1. Delete index
  kubectl exec -n "$NAMESPACE" deploy/opensearch -- curl -s -X DELETE localhost:9200/hpe-search-docs
  
  # 2. Recreate with correct mapping
  # Since deploy.sh already mounts the configmap, we can use the init job to do this easily, or just curl
  kubectl exec -n "$NAMESPACE" deploy/opensearch -- curl -s -X PUT localhost:9200/hpe-search-docs \
    -H "Content-Type: application/json" \
    -d "$(cat infrastructure/opensearch/index-mapping.json)"
  echo "Index recreated."
  
  # 3. Reset Kafka consumer group offset to earliest
  echo "Resetting Kafka offsets for consumer group 'ingestion-worker-group' to earliest..."
  
  # First, scale down ingestion worker so it's not actively consuming
  kubectl scale deploy/ingestion-worker -n "$NAMESPACE" --replicas=0
  kubectl wait --for=delete pod -l app=ingestion-worker -n "$NAMESPACE" --timeout=60s
  
  # Reset offsets
  kubectl exec -n "$NAMESPACE" kafka-0 -- /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 \
    --group ingestion-worker-group \
    --topic file-upload-events \
    --reset-offsets --to-earliest --execute
    
  # Scale back up
  kubectl scale deploy/ingestion-worker -n "$NAMESPACE" --replicas=1
  echo "[OK] Recovery complete. Ingestion worker will now reprocess all documents."
fi
