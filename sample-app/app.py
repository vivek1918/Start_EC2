"""
Sample web application used to test EC2 start + application health checks.
It requires a MySQL-compatible database (MariaDB/MySQL) and refuses to run
without one.

Endpoints:
    GET /health  -> 200 {"status": "UP", "database": "UP"}
                    503 {"status": "DOWN", "database": "DOWN", ...} if the DB is unreachable
    GET /        -> records a visit in the database and returns basic info

Configuration (environment variables):
    APP_PORT         port for `python app.py` (default 8080)
    DB_HOST          default 127.0.0.1
    DB_PORT          default 3306
    DB_NAME          default sampleapp
    DB_USER          default sampleapp
    DB_PASSWORD      default empty
    DB_WAIT_TIMEOUT  seconds `--check-db` waits for the database (default 60)

Run modes:
    python app.py --check-db   wait for the database; exit 1 if unreachable
                               (systemd ExecStartPre, so the app never starts without a DB)
    gunicorn "app:create_app()"  production server; startup fails if the DB is unreachable
"""

import argparse
import os
import socket
import sys
import time
from datetime import datetime, timezone

import pymysql
from flask import Flask, jsonify

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "database": os.environ.get("DB_NAME", "sampleapp"),
    "user": os.environ.get("DB_USER", "sampleapp"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "connect_timeout": 3,
    "read_timeout": 3,
    "write_timeout": 3,
    "autocommit": True,
}

STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_connect():
    return pymysql.connect(**DB_CONFIG)


def database_version():
    """Run a query against the database. Raises pymysql.MySQLError if it fails."""
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT VERSION()")
        return cur.fetchone()[0]


def init_schema():
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS visits ("
            " id INT AUTO_INCREMENT PRIMARY KEY,"
            " host VARCHAR(255) NOT NULL,"
            " visited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            ")"
        )


def wait_for_database(timeout):
    """Return True once the database answers, False if it never does within timeout."""
    target = f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
    deadline = time.monotonic() + timeout
    attempt = 0

    while True:
        attempt += 1
        try:
            version = database_version()
            print(f"Database connection OK: {target} (version {version})", flush=True)
            return True
        except pymysql.MySQLError as exc:
            print(f"Attempt {attempt}: database {target} not reachable: {exc}", flush=True)

        if time.monotonic() >= deadline:
            print(
                f"Database {target} not reachable after {timeout}s; "
                "refusing to start the application.",
                file=sys.stderr,
                flush=True,
            )
            return False

        time.sleep(2)


def create_app():
    """Build the Flask app. Fails (raises) if the database is unreachable."""
    init_schema()

    app = Flask(__name__)

    @app.get("/health")
    def health():
        try:
            database_version()
        except pymysql.MySQLError as exc:
            app.logger.error("Health check: database unreachable: %s", exc)
            return jsonify(status="DOWN", database="DOWN"), 503
        return jsonify(status="UP", database="UP")

    @app.get("/")
    def index():
        host = socket.gethostname()
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO visits (host) VALUES (%s)", (host,))
            cur.execute("SELECT COUNT(*) FROM visits")
            visits = cur.fetchone()[0]
        return jsonify(
            app="sample-app",
            host=host,
            started_at=STARTED_AT,
            database_version=database_version(),
            visits=visits,
        )

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sample app with database check.")
    parser.add_argument(
        "--check-db",
        action="store_true",
        help="Wait for the database and exit 0 if reachable, 1 if not.",
    )
    args = parser.parse_args()

    if args.check_db:
        sys.exit(0 if wait_for_database(int(os.environ.get("DB_WAIT_TIMEOUT", "60"))) else 1)

    create_app().run(host="0.0.0.0", port=int(os.environ.get("APP_PORT", "8080")))
