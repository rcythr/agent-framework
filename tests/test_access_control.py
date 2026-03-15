"""Tests for Phase 4 access control — allowed_users webhook dispatch."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from gateway.main import app
from providers.auth_base import UserIdentity
from shared.models import AgentConfig, TaskSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent_config(allowed_users: list[str]) -> AgentConfig:
    return AgentConfig(
        skills=[],
        tools=[],
        system_prompt="",
        image="localhost:5001/pi-agent-worker:latest",
        gas_limit_input=80_000,
        gas_limit_output=20_000,
        allowed_users=allowed_users,
    )


def _webhook_body(actor: str = "alice") -> dict:
    return {
        "object_kind": "push",
        "ref": "refs/heads/main",
        "user_username": actor,
        "commits": [{"id": "abc123", "message": "fix", "author": {"name": actor}}],
        "project": {"id": 1},
    }


def _webhook_headers() -> dict:
    return {
        "X-Gitlab-Token": "dev-webhook-secret",
        "X-Gitlab-Event": "Push Hook",
        "content-type": "application/json",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_app_deps():
    """Patch all external dependencies of gateway/main.py."""
    mock_provider = MagicMock()
    mock_provider.verify_webhook.return_value = True

    mock_kube = MagicMock()
    mock_kube.spawn_agent_job.return_value = "pi-agent-analyze-push-abc12345"

    mock_db = AsyncMock()
    mock_db.connect = AsyncMock()
    mock_db.close = AsyncMock()
    mock_db.create_job = AsyncMock()

    mock_auth = MagicMock()
    mock_auth.extract_user.return_value = UserIdentity(username="", email="", groups=[])

    with patch("gateway.main._provider", mock_provider), \
         patch("gateway.main._kube", mock_kube), \
         patch("gateway.main._db", mock_db), \
         patch("gateway.main._auth_provider", mock_auth):
        yield mock_provider, mock_kube, mock_db


# ---------------------------------------------------------------------------
# allowed_users check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_actor_in_allowed_users_spawns_job(mock_app_deps):
    """Actor in allowed_users → job is spawned."""
    mock_provider, mock_kube, mock_db = mock_app_deps

    agent_config = _make_agent_config(allowed_users=["alice", "bob"])
    event = MagicMock()
    event.actor = "alice"
    task_spec = TaskSpec(task="analyze_push", project_id=1, context={"sha": "abc123", "actor": "alice", "branch": "main", "commits": []})
    mock_provider.parse_webhook_event.return_value = event

    mock_loader = AsyncMock()
    mock_loader.resolve.return_value = agent_config

    with patch("gateway.main._config_loader", mock_loader), \
         patch("gateway.main.map_event_to_task", return_value=task_spec):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/webhook/gitlab",
                content=json.dumps(_webhook_body("alice")),
                headers=_webhook_headers(),
            )

    assert resp.status_code == 200
    assert resp.json().get("job_name") is not None
    mock_kube.spawn_agent_job.assert_called_once()
    mock_db.create_job.assert_called_once()


@pytest.mark.asyncio
async def test_actor_not_in_allowed_users_no_job(mock_app_deps):
    """Actor NOT in allowed_users → no job, HTTP 200, rejection logged."""
    mock_provider, mock_kube, mock_db = mock_app_deps

    agent_config = _make_agent_config(allowed_users=["alice"])
    event = MagicMock()
    event.actor = "mallory"
    task_spec = TaskSpec(task="analyze_push", project_id=1, context={"sha": "abc123", "actor": "mallory", "branch": "main", "commits": []})
    mock_provider.parse_webhook_event.return_value = event

    mock_loader = AsyncMock()
    mock_loader.resolve.return_value = agent_config

    with patch("gateway.main._config_loader", mock_loader), \
         patch("gateway.main.map_event_to_task", return_value=task_spec):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/webhook/gitlab",
                content=json.dumps(_webhook_body("mallory")),
                headers=_webhook_headers(),
            )

    assert resp.status_code == 200
    assert resp.content == b""
    mock_kube.spawn_agent_job.assert_not_called()
    mock_db.create_job.assert_not_called()


@pytest.mark.asyncio
async def test_empty_allowed_users_no_job_for_any_actor(mock_app_deps):
    """allowed_users: [] → no job spawned for any actor (deny-by-default)."""
    mock_provider, mock_kube, mock_db = mock_app_deps

    agent_config = _make_agent_config(allowed_users=[])
    event = MagicMock()
    event.actor = "alice"
    task_spec = TaskSpec(task="analyze_push", project_id=1, context={"sha": "abc123", "actor": "alice", "branch": "main", "commits": []})
    mock_provider.parse_webhook_event.return_value = event

    mock_loader = AsyncMock()
    mock_loader.resolve.return_value = agent_config

    with patch("gateway.main._config_loader", mock_loader), \
         patch("gateway.main.map_event_to_task", return_value=task_spec):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/webhook/gitlab",
                content=json.dumps(_webhook_body("alice")),
                headers=_webhook_headers(),
            )

    assert resp.status_code == 200
    assert resp.content == b""
    mock_kube.spawn_agent_job.assert_not_called()


@pytest.mark.asyncio
async def test_manual_trigger_bypasses_allowed_users(mock_app_deps):
    """POST /trigger skips the allowed_users check entirely."""
    mock_provider, mock_kube, mock_db = mock_app_deps

    task_spec = TaskSpec(task="review_mr", project_id=1, context={"mr_iid": 42})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/trigger", json=task_spec.model_dump())

    assert resp.status_code == 200
    assert resp.json().get("job_name") is not None
    mock_kube.spawn_agent_job.assert_called_once()


@pytest.mark.asyncio
async def test_allowed_users_passed_through_unchanged(mock_app_deps):
    """allowed_users from ProjectConfig is passed to AgentConfig unchanged."""
    mock_provider, mock_kube, mock_db = mock_app_deps

    expected_users = ["alice", "bob", "carol"]
    agent_config = _make_agent_config(allowed_users=expected_users)
    event = MagicMock()
    event.actor = "alice"
    task_spec = TaskSpec(task="analyze_push", project_id=1, context={"sha": "abc123", "actor": "alice", "branch": "main", "commits": []})
    mock_provider.parse_webhook_event.return_value = event

    mock_loader = AsyncMock()
    mock_loader.resolve.return_value = agent_config

    captured_config = {}

    def capture_spawn(ts, ac):
        captured_config["agent_config"] = ac
        return "pi-agent-test-job"

    mock_kube.spawn_agent_job.side_effect = capture_spawn

    with patch("gateway.main._config_loader", mock_loader), \
         patch("gateway.main.map_event_to_task", return_value=task_spec):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/webhook/gitlab",
                content=json.dumps(_webhook_body("alice")),
                headers=_webhook_headers(),
            )

    assert captured_config["agent_config"].allowed_users == expected_users
