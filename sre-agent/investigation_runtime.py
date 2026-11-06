"""Runtime controls for simple-mode investigations (server_simple.py).

Kept out of server_simple.py so the upstream file only carries thin wiring:

- ConcurrencyGate: global cap on concurrently executing investigations
  (MAX_CONCURRENT_INVESTIGATIONS). Manual runs wait in FIFO order; automatic
  runs use try_acquire() and are rejected when no slot is free.
- RunStatusRegistry: in-memory status of the latest run per thread for
  GET /investigations/{thread_id}. Durable history lives in config-service
  agent_runs; this registry is lost on restart.
- Internal bearer-token check (AGENT_INTERNAL_TOKEN) and the
  {"success", "result", "error"} response envelope.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import re
import secrets
from collections import OrderedDict, deque
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Trigger sources whose runs are started by a machine (no human waiting on the
# stream). They never queue for a slot, run non-interactively, and close their
# SDK session after the run.
AUTOMATIC_TRIGGER_SOURCES = frozenset({"alert"})

DEFAULT_MAX_CONCURRENT_INVESTIGATIONS = 4
DEFAULT_SESSION_IDLE_TTL_SECONDS = 1800
MAX_METADATA_BYTES = 16 * 1024
# Caller-supplied thread ids end up in filesystem paths
# (/tmp/sessions/<thread_id>) and in agent_runs.correlation_id (varchar 255).
THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")

# Machine-readable rejection reasons (result.reason in the envelope).
REASON_CONCURRENCY_LIMIT = "concurrency_limit"
REASON_THREAD_ACTIVE = "thread_active"
REASON_UNAUTHORIZED = "unauthorized"
REASON_INVALID_REQUEST = "invalid_request"
REASON_NOT_FOUND = "not_found"
REASON_NOT_CONFIGURED = "not_configured"


def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    """Integer env var; invalid or below ``minimum`` falls back to ``default``."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("[RUNTIME] %s=%r is not an integer; using %s", name, raw, default)
        return default
    if value < minimum:
        logger.warning(
            "[RUNTIME] %s=%s is below %s; using %s", name, value, minimum, default
        )
        return default
    return value


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def is_automatic(trigger_source: Optional[str]) -> bool:
    return (trigger_source or "").strip().lower() in AUTOMATIC_TRIGGER_SOURCES


# ---------------------------------------------------------------------------
# Concurrency gate
# ---------------------------------------------------------------------------


class ConcurrencyGate:
    """Counting gate with FIFO waiters and direct slot hand-off.

    ``release()`` hands the freed slot straight to the oldest waiter, so a
    ``try_acquire()`` arriving at the same moment cannot jump the queue: while
    anyone waits, ``active == limit`` and ``try_acquire()`` returns False.
    Single event loop only (no thread safety needed in asyncio code).
    """

    def __init__(self, limit: int):
        self.limit = max(1, int(limit))
        self._active = 0
        self._waiters: deque[asyncio.Future] = deque()

    @property
    def active(self) -> int:
        return self._active

    @property
    def waiting(self) -> int:
        return sum(1 for f in self._waiters if not f.done())

    def snapshot(self) -> dict:
        return {"limit": self.limit, "active": self.active, "waiting": self.waiting}

    def try_acquire(self) -> bool:
        if self._active < self.limit and not self.waiting:
            self._active += 1
            return True
        return False

    async def acquire(self) -> None:
        """Wait (FIFO) for a slot. Cancel-safe: a cancelled waiter frees its turn."""
        if self.try_acquire():
            return
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                # The slot was handed to us just before the cancel landed.
                self.release()
            else:
                try:
                    self._waiters.remove(fut)
                except ValueError:
                    pass
            raise

    def release(self) -> None:
        while self._waiters:
            fut = self._waiters.popleft()
            if not fut.done():
                fut.set_result(True)  # slot transferred; active unchanged
                return
        if self._active > 0:
            self._active -= 1


# ---------------------------------------------------------------------------
# Run status registry
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted", "timeout"})


