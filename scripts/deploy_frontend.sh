#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="${1:-quiet-atlas}"
REGION="${AWS_REGION:-us-east-1}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

api_url="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' --output text)"
bucket="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" --query 'Stacks[0].Outputs[?OutputKey==`FrontendBucketName`].OutputValue' --output text)"

if [[ -z "$api_url" || -z "$bucket" || "$api_url" == "None" || "$bucket" == "None" ]]; then
  echo "Could not find ApiUrl and FrontendBucketName outputs for stack $STACK_NAME" >&2
  exit 1
fi

printf 'window.QUIET_ATLAS_CONFIG = { apiUrl: "%s" };\n' "$api_url" > "$ROOT_DIR/frontend/config.js"
aws s3 sync "$ROOT_DIR/frontend" "s3://$bucket" --region "$REGION" --exclude 'config.example.js' --delete
printf 'Published %s to s3://%s\n' "$api_url" "$bucket"
