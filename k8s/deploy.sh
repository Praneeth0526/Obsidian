#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Deploy the HPE Search Engine to Minikube
# =============================================================================
# Usage:
#   ./k8s/deploy.sh           # full deploy
#   ./k8s/deploy.sh --reset   # wipe everything and redeploy from scratch
# =============================================================================
set -euo pipefail

NAMESPACE="hpe-search"
K8S_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${K8S_DIR}/.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# -----------------------------------------------------------------------------
# 0. Preflight
# -----------------------------------------------------------------------------
command -v minikube >/dev/null || die "minikube not found. Install from https://minikube.sigs.k8s.io"
command -v kubectl  >/dev/null || die "kubectl not found."
command -v docker   >/dev/null || die "docker not found."

if [[ "${1:-}" == "--reset" ]]; then
  warn "Resetting namespace ${NAMESPACE}..."
  kubectl delete namespace "${NAMESPACE}" --ignore-not-found
  sleep 5
fi

# -----------------------------------------------------------------------------
# 1. Start Minikube (no-op if already running)
# -----------------------------------------------------------------------------
info "Starting Minikube..."
minikube start \
  --cpus=2 \
  --memory=4096 \
  --disk-size=40g \
  --driver=docker 2>/dev/null || true

# Required for OpenSearch vm.max_map_count sysctl init container
minikube addons enable default-storageclass 2>/dev/null || true

success "Minikube is running."

# -----------------------------------------------------------------------------
# 2. Build custom images inside Minikube's Docker daemon
# -----------------------------------------------------------------------------
info "Pointing Docker to Minikube's daemon..."
eval "$(minikube docker-env)"

info "Building hpe-model-server..."
docker build \
  -t hpe-model-server:latest \
  -f "${REPO_ROOT}/workers/model-server/Dockerfile" \
  "${REPO_ROOT}"

info "Building hpe-ingestion-worker..."
docker build \
  -t hpe-ingestion-worker:latest \
  -f "${REPO_ROOT}/workers/ingestion/Dockerfile" \
  "${REPO_ROOT}"

info "Building hpe-pyworker-2..."
docker build \
  -t hpe-pyworker-2:latest \
  -f "${REPO_ROOT}/Search-Engine/pyworker/Dockerfile" \
  "${REPO_ROOT}/Search-Engine"

info "Building hpe-go-gateway..."
docker build \
  -t hpe-go-gateway:latest \
  -f "${REPO_ROOT}/Search-Engine/gateway/Dockerfile" \
  "${REPO_ROOT}/Search-Engine"

info "Building hpe-frontend..."
docker build \
  -t hpe-frontend:latest \
  -f "${REPO_ROOT}/Search-Engine/frontend/Dockerfile" \
  "${REPO_ROOT}/Search-Engine"

success "All custom images built."

# -----------------------------------------------------------------------------
# 3. Apply manifests
# -----------------------------------------------------------------------------
info "Applying namespace..."
kubectl apply -f "${K8S_DIR}/00-namespace.yaml"

info "Applying ConfigMap..."
kubectl apply -f "${K8S_DIR}/01-configmap.yaml"

info "Uploading OpenSearch index mapping as ConfigMap..."
kubectl create configmap opensearch-index-mapping \
  --from-file=mapping.json="${REPO_ROOT}/infrastructure/opensearch/index-mapping.json" \
  -n "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -

info "Applying PVCs..."
kubectl apply -f "${K8S_DIR}/02-pvcs.yaml"

info "Applying infrastructure..."
kubectl apply -f "${K8S_DIR}/infrastructure/kafka.yaml"
kubectl apply -f "${K8S_DIR}/infrastructure/minio.yaml"
kubectl apply -f "${K8S_DIR}/infrastructure/tika.yaml"
kubectl apply -f "${K8S_DIR}/infrastructure/opensearch.yaml"
kubectl apply -f "${K8S_DIR}/infrastructure/redis.yaml"

# -----------------------------------------------------------------------------
# 4. Wait for infrastructure to be ready
# -----------------------------------------------------------------------------
info "Waiting for Kafka brokers (this may take ~2 min)..."
kubectl rollout status statefulset/kafka -n "${NAMESPACE}" --timeout=180s

info "Creating Kafka topics explicitly to prevent ingestion worker crash..."
kubectl exec kafka-0 -n "${NAMESPACE}" -- /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server localhost:9092 \
  --create --if-not-exists \
  --topic file-upload-events \
  --partitions 3 \
  --replication-factor 3