class RunStatusRegistry:
    """Latest run status per thread (in-memory, bounded)."""

    def __init__(self, max_entries: int = 2000):
        self.max_entries = max_entries
        self._records: "OrderedDict[str, dict]" = OrderedDict()

    def get(self, thread_id: str) -> Optional[dict]:
        rec = self._records.get(thread_id)
        return dict(rec) if rec is not None else None

    def begin(
        self,
        thread_id: str,
        *,
        status: str,
        run_id: Optional[str] = None,
        trigger_source: Optional[str] = None,
        trigger_actor: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        """Start a new record for the thread's next run (replaces the previous one)."""
        rec = {
            "thread_id": thread_id,
            "run_id": run_id,
            "run_recorded": False,
            "status": status,
            "trigger_source": trigger_source,
            "trigger_actor": trigger_actor,
            "metadata": metadata,
            "queued_at": utc_now_iso(),
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "usage": None,
            "error": None,
        }
        self._records.pop(thread_id, None)
        self._records[thread_id] = rec
        self._prune()
        return rec

    def update(self, thread_id: str, **fields: Any) -> Optional[dict]:
        rec = self._records.get(thread_id)
        if rec is None:
            return None
        rec.update(fields)
        return rec

    def _prune(self) -> None:
        if len(self._records) <= self.max_entries:
            return
        # Drop the oldest terminal records first; never drop an active run.
        for tid in list(self._records):
            if len(self._records) <= self.max_entries:
                break
            if self._records[tid].get("status") in TERMINAL_STATUSES:
                self._records.pop(tid, None)

    def clear(self) -> None:
        self._records.clear()


def usage_for_status(turn_usage: Optional[dict]) -> Optional[dict]:
    """Flatten agent.turn_usage_from_snapshots() output for API responses."""
    if not turn_usage:
        return None
    totals = turn_usage.get("totals") or {}
    return {
        "total_cost_usd": turn_usage.get("total_cost_usd"),
        "input_tokens": totals.get("input_tokens"),
        "output_tokens": totals.get("output_tokens"),
        "cache_read_input_tokens": totals.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": totals.get("cache_creation_input_tokens"),
        "model_usage": turn_usage.get("model_usage") or None,
    }


def usage_for_agent_run(turn_usage: Optional[dict]) -> dict:
    """Fields for config-service PATCH /agent-runs/{id} (empty if no usage)."""
    flat = usage_for_status(turn_usage)
    if not flat:
        return {}
    return {
        "cost_usd": flat["total_cost_usd"],
        "input_tokens": flat["input_tokens"],
        "output_tokens": flat["output_tokens"],
        "cache_read_input_tokens": flat["cache_read_input_tokens"],
        "cache_creation_input_tokens": flat["cache_creation_input_tokens"],
        "model_usage": flat["model_usage"],
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def envelope(
    *,
    success: bool,
    result: Any = None,
    error: Optional[str] = None,
    status_code: int = 200,
    headers: Optional[dict] = None,
) -> JSONResponse:
    """Project error format: {"success": bool, "result": ..., "error": "..."}."""
    return JSONResponse(
        status_code=status_code,
        content={"success": success, "result": result, "error": error},
        headers=headers,
    )


def reject(
    status_code: int, reason: str, error: str, headers: Optional[dict] = None, **extra
) -> JSONResponse:
    result = {"reason": reason}
    result.update(extra)
    return envelope(
        success=False,
        result=result,
        error=error,
        status_code=status_code,
        headers=headers,
    )


def internal_auth_error(request: Request) -> Optional[JSONResponse]:
    """Check ``Authorization: Bearer <AGENT_INTERNAL_TOKEN>``.

    Returns None when authorized (or when AGENT_INTERNAL_TOKEN is unset, which
    is the local-dev default), else a 401 envelope. Read per request so the
    secret can be rotated by restarting with a new env and tests can patch it.
    """
    expected = (os.getenv("AGENT_INTERNAL_TOKEN") or "").strip()
    if not expected:
        return None
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if token and secrets.compare_digest(token.encode(), expected.encode()):
        return None
    return reject(
        401,
        REASON_UNAUTHORIZED,
        "Missing or invalid internal token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def validate_metadata(metadata: Any) -> Optional[str]:
    """Return an error message if metadata is not a small JSON object."""
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        return "metadata must be a JSON object"
    try:
        size = len(json.dumps(metadata, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return "metadata must be JSON-serializable"
    if size > MAX_METADATA_BYTES:
        return f"metadata exceeds {MAX_METADATA_BYTES} bytes"
    return None


def validate_thread_id(thread_id: Optional[str]) -> Optional[str]:
    if not thread_id or not THREAD_ID_PATTERN.match(thread_id):
        return (
            "thread_id is required: 1-200 chars of letters, digits, '.', '_', ':' "
            "or '-', starting with a letter or digit"
        )
    return None
