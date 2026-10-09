#!/usr/bin/env bash
#
# Install scf-core (core-backend) on a Linux EC2 instance:
#   docker.service  ->  scf-infra.service (MySQL, Redis, RabbitMQ containers)
#                   ->  scf-core.service  (dependency gate, then app container)
# All three are enabled at boot, so EC2 stop/start brings everything back in order.
#
# Usage (on the instance, from the copied deployments/core-backend folder):
#     sudo bash install.sh [IMAGE_TAR]
#
# IMAGE_TAR: output of `docker save scf-core:local | gzip` (default:
# ./scf-core-image.tar.gz). Omit it on re-runs to keep the current image.
# Safe to re-run; existing passwords and data volumes are kept.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_TAR="${1:-${SCRIPT_DIR}/scf-core-image.tar.gz}"
INSTALL_DIR="/opt/scf-core"
CONFIG_DIR="/etc/scf-core"
INFRA_ENV="${CONFIG_DIR}/infra.env"
APP_ENV="${CONFIG_DIR}/scf-core.env"
APP_PORT=8082
COMPOSE_VERSION="v2.29.7"
APP_TIMEOUT="${APP_TIMEOUT:-600}"

log() { echo "==> $*"; }

if [[ ${EUID} -ne 0 ]]; then
    echo "Run as root: sudo bash $0 [IMAGE_TAR]" >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemd is required but systemctl was not found." >&2
    exit 1
fi

random_secret() {
    head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'
}

env_value() {
    [[ -f "$2" ]] || return 0
    sed -n "s/^$1=//p" "$2" | head -1
}

# ---------------------------------------------------------------------------
# Memory: the app, MySQL and RabbitMQ together need several GB.
# ---------------------------------------------------------------------------

MEM_MB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 ))
if [[ ${MEM_MB} -lt 7000 ]] && ! swapon --show | grep -q .; then
    log "Only ${MEM_MB} MB RAM and no swap: adding a 4 GB swap file"
    fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap defaults 0 0' >> /etc/fstab
fi

# ---------------------------------------------------------------------------
# Docker engine + compose plugin
# ---------------------------------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
    log "Installing Docker"
    if command -v dnf >/dev/null 2>&1; then
        dnf install -y docker
    elif command -v yum >/dev/null 2>&1; then
        yum install -y docker
    elif command -v apt-get >/dev/null 2>&1; then
        apt-get update -y
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
    else
        echo "No supported package manager (dnf, yum, apt-get) found." >&2
        exit 1
    fi
fi

systemctl enable --now docker.service

if ! docker compose version >/dev/null 2>&1; then
    log "Installing docker compose plugin ${COMPOSE_VERSION}"
    case "$(uname -m)" in
        x86_64) ARCH=x86_64 ;;
        aarch64 | arm64) ARCH=aarch64 ;;
        *) echo "Unsupported CPU architecture: $(uname -m)" >&2; exit 1 ;;
    esac
    install -d /usr/local/lib/docker/cli-plugins
    curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
        "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${ARCH}"
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

log "Docker $(docker version --format '{{.Server.Version}}'), $(docker compose version --short 2>/dev/null || echo compose)"

# ---------------------------------------------------------------------------
# Files and configuration
# ---------------------------------------------------------------------------

install -d -m 0755 "${INSTALL_DIR}" "${INSTALL_DIR}/mysql-init"
install -d -m 0700 "${CONFIG_DIR}"
install -m 0644 "${SCRIPT_DIR}/docker-compose.infra.yml" "${INSTALL_DIR}/"
install -m 0644 "${SCRIPT_DIR}/mysql-init/"*.sql "${INSTALL_DIR}/mysql-init/"
install -m 0755 "${SCRIPT_DIR}/check-dependencies.sh" "${INSTALL_DIR}/"

# Keep existing secrets: the MySQL root password is baked into the data volume.
MYSQL_ROOT_PASSWORD="$(env_value MYSQL_ROOT_PASSWORD "${INFRA_ENV}")"
RABBITMQ_PASSWORD="$(env_value RABBITMQ_PASSWORD "${INFRA_ENV}")"
MYSQL_ROOT_PASSWORD="${MYSQL_ROOT_PASSWORD:-$(random_secret)}"
RABBITMQ_PASSWORD="${RABBITMQ_PASSWORD:-$(random_secret)}"

