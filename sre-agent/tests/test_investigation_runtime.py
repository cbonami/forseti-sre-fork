"""Unit tests for investigation_runtime: gate, status registry, auth, validators."""

import asyncio

import investigation_runtime as rt
import pytest
from fastapi import Request


def _request(headers: dict) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw})


# ---------------------------------------------------------------------------
# ConcurrencyGate
# ---------------------------------------------------------------------------


def test_gate_try_acquire_respects_limit():
    gate = rt.ConcurrencyGate(2)
    assert gate.try_acquire()
    assert gate.try_acquire()
    assert not gate.try_acquire()
    assert gate.snapshot() == {"limit": 2, "active": 2, "waiting": 0}
    gate.release()
    assert gate.try_acquire()


def test_gate_limit_floor_is_one():
    assert rt.ConcurrencyGate(0).limit == 1


def test_gate_waiters_are_fifo_and_slot_is_handed_off():
    async def _run():
        gate = rt.ConcurrencyGate(1)
        assert gate.try_acquire()
        order = []

        async def waiter(name):
            await gate.acquire()
            order.append(name)

        t1 = asyncio.create_task(waiter("first"))
        await asyncio.sleep(0)
        t2 = asyncio.create_task(waiter("second"))
        await asyncio.sleep(0)
        assert gate.waiting == 2
        # A free-standing try_acquire cannot jump the queue.
        assert not gate.try_acquire()

        gate.release()  # hand-off to "first"; active stays 1
        await t1
        assert order == ["first"]
        assert gate.active == 1
        assert not gate.try_acquire()

        gate.release()  # hand-off to "second"
        await t2
        assert order == ["first", "second"]
        gate.release()
        assert gate.snapshot() == {"limit": 1, "active": 0, "waiting": 0}

    asyncio.run(_run())


def test_gate_cancelled_waiter_does_not_leak_a_slot():
    async def _run():
        gate = rt.ConcurrencyGate(1)
        assert gate.try_acquire()
        t = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0)
        assert gate.waiting == 1
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert gate.waiting == 0
        gate.release()
        assert gate.active == 0

    asyncio.run(_run())


def test_gate_cancel_after_handoff_returns_the_slot():
    async def _run():
        gate = rt.ConcurrencyGate(1)
        assert gate.try_acquire()
        t = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0)
        gate.release()  # slot handed to t, t not resumed yet
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert gate.active == 0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# RunStatusRegistry + usage shaping
# ---------------------------------------------------------------------------


def test_registry_begin_update_get_returns_copy():
    reg = rt.RunStatusRegistry()
    reg.begin("t1", status="queued", run_id="r1", trigger_source="alert")
    reg.update("t1", status="running")
    rec = reg.get("t1")
    assert rec["status"] == "running"
    assert rec["run_id"] == "r1"
    rec["status"] = "mutated"
    assert reg.get("t1")["status"] == "running"
    assert reg.get("missing") is None


def test_registry_prunes_terminal_records_only():
    reg = rt.RunStatusRegistry(max_entries=2)
    reg.begin("a", status="running")
    reg.begin("b", status="queued")
    reg.update("b", status="completed")
    reg.begin("c", status="running")
    assert reg.get("a") is not None  # active, kept
    assert reg.get("b") is None  # terminal, pruned
    assert reg.get("c") is not None


def test_usage_shaping_for_status_and_agent_run():
    turn = {
        "total_cost_usd": 0.2,
        "usage": {"input_tokens": 5},
        "model_usage": {"m": {"costUSD": 0.2}},
        "totals": {
            "input_tokens": 10,
            "output_tokens": 3,
            "cache_read_input_tokens": 7,
            "cache_creation_input_tokens": 1,
        },
    }
    status = rt.usage_for_status(turn)
    assert status["input_tokens"] == 10
    assert status["total_cost_usd"] == 0.2
    run = rt.usage_for_agent_run(turn)
    assert run == {
        "cost_usd": 0.2,
        "input_tokens": 10,
        "output_tokens": 3,
        "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 1,
        "model_usage": {"m": {"costUSD": 0.2}},
    }
    assert rt.usage_for_agent_run(None) == {}
    assert rt.usage_for_status(None) is None


# ---------------------------------------------------------------------------
# Auth + validation
# ---------------------------------------------------------------------------


def test_internal_auth_open_when_token_unset(monkeypatch):
    monkeypatch.delenv("AGENT_INTERNAL_TOKEN", raising=False)
    assert rt.internal_auth_error(_request({})) is None


def test_internal_auth_requires_matching_bearer(monkeypatch):
    monkeypatch.setenv("AGENT_INTERNAL_TOKEN", "s3cret")
    missing = rt.internal_auth_error(_request({}))
    assert missing.status_code == 401
    wrong = rt.internal_auth_error(_request({"Authorization": "Bearer nope"}))
    assert wrong.status_code == 401
    assert rt.internal_auth_error(_request({"Authorization": "Bearer s3cret"})) is None


def test_validate_metadata():
    assert rt.validate_metadata(None) is None
    assert rt.validate_metadata({"alert_ids": ["a"]}) is None
    assert "object" in rt.validate_metadata(["a"])
    assert "exceeds" in rt.validate_metadata({"x": "y" * (rt.MAX_METADATA_BYTES + 1)})


@pytest.mark.parametrize(
    "tid,ok",
    [
        ("alert-batch-1234", True),
        ("alert:p1:b1.2", True),
        ("", False),
        ("../etc", False),
        ("-leading", False),
        ("a" * 201, False),
        ("has space", False),
    ],
)
def test_validate_thread_id(tid, ok):
    assert (rt.validate_thread_id(tid) is None) is ok


def test_env_int(monkeypatch):
    monkeypatch.setenv("X_TEST_INT", "7")
    assert rt.env_int("X_TEST_INT", 3) == 7
    monkeypatch.setenv("X_TEST_INT", "abc")
    assert rt.env_int("X_TEST_INT", 3) == 3
    monkeypatch.setenv("X_TEST_INT", "0")
    assert rt.env_int("X_TEST_INT", 3, minimum=1) == 3
    monkeypatch.delenv("X_TEST_INT")
    assert rt.env_int("X_TEST_INT", 3) == 3
