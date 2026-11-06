"""agent_runs: trigger_source 'alert', run metadata, and LLM usage/cost persistence."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from src.api.routes.internal import router as internal_router
from src.db import repository
from src.db.models import AgentRun
from src.db.session import get_db

HEADERS = {"X-Internal-Service": "sre-agent"}


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AgentRun.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as s:
        yield s


@pytest.fixture()
def client(db_session):
    app = FastAPI()
    app.include_router(internal_router)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _create(client, run_id="run-alert-1", **extra):
    body = {
        "run_id": run_id,
        "org_id": "local",
        "team_node_id": "default",
        "correlation_id": "alert-batch-1",
        "agent_name": "investigator",
        "trigger_source": "alert",
        "trigger_actor": "alert-policy:p1",
        "trigger_message": "Investigate KubePodCrashLooping",
        "metadata": {
            "alert_ids": ["a1", "a2"],
            "policy_id": "p1",
            "batch_id": "b1",
        },
    }
    body.update(extra)
    return client.post("/api/v1/internal/agent-runs", headers=HEADERS, json=body)


def test_create_accepts_alert_trigger_and_metadata(client, db_session):
    resp = _create(client)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["trigger_source"] == "alert"
    assert data["metadata"] == {
        "alert_ids": ["a1", "a2"],
        "policy_id": "p1",
        "batch_id": "b1",
    }
    run = repository.get_agent_run(db_session, run_id="run-alert-1")
    assert run.trigger_source == "alert"
    assert run.extra_metadata["batch_id"] == "b1"


def test_complete_persists_usage_and_cost(client, db_session):
    assert _create(client).status_code == 200
    resp = client.patch(
        "/api/v1/internal/agent-runs/run-alert-1",
        headers=HEADERS,
        json={
            "status": "completed",
            "duration_seconds": 12.5,
            "output_summary": "done",
            "cost_usd": 0.1234,
            "input_tokens": 1200,
            "output_tokens": 340,
            "cache_read_input_tokens": 5000,
            "cache_creation_input_tokens": 800,
            "model_usage": {
                "claude-sonnet-4-5": {"inputTokens": 1200, "costUSD": 0.1234}
            },
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cost_usd"] == pytest.approx(0.1234)
    assert data["input_tokens"] == 1200
    assert data["output_tokens"] == 340
    assert data["cache_read_input_tokens"] == 5000
    assert data["cache_creation_input_tokens"] == 800
    assert data["model_usage"]["claude-sonnet-4-5"]["costUSD"] == pytest.approx(
        0.1234
    )

    got = client.get("/api/v1/internal/agent-runs/run-alert-1", headers=HEADERS)
    assert got.status_code == 200
    assert got.json()["cost_usd"] == pytest.approx(0.1234)
    assert got.json()["metadata"]["policy_id"] == "p1"


def test_complete_without_usage_is_backward_compatible(client, db_session):
    assert _create(client, run_id="run-old").status_code == 200
    resp = client.patch(
        "/api/v1/internal/agent-runs/run-old",
        headers=HEADERS,
        json={"status": "completed", "duration_seconds": 1.0},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cost_usd"] is None
    assert data["input_tokens"] is None
    assert data["model_usage"] is None


def test_complete_rejects_negative_usage(client):
    assert _create(client, run_id="run-neg").status_code == 200
    resp = client.patch(
        "/api/v1/internal/agent-runs/run-neg",
        headers=HEADERS,
        json={"status": "completed", "duration_seconds": 1.0, "cost_usd": -1},
    )
    assert resp.status_code == 422


def test_repository_usage_none_leaves_existing_values(db_session):
    repository.create_agent_run(
        db_session,
        run_id="run-repo",
        org_id="local",
        team_node_id="default",
        correlation_id="t",
        trigger_source="alert",
        agent_name="investigator",
    )
    repository.complete_agent_run(
        db_session, run_id="run-repo", status="completed", cost_usd=0.5
    )
    run = repository.complete_agent_run(
        db_session, run_id="run-repo", status="completed"
    )
    assert run.cost_usd == pytest.approx(0.5)


def test_list_includes_usage_but_not_output_json(client):
    assert _create(client, run_id="run-list").status_code == 200
    client.patch(
        "/api/v1/internal/agent-runs/run-list",
        headers=HEADERS,
        json={
            "status": "completed",
            "duration_seconds": 1.0,
            "output_json": {"title": "x"},
            "cost_usd": 0.01,
        },
    )
    resp = client.get(
        "/api/v1/internal/agent-runs/list",
        headers=HEADERS,
        params={"org_id": "local"},
    )
    assert resp.status_code == 200, resp.text
    runs = resp.json()["runs"]
    assert runs[0]["cost_usd"] == pytest.approx(0.01)
    assert runs[0]["output_json"] is None
