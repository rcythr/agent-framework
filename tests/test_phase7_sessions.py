"""
Phase 7 — Interactive Sessions tests.

Covers:
7a: Session data layer and broker
    - SessionBroker unit tests
    - DB session CRUD round-trips
    - POST /sessions creates SessionRecord in configuring status, spawns K8s Job
    - POST /internal/sessions/{id}/await-input blocks until message sent; returns content
    - POST /internal/sessions/{id}/interrupt-check returns/clears interrupt

7b: Worker session mode
    - Worker calls interrupt-check at start of each loop iteration (via event handler)
    - Worker injects interrupt message into LLM context
    - Worker calls await-input when agent emits input_request; context updated on resume
    - GET /sessions/{id}/stream delivers SessionMessage and LogEvent records

7c: Session messaging
    - Full session lifecycle: create → await-input → send message → verify resume

7d: Project proxy endpoints
    - GET /projects/search returns results from provider
"""

import asyncio
import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from httpx import AsyncClient, ASGITransport

from gateway.db import Database
from gateway.session_broker import SessionBroker
from gateway.main import app
from shared.models import SessionRecord, SessionContext, SessionMessage, JobRecord


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_session(session_id: str, owner: str = "alice", status: str = "running") -> SessionRecord:
    ctx = SessionContext(
        project_id=1,
        project_path="group/repo",
        branch="main",
        goal="Do something useful",
    )
    return SessionRecord(
        id=session_id,
        owner=owner,
        project_id=1,
        project_path="group/repo",
        branch="main",
        mr_iid=None,
        status=status,
        context=ctx,
        created_at=datetime.now(timezone.utc),
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(path=str(tmp_path / "test.db"))
    await database.connect()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def client(tmp_path):
    """Create test client with isolated DB, mocked KubeClient, and mocked auth."""
    db = Database(path=str(tmp_path / "test.db"))
    await db.connect()

    mock_kube = MagicMock()
    mock_kube.spawn_session_job.return_value = "pi-session-test-abc"

    mock_auth = MagicMock()
    mock_auth.extract_user.return_value = MagicMock(username="alice", email="alice@example.com", groups=[])

    mock_provider = MagicMock()
    mock_provider.search_projects.return_value = [
        {"id": 1, "name": "repo", "path_with_namespace": "group/repo", "namespace": {"name": "group"}}
    ]
    mock_provider.list_branches.return_value = ["main", "dev"]
    mock_provider.list_open_mrs.return_value = []

    from gateway.session_broker import SessionBroker
    fresh_broker = SessionBroker()

    import gateway.main as gw_main
    original_db = gw_main._db
    original_kube = gw_main._kube
    original_auth = gw_main._auth_provider
    original_provider = gw_main._provider
    original_broker = gw_main._session_broker

    gw_main._db = db
    gw_main._kube = mock_kube
    gw_main._auth_provider = mock_auth
    gw_main._provider = mock_provider
    gw_main._session_broker = fresh_broker

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, db, mock_kube, fresh_broker

    gw_main._db = original_db
    gw_main._kube = original_kube
    gw_main._auth_provider = original_auth
    gw_main._provider = original_provider
    gw_main._session_broker = original_broker
    await db.close()


# ── 7a: SessionBroker unit tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broker_send_to_agent_enqueues_and_transitions():
    """send_to_agent enqueues a message and transitions waiting_for_user → running."""
    broker = SessionBroker()
    await broker.register("sess-1")
    broker._statuses["sess-1"] = "waiting_for_user"

    await broker.send_to_agent("sess-1", "hello", "input_response")

    assert broker._statuses["sess-1"] == "running"
    assert not broker._input_queues["sess-1"].empty()
    msg = await broker._input_queues["sess-1"].get()
    assert msg == "hello"


@pytest.mark.asyncio
async def test_broker_await_user_input_blocks_then_returns():
    """await_user_input transitions to waiting_for_user; blocks until message; returns content."""
    broker = SessionBroker()
    await broker.register("sess-2")

    result_holder = []

    async def waiter():
        result = await broker.await_user_input("sess-2", "What now?")
        result_holder.append(result)

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.05)  # Let the waiter block

    assert broker._statuses["sess-2"] == "waiting_for_user"
    await broker.send_to_agent("sess-2", "my answer", "input_response")
    await task

    assert result_holder == ["my answer"]
    assert broker._statuses["sess-2"] == "running"