install -m 0600 /dev/null "${INFRA_ENV}"
cat > "${INFRA_ENV}" <<EOF
MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD}
RABBITMQ_USER=scf
RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}
DEPENDENCY_TIMEOUT=180
EOF

# Spring Boot reads these environment variables before application*.properties.
install -m 0600 /dev/null "${APP_ENV}"
cat > "${APP_ENV}" <<EOF
SPRING_PROFILES_ACTIVE=local
SPRING_DATASOURCE_URL=jdbc:mysql://127.0.0.1:3306/scf_core?characterEncoding=UTF-8&allowPublicKeyRetrieval=true&useSSL=false
SPRING_DATASOURCE_USERNAME=root
SPRING_DATASOURCE_PASSWORD=${MYSQL_ROOT_PASSWORD}
SPRING_RABBITMQ_HOST=127.0.0.1
SPRING_RABBITMQ_PORT=5672
SPRING_RABBITMQ_USERNAME=scf
SPRING_RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}
SECURITY_RABBITMQ_HOST=127.0.0.1
SECURITY_RABBITMQ_PORT=5672
SECURITY_RABBITMQ_USERNAME=scf
SECURITY_RABBITMQ_PASSWORD=${RABBITMQ_PASSWORD}
SPRING_DATA_REDIS_HOST=127.0.0.1
SPRING_DATA_REDIS_PORT=6379
SECURITY_REDIS_HOST=127.0.0.1
SECURITY_REDIS_PORT=6379
SCF_CORE_PORT=${APP_PORT}
INTEGRATION_BUS_BASE_URL=http://127.0.0.1:9999
SOA_FORWARDER_URL=http://127.0.0.1:9998
MANAGEMENT_ENDPOINT_HEALTH_SHOWCOMPONENTS=always
JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=50
EOF

# ---------------------------------------------------------------------------
# Application image
# ---------------------------------------------------------------------------

if [[ -f "${IMAGE_TAR}" ]]; then
    log "Loading application image from ${IMAGE_TAR}"
    docker load -i "${IMAGE_TAR}"
    docker tag scf-core:local scf-core:current
elif ! docker image inspect scf-core:current >/dev/null 2>&1; then
    echo "No image found: pass the scf-core image tar as the first argument." >&2
    exit 1
else
    log "No image tar given; keeping existing scf-core:current"
fi

# ---------------------------------------------------------------------------
# systemd units
# ---------------------------------------------------------------------------

install -m 0644 "${SCRIPT_DIR}/scf-infra.service" /etc/systemd/system/scf-infra.service
install -m 0644 "${SCRIPT_DIR}/scf-core.service" /etc/systemd/system/scf-core.service
systemctl daemon-reload
systemctl enable docker.service scf-infra.service scf-core.service

log "Starting infrastructure (waits until MySQL, Redis and RabbitMQ are healthy)"
systemctl restart scf-infra.service
docker compose --env-file "${INFRA_ENV}" -f "${INSTALL_DIR}/docker-compose.infra.yml" ps

log "Starting scf-core (dependency check first)"
systemctl restart scf-core.service
journalctl -u scf-core.service -b --no-pager | grep 'dependency-check' | tail -8 || true

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

HEALTH_URL="http://127.0.0.1:${APP_PORT}/actuator/health"
log "Waiting up to ${APP_TIMEOUT}s for ${HEALTH_URL}"

DEADLINE=$(( $(date +%s) + APP_TIMEOUT ))
while true; do
    BODY="$(curl -s -m 5 -w ' HTTP_%{http_code}' "${HEALTH_URL}" || true)"
    if [[ "${BODY}" == *"HTTP_200" ]]; then
        echo
        echo "docker.service:    $(systemctl is-active docker.service)"
        echo "scf-infra.service: $(systemctl is-active scf-infra.service)"
        echo "scf-core.service:  $(systemctl is-active scf-core.service)"
        echo "Health:            ${BODY% HTTP_200}"
        echo
        echo "Done. Allow inbound TCP ${APP_PORT} in the security group for the Jenkins health check."
        exit 0
    fi
    if (( $(date +%s) >= DEADLINE )); then
        echo "scf-core did not become healthy within ${APP_TIMEOUT}s. Last response: ${BODY}" >&2
        journalctl -u scf-core.service -n 80 --no-pager >&2 || true
        exit 1
    fi
    echo "  not ready yet (${BODY##* })"
    sleep 10
done
