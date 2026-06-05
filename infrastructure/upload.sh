#!/bin/bash
# upload.sh — Upload files to MinIO directly via NodePort (no port-forwarding needed)
#
# Usage:
#   ./infrastructure/upload.sh <local-path> [bucket-name]
#
# Examples:
#   ./infrastructure/upload.sh ./myfile.pdf
#   ./infrastructure/upload.sh ./docs/ my-bucket
#   ./infrastructure/upload.sh ./reports/q1.pdf uploads

set -e

BUCKET="${2:-uploads}"
LOCAL_PATH="${1:-}"

# Resolve MinIO endpoint from minikube NodePort — no port-forward needed
MINIKUBE_IP=$(minikube ip 2>/dev/null)
if [ -z "$MINIKUBE_IP" ]; then
  echo "Error: Could not get minikube IP. Is minikube running?"
  exit 1
fi
ENDPOINT="http://${MINIKUBE_IP}:30900"

# ── validate input ──────────────────────────────────────────────────────────

if [ -z "$LOCAL_PATH" ]; then
  echo "Usage: $0 <local-file-or-folder> [bucket-name]"
  echo "  bucket-name defaults to: uploads"
  exit 1
fi

if [ ! -e "$LOCAL_PATH" ]; then
  echo "Error: '$LOCAL_PATH' does not exist."
  exit 1
fi

echo "MinIO endpoint : $ENDPOINT"
echo "Bucket         : $BUCKET"
echo ""

# ── upload ──────────────────────────────────────────────────────────────────

if [ -d "$LOCAL_PATH" ]; then
  echo "Uploading folder: ${LOCAL_PATH} → s3://${BUCKET}/"
  aws --endpoint-url "${ENDPOINT}" s3 cp "${LOCAL_PATH}" "s3://${BUCKET}/" --recursive
else
  FILENAME=$(basename "$LOCAL_PATH")
  echo "Uploading file: ${LOCAL_PATH} → s3://${BUCKET}/${FILENAME}"
  aws --endpoint-url "${ENDPOINT}" s3 cp "${LOCAL_PATH}" "s3://${BUCKET}/${FILENAME}"
fi

echo ""
echo "Done! Files uploaded to s3://${BUCKET}/"
echo "Listing bucket contents:"
aws --endpoint-url "${ENDPOINT}" s3 ls "s3://${BUCKET}/" --recursive
