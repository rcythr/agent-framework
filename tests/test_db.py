import pytest
import pytest_asyncio
from datetime import datetime, timezone

from gateway.db import Database
from shared.models import JobRecord, LogEvent


def _make_job(job_id: str, status: str = "pending", task: str = "review_mr") -> JobRecord:
    return JobRecord(
        id=job_id,
        task=task,
        project_id=1,
        project_name="group/repo",
        status=status,
        context={"mr_iid": 1},
        started_at=datetime.now(timezone.utc),
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(path=str(tmp_path / "test.db"))
    await database.connect()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_create_and_get_job(db):
    job = _make_job("job-1")
    await db.create_job(job)
    fetched = await db.get_job("job-1")
    assert fetched.id == "job-1"
    assert fetched.task == "review_mr"
    assert fetched.status == "pending"
    assert str(fetched.project_id) == "1"


@pytest.mark.asyncio
async def test_update_job_status(db):
    job = _make_job("job-2")
    await db.create_job(job)
    finished = datetime.now(timezone.utc)
    await db.update_job_status("job-2", "completed", finished_at=finished)
    fetched = await db.get_job("job-2")
    assert fetched.status == "completed"
    assert fetched.finished_at is not None


@pytest.mark.asyncio
async def test_list_jobs_no_filter(db):
    await db.create_job(_make_job("job-a", status="pending"))
    await db.create_job(_make_job("job-b", status="running"))
    await db.create_job(_make_job("job-c", status="completed"))
    jobs = await db.list_jobs()
    assert len(jobs) == 3


@pytest.mark.asyncio
async def test_list_jobs_status_filter(db):
    await db.create_job(_make_job("job-d", status="pending"))
    await db.create_job(_make_job("job-e", status="running"))
    await db.create_job(_make_job("job-f", status="completed"))
    pending = await db.list_jobs(status=["pending"])
    assert all(j.status == "pending" for j in pending)
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_list_jobs_limit_offset(db):
    for i in range(5):
        await db.create_job(_make_job(f"job-{i}", status="pending"))
    page1 = await db.list_jobs(limit=2, offset=0)
    page2 = await db.list_jobs(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    ids_page1 = {j.id for j in page1}
    ids_page2 = {j.id for j in page2}
    assert ids_page1.isdisjoint(ids_page2)


@pytest.mark.asyncio
async def test_get_job_raises_on_unknown(db):
    with pytest.raises(KeyError):
        await db.get_job("nonexistent-id")


# ---------------------------------------------------------------------------
# Log event tests
# ---------------------------------------------------------------------------

def _make_log_event(job_id: str, sequence: int, event_type: str = "complete") -> LogEvent:
    return LogEvent(
        job_id=job_id,
        sequence=sequence,
        timestamp=datetime.now(timezone.utc),
        event_type=event_type,
        payload={"summary": "done", "total_llm_calls": 1, "total_tool_calls": 0},
    )


@pytest.mark.asyncio
async def test_append_log_event_inserts_row(db):
    event = _make_log_event("job-log-1", 0)
    await db.append_log_event(event)
    events = await db.get_log_events("job-log-1")
    assert len(events) == 1
    assert events[0].job_id == "job-log-1"
    assert events[0].sequence == 0
    assert events[0].event_type == "complete"


@pytest.mark.asyncio
async def test_get_log_events_ordered_by_sequence(db):
    for seq in [2, 0, 1]:
        await db.append_log_event(_make_log_event("job-log-2", seq, event_type="tool_call"))
    events = await db.get_log_events("job-log-2")
    assert [e.sequence for e in events] == [0, 1, 2]


@pytest.mark.asyncio
async def test_get_log_events_empty_for_unknown_job(db):
    events = await db.get_log_events("nonexistent-job")
    assert events == []


@pytest.mark.asyncio
async def test_get_log_events_isolated_by_job_id(db):
    await db.append_log_event(_make_log_event("job-a", 0))
    await db.append_log_event(_make_log_event("job-b", 0))
    events_a = await db.get_log_events("job-a")
    assert len(events_a) == 1
    assert events_a[0].job_id == "job-a"