@pytest.mark.asyncio
async def test_broker_check_interrupt_returns_and_clears():
    """check_interrupt returns pending interrupt and clears it; None when none pending."""
    broker = SessionBroker()
    await broker.register("sess-3")

    broker._interrupts["sess-3"] = "stop and redirect"

    result = broker.check_interrupt("sess-3")
    assert result == "stop and redirect"

    # Second call returns None (cleared)
    result2 = broker.check_interrupt("sess-3")
    assert result2 is None


@pytest.mark.asyncio
async def test_broker_interrupt_sets_flag_not_queue():
    """send_to_agent with interrupt type sets interrupt flag, not input queue."""
    broker = SessionBroker()
    await broker.register("sess-4")

    await broker.send_to_agent("sess-4", "redirect to X", "interrupt")

    assert broker._interrupts.get("sess-4") == "redirect to X"
    assert broker._input_queues["sess-4"].empty()


@pytest.mark.asyncio
async def test_broker_cleanup_removes_queues():
    """cleanup removes all per-session state."""
    broker = SessionBroker()
    await broker.register("sess-5")
    broker._interrupts["sess-5"] = "some interrupt"
    await broker.send_to_agent("sess-5", "msg", "instruction")

    await broker.cleanup("sess-5")

    assert "sess-5" not in broker._input_queues
    assert "sess-5" not in broker._interrupts
    assert "sess-5" not in broker._statuses


# ── 7a: DB session CRUD ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_create_and_get_session(db):
    session = _make_session("s-1")
    await db.create_session(session)
    fetched = await db.get_session("s-1")
    assert fetched.id == "s-1"
    assert fetched.owner == "alice"
    assert fetched.project_path == "group/repo"
    assert fetched.branch == "main"
    assert fetched.status == "running"


@pytest.mark.asyncio
async def test_db_update_session_status(db):
    session = _make_session("s-2")
    await db.create_session(session)
    finished = datetime.now(timezone.utc)
    await db.update_session_status("s-2", "complete", finished_at=finished)
    fetched = await db.get_session("s-2")
    assert fetched.status == "complete"
    assert fetched.finished_at is not None


@pytest.mark.asyncio
async def test_db_get_session_raises_on_unknown(db):
    with pytest.raises(KeyError):
        await db.get_session("nonexistent")


@pytest.mark.asyncio
async def test_db_list_sessions_filters_by_owner(db):
    await db.create_session(_make_session("s-3", owner="alice"))
    await db.create_session(_make_session("s-4", owner="bob"))
    alice_sessions = await db.list_sessions(owner="alice")
    assert all(s.owner == "alice" for s in alice_sessions)
    assert len(alice_sessions) == 1


@pytest.mark.asyncio
async def test_db_session_message_round_trip(db):
    session = _make_session("s-5")
    await db.create_session(session)
    msg = SessionMessage(
        session_id="s-5",
        sequence=0,
        timestamp=datetime.now(timezone.utc),
        role="user",
        content="hello agent",
        message_type="instruction",
    )
    await db.append_session_message(msg)
    msgs = await db.get_session_messages("s-5")
    assert len(msgs) == 1
    assert msgs[0].content == "hello agent"
    assert msgs[0].role == "user"
    assert msgs[0].message_type == "instruction"


@pytest.mark.asyncio
async def test_db_session_messages_ordered_by_sequence(db):
    session = _make_session("s-6")
    await db.create_session(session)
    for seq in [2, 0, 1]:
        msg = SessionMessage(
            session_id="s-6",
            sequence=seq,
            timestamp=datetime.now(timezone.utc),
            role="agent",
            content=f"msg {seq}",
            message_type="agent_response",
        )
        await db.append_session_message(msg)
    msgs = await db.get_session_messages("s-6")
    assert [m.sequence for m in msgs] == [0, 1, 2]


# ── 7a: Integration — POST /sessions ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_session_returns_configuring_then_running(client):
    """POST /sessions creates SessionRecord; kube spawns job with session context."""
    ac, db, mock_kube, broker = client

    resp = await ac.post("/sessions", json={
        "project_id": 1,
        "project_path": "group/repo",
        "branch": "main",
        "goal": "Review the latest changes",
        "gas_limit_input": 160000,
        "gas_limit_output": 40000,
    })

    assert resp.status_code == 200
    data = resp.json()
    # Final status is "running" (gateway transitions after spawning)
    assert data["status"] == "running"
    assert data["project_path"] == "group/repo"
    assert data["branch"] == "main"

    # Kube was called
    mock_kube.spawn_session_job.assert_called_once()

    # DB record exists
    session = await db.get_session(data["id"])
    assert session.owner == "alice"
    assert session.project_id == 1


