#!/usr/bin/env bash
set -euo pipefail

TARGET_CONTAINER="${TARGET_CONTAINER:-${1:-IntelliBot}}"
SOURCE_CONTAINER="${SOURCE_CONTAINER:-demodebt}"
# Default to the official Ubuntu Server 24.04 LTS cloud image
LXC_IMAGE="${LXC_IMAGE:-ubuntu:24.04}"
HOST_PROJECT_PATH="${HOST_PROJECT_PATH:-}"
CONTAINER_PROJECT_PATH="${CONTAINER_PROJECT_PATH:-/workspace/IntelliBot}"
DISK_DEVICE_NAME="${DISK_DEVICE_NAME:-code}"
PORT_DEVICE_NAME="${PORT_DEVICE_NAME:-http}"
HOST_PORT="${HOST_PORT:-5000}"
CONTAINER_PORT="${CONTAINER_PORT:-5000}"
INSTALL_POSTGRES="${INSTALL_POSTGRES:-false}"
GIT_REPO_URL="${GIT_REPO_URL:-}"

command -v lxc >/dev/null 2>&1 || {
  echo "Error: lxc command not found. Please install LXD/LXC before running this script." >&2
  exit 1
}

if lxc info "$TARGET_CONTAINER" >/dev/null 2>&1; then
  echo "Container '$TARGET_CONTAINER' already exists. Aborting to avoid overwriting." >&2
  exit 1
fi

echo "Launching container '$TARGET_CONTAINER' from image '$LXC_IMAGE'..."
if ! lxc launch "$LXC_IMAGE" "$TARGET_CONTAINER"; then
  echo "Failed to launch from '$LXC_IMAGE'. Trying Ubuntu images remote fallback..." >&2
  FALLBACK_IMAGE="images:ubuntu/24.04/cloud"
  echo "Launching container '$TARGET_CONTAINER' from fallback image '$FALLBACK_IMAGE'..."
  lxc launch "$FALLBACK_IMAGE" "$TARGET_CONTAINER"
fi

echo "Waiting for container initialization..."
lxc exec "$TARGET_CONTAINER" -- cloud-init status --wait >/dev/null 2>&1 || true

if lxc info "$SOURCE_CONTAINER" >/dev/null 2>&1; then
  echo "Copying configuration from '$SOURCE_CONTAINER'..."
  TMP_CONFIG="$(mktemp)"
  CLEAN_CONFIG="$(mktemp)"
  trap 'rm -f "$TMP_CONFIG" "$CLEAN_CONFIG"' EXIT

  lxc config show "$SOURCE_CONTAINER" --expanded >"$TMP_CONFIG"
  sed '/^name:/d;/^volatile\./d;/^[[:space:]]*volatile\./d' "$TMP_CONFIG" >"$CLEAN_CONFIG"
  lxc config edit "$TARGET_CONTAINER" <"$CLEAN_CONFIG"
else
  echo "Source container '$SOURCE_CONTAINER' not found; using default profile settings."
fi

echo "Enabling autostart for '$TARGET_CONTAINER'..."
lxc config set "$TARGET_CONTAINER" boot.autostart true

if [ -n "$HOST_PROJECT_PATH" ]; then
  echo "Mounting host path '$HOST_PROJECT_PATH' to '$CONTAINER_PROJECT_PATH'..."
  if ! lxc config device list "$TARGET_CONTAINER" | grep -Fxq "$DISK_DEVICE_NAME"; then
    lxc config device add "$TARGET_CONTAINER" "$DISK_DEVICE_NAME" disk source="$HOST_PROJECT_PATH" path="$CONTAINER_PROJECT_PATH"
  else
    echo "Device '$DISK_DEVICE_NAME' already exists; skipping mount." >&2
  fi
fi

if ! lxc config device list "$TARGET_CONTAINER" | grep -Fxq "$PORT_DEVICE_NAME"; then
  echo "Forwarding host port $HOST_PORT to container port $CONTAINER_PORT..."
  lxc config device add "$TARGET_CONTAINER" "$PORT_DEVICE_NAME" proxy listen=tcp:0.0.0.0:"$HOST_PORT" connect=tcp:127.0.0.1:"$CONTAINER_PORT"
else
  echo "Proxy device '$PORT_DEVICE_NAME' already exists; skipping port forward." >&2
fi

echo "Provisioning packages and Python environment inside '$TARGET_CONTAINER'..."
lxc exec "$TARGET_CONTAINER" -- env INSTALL_POSTGRES="$INSTALL_POSTGRES" GIT_REPO_URL="$GIT_REPO_URL" CONTAINER_PROJECT_PATH="$CONTAINER_PROJECT_PATH" bash <<'INNER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get install -y software-properties-common build-essential git curl
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update
apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip
if [[ "${INSTALL_POSTGRES}" == "true" ]]; then
  apt-get install -y libpq-dev postgresql-client postgresql postgresql-contrib
fi

PROJECT_PATH="${CONTAINER_PROJECT_PATH:-/workspace/IntelliBot}"
mkdir -p "$PROJECT_PATH"

if [[ -n "${GIT_REPO_URL}" && ! -d "$PROJECT_PATH/.git" ]]; then
  echo "Cloning repository ${GIT_REPO_URL} into ${PROJECT_PATH}..."
  git clone "$GIT_REPO_URL" "$PROJECT_PATH"
fi

if [[ -f "$PROJECT_PATH/requirements.txt" ]]; then
  python3.11 -m venv "$PROJECT_PATH/.venv"
  source "$PROJECT_PATH/.venv/bin/activate"
  pip install --upgrade pip
  pip install -r "$PROJECT_PATH/requirements.txt"
fi
INNER

trap - EXIT

echo "Container '$TARGET_CONTAINER' has been provisioned."
echo "Remember to configure OPENAI_API_KEY, SESSION_SECRET, AGENT_ID/AGENT_NAME, and (optionally) LEGACY_DATABASE_URL before running the application."
