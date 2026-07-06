#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deploy-ha.sh [options]

Copy custom_components/hanchuess to a Home Assistant instance via SFTP.
Before deploy, calls backup-ha.sh to download a timestamped backup of remote-dir.
Can be run on Windows using Git Bash.

Options:
  -H, --host HOST              Target host (default: 192.168.0.110)
  -u, --user USER              SSH user (default: root)
  -P, --port PORT              SSH port (default: 22)
  -l, --local-dir PATH         Local source directory (default: <repo>/custom_components/hanchuess)
  -r, --remote-dir PATH        Remote target directory (default: homeassistant/custom_components/hanchuess)
  -b, --backup-root PATH       Local backup root (default: <repo>/.ha-deploy-backups)
      --skip-backup            Skip the pre-deploy backup step
  -p, --password PASSWORD      Password for non-interactive mode (requires sshpass)
      --help                   Show this help

Environment:
  HANCHUESS_SFTP_PASSWORD      Alternative to --password (requires sshpass)

Examples:
  bash tools/deploy-ha.sh --host 192.168.0.110 --user root
  bash tools/deploy-ha.sh --host 192.168.0.110 --user root --password "your_password"
  bash tools/deploy-ha.sh --host 192.168.0.110 --user root --local-dir "/c/Projects/hanchu-ess-ha/custom_components/hanchuess" --remote-dir "homeassistant/custom_components/hanchuess"
  bash tools/deploy-ha.sh --skip-backup
EOF
}

if ! command -v sftp >/dev/null 2>&1; then
  echo "Error: sftp not found in PATH." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

HOST="192.168.0.110"
USER="root"
PORT="22"
LOCAL_DIR="$REPO_ROOT/custom_components/hanchuess"
REMOTE_DIR="homeassistant/custom_components/hanchuess"
BACKUP_ROOT="$REPO_ROOT/.ha-deploy-backups"
SKIP_BACKUP="false"
PASSWORD="${HANCHUESS_SFTP_PASSWORD:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -H|--host)
      HOST="$2"
      shift 2
      ;;
    -u|--user)
      USER="$2"
      shift 2
      ;;
    -P|--port)
      PORT="$2"
      shift 2
      ;;
    -l|--local-dir)
      LOCAL_DIR="$2"
      shift 2
      ;;
    -r|--remote-dir)
      REMOTE_DIR="$2"
      shift 2
      ;;
    -b|--backup-root)
      BACKUP_ROOT="$2"
      shift 2
      ;;
    --skip-backup)
      SKIP_BACKUP="true"
      shift
      ;;
    -p|--password)
      PASSWORD="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! -d "$LOCAL_DIR" ]]; then
  echo "Error: local directory does not exist: $LOCAL_DIR" >&2
  exit 1
fi

BACKUP_SCRIPT="$SCRIPT_DIR/backup-ha.sh"

REMOTE_DIR_SFTP="${REMOTE_DIR//\\//}"
LOCAL_DIR_SFTP="$(cd "$LOCAL_DIR" && pwd)"

BATCH_FILE="$(mktemp)"
cleanup() {
  rm -f "$BATCH_FILE"
}
trap cleanup EXIT

run_sftp_batch() {
  local batch_file="$1"
  local target="${USER}@${HOST}"
  local sftp_args=(-oBatchMode=no -P "$PORT" -b "$batch_file" "$target")
  if [[ -n "$PASSWORD" ]]; then
    if ! command -v sshpass >/dev/null 2>&1; then
      echo "Error: --password/HANCHUESS_SFTP_PASSWORD requires sshpass." >&2
      echo "Install sshpass or omit password to use normal interactive prompting." >&2
      exit 1
    fi
    SSHPASS="$PASSWORD" sshpass -e sftp "${sftp_args[@]}"
  else
    sftp "${sftp_args[@]}"
  fi
}

cat > "$BATCH_FILE" <<EOF
lcd "$LOCAL_DIR_SFTP"
cd "$REMOTE_DIR_SFTP"
put -r *
EOF

if [[ "$SKIP_BACKUP" != "true" ]]; then
  if [[ ! -f "$BACKUP_SCRIPT" ]]; then
    echo "Error: backup script not found: $BACKUP_SCRIPT" >&2
    exit 1
  fi

  BACKUP_ARGS=(
    --host "$HOST"
    --user "$USER"
    --port "$PORT"
    --remote-dir "$REMOTE_DIR"
    --backup-root "$BACKUP_ROOT"
  )
  if [[ -n "$PASSWORD" ]]; then
    BACKUP_ARGS+=(--password "$PASSWORD")
  fi

  bash "$BACKUP_SCRIPT" "${BACKUP_ARGS[@]}"
else
  echo "Skipping pre-deploy backup."
fi

run_sftp_batch "$BATCH_FILE"
