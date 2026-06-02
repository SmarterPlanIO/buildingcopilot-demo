#!/usr/bin/env bash
# Build et push de l'image MCP PALIM vers ECR.
# A lancer dans AWS CloudShell (Docker preinstalle, creds heritees de la console).
#
#   bash Scripts/mcp_server/build_and_push.sh [tag]   # tag par defaut: v1
#
# Etapes : vendorise dossiers_api.py + rerank.py (artefacts de build, gitignores),
# cree le repo ECR si absent, build linux/amd64 (= archi Lambda par defaut),
# tag et push. Idempotent : relancable a volonte.
set -euo pipefail

ACCOUNT="046004768626"
REGION="eu-west-1"
REPO="palim-mcp"
TAG="${1:-v1}"
REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

cd "$(dirname "$0")"

echo "==> Vendoring des modules de 'Streamlit Cloud'"
cp "../Streamlit Cloud/dossiers_api.py" ./dossiers_api_vendored.py
cp "../Streamlit Cloud/rerank.py" ./rerank_vendored.py

echo "==> ECR: repo ${REPO} (creation si absent)"
aws ecr describe-repositories --repository-names "${REPO}" --region "${REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${REPO}" --region "${REGION}" \
       --image-scanning-configuration scanOnPush=true >/dev/null

echo "==> ECR: login"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

echo "==> Build ${REPO}:${TAG} (linux/amd64)"
docker build --platform linux/amd64 -t "${REPO}:${TAG}" .

echo "==> Tag + push vers ${REGISTRY}/${REPO}:${TAG}"
docker tag "${REPO}:${TAG}" "${REGISTRY}/${REPO}:${TAG}"
docker push "${REGISTRY}/${REPO}:${TAG}"

echo "==> OK. Image poussee :"
aws ecr describe-images --repository-name "${REPO}" --region "${REGION}" \
  --image-ids imageTag="${TAG}" \
  --query 'imageDetails[0].{tag:imageTags[0],pushedAt:imagePushedAt,sizeBytes:imageSizeInBytes,digest:imageDigest}' \
  --output table
