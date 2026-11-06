#!/usr/bin/env python3
"""Tiny Alertmanager webhook sink (python stdlib only).

Stands in for a webhook receiver (Forseti intake, legacy SCOM bridge) until the
real one exists. Every POST body is logged to stdout as ONE JSON line:

    {"sink": "<SINK_NAME>", "received_at": "...", "path": "/alerts", "payload": {...}}

so `kubectl logs deploy/sink-forseti | jq` works. The last MAX_KEEP payloads are
also kept in memory:

    GET    /payloads[?limit=N]   -> JSON list, newest last
    DELETE /payloads             -> clear
    GET    /healthz              -> ok
"""

import json
import os
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SINK_NAME = os.environ.get("SINK_NAME", "sink")
PORT = int(os.environ.get("PORT", "8080"))
MAX_KEEP = int(os.environ.get("MAX_KEEP", "500"))

_lock = threading.Lock()
_payloads: deque = deque(maxlen=MAX_KEEP)


class Handler(BaseHTTPRequestHandler):
    server_version = "webhook-sink/1"

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"null")
        except json.JSONDecodeError:
            payload = {"_unparsed": raw.decode("utf-8", "replace")}
        entry = {
            "sink": SINK_NAME,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "path": self.path,
            "payload": payload,
        }
        with _lock:
            _payloads.append(entry)
        sys.stdout.write(json.dumps(entry, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        self._send(200, {"success": True, "result": "received", "error": None})

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/healthz":
            self._send(200, b"ok", "text/plain")
            return
        if url.path == "/payloads":
            limit = int(parse_qs(url.query).get("limit", [MAX_KEEP])[0])
            with _lock:
                items = list(_payloads)[-limit:]
            self._send(200, items)
            return
        self._send(404, {"success": False, "result": None, "error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/payloads":
            with _lock:
                _payloads.clear()
            self._send(200, {"success": True, "result": "cleared", "error": None})
            return
        self._send(404, {"success": False, "result": None, "error": "not found"})

    def log_message(self, fmt, *args) -> None:
        # Keep stdout for payload lines only; access log goes to stderr.
        if not self.path.startswith("/healthz"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    print(f"webhook sink '{SINK_NAME}' listening on :{PORT}", file=sys.stderr, flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
