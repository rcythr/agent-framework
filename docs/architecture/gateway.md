# Gateway

## Gateway — Config Loader — `gateway/config_loader.py`

The config loader is called by the gateway immediately after a webhook event is received and before the K8s Job is spawned. It fetches `config.yaml` from within the project's agent config directory (resolved as `{AGENT_CONFIG_DIR}/config.yaml`, default `.agents/config.yaml`) at the commit SHA that triggered the event via the provider's `get_file_at_sha` method, parses and validates it, then merges it with the global defaults to produce a resolved `AgentConfig` that is passed to the job spawner.

**Responsibilities:**

- Fetch `{agent_config_dir}/config.yaml` from the project repo at the commit SHA that triggered the event via `provider.get_file_at_sha()` (not HEAD, to ensure config matches the code being reviewed); `agent_config_dir` defaults to `.agents/` and is read from the `AGENT_CONFIG_DIR` gateway environment variable
- Parse and validate the YAML against the `ProjectConfig` Pydantic model; fall back to global defaults if the file is absent or invalid, logging a warning
- Load global defaults from `global-config/agent-config.yml` (mounted as a ConfigMap in the gateway pod)
- Resolve skill and tool file paths relative to the agent config directory (e.g. `.agents/skills/`, `.agents/tools/`) so inline definitions stored as files in the repo are fetched alongside the main config
- Merge skills: `global_skills + project_skills`, deduplicating by name
- Merge tools: `global_tools + project_tools`, deduplicating by name
- Resolve prompt: if `prompt_mode: append`, concatenate global base prompt + project prompt; if `prompt_mode: override`, use project prompt only
- Resolve image: if `dockerfile` is set, trigger the image builder (see below) and return the derived image tag; otherwise return the global worker image tag
- Return a fully resolved `AgentConfig` with no optional fields — the job spawner never needs to reason about defaults

**Access control** — after resolving the project config, the gateway checks the event actor against `allowed_users` before spawning a job:

```python
def is_actor_allowed(actor: str, project_config: ProjectConfig) -> bool:
    """
    Return True if the actor is permitted to trigger agent dispatch for this project.
    An empty allowed_users list means no one is permitted (deny-by-default).
    """
    return actor in project_config.allowed_users
```

If the actor is not in `allowed_users`, the gateway returns HTTP 200 (to avoid leaking configuration to the webhook sender) but logs the rejection and does **not** spawn a job. Projects that have not yet configured `.agents/config.yaml` also receive no dispatch — the empty default `allowed_users` is deny-by-default. Projects must explicitly list the usernames authorised to trigger agents.

This check applies to all webhook-triggered dispatch (push, MR open/update, comment). It does not apply to manually triggered jobs via `POST /trigger`, which are protected by the dashboard authentication layer instead.

**Image build flow** (when `dockerfile` is present):

The config loader computes a cache key from `project_id` + the git blob SHA of the project's Dockerfile. If a registry image already exists for this cache key, it is returned immediately without a build. If not, the gateway creates a Kubernetes Job running **Kaniko** to build and push the derived image, waits for completion (with a timeout), then returns the new image tag. The agent job is only spawned after the image is ready.

```
cache key = f"{project_id}-{dockerfile_blob_sha}"
image tag = f"your-registry/pi-agent-project:{cache_key}"
```

This means a project's custom image is only ever built once per unique Dockerfile content, regardless of how many agent runs trigger it.

---

## Gateway — Event Mapper — `gateway/event_mapper.py`

Maps provider-agnostic webhook event models (`PushEvent`, `MREvent`, `CommentEvent` from `providers/base.py`) to `TaskSpec` instances. This module contains no provider-specific code — the provider's `parse_webhook_event` method has already translated the raw payload before this mapper is called.

```python
from shared.models import TaskSpec
from typing import Any

from providers.base import PushEvent, MREvent, CommentEvent

def map_event_to_task(
    event: PushEvent | MREvent | CommentEvent
) -> TaskSpec | None:
    match event:
        case MREvent():
            return TaskSpec(
                task="review_mr",
                project_id=event.project_id,
                context={
                    "mr_iid": event.mr.iid,
                    "source_branch": event.mr.source_branch,
                    "target_branch": event.mr.target_branch,
                    "description": event.mr.description,
                },
            )
        case CommentEvent():
            return TaskSpec(
                task="handle_comment",
                project_id=event.project_id,
                context={
                    "note_body": event.body,
                    "mr_iid": event.mr_iid,
                    "note_id": event.note_id,
                },
            )
        case PushEvent():
            return TaskSpec(
                task="analyze_push",
                project_id=event.project_id,
                context={
                    "commits": [c.model_dump() for c in event.commits],
                    "branch": event.branch,
                },
            )
        case _:
            return None
```

