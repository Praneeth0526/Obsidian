#!/usr/bin/env bash

NAMESPACE="hpe-search"

echo -e "\n\033[1;36mStarting port-forwarding for all HPE Search Engine UIs...\033[0m\n"

# Forwarding processes in the background
kubectl port-forward -n ${NAMESPACE} svc/frontend 30300:3000 > /dev/null 2>&1 &
PID_FRONTEND=$!

kubectl port-forward -n ${NAMESPACE} svc/minio-console 30901:9001 > /dev/null 2>&1 &
PID_MINIO=$!

kubectl port-forward -n ${NAMESPACE} svc/opensearch-dashboards 30601:5601 > /dev/null 2>&1 &
PID_OSD=$!

kubectl port-forward -n ${NAMESPACE} svc/go-gateway 30080:8080 > /dev/null 2>&1 &
PID_API=$!

echo -e "\033[0;32m✓ Port-forwarding is active! Access these URLs on your local PC (via SSH Tunnel or VS Code):\033[0m"
echo "  🌐 Frontend:              http://localhost:30300"
echo "  🔌 Go Gateway (API):      http://localhost:30080"
echo "  🪣  MinIO Console:         http://localhost:30901"
echo "  📊 OpenSearch Dashboards: http://localhost:30601"
echo ""
echo -e "\033[1;33mPress Ctrl+C to stop all port-forwarding.\033[0m"

# Trap Ctrl+C (SIGINT) to cleanly shut down all background forwards
trap "echo -e '\nStopping all port-forwards...'; kill $PID_FRONTEND $PID_MINIO $PID_OSD $PID_API 2>/dev/null; exit 0" SIGINT SIGTERM

# Wait indefinitely until interrupted
wait
