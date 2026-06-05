#!/bin/bash
# Startup Script — brings up Minikube and all port-forwards automatically
# Run this every time you start a new session

set -e

NAMESPACE="hpe-search"

echo "=== Starting HPE Search Infrastructure ==="

# Start minikube
echo "Starting minikube..."
minikube start --driver=docker

# Wait for core pods to be ready
echo "Waiting for MinIO to be ready..."
kubectl wait --for=condition=ready pod -l app=minio -n "$NAMESPACE" --timeout=120s

echo "Starting port-forwards in background..."

# MinIO S3 API
kubectl port-forward svc/minio 9000:9000 -n "$NAMESPACE" &
MINIO_S3_PID=$!

# MinIO Console
kubectl port-forward svc/minio-console 9001:9001 -n "$NAMESPACE" &
MINIO_UI_PID=$!

# Save PIDs so they can be killed later
echo "$MINIO_S3_PID $MINIO_UI_PID" > /tmp/hpe-port-forward.pids

echo ""
echo "=== Infrastructure Ready! ==="
echo "MinIO S3 API   : http://localhost:9000"
echo "MinIO Console  : http://localhost:9001"
echo ""
echo "Credentials    : minioadmin / minioadmin123"
echo ""
echo "To stop port-forwards later, run: kill \$(cat /tmp/hpe-port-forward.pids)"
