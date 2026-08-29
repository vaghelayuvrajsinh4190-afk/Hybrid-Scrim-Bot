"""
Minimal HTTP server to keep the bot process alive on Render's free tier.

Render expects a "web service" to bind to $PORT and respond to HTTP.
Without this, the bot process (which is otherwise just a WebSocket client)
risks being treated as idle and stopped.

Uses waitress as the WSGI server (same as the old bot) and Flask for the
single-route app, started on a background daemon thread so it doesn't
block the Discord event loop.
"""

from __future__ import annotations

import logging
import os
import threading

from flask import Flask

log = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def index():
    return "Bot is alive.", 200


def start() -> None:
    """Start the keep-alive HTTP server on a background daemon thread.

    Binds to ``$PORT`` (Render injects this) or falls back to 8080.
    """
    port = int(os.environ.get("PORT", 8080))

    def _run():
        from waitress import serve

        log.info("Keep-alive server listening on port %d", port)
        serve(app, host="0.0.0.0", port=port, _quiet=True)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
