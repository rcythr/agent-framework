"""Integration tests for gateway logging endpoints."""
import asyncio
import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport

from gateway.db import Database
import gateway.main as gw_main
from gateway.main import app
from shared.models import JobRecord, LogEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


def _make_log_event(job_id: str, sequence: int, event_type: str = "complete") -> dict:
    return {
        "job_id": job_id,
        "sequence": sequence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": {"summary": "done", "total_llm_calls": 1, "total_tool_calls": 0},
    }


@pytest_asyncio.fixture
async def client(tmp_path):
    db = Database(path=str(tmp_path / "test.db"))
    await db.connect()

    original_db = gw_main._db
    original_subscribers = dict(gw_main._subscribers)
    gw_main._db = db
    gw_main._subscribers.clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, db

    gw_main._db = original_db
    gw_main._subscribers.clear()
    gw_main._subscribers.update(original_subscribers)
    await db.close()


# ---------------------------------------------------------------------------
# POST /internal/log
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_log_persists_event(client):
    ac, db = client
    payload = _make_log_event("job-1", 0, "complete")
    resp = await ac.post("/internal/log", json=payload)
    assert resp.status_code == 200

    events = await db.get_log_events("job-1")
    assert len(events) == 1
    assert events[0].event_type == "complete"
    assert events[0].sequence == 0


@pytest.mark.asyncio
async def test_post_log_returns_200(client):
    ac, _ = client
    payload = _make_log_event("job-2", 0, "llm_query")
    resp = await ac.post("/internal/log", json=payload)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_log_malformed_payload_returns_422(client):
    ac, _ = client
    resp = await ac.post("/internal/log", json={"not_a_log_event": True})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /agents/{id}/logs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_logs_returns_events_in_sequence_order(client):
    ac, db = client
    # Insert events out of order via direct DB
    for seq in [2, 0, 1]:
        event = LogEvent(
            job_id="job-3",
            sequence=seq,
            timestamp=datetime.now(timezone.utc),
            event_type="tool_call",
            payload={"tool_name": "echo", "arguments": {}},
        )
        await db.append_log_event(event)

    resp = await ac.get("/agents/job-3/logs")
    assert resp.status_code == 200
    events = resp.json()
    assert [e["sequence"] for e in events] == [0, 1, 2]


@pytest.mark.asyncio
async def test_get_logs_returns_empty_list_for_unknown_job(client):
    ac, _ = client
    resp = await ac.get("/agents/nonexistent/logs")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_logs_all_event_types(client):
    ac, _ = client
    event_types = [
        "llm_query", "llm_response", "tool_call", "tool_result",
        "gas_updated", "complete",
    ]
    payloads_by_type = {
        "llm_query": {"messages": [], "model": "gpt-4o", "tools": []},
        "llm_response": {"content": "ok", "tool_calls": [], "input_tokens": 10, "output_tokens": 5},
        "tool_call": {"tool_name": "echo", "arguments": {}},
        "tool_result": {"tool_name": "echo", "result": "hi", "duration_ms": 5},
        "gas_updated": {"gas_used_input": 10, "gas_limit_input": 100, "gas_used_output": 5, "gas_limit_output": 50, "input_tokens": 10, "output_tokens": 5},
        "complete": {"summary": "done", "total_llm_calls": 1, "total_tool_calls": 1},
    }
    for seq, et in enumerate(event_types):
        payload = {
            "job_id": "job-all",
            "sequence": seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": et,
            "payload": payloads_by_type[et],
        }
        resp = await ac.post("/internal/log", json=payload)
        assert resp.status_code == 200

    resp = await ac.get("/agents/job-all/logs")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == len(event_types)
    assert [e["event_type"] for e in events] == event_types


# ---------------------------------------------------------------------------
# GET /agents/{id}/logs/stream (SSE)
#
# httpx's ASGITransport buffers the full response, so SSE tests must close
# the stream from the server side.  We do this by POSTing a terminal job
# status after the expected events, which sends None to the queue and causes
# the generator to exit cleanly.
# ---------------------------------------------------------------------------

def _parse_sse_events(content: bytes) -> list[dict]:
    events = []
    for line in content.decode().splitlines():
        if line.startswith("data: "):
            data = line[6:].strip()
            if data:
                events.append(json.loads(data))
    return events


@pytest.mark.asyncio
async def test_sse_stream_replays_existing_events(client):
    ac, db = client
    job_id = "job-sse"

    await db.create_job(_make_job(job_id, status="running"))
    for seq in range(3):
        event = LogEvent(
            job_id=job_id,
            sequence=seq,
            timestamp=datetime.now(timezone.utc),
            event_type="llm_query",
            payload={"messages": [], "model": "gpt-4o", "tools": []},
        )
        await db.append_log_event(event)

    async def read_sse():
        return await ac.get(f"/agents/{job_id}/logs/stream")

    async def close_stream():
        # Give the SSE generator time to start and register its queue
        await asyncio.sleep(0.05)
        await ac.post(f"/internal/jobs/{job_id}/status", json={"status": "completed"})

    results = await asyncio.gather(read_sse(), close_stream())
    received = _parse_sse_events(results[0].content)

    assert len(received) == 3
    assert [e["sequence"] for e in received] == [0, 1, 2]
    assert all(e["event_type"] == "llm_query" for e in received)


@pytest.mark.asyncio
async def test_sse_stream_delivers_new_events(client):
    """SSE stream delivers events posted after connecting (beyond the replay)."""
    ac, db = client
    job_id = "job-sse-live"

    await db.create_job(_make_job(job_id, status="running"))

    async def read_sse():
        return await ac.get(f"/agents/{job_id}/logs/stream")

    async def post_and_close():
        # Give SSE generator time to start
        await asyncio.sleep(0.05)
        for seq in range(2):
            payload = _make_log_event(job_id, seq, "tool_call")
            await ac.post("/internal/log", json=payload)
        # Close the stream by posting terminal status
        await ac.post(f"/internal/jobs/{job_id}/status", json={"status": "completed"})

    results = await asyncio.gather(read_sse(), post_and_close())
    received = _parse_sse_events(results[0].content)

    assert len(received) == 2
    assert all(e["event_type"] == "tool_call" for e in received)
    assert [e["sequence"] for e in received] == [0, 1]
