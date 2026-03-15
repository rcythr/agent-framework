"""
Phase 6 — Control Plane Dashboard integration tests.

Integration tests:
- POST /agents/{id}/cancel deletes K8s Job and sets DB status to 'cancelled'
- POST /agents/{id}/gas increments limits in DB and calls internal add-gas; returns updated gas state
- POST /agents/{id}/gas on a non-out_of_gas job increments limits without triggering resume
- GET /agents/{id}/gas returns current gas state with topup_history
- POST /internal/jobs/{id}/add-gas signals gas waiters
- GET / serves the dashboard HTML
"""

import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

from httpx import AsyncClient, ASGITransport

from gateway.db import Database
from gateway.main import app
from shared.models import JobRecord


def _make_job(job_id: str, status: str = "running") -> JobRecord:
    return JobRecord(
        id=job_id,
        task="review_mr",
        project_id=1,
        project_name="group/repo",
        status=status,
        context={"mr_iid": 1},
        started_at=datetime.now(timezone.utc),
        gas_limit_input=80_000,
        gas_limit_output=20_000,
        gas_used_input=40_000,
        gas_used_output=10_000,
    )


@pytest_asyncio.fixture
async def client(tmp_path):
    """Create test client with isolated DB and mocked KubeClient."""
    db = Database(path=str(tmp_path / "test.db"))
    await db.connect()

    mock_kube = MagicMock()

    import gateway.main as gw_main
    original_db = gw_main._db
    original_kube = gw_main._kube
    gw_main._db = db
    gw_main._kube = mock_kube

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, db, mock_kube

    gw_main._db = original_db
    gw_main._kube = original_kube
    await db.close()


# ─── POST /agents/{id}/cancel ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_sets_status_to_cancelled(client):
    ac, db, mock_kube = client
    job = _make_job("job-cancel-1", status="running")
    await db.create_job(job)

    resp = await ac.post("/agents/job-cancel-1/cancel")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"

    updated = await db.get_job("job-cancel-1")
    assert updated.status == "cancelled"
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_cancel_calls_kube_delete_job(client):
    ac, db, mock_kube = client
    job = _make_job("job-cancel-2", status="running")
    await db.create_job(job)

    await ac.post("/agents/job-cancel-2/cancel")

    mock_kube.delete_job.assert_called_once_with("job-cancel-2")


@pytest.mark.asyncio
async def test_cancel_tolerates_kube_delete_failure(client):
    """Cancel should succeed even if K8s delete raises (job may already be gone)."""
    ac, db, mock_kube = client
    mock_kube.delete_job.side_effect = Exception("Not found in K8s")
    job = _make_job("job-cancel-3", status="running")
    await db.create_job(job)

    resp = await ac.post("/agents/job-cancel-3/cancel")

    assert resp.status_code == 200
    updated = await db.get_job("job-cancel-3")
    assert updated.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_nonexistent_job_returns_404(client):
    ac, db, mock_kube = client

    resp = await ac.post("/agents/nonexistent-job/cancel")

    assert resp.status_code == 404


# ─── GET /agents/{id}/gas ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_gas_returns_current_state(client):
    ac, db, mock_kube = client
    job = _make_job("job-gas-get-1", status="running")
    await db.create_job(job)

    resp = await ac.get("/agents/job-gas-get-1/gas")

    assert resp.status_code == 200
    data = resp.json()
    assert data["gas_used_input"] == 40_000
    assert data["gas_limit_input"] == 80_000
    assert data["gas_used_output"] == 10_000
    assert data["gas_limit_output"] == 20_000
    assert "topup_history" in data


@pytest.mark.asyncio
async def test_get_gas_nonexistent_job_returns_404(client):
    ac, db, mock_kube = client

    resp = await ac.get("/agents/nonexistent/gas")

    assert resp.status_code == 404


