#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/deploy.sh --host user@server [options]

Options:
  --host        SSH target, for example root@203.0.113.10
  --remote-dir  Remote project directory, default /opt/short-vedio-manage
  --env-file    Local env file to upload as remote .env
  --identity    SSH private key path
  --port        SSH port, default 22
  -h, --help    Show this help message

Examples:
  cp .env.example .env.production
  ./scripts/deploy.sh --host root@203.0.113.10 --env-file .env.production
EOF
}

log() {
  printf '[deploy] %s\n' "$*"
}

fail() {
  printf '[deploy] %s\n' "$*" >&2
  exit 1
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_DIR="/opt/short-vedio-manage"
HOST=""
ENV_FILE=""
IDENTITY_FILE=""
SSH_PORT="22"
APP_NAME="short-vedio-manage"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      [[ $# -ge 2 ]] || fail "Missing value for --host"
      HOST="$2"
      shift 2
      ;;
    --remote-dir)
      [[ $# -ge 2 ]] || fail "Missing value for --remote-dir"
      REMOTE_DIR="$2"
      shift 2
      ;;
    --env-file)
      [[ $# -ge 2 ]] || fail "Missing value for --env-file"
      ENV_FILE="$2"
      shift 2
      ;;
    --identity)
      [[ $# -ge 2 ]] || fail "Missing value for --identity"
      IDENTITY_FILE="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || fail "Missing value for --port"
      SSH_PORT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "Unknown argument: $1"
      ;;
  esac
done

[[ -n "$HOST" ]] || fail "Missing required argument --host"

for required_cmd in tar scp ssh; do
  command -v "$required_cmd" >/dev/null 2>&1 || fail "Missing required command: $required_cmd"
done

if [[ -n "$ENV_FILE" ]]; then
  [[ -f "$ENV_FILE" ]] || fail "Env file not found: $ENV_FILE"
  ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
fi

SSH_ARGS=(-p "$SSH_PORT")
if [[ -n "$IDENTITY_FILE" ]]; then
  SSH_ARGS+=(-i "$IDENTITY_FILE")
fi
SCP_ARGS=(-P "$SSH_PORT")
if [[ -n "$IDENTITY_FILE" ]]; then
  SCP_ARGS+=(-i "$IDENTITY_FILE")
fi

timestamp="$(date '+%Y%m%d%H%M%S')"
archive_path="$(mktemp "/tmp/${APP_NAME}-${timestamp}.XXXXXX.tgz")"
remote_archive="/tmp/${APP_NAME}-${timestamp}.tgz"

cleanup() {
  rm -f "$archive_path"
}

trap cleanup EXIT

log "Packaging release archive"
tar -C "$ROOT_DIR" \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='.env.local' \
  --exclude='.env.production' \
  --exclude='.env.staging' \
  --exclude='.claude' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='data/*.db' \
  --exclude='data/*.db-shm' \
  --exclude='data/*.db-wal' \
  --exclude='data/remote_uploads' \
  --exclude='*.docx' \
  --exclude='*.xlsx' \
  --exclude='table.png' \
  -czf "$archive_path" .

log "Preparing remote directory"
ssh "${SSH_ARGS[@]}" "$HOST" "mkdir -p '$REMOTE_DIR'"

log "Uploading release archive"
scp "${SCP_ARGS[@]}" "$archive_path" "$HOST:$remote_archive"

if [[ -n "$ENV_FILE" ]]; then
  log "Uploading env file"
  scp "${SCP_ARGS[@]}" "$ENV_FILE" "$HOST:$REMOTE_DIR/.env.upload"
fi

log "Running remote deployment"
ssh "${SSH_ARGS[@]}" "$HOST" \
  "REMOTE_DIR='$REMOTE_DIR' REMOTE_ARCHIVE='$remote_archive' HAVE_ENV_UPLOAD='${ENV_FILE:+1}' bash -se" <<'EOF'
set -Eeuo pipefail

mkdir -p "$REMOTE_DIR" "$REMOTE_DIR/backups" "$REMOTE_DIR/data"

existing_owner=""
if command -v stat >/dev/null 2>&1; then
  existing_owner="$(stat -c '%U:%G' "$REMOTE_DIR" 2>/dev/null || true)"
fi

if [[ "${HAVE_ENV_UPLOAD:-}" == "1" ]]; then
  mv "$REMOTE_DIR/.env.upload" "$REMOTE_DIR/.env"
fi

if [[ ! -f "$REMOTE_DIR/.env" ]]; then
  echo "[deploy] Missing $REMOTE_DIR/.env. Create it from .env.example or rerun with --env-file." >&2
  exit 1
fi

if [[ -f "$REMOTE_DIR/data/dramas.db" ]]; then
  backup_file="$REMOTE_DIR/backups/dramas-$(date '+%Y%m%d%H%M%S').db"
  cp "$REMOTE_DIR/data/dramas.db" "$backup_file"
  echo "[deploy] Database backup created: $backup_file"
fi

tar -xzf "$REMOTE_ARCHIVE" -C "$REMOTE_DIR"
rm -f "$REMOTE_ARCHIVE"

if [[ -n "$existing_owner" ]] && [[ "$existing_owner" != "root:root" ]]; then
  chown -R "$existing_owner" "$REMOTE_DIR"
fi

if docker compose version >/dev/null 2>&1; then
  compose_cmd=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd=(docker-compose)
else
  echo "[deploy] docker compose or docker-compose is required on the remote server." >&2
  exit 1
fi

cd "$REMOTE_DIR"
"${compose_cmd[@]}" up -d --build --remove-orphans
"${compose_cmd[@]}" ps
EOF

log "Deployment completed"
