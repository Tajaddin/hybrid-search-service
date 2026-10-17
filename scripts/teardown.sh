#!/usr/bin/env bash
# Destroy the VM and network scripts/deploy.sh created. GHCR images stay (free).
set -euo pipefail
cd "$(dirname "$0")/.."
terraform -chdir=infra/terraform destroy -input=false -auto-approve
echo "destroyed"