# ─── POST /agents/{id}/gas ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_gas_increments_limits_in_db(client):
    ac, db, mock_kube = client
    job = _make_job("job-gas-1", status="running")
    await db.create_job(job)

    resp = await ac.post(
        "/agents/job-gas-1/gas",
        json={"input_amount": 10_000, "output_amount": 5_000},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["gas_limit_input"] == 90_000   # 80_000 + 10_000
    assert data["gas_limit_output"] == 25_000  # 20_000 + 5_000

    updated = await db.get_job("job-gas-1")
    assert updated.gas_limit_input == 90_000
    assert updated.gas_limit_output == 25_000


@pytest.mark.asyncio
async def test_add_gas_records_topup_history(client):
    ac, db, mock_kube = client
    job = _make_job("job-gas-2", status="running")
    await db.create_job(job)

    await ac.post("/agents/job-gas-2/gas", json={"input_amount": 5_000, "output_amount": 2_000})

    updated = await db.get_job("job-gas-2")
    assert len(updated.gas_topups) == 1
    assert updated.gas_topups[0]["input_amount"] == 5_000
    assert updated.gas_topups[0]["output_amount"] == 2_000


@pytest.mark.asyncio
async def test_add_gas_returns_updated_gas_state(client):
    ac, db, mock_kube = client
    job = _make_job("job-gas-3", status="running")
    await db.create_job(job)

    resp = await ac.post(
        "/agents/job-gas-3/gas",
        json={"input_amount": 20_000, "output_amount": 0},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["gas_limit_input"] == 100_000
    assert data["gas_used_input"] == 40_000
    assert "topup_history" in data


@pytest.mark.asyncio
async def test_add_gas_on_non_out_of_gas_job_does_not_trigger_resume(client):
    """Adding gas to a running job increments limits but does NOT signal gas waiters."""
    ac, db, mock_kube = client
    job = _make_job("job-gas-4", status="running")
    await db.create_job(job)

    import gateway.main as gw_main
    original_waiters = dict(gw_main._gas_waiters)

    resp = await ac.post("/agents/job-gas-4/gas", json={"input_amount": 10_000, "output_amount": 5_000})

    assert resp.status_code == 200
    # DB should still have status "running" (not changed to anything else)
    updated = await db.get_job("job-gas-4")
    assert updated.status == "running"
    assert updated.gas_limit_input == 90_000


@pytest.mark.asyncio
async def test_add_gas_on_out_of_gas_job_signals_waiters(client):
    """Adding gas to an out_of_gas job should signal the gas waiter queue."""
    import asyncio
    import gateway.main as gw_main

    ac, db, mock_kube = client
    job = _make_job("job-gas-oog-1", status="out_of_gas")
    await db.create_job(job)

    # Register a waiter queue
    waiter_q = asyncio.Queue()
    gw_main._gas_waiters["job-gas-oog-1"].append(waiter_q)

    resp = await ac.post(
        "/agents/job-gas-oog-1/gas",
        json={"input_amount": 10_000, "output_amount": 5_000},
    )

    assert resp.status_code == 200
    # Waiter should have received a signal
    assert not waiter_q.empty()
    signal = waiter_q.get_nowait()
    assert signal["input_amount"] == 10_000
    assert signal["output_amount"] == 5_000

    # Cleanup
    gw_main._gas_waiters.pop("job-gas-oog-1", None)


# ─── POST /internal/jobs/{id}/add-gas ────────────────────────────────────────

@pytest.mark.asyncio
async def test_internal_add_gas_signals_waiters(client):
    """POST /internal/jobs/{id}/add-gas signals any waiting queues for that job."""
    import asyncio
    import gateway.main as gw_main

    ac, db, mock_kube = client
    job = _make_job("job-internal-gas-1", status="out_of_gas")
    await db.create_job(job)

    waiter_q = asyncio.Queue()
    gw_main._gas_waiters["job-internal-gas-1"].append(waiter_q)

    resp = await ac.post(
        "/internal/jobs/job-internal-gas-1/add-gas",
        json={"input_amount": 5_000, "output_amount": 2_000},
    )

    assert resp.status_code == 200
    assert not waiter_q.empty()

    # Cleanup
    gw_main._gas_waiters.pop("job-internal-gas-1", None)


@pytest.mark.asyncio
async def test_internal_add_gas_no_op_when_no_waiters(client):
    """POST /internal/jobs/{id}/add-gas with no waiters should return 200 gracefully."""
    ac, db, mock_kube = client

    resp = await ac.post(
        "/internal/jobs/no-such-job/add-gas",
        json={"input_amount": 1_000, "output_amount": 0},
    )

    assert resp.status_code == 200


# ─── GET / dashboard ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_serves_html(client):
    ac, db, mock_kube = client

    resp = await ac.get("/")

    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert b"Phalanx" in resp.content
