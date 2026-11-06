"""server_simple runtime controls, end to end over ASGI with a fake SDK session.

Covers: global concurrency gate (manual waits with a `queued` SSE event,
automatic gets 429), POST /investigations + GET status, metadata/usage
persistence calls to config-service, automatic-session close, idle reaper,
queue-message on a running automatic run, and /investigate backward compat.
"""

import asyncio
import json
import time
from unittest.mock import MagicMock

import httpx
import investigation_runtime as rt
import pytest
import server_simple as srv
from events import StreamEvent, result_event

USAGE = {
    "total_cost_usd": 0.12,
    "usage": {"input_tokens": 100, "output_tokens": 20},
    "model_usage": {"claude-sonnet-4-5": {"inputTokens": 150, "costUSD": 0.12}},
    "totals": {
        "input_tokens": 150,
        "output_tokens": 20,
        "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 0,
    },
}


class FakeSession:
    """Stand-in for agent.InteractiveAgentSession (no CLI subprocess)."""

    instances: list = []
    blockers: dict = {}  # thread_id -> asyncio.Event gating execute()
    fail_start: set = set()

    def __init__(self, thread_id, team_config=None, resume=None, interactive=True):
        self.thread_id = thread_id
        self.interactive = interactive
        self.client = object()
        self.session_id = f"sess-{thread_id}"
        self.is_running = False
        self.cleaned = False
        self.last_turn_usage = None
        self.prompts: list = []
        self.pending: list = []
        FakeSession.instances.append(self)

    async def start(self):
        if self.thread_id in FakeSession.fail_start:
            raise RuntimeError("cli failed to start")

    async def cleanup(self):
        self.cleaned = True
        self.client = None

    def enqueue_message(self, text):
        self.pending.append(text)
        return len(self.pending)

    async def interrupt(self):
        if False:  # pragma: no cover
            yield None

    async def execute(self, prompt, images=None):
        self.is_running = True
        self.prompts.append(prompt)
        try:
            yield StreamEvent(
                type="thought", data={"text": "looking"}, thread_id=self.thread_id
            )
            blocker = FakeSession.blockers.get(self.thread_id)
            if blocker is not None:
                await blocker.wait()
            self.last_turn_usage = USAGE
            yield result_event(
                self.thread_id,
                "Root cause: bad deploy",
                success=True,
                subtype="success",
                usage=USAGE["usage"],
                total_cost_usd=USAGE["total_cost_usd"],
                model_usage=USAGE["model_usage"],
            )
        finally:
            self.is_running = False


def _ok_response(json_body=None):
    resp = MagicMock()
    resp.raise_for_status = lambda: None
    resp.json = lambda: json_body or {}
    return resp


