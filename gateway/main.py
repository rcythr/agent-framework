import asyncio
import os
import secrets
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from sse_starlette.sse import EventSourceResponse

from gateway.config_loader import ConfigLoader
from gateway.db import Database
from gateway.event_mapper import map_event_to_task
from gateway.kube_client import KubeClient
from gateway.session_broker import SessionBroker
from providers.registry import get_provider
from providers.auth_registry import get_auth_provider
from shared.models import (
    AgentConfig, JobRecord, SkillDef, ToolDef, TaskSpec, LogEvent,
    SessionRecord, SessionMessage, SessionContext, ActivationRecord,
)

WEBHOOK_SECRET = (
    os.getenv("WEBHOOK_SECRET")
    or os.getenv("GITLAB_WEBHOOK_SECRET")
    or os.getenv("GITHUB_WEBHOOK_SECRET")
    or os.getenv("BITBUCKET_WEBHOOK_SECRET")
    or os.getenv("GITEA_WEBHOOK_SECRET")
    or "dev-webhook-secret"
)
PHALANX_WEBHOOK_URL = os.getenv("PHALANX_WEBHOOK_URL", "")
DEFAULT_SESSION_INPUT_GAS_LIMIT = int(os.getenv("DEFAULT_SESSION_INPUT_GAS_LIMIT", "160000"))
DEFAULT_SESSION_OUTPUT_GAS_LIMIT = int(os.getenv("DEFAULT_SESSION_OUTPUT_GAS_LIMIT", "40000"))

