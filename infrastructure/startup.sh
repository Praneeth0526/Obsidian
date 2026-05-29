#!/bin/bash
# Role 1 - Startup Script
# Run this every time you restart WSL to bring everything back up

set -e

echo "=== Starting Role 1 Infrastructure ==="

# Start minikube
echo "Starting minikube..."
minikube start --driver=docker

# Wait for pods to be ready
echo "Waiting for pods to be ready..."
kubectl wait --for=condition=ready pod -l app=minio --timeout=120s
kubectl wait --for=condition=ready pod -l strimzi.io/cluster=my-cluster -n kafka --timeout=120s

# Start MinIO port-forwards
echo "Starting MinIO port-forwards..."
kubectl port-forward svc/minio 9000:9000 &
kubectl port-forward pod/$(kubectl get pod -l release=minio -o jsonpath="{.items[0].metadata.name}") 9001:9001 &

echo ""
echo "=== Infrastructure Ready! ==="
echo "MinIO S3 API  : http://localhost:9000"
echo "MinIO Console : http://localhost:9001"
echo "Kafka Bootstrap: my-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092"
echo ""
echo "Credentials: minioadmin / minioadmin123"