@pytest.fixture
def env(monkeypatch):
    """Isolated server state, limit=1 gate, mocked config-service + memory."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("CONFIG_SERVICE_URL", raising=False)
    monkeypatch.delenv("AGENT_INTERNAL_TOKEN", raising=False)
    monkeypatch.setattr("agent.InteractiveAgentSession", FakeSession)
    FakeSession.instances = []
    FakeSession.blockers = {}
    FakeSession.fail_start = set()

    for name in (
        "_background_tasks",
        "_message_queues",
        "_response_queues",
        "_active_sessions",
        "_team_token_by_thread",
        "_team_identity_by_thread",
        "_run_id_by_thread",
        "_trigger_source_by_thread",
        "_trigger_actor_by_thread",
        "_last_activity_by_thread",
        "_drain_tasks",
    ):
        monkeypatch.setattr(srv, name, {})
    for name in ("_automatic_threads", "_busy_threads", "_all_agent_tasks"):
        monkeypatch.setattr(srv, name, set())
    monkeypatch.setattr(srv, "_run_status", rt.RunStatusRegistry())
    monkeypatch.setattr(srv, "_gate", rt.ConcurrencyGate(1))

    calls = {"post": [], "patch": [], "put": []}

    def fake_post(url, json=None, **kw):
        calls["post"].append((url, json))
        return _ok_response()

    def fake_patch(url, json=None, **kw):
        calls["patch"].append((url, json))
        return _ok_response()

    def fake_put(url, json=None, **kw):
        calls["put"].append((url, json))
        return _ok_response()

    monkeypatch.setattr(srv.httpx, "post", fake_post)
    monkeypatch.setattr(srv.httpx, "patch", fake_patch)
    monkeypatch.setattr(srv.httpx, "put", fake_put)
    monkeypatch.setattr(srv, "_lookup_latest_sdk_session_id", lambda tid: None)
    monkeypatch.setattr(srv, "write_thread_run_id", lambda *a, **k: None)
    monkeypatch.setattr(srv._il, "finalize_investigation", lambda **k: None)
    return calls


def _client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=srv.app), base_url="http://agent"
    )


async def _wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


def _sse_events(body: str) -> list:
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _status(thread_id):
    rec = srv._run_status.get(thread_id)
    return rec and rec["status"]


# ---------------------------------------------------------------------------
# Concurrency gate
# ---------------------------------------------------------------------------


def test_automatic_run_rejected_with_429_when_no_slot(env):
    async def _run():
        FakeSession.blockers["busy-1"] = asyncio.Event()
        async with _client() as c:
            r = await c.post(
                "/investigations",
                json={"prompt": "manual", "thread_id": "busy-1", "trigger_source": "api"},
            )
            assert r.status_code == 202
            await _wait_for(lambda: _status("busy-1") == "running")

            r = await c.post(
                "/investigations",
                json={
                    "prompt": "alert",
                    "thread_id": "alert-1",
                    "trigger_source": "alert",
                    "metadata": {"batch_id": "b1"},
                },
            )
            assert r.status_code == 429
            body = r.json()
            assert body["success"] is False
            assert body["result"]["reason"] == "concurrency_limit"
            assert body["result"]["limit"] == 1
            assert body["error"]
            # Rejection leaves no state behind.
            assert "alert-1" not in srv._background_tasks
            assert srv._run_status.get("alert-1") is None

            # Same rule on the streaming endpoint.
            r = await c.post(
                "/investigate",
                json={"prompt": "alert", "thread_id": "alert-2", "trigger_source": "alert"},
            )
            assert r.status_code == 429
            assert r.json()["result"]["reason"] == "concurrency_limit"

            FakeSession.blockers["busy-1"].set()
            await _wait_for(lambda: _status("busy-1") == "completed")
            assert srv._gate.active == 0

            r = await c.post(
                "/investigations",
                json={"prompt": "alert", "thread_id": "alert-1", "trigger_source": "alert"},
            )
            assert r.status_code == 202
            await _wait_for(lambda: _status("alert-1") == "completed")

    asyncio.run(_run())


def test_manual_run_waits_for_slot_and_streams_queued_event(env):
    async def _run():
        FakeSession.blockers["auto-hold"] = asyncio.Event()
        async with _client() as c:
            r = await c.post(
                "/investigations",
                json={"prompt": "a", "thread_id": "auto-hold", "trigger_source": "alert"},
            )
            assert r.status_code == 202
            await _wait_for(lambda: _status("auto-hold") == "running")

            manual = asyncio.create_task(
                c.post("/investigate", json={"prompt": "why?", "thread_id": "man-1"})
            )
            await _wait_for(lambda: srv._gate.waiting == 1)
            assert _status("man-1") == "queued"
            assert not manual.done()

            FakeSession.blockers["auto-hold"].set()
            resp = await asyncio.wait_for(manual, timeout=5)
            assert resp.status_code == 200
            events = _sse_events(resp.text)
            types = [e["type"] for e in events]
            assert types[0] == "queued"
            assert events[0]["data"] == {"position": 1, "active": 1, "limit": 1}
            assert types.index("queued") < types.index("run_started")
            assert types[-1] == "result"
            assert srv._gate.active == 0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Async start + status
# ---------------------------------------------------------------------------


def test_async_start_returns_immediately_and_status_tracks_run(env):
    async def _run():
        FakeSession.blockers["alert-s"] = asyncio.Event()
        async with _client() as c:
            r = await c.post(
                "/investigations",
                json={
                    "prompt": "KubePodCrashLooping in payments",
                    "thread_id": "alert-s",
                    "trigger_source": "alert",
                    "trigger_actor": "alert-policy:p1",
                    "metadata": {"alert_ids": ["a1"], "policy_id": "p1", "batch_id": "b1"},
                },
            )
            assert r.status_code == 202
            body = r.json()
            assert body["success"] is True and body["error"] is None
            result = body["result"]
            assert result["thread_id"] == "alert-s"
            assert result["status"] == "running"
            assert result["trigger_source"] == "alert"
            run_id = result["run_id"]
            assert run_id

            await _wait_for(lambda: FakeSession.instances)
            s = await c.get("/investigations/alert-s")
            assert s.status_code == 200
            rec = s.json()["result"]
            assert rec["status"] == "running"
            assert rec["run_id"] == run_id
            assert rec["metadata"]["batch_id"] == "b1"
            assert rec["session_active"] is True

            FakeSession.blockers["alert-s"].set()
            await _wait_for(lambda: _status("alert-s") == "completed")
            rec = (await c.get("/investigations/alert-s")).json()["result"]
            assert rec["run_recorded"] is True
            assert rec["started_at"] and rec["completed_at"] and rec["queued_at"]
            assert rec["usage"]["total_cost_usd"] == pytest.approx(0.12)
            assert rec["usage"]["input_tokens"] == 150
            assert rec["error"] is None

            # Automatic thread: session closed after the run, nothing leaked.
            await _wait_for(lambda: FakeSession.instances[0].cleaned)
            await _wait_for(lambda: "alert-s" not in srv._background_tasks)
            rec = (await c.get("/investigations/alert-s")).json()["result"]
            assert rec["session_active"] is False
            assert "alert-s" not in srv._active_sessions
            assert "alert-s" not in srv._automatic_threads
            assert FakeSession.instances[0].interactive is False
            await _wait_for(lambda: "alert-s" not in srv._drain_tasks)

            r404 = await c.get("/investigations/unknown-thread")
            assert r404.status_code == 404
            assert r404.json()["result"]["reason"] == "not_found"

    asyncio.run(_run())


def test_async_start_validation_and_conflicts(env):
    async def _run():
        FakeSession.blockers["t-busy"] = asyncio.Event()
        async with _client() as c:
            bad = await c.post(
                "/investigations", json={"prompt": "x", "thread_id": "../etc"}
            )
            assert bad.status_code == 400
            assert bad.json()["result"]["reason"] == "invalid_request"

            bad = await c.post(
                "/investigations",
                json={"prompt": "x", "thread_id": "t-src", "trigger_source": "pagerduty"},
            )
            assert bad.status_code == 400

            bad = await c.post(
                "/investigations",
                json={"prompt": "x", "thread_id": "t-meta", "metadata": ["not", "obj"]},
            )
            assert bad.status_code == 422  # pydantic: metadata must be an object

            bad = await c.post(
                "/investigations", json={"prompt": "  ", "thread_id": "t-empty"}
            )
            assert bad.status_code == 400

            ok = await c.post(
                "/investigations", json={"prompt": "x", "thread_id": "t-busy"}
            )
            assert ok.status_code == 202
            dup = await c.post(
                "/investigations",
                json={"prompt": "x", "thread_id": "t-busy", "trigger_source": "alert"},
            )
            assert dup.status_code == 409
            assert dup.json()["result"]["reason"] == "thread_active"
            FakeSession.blockers["t-busy"].set()
            await _wait_for(lambda: _status("t-busy") == "completed")

    asyncio.run(_run())


def test_investigate_rejected_while_detached_run_active_then_allowed(env):
    async def _run():
        FakeSession.blockers["t-det"] = asyncio.Event()
        async with _client() as c:
            r = await c.post(
                "/investigations", json={"prompt": "x", "thread_id": "t-det"}
            )
            assert r.status_code == 202
            await _wait_for(lambda: _status("t-det") == "running")
            r = await c.post("/investigate", json={"prompt": "y", "thread_id": "t-det"})
            assert r.status_code == 409
            assert r.json()["result"]["reason"] == "thread_active"

            FakeSession.blockers["t-det"].set()
            await _wait_for(lambda: "t-det" not in srv._drain_tasks)
            FakeSession.blockers.pop("t-det")
            # Manual thread stays warm; a streamed follow-up now works.
            r = await c.post("/investigate", json={"prompt": "y", "thread_id": "t-det"})
            assert r.status_code == 200
            assert [e["type"] for e in _sse_events(r.text)][-1] == "result"
            assert len(FakeSession.instances) == 1  # same session reused

    asyncio.run(_run())


def test_async_endpoints_require_internal_token_when_set(env, monkeypatch):
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "s3cret")

    async def _run():
        async with _client() as c:
            body = {"prompt": "x", "thread_id": "t-auth", "trigger_source": "alert"}
            r = await c.post("/investigations", json=body)
            assert r.status_code == 401
            assert r.json()["result"]["reason"] == "unauthorized"
            r = await c.post(
                "/investigations",
                json=body,
                headers={"Authorization": "Bearer wrong"},
            )
            assert r.status_code == 401
            assert (await c.get("/investigations/t-auth")).status_code == 401

            auth = {"Authorization": "Bearer s3cret"}
            r = await c.post("/investigations", json=body, headers=auth)
            assert r.status_code == 202
            await _wait_for(lambda: _status("t-auth") == "completed")
            r = await c.get("/investigations/t-auth", headers=auth)
            assert r.status_code == 200

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Persistence calls (metadata + usage)
# ---------------------------------------------------------------------------


def test_metadata_trigger_source_and_usage_reach_config_service(env):
    calls = env

    async def _run():
        async with _client() as c:
            r = await c.post(
                "/investigations",
                json={
                    "prompt": "p",
                    "thread_id": "alert-p",
                    "trigger_source": "alert",
                    "metadata": {"alert_ids": ["a1", "a2"], "policy_id": "p1"},
                },
            )
            run_id = r.json()["result"]["run_id"]
            await _wait_for(lambda: _status("alert-p") == "completed")
            return run_id

    run_id = asyncio.run(_run())
    creates = [b for u, b in calls["post"] if u.endswith("/api/v1/internal/agent-runs")]
    assert len(creates) == 1
    create = creates[0]
    assert create["run_id"] == run_id
    assert create["trigger_source"] == "alert"
    assert create["metadata"] == {"alert_ids": ["a1", "a2"], "policy_id": "p1"}
    assert create["correlation_id"] == "alert-p"

    patches = [(u, b) for u, b in calls["patch"] if u.endswith(f"/agent-runs/{run_id}")]
    assert len(patches) == 1
    done = patches[0][1]
    assert done["status"] == "completed"
    assert done["cost_usd"] == pytest.approx(0.12)
    assert done["input_tokens"] == 150
    assert done["output_tokens"] == 20
    assert done["cache_read_input_tokens"] == 5
    assert done["model_usage"]["claude-sonnet-4-5"]["costUSD"] == pytest.approx(0.12)


def test_investigate_passes_metadata_and_alert_trigger_source(env):
    calls = env

    async def _run():
        async with _client() as c:
            r = await c.post(
                "/investigate",
                json={
                    "prompt": "p",
                    "thread_id": "alert-sse",
                    "trigger_source": "alert",
                    "metadata": {"batch_id": "b9"},
                },
            )
            assert r.status_code == 200
            events = _sse_events(r.text)
            result = [e for e in events if e["type"] == "result"][0]
            assert result["data"]["total_cost_usd"] == pytest.approx(0.12)
            assert result["data"]["usage"] == USAGE["usage"]
            await _wait_for(lambda: "alert-sse" not in srv._background_tasks)

    asyncio.run(_run())
    create = [b for u, b in calls["post"] if u.endswith("/internal/agent-runs")][0]
    assert create["trigger_source"] == "alert"
    assert create["metadata"] == {"batch_id": "b9"}
    assert FakeSession.instances[0].interactive is False


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def test_queue_message_works_while_automatic_run_is_running(env):
    async def _run():
        FakeSession.blockers["alert-q"] = asyncio.Event()
        async with _client() as c:
            await c.post(
                "/investigations",
                json={"prompt": "p", "thread_id": "alert-q", "trigger_source": "alert"},
            )
            await _wait_for(
                lambda: FakeSession.instances and FakeSession.instances[0].is_running
            )
            r = await c.post(
                "/threads/alert-q/queue-message", json={"text": "also check redis"}
            )
            assert r.status_code == 200
            assert r.json() == {"queued": True, "pending_count": 1}
            assert FakeSession.instances[0].pending == ["also check redis"]
            FakeSession.blockers["alert-q"].set()
            await _wait_for(lambda: _status("alert-q") == "completed")

    asyncio.run(_run())


def test_idle_reaper_closes_manual_session_and_thread_can_resume(env, monkeypatch):
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "60")

    async def _run():
        async with _client() as c:
            r = await c.post("/investigate", json={"prompt": "p", "thread_id": "man-r"})
            assert r.status_code == 200
            # Manual thread stays warm for follow-ups.
            assert "man-r" in srv._background_tasks
            task = srv._background_tasks["man-r"]

            assert srv._reap_idle_sessions(now=time.monotonic()) == []  # not idle yet
            reaped = srv._reap_idle_sessions(now=time.monotonic() + 61)
            assert reaped == ["man-r"]
            assert "man-r" not in srv._background_tasks  # detached synchronously
            await asyncio.wait_for(task, timeout=5)
            assert FakeSession.instances[0].cleaned is True
            assert "man-r" not in srv._active_sessions

            # A follow-up starts a fresh task/session on the same thread.
            r = await c.post("/investigate", json={"prompt": "again", "thread_id": "man-r"})
            assert r.status_code == 200
            assert len(FakeSession.instances) == 2
            assert FakeSession.instances[1].prompts == ["again"]

    asyncio.run(_run())


def test_reaper_skips_running_and_disabled(env, monkeypatch):
    monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "60")

    async def _run():
        FakeSession.blockers["man-busy"] = asyncio.Event()
        async with _client() as c:
            await c.post("/investigations", json={"prompt": "p", "thread_id": "man-busy"})
            await _wait_for(lambda: _status("man-busy") == "running")
            assert srv._reap_idle_sessions(now=time.monotonic() + 10_000) == []

            monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "0")
            FakeSession.blockers["man-busy"].set()
            await _wait_for(lambda: _status("man-busy") == "completed")
            assert srv._reap_idle_sessions(now=time.monotonic() + 10_000) == []
            assert "man-busy" in srv._background_tasks

            monkeypatch.setenv("SESSION_IDLE_TTL_SECONDS", "60")
            assert srv._reap_idle_sessions(now=time.monotonic() + 10_000) == ["man-busy"]
            await _wait_for(lambda: FakeSession.instances[0].cleaned)

    asyncio.run(_run())


def test_session_start_failure_releases_slot_and_marks_failed(env):
    FakeSession.fail_start = {"alert-f"}

    async def _run():
        async with _client() as c:
            r = await c.post(
                "/investigations",
                json={"prompt": "p", "thread_id": "alert-f", "trigger_source": "alert"},
            )
            assert r.status_code == 202
            await _wait_for(lambda: _status("alert-f") == "failed")
            assert srv._gate.active == 0
            await _wait_for(lambda: "alert-f" not in srv._background_tasks)
            rec = (await c.get("/investigations/alert-f")).json()["result"]
            assert "cli failed to start" in rec["error"]

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Backward compatibility of /investigate
# ---------------------------------------------------------------------------


def test_investigate_legacy_request_keeps_sse_contract(env):
    calls = env

    async def _run():
        async with _client() as c:
            r = await c.post("/investigate", json={"prompt": "what broke?"})
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            events = _sse_events(r.text)
            # Manual thread: kept alive for follow-ups (until the idle reaper).
            assert events[0]["thread_id"] in srv._background_tasks
            return events

    events = asyncio.run(_run())
    types = [e["type"] for e in events]
    assert types == ["run_started", "thought", "result"]  # no queued: slot free
    for e in events:
        assert set(e) == {"type", "data", "thread_id", "timestamp"}
    thread_id = events[0]["thread_id"]
    assert thread_id.startswith("thread-")
    result = events[-1]["data"]
    assert result["text"] == "Root cause: bad deploy"
    assert result["success"] is True and result["subtype"] == "success"
    assert FakeSession.instances[0].interactive is True
    create = [b for u, b in calls["post"] if u.endswith("/internal/agent-runs")][0]
    assert create["trigger_source"] == "web_ui"
    assert create["metadata"] is None


def test_health_reports_concurrency(env):
    async def _run():
        async with _client() as c:
            return (await c.get("/health")).json()

    body = asyncio.run(_run())
    assert body["concurrency"] == {"limit": 1, "active": 0, "waiting": 0}
    assert body["status"] == "healthy"
