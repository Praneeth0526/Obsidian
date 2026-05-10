#!/bin/bash
# HPE CPP Project - Stop script for Linux/Mac

if [ -f logs/indexer.pid ]; then
  kill $(cat logs/indexer.pid) 2>/dev/null && echo "[+] Indexer stopped" || echo "[-] Indexer already stopped"
  rm logs/indexer.pid
fi

if [ -f logs/api.pid ]; then
  kill $(cat logs/api.pid) 2>/dev/null && echo "[+] API stopped" || echo "[-] API already stopped"
  rm logs/api.pid
fi

if [ -f logs/search_worker.pid ]; then
  kill $(cat logs/search_worker.pid) 2>/dev/null && echo "[+] Search Worker stopped" || echo "[-] Search Worker already stopped"
  rm logs/search_worker.pid
fi