---

## Gateway — Persistence — `gateway/db.py`

An `aiosqlite`-backed store with two tables. The gateway writes to this on every job spawn, status update, and incoming log event from workers.

The `jobs` table stores one row per agent run: id, task, project details, status, start/finish timestamps, and the original task context. The `log_events` table stores every structured log event emitted by any worker, indexed by `job_id` and `sequence` for ordered replay.

Key methods:

- `create_job(job: JobRecord)` — called by the gateway when a K8s Job is spawned
- `update_job_status(job_id, status, finished_at?, result?)` — called on worker completion/failure callbacks; stores the agent's final text response when provided
- `append_log_event(event: LogEvent)` — called by the internal log ingest endpoint
- `get_job(job_id) → JobRecord` — used by the dashboard API
- `list_jobs(status?, limit?, offset?) → list[JobRecord]` — drives the active and history views
- `get_log_events(job_id) → list[LogEvent]` — full replay for historical jobs

---

## Gateway — Kubernetes Job Spawner — `gateway/kube_client.py`

Spawns an ephemeral Kubernetes Job for each incoming task. Accepts a fully resolved `AgentConfig` alongside the `TaskSpec` and uses it to select the correct pod image (global default or project-derived), inject the composed system prompt, and mount the resolved skill and tool lists as environment variables or a ConfigMap volume.

```python
import time
import os
import json
from kubernetes import client, config
from shared.models import TaskSpec


class KubeClient:
    def __init__(self):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.batch_api = client.BatchV1Api()
        self.namespace = os.getenv("AGENT_NAMESPACE", "pi-agents")

    def spawn_agent_job(self, task_spec: TaskSpec) -> str:
        job_name = f"pi-agent-{task_spec.task.replace('_', '-')}-{int(time.time())}"

        job = client.V1Job(
            metadata=client.V1ObjectMeta(name=job_name),
            spec=client.V1JobSpec(
                ttl_seconds_after_finished=300,
                template=client.V1PodTemplateSpec(
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        service_account_name="pi-agent-worker",
                        containers=[
                            client.V1Container(
                                name="agent",
                                image=os.getenv("PI_AGENT_IMAGE"),
                                command=["python", "-m", "worker.main"],
                                env=[
                                    client.V1EnvVar(name="TASK", value=task_spec.task),
                                    client.V1EnvVar(name="PROJECT_ID", value=str(task_spec.project_id)),
                                    client.V1EnvVar(name="TASK_CONTEXT", value=json.dumps(task_spec.context)),
                                    client.V1EnvVar(
                                        name="GITLAB_TOKEN",
                                        value_from=client.V1EnvVarSource(
                                            secret_key_ref=client.V1SecretKeySelector(name="gitlab-creds", key="token")
                                        ),
                                    ),
                                    client.V1EnvVar(
                                        name="OPENAI_API_KEY",
                                        value_from=client.V1EnvVarSource(
                                            secret_key_ref=client.V1SecretKeySelector(name="llm-creds", key="api-key")
                                        ),
                                    ),
                                    client.V1EnvVar(name="LLM_ENDPOINT", value=os.getenv("LLM_ENDPOINT", "")),
                                ],
                            )
                        ],
                    )
                ),
            ),
        )

        self.batch_api.create_namespaced_job(self.namespace, job)
        return job_name
```

---


## Gateway — Session Broker — `gateway/session_broker.py`

The session broker manages the lifecycle and bidirectional communication channel for all active interactive sessions. It is an in-memory component backed by the persistent `SessionRecord` and `SessionMessage` tables in SQLite.

**Responsibilities:**

- Maintain a per-session `asyncio.Queue` for inbound user messages (instructions, interrupts, and input responses)
- Expose `send_to_agent(session_id, message)` — called by the gateway API when a user sends a message; enqueues the message and, if the session status is `waiting_for_user`, transitions it back to `running`
- Expose `await_user_input(session_id, question)` — called by the worker via the internal API when the agent emits an `input_request` event; transitions session to `waiting_for_user`, blocks until a message arrives in the queue, returns the user's response to the worker
- Expose `check_interrupt(session_id)` — called by the worker at the top of each agent loop iteration; returns a pending interrupt message if one exists, or `None`; the worker injects it into the agent's context before the next LLM call
- Persist every inbound and outbound message to the `session_messages` table so the full conversation is replayable from history
- Clean up queues when a session reaches a terminal state (`complete`, `failed`, `cancelled`)

**Session state machine:**

