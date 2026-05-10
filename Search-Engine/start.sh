#!/bin/bash
# HPE CPP Project - Start script for Linux/Mac

set -e

# Load .env
if [ ! -f .env ]; then
  echo "[!] .env file not found. Copy .env.example to .env and fill in your values."
  exit 1
fi
export $(grep -v '^#' .env | xargs)

# Activate virtualenv
if [ ! -d ".venv" ]; then
  echo "[!] Virtual environment not found. Run: python3 -m venv .venv && pip install -r requirements.txt"
  exit 1
fi
source .venv/bin/activate

echo "================================================"
echo "  HPE Object Storage Search Engine"
echo "================================================"
echo ""

# Kill any previous instances
pkill -f "indexer.py" 2>/dev/null && echo "[~] Stopped old indexer" || true
pkill -f "api.py"     2>/dev/null && echo "[~] Stopped old API"     || true
pkill -f "search_worker.py" 2>/dev/null && echo "[~] Stopped old search worker" || true
sleep 1

# Start indexer in background, log to file
echo "[*] Starting indexer..."
nohup python3 indexer/indexer.py > logs/indexer.log 2>&1 &
INDEXER_PID=$!
echo "[+] Indexer running (PID $INDEXER_PID)"

# Start API in background, log to file
echo "[*] Starting API..."
nohup python3 api/api.py > logs/api.log 2>&1 &
API_PID=$!
echo "[+] API running (PID $API_PID)"

# Start search worker in background, log to file
echo "[*] Starting Search Worker (gRPC)..."
nohup python3 pyworker/search_worker.py > logs/search_worker.log 2>&1 &
SEARCH_WORKER_PID=$!
echo "[+] Search Worker running (PID $SEARCH_WORKER_PID)"

echo ""
echo "------------------------------------------------"
echo "  Search UI  →  http://localhost:8000"
echo "  API Docs   →  http://localhost:8000/docs"
echo "  gRPC       →  ${GRPC_HOST:-0.0.0.0}:${GRPC_SEARCH_PORT:-50052}"
echo "------------------------------------------------"
echo ""
echo "Logs: logs/indexer.log | logs/api.log | logs/search_worker.log"
echo "To stop: ./stop.sh"
echo ""

# Save PIDs for stop script
mkdir -p logs
echo $INDEXER_PID > logs/indexer.pid
echo $API_PID     > logs/api.pid
echo $SEARCH_WORKER_PID > logs/search_worker.pid
