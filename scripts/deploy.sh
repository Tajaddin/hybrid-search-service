#!/usr/bin/env bash
# Deploy to the Oracle Cloud free-tier VM: build multi-arch images, push to GHCR, apply the Terraform, copy
# the prepared data and reports to the VM, build the index there and start the API and UI.
# Needs: docker with buildx, gh (logged in), terraform, an OCI API key in infra/terraform/terraform.tfvars.
set -euo pipefail
cd "$(dirname "$0")/.."

OWNER="${GHCR_OWNER:-$(gh api user --jq .login | tr '[:upper:]' '[:lower:]')}"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
TFVARS=infra/terraform/terraform.tfvars
[ -f "$TFVARS" ] || { echo "copy infra/terraform/terraform.tfvars.example to $TFVARS and fill it in"; exit 1; }
[ -f data/prepared/embeddings.npy ] || { echo "run make prepare index first (the embeddings are uploaded, not recomputed on the VM)"; exit 1; }

echo "== images (linux/arm64 for the A1 VM, linux/amd64 for laptops)"
gh auth token | docker login ghcr.io -u "$OWNER" --password-stdin
docker buildx create --use --name hss >/dev/null 2>&1 || docker buildx use hss
docker buildx build --platform linux/arm64,linux/amd64 -t "ghcr.io/$OWNER/hybrid-search-service/api:$TAG" -t "ghcr.io/$OWNER/hybrid-search-service/api:latest" --push .
docker buildx build --platform linux/arm64,linux/amd64 -t "ghcr.io/$OWNER/hybrid-search-service/ui:$TAG" -t "ghcr.io/$OWNER/hybrid-search-service/ui:latest" --push ui

echo "== terraform"
terraform -chdir=infra/terraform init -input=false
terraform -chdir=infra/terraform apply -input=false -auto-approve -var "ghcr_owner=$OWNER" -var "image_tag=$TAG"
IP=$(terraform -chdir=infra/terraform output -raw public_ip)

echo "== waiting for cloud-init on $IP"
for i in $(seq 1 60); do ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "ubuntu@$IP" "cloud-init status --wait >/dev/null 2>&1 && docker ps >/dev/null 2>&1" && break; sleep 10; done

echo "== data and reports"
rsync -az --progress data/prepared/ "ubuntu@$IP:/opt/hss/data/prepared/"
rsync -az reports/*.json "ubuntu@$IP:/opt/hss/reports/" || true
rsync -az load/ "ubuntu@$IP:/opt/hss/load/"

echo "== index and start"
ssh "ubuntu@$IP" "cd /opt/hss && docker compose pull api ui && docker compose --profile jobs run --rm indexer && docker compose up -d api ui"

echo
echo "UI:  http://$IP"
echo "API: http://$IP/api/health"
echo "Tear down with: make teardown"