@pytest.mark.asyncio
async def test_create_session_spawns_k8s_job_with_session_id(client):
    """Verify spawn_session_job is called with a SessionRecord."""
    ac, db, mock_kube, broker = client

    await ac.post("/sessions", json={
        "project_id": 2,
        "project_path": "ns/proj2",
        "branch": "dev",
        "goal": "Analyze push",
    })

    mock_kube.spawn_session_job.assert_called_once()
    call_args = mock_kube.spawn_session_job.call_args[0]
    session_arg = call_args[0]
    assert session_arg.project_path == "ns/proj2"
    assert session_arg.branch == "dev"
    assert session_arg.context.goal == "Analyze push"


# ── 7a: Integration — internal await-input / interrupt-check ─────────────────

@pytest.mark.asyncio
async def test_internal_await_input_blocks_until_message_sent(client):
    """POST /internal/sessions/{id}/await-input blocks until POST /sessions/{id}/messages."""
    ac, db, mock_kube, broker = client

    # Create a session
    create_resp = await ac.post("/sessions", json={
        "project_id": 1,
        "project_path": "g/r",
        "branch": "main",
        "goal": "Test interactive",
    })
    session_id = create_resp.json()["id"]

    # Start await-input in background
    await_result = []
    async def do_await():
        resp = await ac.post(
            f"/internal/sessions/{session_id}/await-input",
            json={"question": "What should I do next?"},
            timeout=5.0,
        )
        await_result.append(resp.json())

    task = asyncio.create_task(do_await())
    await asyncio.sleep(0.1)  # Let await-input block

    # Send user message
    msg_resp = await ac.post(
        f"/sessions/{session_id}/messages",
        json={"content": "Please check the tests", "message_type": "input_response"},
    )
    assert msg_resp.status_code == 200

    await asyncio.wait_for(task, timeout=3.0)
    assert await_result[0]["content"] == "Please check the tests"


@pytest.mark.asyncio
async def test_internal_interrupt_check_returns_and_clears(client):
    """POST /internal/sessions/{id}/interrupt-check returns interrupt; empty on second call."""
    ac, db, mock_kube, broker = client

    create_resp = await ac.post("/sessions", json={
        "project_id": 1,
        "project_path": "g/r",
        "branch": "main",
        "goal": "Test interrupt",
    })
    session_id = create_resp.json()["id"]

    # Send an interrupt message
    await ac.post(
        f"/sessions/{session_id}/messages",
        json={"content": "Stop and focus on X", "message_type": "interrupt"},
    )

    # First interrupt-check returns the interrupt
    check1 = await ac.post(f"/internal/sessions/{session_id}/interrupt-check", json={})
    assert check1.status_code == 200
    assert check1.json().get("interrupt") == "Stop and focus on X"

    # Second interrupt-check returns empty (cleared)
    check2 = await ac.post(f"/internal/sessions/{session_id}/interrupt-check", json={})
    assert check2.status_code == 200
    assert "interrupt" not in check2.json() or check2.json().get("interrupt") is None


# ── 7b: Worker session mode ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_event_handler_calls_interrupt_check_on_llm_query():
    """Worker in session mode calls interrupt-check at start of each loop iteration."""
    from worker.agent_runner import _SessionEventHandler
    from worker.agent import AgentEvent

    mock_logger = MagicMock()
    mock_logger.handle_event = AsyncMock()

    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        if "/interrupt-check" in str(args):
            call_count += 1
        resp = MagicMock()
        resp.json.return_value = {}
        return resp

    mock_http = MagicMock()
    mock_http.post = mock_post

    handler = _SessionEventHandler(
        agent_logger=mock_logger,
        session_id="sess-test",
        gateway_url="http://gateway",
        http_client=mock_http,
    )

    # Simulate three llm_query events
    for _ in range(3):
        await handler(AgentEvent(event_type="llm_query", payload={"messages": 2}))

    assert call_count == 3


