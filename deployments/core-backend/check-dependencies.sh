#!/usr/bin/env bash
#
# Gate for scf-core.service (ExecStartPre): the application container is only
# started when Docker, MySQL, Redis and RabbitMQ are all reachable. Exits 1
# (so systemd does not start the app) if any of them is still down after
# DEPENDENCY_TIMEOUT seconds.
#
# Reads MYSQL_ROOT_PASSWORD from the environment (/etc/scf-core/infra.env).

set -uo pipefail

TIMEOUT="${DEPENDENCY_TIMEOUT:-180}"
INTERVAL=5
DEADLINE=$(( $(date +%s) + TIMEOUT ))

log() { echo "[dependency-check] $*"; }

tcp_open() {
    timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/$1" 2>/dev/null
}

check_docker() {
    docker info --format '{{.ServerVersion}}' 2>/dev/null
}

check_mysql() {
    tcp_open 3306 || return 1
    local count
    count="$(docker exec -e MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" scf-mysql \
        mysql -h 127.0.0.1 -uroot -N -e \
        "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN ('scf_core','scf_core_history')" \
        2>/dev/null)" || return 1
    [[ "${count}" == "2" ]] && echo "login OK, schemas scf_core and scf_core_history present"
}

check_redis() {
    tcp_open 6379 || return 1
    local reply
    reply="$(docker exec scf-redis redis-cli ping 2>/dev/null)" || return 1
    [[ "${reply}" == "PONG" ]] && echo "PING -> PONG"
}

check_rabbitmq() {
    tcp_open 5672 || return 1
    docker exec scf-rabbitmq rabbitmq-diagnostics -q check_port_connectivity >/dev/null 2>&1 || return 1
    echo "AMQP port 5672 accepting connections"
}

wait_for() {
    local step="$1" name="$2" fn="$3" detail attempt=0
    while true; do
        attempt=$(( attempt + 1 ))
        if detail="$(${fn})"; then
            log "${step} ${name}: UP (${detail})"
            return 0
        fi
        if (( $(date +%s) >= DEADLINE )); then
            log "${step} ${name}: DOWN after ${TIMEOUT}s"
            return 1
        fi
        log "${step} ${name}: not ready (attempt ${attempt}), retrying in ${INTERVAL}s"
        sleep "${INTERVAL}"
    done
}

log "Checking dependencies before starting scf-core (timeout ${TIMEOUT}s)"

if ! {
    wait_for "[1/4]" "Docker daemon" check_docker &&
    wait_for "[2/4]" "MySQL 127.0.0.1:3306" check_mysql &&
    wait_for "[3/4]" "Redis 127.0.0.1:6379" check_redis &&
    wait_for "[4/4]" "RabbitMQ 127.0.0.1:5672" check_rabbitmq
}; then
    log "RESULT: dependencies not ready - scf-core will NOT be started"
    exit 1
fi

log "RESULT: all dependencies UP - starting scf-core"
