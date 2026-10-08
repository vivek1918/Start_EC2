#!/usr/bin/env bash
#
# Install the sample app on a Linux EC2 instance as a systemd service that
# starts automatically on every boot (including EC2 stop/start).
#
# Usage (on the instance, from the copied sample-app folder):
#     sudo bash deploy/linux/install.sh [PORT]
#
# PORT defaults to 80. Safe to re-run; it updates the app and restarts it.

set -euo pipefail

APP_NAME="sample-app"
APP_USER="sampleapp"
APP_PORT="${1:-${APP_PORT:-80}}"
INSTALL_DIR="/opt/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SRC="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ ${EUID} -ne 0 ]]; then
    echo "Run as root: sudo bash $0 [PORT]" >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemd is required but systemctl was not found." >&2
    exit 1
fi

python_venv_works() {
    local tmp rc
    tmp="$(mktemp -d)"
    if python3 -m venv "${tmp}/venv" >/dev/null 2>&1; then rc=0; else rc=1; fi
    rm -rf "${tmp}"
    return "${rc}"
}

install_python() {
    echo "Installing python3 with venv support..."
    if command -v dnf >/dev/null 2>&1; then
        dnf install -y python3 python3-pip
    elif command -v yum >/dev/null 2>&1; then
        yum install -y python3 python3-pip
    elif command -v apt-get >/dev/null 2>&1; then
        apt-get update -y
        DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv
    else
        echo "No supported package manager (dnf, yum, apt-get) found." >&2
        exit 1
    fi
}

health_ok() {
    python3 - "$1" <<'EOF'
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
        sys.exit(0 if response.getcode() == 200 else 1)
except Exception:
    sys.exit(1)
EOF
}

echo "==> Installing ${APP_NAME} on port ${APP_PORT}"

if ! command -v python3 >/dev/null 2>&1 || ! python_venv_works; then
    install_python
fi

if ! id "${APP_USER}" >/dev/null 2>&1; then
    echo "Creating system user ${APP_USER}"
    useradd --system --user-group --no-create-home \
        --shell "$(command -v nologin || echo /bin/false)" "${APP_USER}"
fi

install -d -m 0755 "${INSTALL_DIR}" "${CONFIG_DIR}"
install -m 0644 "${APP_SRC}/app.py" "${APP_SRC}/requirements.txt" "${INSTALL_DIR}/"

echo "Creating virtualenv and installing dependencies"
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"

echo "APP_PORT=${APP_PORT}" > "${CONFIG_DIR}/${APP_NAME}.env"
chmod 0644 "${CONFIG_DIR}/${APP_NAME}.env"

install -m 0644 "${SCRIPT_DIR}/${APP_NAME}.service" "/etc/systemd/system/${APP_NAME}.service"

systemctl daemon-reload
systemctl enable "${APP_NAME}.service"
systemctl restart "${APP_NAME}.service"

HEALTH_URL="http://127.0.0.1:${APP_PORT}/health"
echo "Verifying ${HEALTH_URL}"

for attempt in $(seq 1 15); do
    if health_ok "${HEALTH_URL}"; then
        echo "Service enabled at boot: $(systemctl is-enabled "${APP_NAME}.service")"
        echo "Service state:           $(systemctl is-active "${APP_NAME}.service")"
        echo "Health check:            HTTP 200 from ${HEALTH_URL}"
        echo
        echo "Done. Make sure the instance's security group allows inbound TCP ${APP_PORT}"
        echo "from the Jenkins server so the remote health check can reach it."
        exit 0
    fi
    echo "Attempt ${attempt}: application not ready yet"
    sleep 2
done

echo "Application did not become healthy. Recent logs:" >&2
journalctl -u "${APP_NAME}.service" -n 50 --no-pager >&2 || true
exit 1
