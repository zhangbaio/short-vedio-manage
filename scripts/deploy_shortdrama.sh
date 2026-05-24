#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

./scripts/deploy.sh \
  --host root@8.131.149.195 \
  --remote-dir /www/wwwroot/www.shortdrama.momotools.top/short-vedio-manage \
  --env-file .env.production
