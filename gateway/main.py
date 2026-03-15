import asyncio
import os
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from sse_starlette.sse import EventSourceResponse

from gateway.config_loader import ConfigLoader
from gateway.db import Database
from gateway.event_mapper import map_event_to_task
from gateway.kube_client import KubeClient
from providers.registry import get_provider
from providers.auth_registry import get_auth_provider
from shared.models import AgentConfig, JobRecord, SkillDef, ToolDef, TaskSpec, LogEvent

WEBHOOK_SECRET = os.getenv("GITLAB_WEBHOOK_SECRET", "dev-webhook-secret")

_db = Database()
_kube: KubeClient | None = None
_provider = None
_auth_provider = None
_config_loader: ConfigLoader | None = None
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "out_of_gas"}
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


@app.post("/webhook/gitlab")
async def webhook_gitlab(request: Request):
    body_bytes = await request.body()
    headers = dict(request.headers)

    # Step 1: verify HMAC / token
    if not _provider.verify_webhook(headers, body_bytes, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    # Step 2: parse event
    import json as _json
    body_dict = _json.loads(body_bytes)
    event = _provider.parse_webhook_event(headers, body_dict)

    # Step 3: map to task
    task_spec = map_event_to_task(event)
    if task_spec is None:
        return Response(status_code=200)

    # Step 4: resolve agent config from per-project .agents/config.yaml
    sha = task_spec.context.get("sha") or task_spec.context.get("commits", [{}])[0].get("sha", "HEAD")
    agent_config = await _config_loader.resolve(task_spec.project_id, sha)

    # Step 5: check actor against allowed_users (deny-by-default: empty list = no dispatch)
    actor = getattr(event, "actor", None)
    if not agent_config.allowed_users or actor not in agent_config.allowed_users:
        import logging
        logging.getLogger(__name__).info(
            "Webhook dispatch rejected: actor=%r not in allowed_users=%r for project %s",
            actor, agent_config.allowed_users, task_spec.project_id,
        )
        return Response(status_code=200)

    # Step 6: spawn K8s job
    job_name = _kube.spawn_agent_job(task_spec, agent_config)

    # Step 7: persist job record
    await _db.create_job(_make_job_record(job_name, task_spec, agent_config))

    return {"job_name": job_name}


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


@app.get("/")
async def dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", "index.html")
    return FileResponse(dashboard_path)
