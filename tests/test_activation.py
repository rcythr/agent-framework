"""
Tests for activation DB methods, the per-repo secret lookup in the webhook handler,
and the activate/deactivate/list gateway endpoints.
"""
import hashlib
import hmac
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient, ASGITransport

from gateway.main import app
from providers.auth_base import UserIdentity
from providers.base import WebhookRegistration
from shared.models import ActivationRecord


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_activation(project_id: str = "alice/myrepo") -> ActivationRecord:
    return ActivationRecord(
        project_id=project_id,
        webhook_id="hook-42",
        secret="per-repo-secret",
        activated_by="alice",
        activated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


# ── DB activation methods ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activate_and_get():
    from gateway.db import Database
    db = Database(":memory:")
    await db.connect()

    await db.activate_project(_make_activation())

    result = await db.get_activation("alice/myrepo")
    assert result is not None
    assert result.project_id == "alice/myrepo"
    assert result.webhook_id == "hook-42"
    assert result.secret == "per-repo-secret"
    assert result.activated_by == "alice"

    await db.close()


@pytest.mark.asyncio
async def test_get_activation_missing_returns_none():
    from gateway.db import Database
    db = Database(":memory:")
    await db.connect()

    assert await db.get_activation("nobody/norepo") is None

    await db.close()


@pytest.mark.asyncio
async def test_deactivate_removes_record():
    from gateway.db import Database
    db = Database(":memory:")
    await db.connect()

    await db.activate_project(_make_activation())
    await db.deactivate_project("alice/myrepo")

    assert await db.get_activation("alice/myrepo") is None

    await db.close()


@pytest.mark.asyncio
async def test_list_activations():
    from gateway.db import Database
    db = Database(":memory:")
    await db.connect()

    for i in range(3):
        await db.activate_project(ActivationRecord(
            project_id=f"alice/repo{i}",
            webhook_id=f"hook-{i}",
            secret=f"secret{i}",
            activated_by="alice",
            activated_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
        ))

    results = await db.list_activations()
    assert len(results) == 3
    assert {r.project_id for r in results} == {"alice/repo0", "alice/repo1", "alice/repo2"}

    await db.close()


# ── Per-repo secret lookup ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_uses_per_repo_secret():
    """Webhook signed with per-repo secret is accepted."""
    from providers.github.webhook import verify_webhook, parse_webhook_event

    repo_secret = "per-repo-secret-xyz"
    body = json.dumps({
        "ref": "refs/heads/main",
        "after": "abc123",
        "repository": {"id": 1, "full_name": "alice/myrepo"},
        "pusher": {"name": "alice"},
        "commits": [],
    }).encode()
    sig = "sha256=" + hmac.new(repo_secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": "push",
        "X-Hub-Signature-256": sig,
        "content-type": "application/json",
    }

    activation = ActivationRecord(
        project_id="alice/myrepo",
        webhook_id="h1",
        secret=repo_secret,
        activated_by="alice",
        activated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    mock_db = AsyncMock()
    mock_db.get_activation.return_value = activation
    mock_db.create_job = AsyncMock()

    mock_provider = MagicMock()
    mock_provider.parse_webhook_event.side_effect = parse_webhook_event
    mock_provider.verify_webhook.side_effect = verify_webhook

    mock_loader = AsyncMock()
    mock_loader.resolve.return_value = MagicMock(allowed_users=[])

    with patch("gateway.main._db", mock_db), \
         patch("gateway.main._provider", mock_provider), \
         patch("gateway.main._config_loader", mock_loader):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/webhook", content=body, headers=headers)

    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_webhook_rejects_wrong_secret():
    """Webhook signed with wrong secret returns 401 even if a per-repo record exists."""
    from providers.github.webhook import verify_webhook, parse_webhook_event

    repo_secret = "correct-secret"
    body = json.dumps({
        "ref": "refs/heads/main",
        "after": "abc123",
        "repository": {"id": 1, "full_name": "alice/myrepo"},
        "pusher": {"name": "alice"},
        "commits": [],
    }).encode()
    headers = {
        "X-GitHub-Event": "push",
        "X-Hub-Signature-256": "sha256=deadbeefdeadbeef",
        "content-type": "application/json",
    }

    activation = ActivationRecord(
        project_id="alice/myrepo",
        webhook_id="h1",
        secret=repo_secret,
        activated_by="alice",
        activated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )

    mock_db = AsyncMock()
    mock_db.get_activation.return_value = activation

    mock_provider = MagicMock()
    mock_provider.parse_webhook_event.side_effect = parse_webhook_event
    mock_provider.verify_webhook.side_effect = verify_webhook

    with patch("gateway.main._db", mock_db), \
         patch("gateway.main._provider", mock_provider):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/webhook", content=body, headers=headers)

    assert resp.status_code == 401


# ── Activate endpoint ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activate_endpoint_success():
    mock_db = AsyncMock()
    mock_db.get_activation.return_value = None

    mock_provider = MagicMock()
    mock_provider.register_webhook.return_value = WebhookRegistration(
        webhook_id="hook-42",
        webhook_url="https://phalanx.example.com/webhook",
    )

    mock_auth = MagicMock()
    mock_auth.extract_user.return_value = UserIdentity(
        username="alice", email="alice@example.com", groups=[]
    )

    with patch("gateway.main._db", mock_db), \
         patch("gateway.main._provider", mock_provider), \
         patch("gateway.main._auth_provider", mock_auth), \
         patch("gateway.main.PHALANX_WEBHOOK_URL", "https://phalanx.example.com"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/projects/alice%2Fmyrepo/activate",
                headers={"X-Forwarded-Access-Token": "user-token"},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == "alice/myrepo"
    assert data["webhook_url"].endswith("/webhook")
    assert data["activated_by"] == "alice"
    mock_provider.register_webhook.assert_called_once()
    mock_db.activate_project.assert_called_once()


@pytest.mark.asyncio
async def test_activate_endpoint_already_activated_returns_409():
    mock_db = AsyncMock()
    mock_db.get_activation.return_value = _make_activation()

    with patch("gateway.main._db", mock_db), \
         patch("gateway.main.PHALANX_WEBHOOK_URL", "https://phalanx.example.com"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/projects/alice%2Fmyrepo/activate")

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_activate_endpoint_no_webhook_url_returns_500():
    mock_db = AsyncMock()
    mock_db.get_activation.return_value = None

    with patch("gateway.main._db", mock_db), \
         patch("gateway.main.PHALANX_WEBHOOK_URL", ""):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/projects/alice%2Fmyrepo/activate")

    assert resp.status_code == 500


# ── Deactivate endpoint ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivate_endpoint_success():
    mock_db = AsyncMock()
    mock_db.get_activation.return_value = _make_activation()

    mock_provider = MagicMock()

    with patch("gateway.main._db", mock_db), \
         patch("gateway.main._provider", mock_provider):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(
                "/projects/alice%2Fmyrepo/activate",
                headers={"X-Forwarded-Access-Token": "user-token"},
            )

    assert resp.status_code == 200
    assert resp.json()["status"] == "deactivated"
    mock_provider.delete_webhook.assert_called_once_with("alice/myrepo", "hook-42", "user-token")
    mock_db.deactivate_project.assert_called_once_with("alice/myrepo")


@pytest.mark.asyncio
async def test_deactivate_endpoint_not_found_returns_404():
    mock_db = AsyncMock()
    mock_db.get_activation.return_value = None

    with patch("gateway.main._db", mock_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/projects/alice%2Fmyrepo/activate")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deactivate_removes_db_record_even_if_provider_fails():
    """Remote webhook deletion failure still cleans up the DB record."""
    mock_db = AsyncMock()
    mock_db.get_activation.return_value = _make_activation()

    mock_provider = MagicMock()
    mock_provider.delete_webhook.side_effect = Exception("network error")

    with patch("gateway.main._db", mock_db), \
         patch("gateway.main._provider", mock_provider):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/projects/alice%2Fmyrepo/activate")

    assert resp.status_code == 200
    mock_db.deactivate_project.assert_called_once_with("alice/myrepo")


# ── List activations endpoint ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_activations_endpoint():
    activations = [
        _make_activation("alice/repo1"),
        _make_activation("bob/repo2"),
    ]
    activations[1].activated_by = "bob"

    mock_db = AsyncMock()
    mock_db.list_activations.return_value = activations

    with patch("gateway.main._db", mock_db):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/projects/activations")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert {r["project_id"] for r in data} == {"alice/repo1", "bob/repo2"}
    # Secret must NOT be in any response
    for r in data:
        assert "secret" not in r