@pytest.mark.asyncio
async def test_session_event_handler_injects_interrupt_into_agent():
    """Worker injects interrupt message into LLM context when interrupt is returned."""
    from worker.agent_runner import _SessionEventHandler
    from worker.agent import AgentEvent, Agent

    mock_logger = MagicMock()
    mock_logger.handle_event = AsyncMock()

    mock_agent = MagicMock()
    mock_agent.steer = MagicMock()

    async def mock_post(url, **kwargs):
        resp = MagicMock()
        if "/interrupt-check" in url:
            resp.json.return_value = {"interrupt": "focus on tests"}
        else:
            resp.json.return_value = {}
        return resp

    mock_http = MagicMock()
    mock_http.post = mock_post

    handler = _SessionEventHandler(
        agent_logger=mock_logger,
        session_id="sess-x",
        gateway_url="http://gateway",
        http_client=mock_http,
    )
    handler.agent = mock_agent

    await handler(AgentEvent(event_type="llm_query", payload={"messages": 1}))

    mock_agent.steer.assert_called_once_with("focus on tests")


@pytest.mark.asyncio
async def test_session_event_handler_calls_await_input_on_input_request():
    """Worker calls await-input when agent emits input_request; follow_up is set."""
    from worker.agent_runner import _SessionEventHandler
    from worker.agent import AgentEvent

    mock_logger = MagicMock()
    mock_logger.handle_event = AsyncMock()

    mock_agent = MagicMock()
    mock_agent.follow_up = MagicMock()

    await_input_called = []

    async def mock_post(url, **kwargs):
        resp = MagicMock()
        if "/await-input" in url:
            await_input_called.append(kwargs.get("json", {}))
            resp.json.return_value = {"content": "user says: proceed"}
        else:
            resp.json.return_value = {}
        return resp

    mock_http = MagicMock()
    mock_http.post = mock_post

    handler = _SessionEventHandler(
        agent_logger=mock_logger,
        session_id="sess-y",
        gateway_url="http://gateway",
        http_client=mock_http,
    )
    handler.agent = mock_agent

    await handler(AgentEvent(event_type="input_request", payload={"question": "Should I continue?"}))

    assert len(await_input_called) == 1
    assert await_input_called[0]["question"] == "Should I continue?"
    mock_agent.follow_up.assert_called_once_with("user says: proceed")


# ── 7b: SSE stream ────────────────────────────────────────────────────────────

def _parse_session_sse(content: bytes) -> list[dict]:
    events = []
    for line in content.decode().splitlines():
        if line.startswith("data: "):
            data = line[6:].strip()
            if data:
                try:
                    events.append(json.loads(data))
                except json.JSONDecodeError:
                    pass
    return events


@pytest.mark.asyncio
async def test_session_stream_replays_existing_messages(client):
    """GET /sessions/{id}/stream replays SessionMessage and LogEvent records in order."""
    ac, db, mock_kube, broker = client

    # Create session
    create_resp = await ac.post("/sessions", json={
        "project_id": 1,
        "project_path": "g/r",
        "branch": "main",
        "goal": "Stream test",
    })
    session_id = create_resp.json()["id"]

    # Post a message so there is something to replay
    await ac.post(
        f"/sessions/{session_id}/messages",
        json={"content": "test instruction", "message_type": "instruction"},
    )

    # SSE with httpx ASGI transport buffers until connection closes.
    # Close by posting terminal status after giving generator time to register.
    async def read_stream():
        return await ac.get(f"/sessions/{session_id}/stream")

    async def close_stream():
        await asyncio.sleep(0.05)
        await ac.post(
            f"/internal/sessions/{session_id}/status",
            json={"status": "complete"},
        )

    results = await asyncio.gather(read_stream(), close_stream())
    events = _parse_session_sse(results[0].content)

    # Should contain session_message records (at least the user instruction)
    session_messages = [e for e in events if e.get("type") == "session_message"]
    assert len(session_messages) >= 1
    assert any(e.get("content") == "test instruction" for e in session_messages)