info "Waiting for OpenSearch..."
kubectl rollout status deployment/opensearch -n "${NAMESPACE}" --timeout=180s

info "Creating OpenSearch index mapping explicitly to prevent ingestion worker crash..."
kubectl exec deployment/opensearch -n "${NAMESPACE}" -- /bin/sh -c '
  if curl -sf -I http://localhost:9200/hpe-search-docs; then
    echo "Index exists, skipping creation."
  else
    echo "Creating index with mapping..."
    curl -sf -X PUT http://localhost:9200/hpe-search-docs \
      -H "Content-Type: application/json" \
      -d @/usr/share/opensearch/data/mapping.json || \
    curl -sf -X PUT http://localhost:9200/hpe-search-docs \
      -H "Content-Type: application/json" \
      -d "$(cat /etc/opensearch-index-mapping/mapping.json 2>/dev/null || echo "{}")"
  fi
' || true

info "Waiting for MinIO..."
kubectl rollout status deployment/minio -n "${NAMESPACE}" --timeout=120s

# -----------------------------------------------------------------------------
# 5. Run init jobs
# -----------------------------------------------------------------------------
info "Running Kafka init job..."
kubectl delete -f "${K8S_DIR}/infrastructure/kafka-init-job.yaml" --ignore-not-found
kubectl apply -f "${K8S_DIR}/infrastructure/kafka-init-job.yaml"
kubectl wait --for=condition=complete job/kafka-init -n "${NAMESPACE}" --timeout=120s

info "Running MinIO init job..."
kubectl delete -f "${K8S_DIR}/infrastructure/minio-init-job.yaml" --ignore-not-found
kubectl apply -f "${K8S_DIR}/infrastructure/minio-init-job.yaml"
kubectl wait --for=condition=complete job/minio-init -n "${NAMESPACE}" --timeout=120s

info "Running OpenSearch init job..."
kubectl delete -f "${K8S_DIR}/infrastructure/opensearch-init-job.yaml" --ignore-not-found
kubectl apply -f "${K8S_DIR}/infrastructure/opensearch-init-job.yaml"
kubectl wait --for=condition=complete job/opensearch-init -n "${NAMESPACE}" --timeout=120s

# -----------------------------------------------------------------------------
# 6. Deploy application services
# -----------------------------------------------------------------------------
info "Deploying ingestion pipeline..."
kubectl apply -f "${K8S_DIR}/ingestion/model-server.yaml"
kubectl apply -f "${K8S_DIR}/ingestion/ingestion-worker.yaml"

info "Deploying search pipeline..."
kubectl apply -f "${K8S_DIR}/search/pyworker.yaml"
kubectl apply -f "${K8S_DIR}/search/go-gateway.yaml"
kubectl apply -f "${K8S_DIR}/search/frontend.yaml"

# Force pods to pick up newly-built local images (imagePullPolicy: Never + mutable :latest tag
# means Kubernetes won't restart pods automatically when the local image changes).
info "Rolling out updated images..."
kubectl rollout restart deployment/model-server    -n "${NAMESPACE}"
kubectl rollout restart deployment/ingestion-worker -n "${NAMESPACE}"
kubectl rollout restart deployment/pyworker        -n "${NAMESPACE}"
kubectl rollout restart deployment/go-gateway      -n "${NAMESPACE}"
kubectl rollout restart deployment/frontend        -n "${NAMESPACE}"
kubectl rollout status  deployment/frontend        -n "${NAMESPACE}" --timeout=120s

# -----------------------------------------------------------------------------
# 7. Summary
# -----------------------------------------------------------------------------
echo ""
success "=== Deployment complete ==="
echo ""
MINIKUBE_IP=$(minikube ip)
echo -e "  ${CYAN}Frontend:${NC}              http://${MINIKUBE_IP}:30300"
echo -e "  ${CYAN}Go Gateway (API):${NC}      http://${MINIKUBE_IP}:30080"
echo -e "  ${CYAN}MinIO Console:${NC}         http://${MINIKUBE_IP}:30901"
echo -e "  ${CYAN}OpenSearch Dashboards:${NC} http://${MINIKUBE_IP}:30601"
echo ""
echo -e "  Watch pods:  ${YELLOW}kubectl get pods -n ${NAMESPACE} -w${NC}"
echo -e "  View logs:   ${YELLOW}kubectl logs -n ${NAMESPACE} deploy/<name> -f${NC}"
echo ""
