"""Per-turn usage/cost from SDK ResultMessage, and non-interactive sessions."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent import (
    InteractiveAgentSession,
    snapshot_result_usage,
    turn_usage_from_snapshots,
)
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from events import result_event


def _result(cost, inp, out, model_cost=None, model_inp=None, **kw):
    defaults = dict(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="sess-1",
        result="OK",
        total_cost_usd=cost,
        usage={
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": 10,
            "cache_creation_input_tokens": 0,
            "service_tier": "standard",
        },
    )
    if model_cost is not None:
        defaults["model_usage"] = {
            "claude-sonnet-4-5": {
                "inputTokens": model_inp,
                "outputTokens": out,
                "cacheReadInputTokens": 10,
                "cacheCreationInputTokens": 0,
                "costUSD": model_cost,
                "contextWindow": 200000,
            }
        }
    defaults.update(kw)
    return ResultMessage(**defaults)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_snapshot_keeps_only_cumulative_numeric_fields():
    snap = snapshot_result_usage(_result(0.5, 100, 20, model_cost=0.5, model_inp=150))
    assert snap["total_cost_usd"] == 0.5
    assert snap["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 0,
    }
    model = snap["model_usage"]["claude-sonnet-4-5"]
    assert "contextWindow" not in model
    assert model["costUSD"] == 0.5


def test_snapshot_none_without_message():
    assert snapshot_result_usage(None) is None
    assert turn_usage_from_snapshots(None, None) is None


def test_turn_usage_is_delta_of_running_totals():
    first = snapshot_result_usage(_result(0.10, 100, 20, model_cost=0.10, model_inp=300))
    second = snapshot_result_usage(_result(0.25, 160, 50, model_cost=0.25, model_inp=500))
    turn1 = turn_usage_from_snapshots(first, None)
    turn2 = turn_usage_from_snapshots(second, first)
    assert turn1["total_cost_usd"] == pytest.approx(0.10)
    assert turn2["total_cost_usd"] == pytest.approx(0.15)
    assert turn2["usage"]["input_tokens"] == 60
    assert turn2["usage"]["output_tokens"] == 30
    # model_usage includes subagents -> preferred for persisted totals
    assert turn2["totals"]["input_tokens"] == 200
    assert turn2["model_usage"]["claude-sonnet-4-5"]["costUSD"] == pytest.approx(0.15)


def test_turn_usage_counter_reset_uses_current_as_base():
    big = snapshot_result_usage(_result(1.0, 1000, 100))
    small = snapshot_result_usage(_result(0.2, 50, 5))
    turn = turn_usage_from_snapshots(small, big)
    assert turn["total_cost_usd"] == pytest.approx(0.2)
    assert turn["usage"]["input_tokens"] == 50
    # no model_usage -> totals from aggregated usage
    assert turn["totals"]["input_tokens"] == 50


def test_result_event_without_usage_keeps_legacy_shape():
    ev = result_event("t", "text", success=True, subtype="success")
    assert set(ev.data) == {"text", "success", "subtype"}


def test_result_event_with_usage_adds_fields():
    ev = result_event(
        "t",
        "text",
        usage={"input_tokens": 1},
        total_cost_usd=0.01,
        model_usage={"m": {"costUSD": 0.01}},
    )
    assert ev.data["usage"] == {"input_tokens": 1}
    assert ev.data["total_cost_usd"] == 0.01
    assert ev.data["model_usage"] == {"m": {"costUSD": 0.01}}


# ---------------------------------------------------------------------------
# execute() integration with a mocked SDK client
# ---------------------------------------------------------------------------


def _mock_client(results):
    """Client whose receive_messages yields one assistant msg + the next result."""
    client = MagicMock()

    async def fake_query(prompt):
        async for _ in prompt:
            pass

    it = iter(results)

    def fake_receive_messages():
        res = next(it)

        async def gen():
            yield AssistantMessage(content=[TextBlock(text="done")], model="test")
            yield res

        return gen()

    client.query = AsyncMock(side_effect=fake_query)
    client.receive_messages = MagicMock(side_effect=fake_receive_messages)
    return client


def test_execute_reports_per_turn_usage_in_result_and_last_turn_usage():
    async def _run():
        session = InteractiveAgentSession("t-usage")
        session.client = _mock_client(
            [
                _result(0.10, 100, 20, model_cost=0.10, model_inp=100),
                _result(0.30, 250, 60, model_cost=0.30, model_inp=250),
            ]
        )
        ev1 = [e async for e in session.execute("first")]
        res1 = [e for e in ev1 if e.type == "result"][0]
        assert res1.data["total_cost_usd"] == pytest.approx(0.10)
        assert session.last_turn_usage["totals"]["input_tokens"] == 100

        ev2 = [e async for e in session.execute("second")]
        res2 = [e for e in ev2 if e.type == "result"][0]
        assert res2.data["total_cost_usd"] == pytest.approx(0.20)
        assert res2.data["usage"]["input_tokens"] == 150
        assert session.last_turn_usage["total_cost_usd"] == pytest.approx(0.20)
        assert session.last_turn_usage["totals"]["output_tokens"] == 40

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Non-interactive sessions (trigger_source "alert")
# ---------------------------------------------------------------------------


def test_interactive_session_keeps_ask_user_question():
    session = InteractiveAgentSession("t-inter")
    assert session.interactive is True
    assert "AskUserQuestion" in session.options.allowed_tools
    assert "AskUserQuestion" not in (session.options.disallowed_tools or [])


def test_non_interactive_session_disallows_ask_user_question():
    session = InteractiveAgentSession("t-auto", interactive=False)
    assert "AskUserQuestion" not in session.options.allowed_tools
    assert "AskUserQuestion" in session.options.disallowed_tools
    # Other tools are untouched
    assert "Bash" in session.options.allowed_tools


def test_non_interactive_can_use_tool_denies_question_without_waiting():
    from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

    session = InteractiveAgentSession("t-auto-deny", interactive=False)

    async def _run():
        # Must return immediately (no 60s wait for an answer).
        denied = await asyncio.wait_for(
            session._can_use_tool_handler(
                "AskUserQuestion", {"questions": [{"question": "?"}]}, None
            ),
            timeout=1.0,
        )
        allowed = await session._can_use_tool_handler("Bash", {"command": "ls"}, None)
        return denied, allowed

    denied, allowed = asyncio.run(_run())
    assert isinstance(denied, PermissionResultDeny)
    assert "automatic investigation" in denied.message
    assert isinstance(allowed, PermissionResultAllow)


def test_non_interactive_session_still_applies_agent_timeout(monkeypatch):
    """AGENT_TIMEOUT_SECONDS bounds automatic runs exactly like manual ones."""
    monkeypatch.setenv("AGENT_TIMEOUT_SECONDS", "1")

    async def _run():
        session = InteractiveAgentSession("t-auto-timeout", interactive=False)
        client = MagicMock()

        async def fake_query(prompt):
            async for _ in prompt:
                pass

        async def hang():
            await asyncio.sleep(30)
            yield None  # pragma: no cover

        def fake_receive_response():
            async def gen():
                if False:  # pragma: no cover
                    yield None

            return gen()

        client.query = AsyncMock(side_effect=fake_query)
        client.receive_messages = MagicMock(side_effect=lambda: hang())
        client.receive_response = MagicMock(side_effect=fake_receive_response)
        client.interrupt = AsyncMock()
        session.client = client
        events = await asyncio.wait_for(
            _collect(session.execute("go")), timeout=10.0
        )
        return events

    events = asyncio.run(_run())
    errors = [e for e in events if e.type == "error"]
    assert errors and "time limit" in errors[0].data["message"]


async def _collect(agen):
    return [e async for e in agen]
