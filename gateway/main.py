import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response, HTTPException
from contextlib import asynccontextmanager

from gateway.db import Database
from gateway.event_mapper import map_event_to_task
from gateway.kube_client import KubeClient
from providers.registry import get_provider
from providers.auth_registry import get_auth_provider
from shared.models import AgentConfig, JobRecord, SkillDef, ToolDef, TaskSpec

WEBHOOK_SECRET = os.getenv("GITLAB_WEBHOOK_SECRET", "dev-webhook-secret")

_db = Database()
_kube: KubeClient | None = None
_provider = None
_auth_provider = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _kube, _provider, _auth_provider
    await _db.connect()
    _kube = KubeClient()
    _provider = get_provider()
    _auth_provider = get_auth_provider()
    yield
    await _db.close()


app = FastAPI(lifespan=lifespan)


def _default_agent_config() -> AgentConfig:
    """Return a permissive default AgentConfig for Phase 1 (before Phase 4 wires config_loader)."""
    return AgentConfig(
        skills=[],
        tools=[],
        system_prompt="",
        image=os.getenv("PI_AGENT_IMAGE", "localhost:5001/pi-agent-worker:latest"),
        gas_limit_input=80_000,
        gas_limit_output=20_000,
    )


def _make_job_record(job_name: str, task_spec: TaskSpec) -> JobRecord:
    return JobRecord(
        id=job_name,
        task=task_spec.task,
        project_id=task_spec.project_id,
        project_name=str(task_spec.project_id),
        status="pending",
        context=task_spec.context,
        started_at=datetime.now(timezone.utc),
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

    # Step 4: resolve agent config (Phase 1: use defaults; Phase 4 wires config_loader)
    agent_config = _default_agent_config()

    # Step 5: check actor against allowed_users (empty list = allow all)
    actor = getattr(event, "actor", None)
    if agent_config.allowed_users and actor not in agent_config.allowed_users:
        return Response(status_code=200)

    # Step 6: spawn K8s job
    job_name = _kube.spawn_agent_job(task_spec)

    # Step 7: persist job record
    await _db.create_job(_make_job_record(job_name, task_spec))

    return {"job_name": job_name}


@app.post("/trigger")
async def trigger(task_spec: TaskSpec):
    agent_config = _default_agent_config()

    job_name = _kube.spawn_agent_job(task_spec)
    await _db.create_job(_make_job_record(job_name, task_spec))

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
    return {"job_id": job_id, "status": status}


@app.get("/internal/oauth2-proxy-config")
async def oauth2_proxy_config():
    cfg = _auth_provider.oauth_proxy_config()
    args = [f"--provider={cfg.provider_flag}"] + cfg.extra_flags
    return {"args": args}
