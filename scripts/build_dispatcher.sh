#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="${AWS_REGION:-$(aws configure get region)}"
REPOSITORY_NAME="${REPOSITORY_NAME:-aws-job-scraper-dispatcher}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -z "${AWS_REGION}" ]]; then
  echo "AWS_REGION is not set and no default AWS region is configured." >&2
  exit 1
fi

REPOSITORY_URI="$(aws ecr describe-repositories \
  --repository-names "${REPOSITORY_NAME}" \
  --query 'repositories[0].repositoryUri' \
  --output text \
  --region "${AWS_REGION}")"
REGISTRY="${REPOSITORY_URI%/*}"
IMAGE_URI="${REPOSITORY_URI}:${IMAGE_TAG}"

aws ecr get-login-password --region "${AWS_REGION}" |
  docker login --username AWS --password-stdin "${REGISTRY}"

docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  -t "${IMAGE_URI}" \
  --push \
  "${ROOT_DIR}/lambda/dispatcher"

echo "${IMAGE_URI}"