_db = Database()
_kube: KubeClient | None = None
_provider = None
_auth_provider = None
_config_loader: ConfigLoader | None = None
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
_session_broker = SessionBroker()
_session_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "out_of_gas"}
_SESSION_TERMINAL_STATUSES = {"complete", "failed", "cancelled", "out_of_gas"}
_gas_waiters: dict[str, list[asyncio.Queue]] = defaultdict(list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kube, _provider, _auth_provider, _config_loader
    await _db.connect()
    _kube = KubeClient()
    _provider = get_provider()
    _auth_provider = get_auth_provider()
    _config_loader = ConfigLoader(provider=_provider, kube_client=_kube)
    yield
    await _db.close()


app = FastAPI(lifespan=lifespan)

_dist_assets = os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist", "assets")
if os.path.isdir(_dist_assets):
    app.mount("/assets", StaticFiles(directory=_dist_assets), name="static_assets")


def _default_agent_config() -> AgentConfig:
    """Return a permissive default AgentConfig used for manual /trigger calls."""
    return AgentConfig(
        skills=[],
        tools=[],
        system_prompt="",
        image=os.getenv("PI_AGENT_IMAGE", "localhost:5001/pi-agent-worker:latest"),
        gas_limit_input=80_000,
        gas_limit_output=20_000,
        allowed_users=[],
    )


def _make_job_record(job_name: str, task_spec: TaskSpec, agent_config: AgentConfig, triggered_by: str = "system") -> JobRecord:
    return JobRecord(
        id=job_name,
        task=task_spec.task,
        project_id=task_spec.project_id,
        project_name=str(task_spec.project_id),
        status="pending",
        context=task_spec.context,
        started_at=datetime.now(timezone.utc),
        triggered_by=triggered_by,
        gas_limit_input=agent_config.gas_limit_input,
        gas_limit_output=agent_config.gas_limit_output,
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


async def _process_webhook(request: Request):
    import json as _json
    body_bytes = await request.body()
    headers = dict(request.headers)
    body_dict = _json.loads(body_bytes)

    # Step 1: parse first (safe) to extract project_id for per-repo secret lookup
    event = _provider.parse_webhook_event(headers, body_dict)
    project_id_str = str(event.project_id) if event else None

    # Step 2: look up per-repo secret from DB; fall back to global secret
    secret = WEBHOOK_SECRET
    if project_id_str:
        activation = await _db.get_activation(project_id_str)
        if activation:
            secret = activation.secret

    # Step 3: verify signature with the resolved secret
    if not _provider.verify_webhook(headers, body_bytes, secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if event is None:
        return Response(status_code=200)

    # Step 4: map to task
    task_spec = map_event_to_task(event)
    if task_spec is None:
        return Response(status_code=200)

    # Step 5: resolve agent config from per-project .agents/config.yaml
    commits = task_spec.context.get("commits") or []
    sha = task_spec.context.get("sha") or (commits[0].get("sha", "HEAD") if commits else "HEAD")
    agent_config = await _config_loader.resolve(task_spec.project_id, sha)

    # Step 6: check actor against allowed_users (deny-by-default: empty list = no dispatch)
    actor = getattr(event, "actor", None)
    if not agent_config.allowed_users or actor not in agent_config.allowed_users:
        import logging
        logging.getLogger(__name__).info(
            "Webhook dispatch rejected: actor=%r not in allowed_users=%r for project %s",
            actor, agent_config.allowed_users, task_spec.project_id,
        )
        return Response(status_code=200)

    # Step 7: spawn K8s job
    job_name = _kube.spawn_agent_job(task_spec, agent_config)

    # Step 8: persist job record
    await _db.create_job(_make_job_record(job_name, task_spec, agent_config))

    return {"job_name": job_name}


@app.post("/webhook")
async def webhook(request: Request):
    return await _process_webhook(request)


@app.post("/webhook/gitlab")  # kept for backward compatibility with existing webhook configs
async def webhook_gitlab(request: Request):
    return await _process_webhook(request)


@app.post("/trigger")
async def trigger(task_spec: TaskSpec, request: Request):
    # Manual trigger: skip allowed_users check (access controlled by dashboard auth layer)
    agent_config = _default_agent_config()

    # Identify the operator via auth_provider — never read X-Forwarded-User directly
    identity = _auth_provider.extract_user(dict(request.headers))
    triggered_by = identity.username if identity.username else "system"

    job_name = _kube.spawn_agent_job(task_spec, agent_config)
    await _db.create_job(_make_job_record(job_name, task_spec, agent_config, triggered_by=triggered_by))

    return {"job_name": job_name}


@app.get("/agents")
async def list_agents():
    jobs = await _db.list_jobs(status=["pending", "running"])
    return [j.model_dump(mode="json") for j in jobs]


@app.get("/agents/history")
async def agents_history(limit: int = 50, offset: int = 0):
    jobs = await _db.list_jobs(
        status=["completed", "failed", "cancelled", "out_of_gas"],
        limit=limit,
        offset=offset,
    )
    return [j.model_dump(mode="json") for j in jobs]


@app.post("/internal/jobs/{job_id}/status")
async def update_job_status(job_id: str, body: dict):
    status = body.get("status")
    try:
        await _db.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    finished_at = datetime.now(timezone.utc)
    await _db.update_job_status(job_id, status, finished_at=finished_at)
    if status in _TERMINAL_STATUSES:
        for q in _subscribers.pop(job_id, []):
            await q.put(None)
    return {"job_id": job_id, "status": status}


@app.post("/internal/log")
async def post_log(event: LogEvent):
    await _db.append_log_event(event)
    for q in list(_subscribers.get(event.job_id, [])):
        await q.put(event)
    # Also notify session SSE subscribers (session workers post with job_id=session_id)
    session_data = {"type": "log_event", **event.model_dump(mode="json")}
    for q in list(_session_subscribers.get(event.job_id, [])):
        await q.put(session_data)
    return Response(status_code=200)


@app.get("/agents/{job_id}/logs")
async def get_logs(job_id: str):
    events = await _db.get_log_events(job_id)
    return [e.model_dump(mode="json") for e in events]


@app.get("/agents/{job_id}/logs/stream")
async def stream_logs(job_id: str, request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers[job_id].append(queue)

    async def event_generator():
        try:
            for event in await _db.get_log_events(job_id):
                yield {"data": event.model_dump_json()}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    if event is None:
                        break
                    yield {"data": event.model_dump_json()}
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield {"data": ""}
        finally:
            if queue in _subscribers.get(job_id, []):
                _subscribers[job_id].remove(queue)
            if not _subscribers.get(job_id):
                _subscribers.pop(job_id, None)

    return EventSourceResponse(event_generator())


@app.get("/internal/oauth2-proxy-config")
async def oauth2_proxy_config():
    cfg = _auth_provider.oauth_proxy_config()
    args = [f"--provider={cfg.provider_flag}"] + cfg.extra_flags
    return {"args": args}


@app.post("/agents/{job_id}/cancel")
async def cancel_agent(job_id: str):
    try:
        await _db.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    try:
        _kube.delete_job(job_id)
    except Exception:
        pass  # Job may already be gone or not exist in K8s
    finished_at = datetime.now(timezone.utc)
    await _db.update_job_status(job_id, "cancelled", finished_at=finished_at)
    for q in _subscribers.pop(job_id, []):
        await q.put(None)
    return {"job_id": job_id, "status": "cancelled"}


@app.get("/agents/{job_id}/gas")
async def get_gas(job_id: str):
    try:
        job = await _db.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return {
        "gas_used_input": job.gas_used_input,
        "gas_limit_input": job.gas_limit_input,
        "gas_used_output": job.gas_used_output,
        "gas_limit_output": job.gas_limit_output,
        "topup_history": job.gas_topups,
    }


@app.post("/agents/{job_id}/gas")
async def add_gas(job_id: str, body: dict):
    try:
        job = await _db.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    input_amount = body.get("input_amount", 0)
    output_amount = body.get("output_amount", 0)
    await _db.add_gas(job_id, input_amount=input_amount, output_amount=output_amount)
    if job.status == "out_of_gas":
        # Unblock the waiting worker via in-memory gas waiters
        for q in list(_gas_waiters.get(job_id, [])):
            await q.put({"input_amount": input_amount, "output_amount": output_amount})
    job = await _db.get_job(job_id)
    return {
        "gas_used_input": job.gas_used_input,
        "gas_limit_input": job.gas_limit_input,
        "gas_used_output": job.gas_used_output,
        "gas_limit_output": job.gas_limit_output,
        "topup_history": job.gas_topups,
        "status": job.status,
    }


@app.post("/internal/jobs/{job_id}/add-gas")
async def internal_add_gas(job_id: str, body: dict):
    """Signal any worker suspended in out_of_gas state to resume."""
    for q in list(_gas_waiters.get(job_id, [])):
        await q.put(body)
    return {"job_id": job_id}


# ── Session endpoints ────────────────────────────────────────────────────────

@app.post("/sessions")
async def create_session(body: dict, request: Request):
    identity = _auth_provider.extract_user(dict(request.headers))
    owner = identity.username or "anonymous"
    context = SessionContext(**body)
    session_id = f"session-{uuid.uuid4().hex[:12]}"
    session = SessionRecord(
        id=session_id,
        owner=owner,
        project_id=context.project_id,
        project_path=context.project_path,
        branch=context.branch,
        mr_iid=context.mr_iid,
        status="configuring",
        context=context,
        created_at=datetime.now(timezone.utc),
        gas_limit_input=context.gas_limit_input,
        gas_limit_output=context.gas_limit_output,
    )
    await _db.create_session(session)
    _kube.spawn_session_job(session)
    await _session_broker.register(session_id)
    await _db.update_session_status(session_id, "running")
    session = await _db.get_session(session_id)
    return session.model_dump(mode="json")


@app.get("/sessions")
async def list_sessions(request: Request, status: str | None = None):
    identity = _auth_provider.extract_user(dict(request.headers))
    owner = identity.username or "anonymous"
    status_filter = [status] if status else None
    sessions = await _db.list_sessions(owner=owner, status=status_filter)
    return [s.model_dump(mode="json") for s in sessions]


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    try:
        session = await _db.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    identity = _auth_provider.extract_user(dict(request.headers))
    owner = identity.username or "anonymous"
    if session.owner != owner and owner != "anonymous":
        raise HTTPException(status_code=403, detail="Forbidden")
    return session.model_dump(mode="json")


@app.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    try:
        await _db.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    messages = await _db.get_session_messages(session_id)
    return [m.model_dump(mode="json") for m in messages]


@app.post("/sessions/{session_id}/messages")
async def post_session_message(session_id: str, body: dict):
    try:
        await _db.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    content = body.get("content", "")
    message_type = body.get("message_type", "instruction")
    existing = await _db.get_session_messages(session_id)
    sequence = len(existing)
    message = SessionMessage(
        session_id=session_id,
        sequence=sequence,
        timestamp=datetime.now(timezone.utc),
        role="user",
        content=content,
        message_type=message_type,
    )
    await _db.append_session_message(message)
    await _session_broker.send_to_agent(session_id, content, message_type)
    # Notify SSE subscribers
    data = {"type": "session_message", **message.model_dump(mode="json")}
    for q in list(_session_subscribers.get(session_id, [])):
        await q.put(data)
    return message.model_dump(mode="json")


@app.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    _session_subscribers[session_id].append(queue)

    import json as _json

    async def event_generator():
        try:
            # Replay existing session messages
            for msg in await _db.get_session_messages(session_id):
                yield {"data": _json.dumps({"type": "session_message", **msg.model_dump(mode="json")})}
            # Replay existing log events (worker posts to /internal/log with job_id=session_id)
            for event in await _db.get_log_events(session_id):
                yield {"data": _json.dumps({"type": "log_event", **event.model_dump(mode="json")})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.0)
                    if item is None:
                        break
                    yield {"data": _json.dumps(item)}
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield {"data": ""}
        finally:
            if queue in _session_subscribers.get(session_id, []):
                _session_subscribers[session_id].remove(queue)
            if not _session_subscribers.get(session_id):
                _session_subscribers.pop(session_id, None)

    return EventSourceResponse(event_generator())


@app.get("/sessions/{session_id}/gas")
async def get_session_gas(session_id: str):
    try:
        session = await _db.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return {
        "gas_used_input": session.gas_used_input,
        "gas_limit_input": session.gas_limit_input,
        "gas_used_output": session.gas_used_output,
        "gas_limit_output": session.gas_limit_output,
        "topup_history": session.gas_topups,
    }


@app.post("/sessions/{session_id}/gas")
async def add_session_gas(session_id: str, body: dict):
    try:
        await _db.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    input_amount = body.get("input_amount", 0)
    output_amount = body.get("output_amount", 0)
    await _db.add_session_gas(session_id, input_amount=input_amount, output_amount=output_amount)
    session = await _db.get_session(session_id)
    return {
        "gas_used_input": session.gas_used_input,
        "gas_limit_input": session.gas_limit_input,
        "gas_used_output": session.gas_used_output,
        "gas_limit_output": session.gas_limit_output,
        "topup_history": session.gas_topups,
        "status": session.status,
    }


@app.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str):
    try:
        session = await _db.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    finished_at = datetime.now(timezone.utc)
    await _db.update_session_status(session_id, "cancelled", finished_at=finished_at)
    await _session_broker.cleanup(session_id)
    for q in _session_subscribers.pop(session_id, []):
        await q.put(None)
    return {"session_id": session_id, "status": "cancelled"}


# ── Internal session endpoints (no auth, cluster-only) ────────────────────────

@app.post("/internal/sessions/{session_id}/await-input")
async def internal_await_input(session_id: str, body: dict):
    """Worker suspends here; blocks until user sends a message; returns message content."""
    question = body.get("question", "")
    # Emit an agent_response session message with the question so the conversation is recorded
    existing = await _db.get_session_messages(session_id)
    sequence = len(existing)
    agent_msg = SessionMessage(
        session_id=session_id,
        sequence=sequence,
        timestamp=datetime.now(timezone.utc),
        role="agent",
        content=question,
        message_type="input_request",
    )
    await _db.append_session_message(agent_msg)
    data = {"type": "session_message", **agent_msg.model_dump(mode="json")}
    for q in list(_session_subscribers.get(session_id, [])):
        await q.put(data)
    # Transition to waiting_for_user and block
    await _db.update_session_status(session_id, "waiting_for_user")
    response = await _session_broker.await_user_input(session_id, question)
    await _db.update_session_status(session_id, "running")
    return {"content": response}


@app.post("/internal/sessions/{session_id}/interrupt-check")
async def internal_interrupt_check(session_id: str):
    """Worker polls here at loop start; returns interrupt if pending, else empty."""
    interrupt = _session_broker.check_interrupt(session_id)
    if interrupt:
        return {"interrupt": interrupt}
    return {}


@app.post("/internal/sessions/{session_id}/status")
async def internal_session_status(session_id: str, body: dict):
    """Worker updates session status on completion/failure."""
    status = body.get("status")
    finished_at = None
    if status in _SESSION_TERMINAL_STATUSES:
        finished_at = datetime.now(timezone.utc)
    try:
        await _db.update_session_status(session_id, status, finished_at=finished_at)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    if status in _SESSION_TERMINAL_STATUSES:
        await _session_broker.cleanup(session_id)
        for q in _session_subscribers.pop(session_id, []):
            await q.put(None)
    return {"session_id": session_id, "status": status}


@app.post("/internal/sessions/{session_id}/log")
async def internal_session_log(session_id: str, body: dict):
    """Worker posts agent response messages here."""
    content = body.get("content", "")
    existing = await _db.get_session_messages(session_id)
    sequence = len(existing)
    msg = SessionMessage(
        session_id=session_id,
        sequence=sequence,
        timestamp=datetime.now(timezone.utc),
        role="agent",
        content=content,
        message_type="agent_response",
    )
    await _db.append_session_message(msg)
    data = {"type": "session_message", **msg.model_dump(mode="json")}
    for q in list(_session_subscribers.get(session_id, [])):
        await q.put(data)
    return Response(status_code=200)


# ── Project proxy endpoints ──────────────────────────────────────────────────

def _get_user_token(request: Request) -> str:
    """Extract user's OAuth token from request headers (set by oauth2-proxy)."""
    headers = dict(request.headers)
    return (
        headers.get("X-Forwarded-Access-Token")
        or headers.get("x-forwarded-access-token")
        or headers.get("Authorization", "").removeprefix("Bearer ")
        or ""
    )


@app.get("/projects/search")
async def search_projects(q: str = "", request: Request = None):
    user_token = _get_user_token(request)
    results = _provider.search_projects(q, user_token)
    return results


@app.get("/projects/activations")
async def list_activations():
    activations = await _db.list_activations()
    return [
        {
            "project_id": a.project_id,
            "webhook_id": a.webhook_id,
            "activated_by": a.activated_by,
            "activated_at": a.activated_at.isoformat(),
        }
        for a in activations
    ]


@app.post("/projects/{project_id:path}/activate")
async def activate_project(project_id: str, request: Request):
    if not PHALANX_WEBHOOK_URL:
        raise HTTPException(status_code=500, detail="PHALANX_WEBHOOK_URL is not configured")

    existing = await _db.get_activation(project_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"{project_id!r} is already activated")

    user_token = _get_user_token(request)
    identity = _auth_provider.extract_user(dict(request.headers))

    webhook_url = PHALANX_WEBHOOK_URL.rstrip("/") + "/webhook"
    secret = secrets.token_hex(32)

    reg = _provider.register_webhook(project_id, webhook_url, secret, user_token)

    activation = ActivationRecord(
        project_id=project_id,
        webhook_id=reg.webhook_id,
        secret=secret,
        activated_by=identity.username or "unknown",
        activated_at=datetime.now(timezone.utc),
    )
    await _db.activate_project(activation)

    return {
        "project_id": project_id,
        "webhook_url": webhook_url,
        "activated_by": activation.activated_by,
    }


@app.delete("/projects/{project_id:path}/activate")
async def deactivate_project(project_id: str, request: Request):
    activation = await _db.get_activation(project_id)
    if not activation:
        raise HTTPException(status_code=404, detail=f"{project_id!r} is not activated")

    user_token = _get_user_token(request)
    try:
        _provider.delete_webhook(project_id, activation.webhook_id, user_token)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to delete webhook %s for %s; removing activation record anyway",
            activation.webhook_id, project_id,
        )

    await _db.deactivate_project(project_id)
    return {"project_id": project_id, "status": "deactivated"}


@app.get("/projects/{project_id}/branches")
async def list_branches(project_id: str, request: Request):
    user_token = _get_user_token(request)
    branches = _provider.list_branches(project_id, user_token)
    return branches


@app.get("/projects/{project_id}/mrs")
async def list_mrs(project_id: str, request: Request):
    user_token = _get_user_token(request)
    mrs = _provider.list_open_mrs(project_id, user_token)
    return [mr.model_dump(mode="json") for mr in mrs]


@app.get("/")
async def dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist", "index.html")
    return FileResponse(dashboard_path)
