"""
Blue Horizon Platform backend entry point (Adel).

Run from project root:
    PYTHONPATH=. python -m platform.backend.app

Note: the top-level package is named `platform` per task division.
If this shadows the stdlib `platform` module in some environments,
run the app with the path adjustments below (already applied).
"""

from __future__ import annotations

import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PLATFORM_DIR = os.path.dirname(_BACKEND_DIR)
_ROOT = os.path.dirname(_PLATFORM_DIR)

# Prefer project root for state_graph / mcp_server, but keep stdlib resolvable:
# Package renamed to web_platform to avoid shadowing the stdlib "platform" module.
if _ROOT not in sys.path:
    sys.path.insert(1, _ROOT)

from flask import Flask, jsonify
from flask_cors import CORS

# Relative imports within this package
from web_platform.backend.routes_admin import admin_bp
from web_platform.backend.routes_agents import agents_bp


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    app.register_blueprint(agents_bp)
    app.register_blueprint(admin_bp)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "blue-horizon-platform"})

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=True)