# ── 7c: Full session lifecycle ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_session_lifecycle(client):
    """Create session → simulate await-input → send user message → verify resume."""
    ac, db, mock_kube, broker = client

    # Create session
    resp = await ac.post("/sessions", json={
        "project_id": 1,
        "project_path": "g/r",
        "branch": "main",
        "goal": "Full lifecycle test",
    })
    assert resp.status_code == 200
    session_id = resp.json()["id"]

    # Simulate worker calling await-input (in background, blocks)
    answer_received = []
    async def worker_await():
        r = await ac.post(
            f"/internal/sessions/{session_id}/await-input",
            json={"question": "Shall I proceed?"},
            timeout=5.0,
        )
        answer_received.append(r.json()["content"])

    task = asyncio.create_task(worker_await())
    await asyncio.sleep(0.1)

    # Verify session is waiting_for_user in DB
    session = await db.get_session(session_id)
    assert session.status == "waiting_for_user"

    # User sends a response
    msg_resp = await ac.post(
        f"/sessions/{session_id}/messages",
        json={"content": "Yes, proceed", "message_type": "input_response"},
    )
    assert msg_resp.status_code == 200

    await asyncio.wait_for(task, timeout=3.0)
    assert answer_received == ["Yes, proceed"]

    # Simulate worker completing
    status_resp = await ac.post(
        f"/internal/sessions/{session_id}/status",
        json={"status": "complete"},
    )
    assert status_resp.status_code == 200

    # Verify final status
    final = await db.get_session(session_id)
    assert final.status == "complete"
    assert final.finished_at is not None


# ── 7d: Project proxy endpoints ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_projects_search_returns_provider_results(client):
    """GET /projects/search proxies to provider and returns results."""
    ac, db, mock_kube, broker = client

    resp = await ac.get("/projects/search?q=repo")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(p.get("name") == "repo" for p in data)


@pytest.mark.asyncio
async def test_projects_branches_returns_list(client):
    """GET /projects/{id}/branches returns branch list from provider."""
    ac, db, mock_kube, broker = client

    resp = await ac.get("/projects/1/branches")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert "main" in data


@pytest.mark.asyncio
async def test_projects_mrs_returns_list(client):
    """GET /projects/{id}/mrs returns open MRs from provider."""
    ac, db, mock_kube, broker = client

    resp = await ac.get("/projects/1/mrs")

    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ── Session CRUD API tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_session_returns_session(client):
    ac, db, mock_kube, broker = client
    create_resp = await ac.post("/sessions", json={
        "project_id": 1,
        "project_path": "g/r",
        "branch": "main",
        "goal": "Get test",
    })
    session_id = create_resp.json()["id"]

    resp = await ac.get(f"/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == session_id


@pytest.mark.asyncio
async def test_get_session_nonexistent_returns_404(client):
    ac, db, mock_kube, broker = client
    resp = await ac.get("/sessions/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_sessions_returns_owner_sessions(client):
    ac, db, mock_kube, broker = client
    await ac.post("/sessions", json={
        "project_id": 1, "project_path": "g/r", "branch": "main", "goal": "sess A",
    })
    await ac.post("/sessions", json={
        "project_id": 2, "project_path": "g/r2", "branch": "dev", "goal": "sess B",
    })

    resp = await ac.get("/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
    assert all(s["owner"] == "alice" for s in data)


@pytest.mark.asyncio
async def test_get_session_messages_returns_messages(client):
    ac, db, mock_kube, broker = client
    create_resp = await ac.post("/sessions", json={
        "project_id": 1, "project_path": "g/r", "branch": "main", "goal": "msg test",
    })
    session_id = create_resp.json()["id"]

    await ac.post(f"/sessions/{session_id}/messages", json={
        "content": "first message", "message_type": "instruction",
    })

    resp = await ac.get(f"/sessions/{session_id}/messages")
    assert resp.status_code == 200
    msgs = resp.json()
    assert any(m["content"] == "first message" for m in msgs)


@pytest.mark.asyncio
async def test_session_gas_endpoints(client):
    ac, db, mock_kube, broker = client
    create_resp = await ac.post("/sessions", json={
        "project_id": 1, "project_path": "g/r", "branch": "main", "goal": "gas test",
        "gas_limit_input": 160000, "gas_limit_output": 40000,
    })
    session_id = create_resp.json()["id"]

    # Get gas
    gas_resp = await ac.get(f"/sessions/{session_id}/gas")
    assert gas_resp.status_code == 200
    gas = gas_resp.json()
    assert gas["gas_limit_input"] == 160000
    assert gas["gas_limit_output"] == 40000

    # Add gas
    topup_resp = await ac.post(f"/sessions/{session_id}/gas", json={
        "input_amount": 20000, "output_amount": 5000,
    })
    assert topup_resp.status_code == 200
    updated = topup_resp.json()
    assert updated["gas_limit_input"] == 180000
    assert updated["gas_limit_output"] == 45000