```
configuring ──(job spawned)──▶ running
                                  │
              ┌───────────────────┼──────────────────┐
              │                   │                  │
   (agent asks question)  (agent loop     (input or output limit reached)
              │             iterates)               │
              ▼                   │                  ▼
      waiting_for_user            │           out_of_gas
              │                   │                  │
     (user responds)              │        (user adds gas)
              │                   │                  │
              └───────────────────┴──────────────────┘
                                  │
                         ┌────────┴────────┐
                         ▼                 ▼
                      complete           failed
```

At any point in `running` or `waiting_for_user`, the user can send an interrupt which transitions the session back to `running` with the redirect injected into the next loop iteration.

---

## Gateway — FastAPI Server — `gateway/main.py`

Receives GitLab webhooks and manual trigger requests, validates webhook tokens, delegates to the Kubernetes job spawner, and serves the dashboard API and the React SPA.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/{provider}` | Receives provider webhook events (e.g. `/webhook/gitlab`, `/webhook/github`); provider verifies signature, parses to shared event model, checks actor against `allowed_users` in project config before dispatch |
| `POST` | `/trigger` | Manual trigger: accepts a `TaskSpec`, spawns an agent job |
| `POST` | `/internal/log` | Called by worker pods to ingest structured `LogEvent` records |
| `POST` | `/internal/jobs/{id}/status` | Called by worker pods on completion or failure; accepts optional `result` field containing the agent's final text response |
| `GET` | `/internal/jobs/{id}/await-result` | Long-poll: blocks until the job reaches a terminal status, then returns `{"status": "...", "result": "..."}`. Returns immediately if the job is already terminal. Query param `timeout` (seconds, default 300) caps wait time. Used by parent agents waiting for sub-agent output. |
| `GET` | `/agents` | List active jobs (status `pending` or `running`) |
| `GET` | `/agents/history` | Paginated list of completed/failed/cancelled jobs |
| `GET` | `/agents/{id}` | Single job record with metadata |
| `GET` | `/agents/{id}/logs` | Full log event list for a job (for history replay) |
| `GET` | `/agents/{id}/logs/stream` | SSE stream of log events for a live running job |
| `POST` | `/agents/{id}/cancel` | Deletes the K8s Job and marks the DB record cancelled |
| `GET` | `/agents/{id}/gas` | Return `gas_used_input`, `gas_used_output`, `gas_limit_input`, `gas_limit_output`, `topup_history` for a job |
| `POST` | `/agents/{id}/gas` | Add input and/or output tokens to a job's limits; body: `{"input_amount": N, "output_amount": M}`; resumes `out_of_gas` jobs |
| `POST` | `/sessions` | Create a new interactive session: accepts `SessionContext`, resolves config, spawns K8s Job, returns `SessionRecord` |
| `GET` | `/sessions` | List the authenticated user's sessions (active and recent) |
| `GET` | `/sessions/{id}` | Single session record with metadata and status |
| `GET` | `/sessions/{id}/messages` | Full conversation history for a session |
| `GET` | `/sessions/{id}/stream` | SSE stream delivering both `SessionMessage` and `LogEvent` records interleaved in real time |
| `POST` | `/sessions/{id}/messages` | Send a user message to a running session (instruction, interrupt, or input response) |
| `POST` | `/sessions/{id}/cancel` | Cancel a running session |
| `GET` | `/sessions/{id}/gas` | Return `gas_used_input`, `gas_used_output`, `gas_limit_input`, `gas_limit_output`, `topup_history` for a session |
| `POST` | `/sessions/{id}/gas` | Add input and/or output tokens to a session's limits; body: `{"input_amount": N, "output_amount": M}`; resumes `out_of_gas` sessions |
| `GET` | `/projects/search` | Proxy to provider `search_projects()` filtered to projects the authenticated user can access; used by the session launcher |
| `GET` | `/projects/{id}/branches` | Proxy to provider `list_branches()` for a given project; used by the session launcher |
| `GET` | `/projects/{id}/mrs` | Proxy to provider `list_open_mrs()` for a given project; used by the session launcher |
| `POST` | `/internal/sessions/{id}/await-input` | Called by worker when agent needs user input; broker suspends session until user responds |
| `POST` | `/internal/sessions/{id}/interrupt-check` | Called by worker at loop start to check for pending interrupts |
| `POST` | `/internal/jobs/{id}/add-gas` | Called by gateway to unblock a paused job worker when gas is topped up |
| `POST` | `/internal/sessions/{id}/add-gas` | Called by gateway to unblock a paused session worker when gas is topped up |
| `GET` | `/` | Serves the dashboard React SPA (`dashboard/index.html`) |
| `GET` | `/healthz` | Liveness probe |

The `/internal/*` endpoints are cluster-internal only, protected by a shared secret injected into worker pods via K8s Secret, and not exposed through the Kubernetes Ingress. This includes the new session input-awaiting and interrupt-check endpoints which form the synchronisation channel between interactive worker pods and the broker.

---
