import pytest
import pytest_asyncio
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport

from gateway.db import Database
from gateway.main import app, _db
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
    )


@pytest_asyncio.fixture
async def client(tmp_path):
    """Create test client with an isolated in-memory database."""
    db = Database(path=str(tmp_path / "test.db"))
    await db.connect()

    # Replace the module-level _db with our test instance
    import gateway.main as gw_main
    original_db = gw_main._db
    gw_main._db = db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, db

    gw_main._db = original_db
    await db.close()


@pytest.mark.asyncio
async def test_job_status_completed_updates_db(client):
    ac, db = client
    job = _make_job("job-1", status="running")
    await db.create_job(job)

    resp = await ac.post("/internal/jobs/job-1/status", json={"status": "completed"})
    assert resp.status_code == 200

    updated = await db.get_job("job-1")
    assert updated.status == "completed"
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_job_status_failed_updates_db(client):
    ac, db = client
    job = _make_job("job-2", status="running")
    await db.create_job(job)

    resp = await ac.post("/internal/jobs/job-2/status", json={"status": "failed"})
    assert resp.status_code == 200

    updated = await db.get_job("job-2")
    assert updated.status == "failed"


@pytest.mark.asyncio
async def test_job_status_unknown_job_returns_404(client):
    ac, _ = client
    resp = await ac.post("/internal/jobs/nonexistent-id/status", json={"status": "completed"})
    assert resp.status_code == 404
