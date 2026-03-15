"""
Phase 5 — Authentication tests.

Integration tests:
- POST /trigger with forwarded headers → extract_user() called, triggered_by set to username
- POST /trigger without forwarded headers → triggered_by = "system"
- GET /internal/oauth2-proxy-config → returns correct CLI args for GitLabAuthProvider

Unit tests:
- Gateway never directly reads 'X-Forwarded-User' outside providers/ directory
"""

import ast
import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from gateway.main import app
from providers.auth_base import UserIdentity
from shared.models import TaskSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_deps(username: str = ""):
    mock_kube = MagicMock()
    mock_kube.spawn_agent_job.return_value = "pi-agent-test-job"

    mock_db = AsyncMock()
    mock_db.create_job = AsyncMock()

    mock_auth = MagicMock()
    mock_auth.extract_user.return_value = UserIdentity(
        username=username, email=f"{username}@example.com" if username else "", groups=[]
    )

    return mock_kube, mock_db, mock_auth


# ---------------------------------------------------------------------------
# Integration: POST /trigger sets triggered_by from auth_provider.extract_user()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trigger_with_forwarded_user_sets_triggered_by():
    """POST /trigger with X-Forwarded-User header → triggered_by = username."""
    mock_kube, mock_db, mock_auth = _make_mock_deps(username="alice")

    task = TaskSpec(task="review_mr", project_id=1, context={"mr_iid": 42})

    captured = {}

    async def capture_create_job(job_record):
        captured["record"] = job_record

    mock_db.create_job.side_effect = capture_create_job

    with patch("gateway.main._kube", mock_kube), \
         patch("gateway.main._db", mock_db), \
         patch("gateway.main._auth_provider", mock_auth):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/trigger",
                json=task.model_dump(),
                headers={"X-Forwarded-User": "alice"},
            )

    assert resp.status_code == 200
    mock_auth.extract_user.assert_called_once()
    assert captured["record"].triggered_by == "alice"


@pytest.mark.asyncio
async def test_trigger_without_forwarded_headers_sets_system():
    """POST /trigger with no forwarded headers → triggered_by = 'system'."""
    mock_kube, mock_db, mock_auth = _make_mock_deps(username="")

    task = TaskSpec(task="review_mr", project_id=1, context={"mr_iid": 42})

    captured = {}

    async def capture_create_job(job_record):
        captured["record"] = job_record

    mock_db.create_job.side_effect = capture_create_job

    with patch("gateway.main._kube", mock_kube), \
         patch("gateway.main._db", mock_db), \
         patch("gateway.main._auth_provider", mock_auth):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/trigger", json=task.model_dump())

    assert resp.status_code == 200
    mock_auth.extract_user.assert_called_once()
    assert captured["record"].triggered_by == "system"


@pytest.mark.asyncio
async def test_trigger_calls_extract_user_not_direct_header():
    """POST /trigger must call auth_provider.extract_user(), not bypass it."""
    mock_kube, mock_db, mock_auth = _make_mock_deps(username="bob")

    task = TaskSpec(task="analyze_push", project_id=2, context={})

    with patch("gateway.main._kube", mock_kube), \
         patch("gateway.main._db", mock_db), \
         patch("gateway.main._auth_provider", mock_auth):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/trigger",
                json=task.model_dump(),
                headers={"X-Forwarded-User": "bob"},
            )

    # Must have gone through extract_user, not bypassed it
    mock_auth.extract_user.assert_called_once()


# ---------------------------------------------------------------------------
# Integration: GET /internal/oauth2-proxy-config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oauth2_proxy_config_returns_gitlab_provider_args():
    """GET /internal/oauth2-proxy-config returns args for GitLabAuthProvider."""
    from providers.gitlab.auth import GitLabAuthProvider

    real_auth = GitLabAuthProvider()

    with patch("gateway.main._auth_provider", real_auth):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/internal/oauth2-proxy-config")

    assert resp.status_code == 200
    data = resp.json()
    assert "args" in data
    args = data["args"]
    assert "--provider=gitlab" in args
    assert any("--gitlab-group" in a for a in args)


# ---------------------------------------------------------------------------
# Unit: gateway/main.py must not directly read 'X-Forwarded-User'
# ---------------------------------------------------------------------------

def _collect_string_literals(filepath: str) -> list[str]:
    """Parse a Python file and return all string constants."""
    source = Path(filepath).read_text()
    tree = ast.parse(source)
    literals = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)
    return literals


def test_gateway_main_does_not_directly_read_x_forwarded_user():
    """
    Enforce: no string literal 'X-Forwarded-User' in gateway/main.py.
    The gateway must always go through auth_provider.extract_user().
    """
    gateway_main = Path(__file__).parent.parent / "gateway" / "main.py"
    literals = _collect_string_literals(str(gateway_main))
    assert "X-Forwarded-User" not in literals, (
        "gateway/main.py must not directly reference 'X-Forwarded-User'. "
        "Use auth_provider.extract_user() instead."
    )
    # Also check lowercase variant
    assert "x-forwarded-user" not in literals, (
        "gateway/main.py must not directly reference 'x-forwarded-user'. "
        "Use auth_provider.extract_user() instead."
    )


def test_only_providers_dir_reads_x_forwarded_user():
    """
    Ensure no string literal 'X-Forwarded-User' (or lowercase variant) appears
    outside the providers/ directory. Comments are not checked — only AST literals.
    """
    repo_root = Path(__file__).parent.parent
    offending_files = []

    for py_file in repo_root.rglob("*.py"):
        relative = py_file.relative_to(repo_root)
        parts = relative.parts
        # Only providers/ may use this header name as a string literal
        if parts[0] in ("providers", "tests"):
            continue
        literals = _collect_string_literals(str(py_file))
        if any(lit.lower() == "x-forwarded-user" for lit in literals):
            offending_files.append(str(relative))

    assert offending_files == [], (
        f"String literal 'X-Forwarded-User' found outside providers/ in: {offending_files}. "
        "The gateway must delegate header reading to auth_provider.extract_user()."
    )


# ---------------------------------------------------------------------------
# Integration: triggered_by is persisted on the JobRecord
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_triggered_by_recorded_on_job_record():
    """triggered_by from extract_user is stored on the persisted JobRecord."""
    mock_kube, mock_db, mock_auth = _make_mock_deps(username="carol")
    task = TaskSpec(task="review_mr", project_id=3, context={})

    created_records = []

    async def capture(jr):
        created_records.append(jr)

    mock_db.create_job.side_effect = capture

    with patch("gateway.main._kube", mock_kube), \
         patch("gateway.main._db", mock_db), \
         patch("gateway.main._auth_provider", mock_auth):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/trigger",
                json=task.model_dump(),
                headers={"X-Forwarded-User": "carol"},
            )

    assert len(created_records) == 1
    assert created_records[0].triggered_by == "carol"
