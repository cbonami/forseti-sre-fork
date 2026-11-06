#!/usr/bin/env python3
"""Synthetic "noisy" Prometheus exporter (python stdlib only).

Exposes metrics shaped to trigger the kinds of alerts an SRE wants to tune away
(see rules/synthetic.yaml):

  (a) per-user alert   synthetic_login_failures_total{service,user_id}
        N_USERS users (default 20) fail logins during the "on" phase of a cycle
        (PER_USER_CYCLE_SECONDS, default 1800 s; on for the first
        PER_USER_ON_SECONDS, default 900 s, starting at process start).
        A rule on rate() per user_id yields one alert per user -> a burst of
        ~20 alerts from one rule with a high-cardinality label.
  (b) flapping alert   synthetic_queue_depth{service,queue}
        square wave: FLAP_PERIOD_SECONDS (default 240 s), high (~150) for the
        first half, low (~20) for the second. Threshold 100 with a short `for`
        -> fire/resolve every few minutes.
  (c) too-low threshold synthetic_disk_usage_ratio{service,mountpoint}
        steady ~0.62 (+/- 0.03). A rule with threshold 0.5 fires forever:
        a classic false positive that should be tuned, not investigated.

GET /metrics -> Prometheus text format; GET /healthz -> ok.
"""

import math
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "9100"))
N_USERS = int(os.environ.get("N_USERS", "20"))
PER_USER_CYCLE = float(os.environ.get("PER_USER_CYCLE_SECONDS", "1800"))
PER_USER_ON = float(os.environ.get("PER_USER_ON_SECONDS", "900"))
FLAP_PERIOD = float(os.environ.get("FLAP_PERIOD_SECONDS", "240"))

START = time.time()
USERS = [f"user-{i:02d}" for i in range(1, N_USERS + 1)]

_lock = threading.Lock()
_last = START
_login_failures = {u: 0.0 for u in USERS}
_login_attempts = {u: 0.0 for u in USERS}


def _per_user_on(now: float) -> bool:
    return ((now - START) % PER_USER_CYCLE) < PER_USER_ON


def _advance(now: float) -> None:
    """Integrate counters from the last call up to `now`."""
    global _last
    dt = max(0.0, now - _last)
    _last = now
    on = _per_user_on(now)
    for i, u in enumerate(USERS):
        _login_attempts[u] += dt * (0.5 + 0.05 * i)
        if on:
            # 0.2 .. ~0.6 failures/s per user while the "incident" lasts
            _login_failures[u] += dt * (0.2 + 0.02 * i)


def render() -> str:
    now = time.time()
    with _lock:
        _advance(now)
        failures = dict(_login_failures)
        attempts = dict(_login_attempts)
    flap_high = ((now - START) % FLAP_PERIOD) < FLAP_PERIOD / 2
    queue_depth = (150 if flap_high else 20) + random.randint(-5, 5)
    disk = 0.62 + 0.03 * math.sin((now - START) / 300.0)

    lines = [
        "# HELP synthetic_exporter_info Synthetic noisy exporter for alert-tuning tests.",
        "# TYPE synthetic_exporter_info gauge",
        'synthetic_exporter_info{version="1"} 1',
        "# HELP synthetic_login_attempts_total Login attempts per user.",
        "# TYPE synthetic_login_attempts_total counter",
    ]
    for u in USERS:
        lines.append(f'synthetic_login_attempts_total{{service="customer-portal",user_id="{u}"}} {attempts[u]:.3f}')
    lines += [
        "# HELP synthetic_login_failures_total Failed logins per user.",
        "# TYPE synthetic_login_failures_total counter",
    ]
    for u in USERS:
        lines.append(f'synthetic_login_failures_total{{service="customer-portal",user_id="{u}"}} {failures[u]:.3f}')
    lines += [
        "# HELP synthetic_queue_depth Messages waiting in the batch queue (square wave).",
        "# TYPE synthetic_queue_depth gauge",
        f'synthetic_queue_depth{{service="order-batcher",queue="orders"}} {queue_depth}',
        "# HELP synthetic_disk_usage_ratio Used/total bytes of the reports volume.",
        "# TYPE synthetic_disk_usage_ratio gauge",
        f'synthetic_disk_usage_ratio{{service="report-generator",mountpoint="/var/lib/reports"}} {disk:.4f}',
    ]
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    server_version = "synthetic-exporter/1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/metrics"):
            body = render().encode()
            ctype = "text/plain; version=0.0.4; charset=utf-8"
            code = 200
        elif self.path.startswith("/healthz"):
            body, ctype, code = b"ok", "text/plain", 200
        else:
            body, ctype, code = b"not found", "text/plain", 404
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:
        pass


if __name__ == "__main__":
    print(f"synthetic exporter on :{PORT} users={N_USERS}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
