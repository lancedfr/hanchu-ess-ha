#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deploy-ha.sh [options]

Copy custom_components/hanchuess to a Home Assistant instance via SFTP.

Options:
  -H, --host HOST              Target host (default: 192.168.0.110)
  -u, --user USER              SSH user (default: root)
  -P, --port PORT              SSH port (default: 22)
  -l, --local-dir PATH         Local source directory (default: <repo>/custom_components/hanchuess)
  -r, --remote-dir PATH        Remote target directory (default: homeassistant/custom_components/hanchuess)
  -p, --password PASSWORD      Password for non-interactive mode (requires sshpass)
      --help                   Show this help

Environment:
  HANCHUESS_SFTP_PASSWORD      Alternative to --password (requires sshpass)
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

LOCAL_DIR_SFTP="$(cd "$LOCAL_DIR" && pwd)"
REMOTE_DIR_SFTP="${REMOTE_DIR//\\//}"

BATCH_FILE="$(mktemp)"
cleanup() {
  rm -f "$BATCH_FILE"
}
trap cleanup EXIT

cat > "$BATCH_FILE" <<EOF
lcd "$LOCAL_DIR_SFTP"
cd "$REMOTE_DIR_SFTP"
put -r *
EOF

TARGET="${USER}@${HOST}"
SFTP_ARGS=(-oBatchMode=no -P "$PORT" -b "$BATCH_FILE" "$TARGET")

if [[ -n "$PASSWORD" ]]; then
  if ! command -v sshpass >/dev/null 2>&1; then
    echo "Error: --password/HANCHUESS_SFTP_PASSWORD requires sshpass." >&2
    echo "Install sshpass or omit password to use normal interactive prompting." >&2
    exit 1
  fi
  SSHPASS="$PASSWORD" sshpass -e sftp "${SFTP_ARGS[@]}"
else
  sftp "${SFTP_ARGS[@]}"
fi

