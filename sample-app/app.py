"""
Sample web application used to test EC2 start + application health checks.

Endpoints:
    GET /health  -> 200 {"status": "UP"}
    GET /        -> basic instance information

Port is read from the APP_PORT environment variable (default 8080).
"""

import os
import socket
from datetime import datetime, timezone

from flask import Flask, jsonify

app = Flask(__name__)

STARTED_AT = datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.get("/health")
def health():
    return jsonify(status="UP")


@app.get("/")
def index():
    return jsonify(
        app="sample-app",
        host=socket.gethostname(),
        started_at=STARTED_AT,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("APP_PORT", "8080")))
