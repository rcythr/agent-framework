# Agent System × GitLab Integration Proposal

## Overview

This document proposes an architecture and implementation plan for an **autonomous agent system** that integrates with **Git repository hosting platforms**, deployed on **Kubernetes**. The initial implementation targets GitLab, but the system is designed around a provider abstraction layer so that support for other platforms (GitHub, Bitbucket, Gitea, etc.) can be added without changes to the core agent loop, gateway, or dashboard. The agent loop is implemented in Python as the `Agent` class — an LLM call loop with tool dispatch, event emission, and a two-queue message model for steering and follow-ups. Its design draws on ideas from minimal agentic loop libraries. The system autonomously reacts to repository events, performs code review, implements changes, and can be triggered manually via the dashboard or API.

---

## Goals

- React to repository events (push, merge/pull requests, comments) in real time, via a provider-agnostic webhook ingestion layer
- Act as an autonomous coding and review agent on repositories hosted on any supported provider
- Support manual triggering from the dashboard UI and provider CI pipelines
- Read and write code, open merge requests, and commit changes
- Post comments and inline review notes on merge requests and issues
- Report pipeline status back to GitLab
- Provide a human-facing control plane dashboard to monitor active agents, view history, and inspect full execution traces
- Stream structured agent logs (LLM queries, tool calls, tool outputs) in real time to the dashboard
- Allow individual GitLab projects to configure their agent environment — skills, tools, system prompt, and runtime image — via a configurable project directory (defaulting to `.agents/`), extending global defaults rather than replacing them
- Provide an interactive **Agent Session** interface in the dashboard where users can launch an ad hoc agent against any project they have access to on any connected provider, converse with it in real time, steer it mid-run, and have the agent ask clarifying questions — without any local setup

---

## Architecture

The system is composed of three distinct layers:

1. **Gateway Service** — a persistent, always-on FastAPI server that receives provider webhooks and manual trigger requests, spawns ephemeral worker jobs, persists job and log state, serves the dashboard API, and brokers bidirectional messaging between users and interactive agent sessions
2. **Worker Jobs** — short-lived Kubernetes Jobs, one per agent task or session, each running an `Agent` instance with a provider-supplied tool suite and a structured logger that streams events back to the gateway
3. **Control Plane Dashboard** — a browser-based React UI served by the gateway, providing real-time agent monitoring, log streaming, history browsing, agent management actions, and an interactive Agent Session interface for ad hoc work

```
                                              Browser
                                                 │
                                    dashboard /  │  / webhook (no auth)
                                                 ▼
Provider OAuth2 ◀─ authn ──▶  oauth2-proxy (K8s Deployment)
                                                 │
                                   sets X-Forwarded-User header
                                                 ▼
Provider ──webhook/API──▶ Pi Agent Gateway (persistent K8s Deployment)
                               │   │   │
                    ┌──────────┘   │   └─────────────────┐
                    ▼              ▼                      ▼
              K8s Job Spawner   SQLite DB          Dashboard API
                    │          (jobs + logs)      (REST + SSE)
                    │              ▲                      ▲
                    ▼              │                      │
              Worker Pod  ─log events─▶ POST /internal/log
             (Agent +                                     │
           AgentLogger +                          Browser Dashboard
          ProviderToolkit)                       (active agents,
                    │                          history, live logs)
                    ▼
             OpenAI-compat API  +  Provider API
```

### Design Principles

- **Gateway owns all state** — job records and log events are persisted in the gateway's SQLite database (swappable for Postgres in production), making the dashboard independent of running pods
- **Workers stream logs in real time** — the `AgentLogger` wraps the `Agent` loop and POSTs structured log events to the gateway as they occur, so the dashboard reflects live progress
- **Log events are typed and structured** — every event has an explicit type (`llm_query`, `llm_response`, `tool_call`, `tool_result`, `complete`, `error`) enabling the dashboard to render each differently rather than as raw text
- **Dashboard uses SSE for live updates** — the gateway exposes a `/agents/{id}/logs/stream` Server-Sent Events endpoint; the dashboard subscribes per agent and appends events as they arrive
- **Workers are fully ephemeral** — isolated per task, auto-cleaned via `ttlSecondsAfterFinished`; all observable state lives in the gateway DB, not the pod
- **Provider abstraction is the integration boundary** — all repository provider API interaction is encapsulated behind a `RepositoryProvider` abstract base class; the agent runtime, gateway, and config loader program against this interface exclusively and are unaware of which provider is in use
- **Secrets never leave Kubernetes** — GitLab tokens and LLM API keys are injected via K8s Secrets, not environment files or CI variables
- **Project config is fetched at spawn time** — the gateway reads the project config directory (default `.agents/`) from the project repo via the provider API immediately before creating the K8s Job, so config changes take effect on the next agent run with no redeployment
- **Global defaults are always present** — project config extends the global skill and tool set; it cannot remove globally registered tools, ensuring baseline capabilities are always available
- **Custom images are layered, not replaced** — project Dockerfiles use the global worker image as their `FROM` base; derived images are built by a Kaniko sidecar at spawn time, tagged by project ID and Dockerfile commit SHA, and cached in the registry
- **Sessions and jobs share the same worker** — interactive sessions are K8s Jobs running the same worker image as webhook-triggered jobs; the difference is behavioural: session workers hold a long-lived connection to the gateway's message broker and can suspend their loop waiting for user input
- **User messages are queued, not pushed** — the gateway holds an in-memory message queue per session; the agent polls it between loop iterations, ensuring interrupts and clarifications are handled safely at iteration boundaries rather than mid-tool-execution
- **Sessions are scoped to the authenticated user** — each session is owned by the `X-Forwarded-User` identity from oauth2-proxy; users can only view and interact with their own sessions

---

## Project Structure

```
phalanx/
├── gateway/
│   ├── main.py              # FastAPI server: webhooks, triggers, dashboard API, SSE, session endpoints
│   ├── kube_client.py       # K8s Job spawner
│   ├── event_mapper.py      # GitLab event → task spec
│   ├── db.py                # SQLite persistence (jobs, log events, sessions, messages)
│   ├── config_loader.py     # Fetches + merges project config dir with global defaults
│   └── session_broker.py    # In-memory message queues + session state for interactive sessions
├── providers/
│   ├── base.py              # RepositoryProvider ABC + shared data models (MR, Commit, etc.)
│   ├── auth_base.py         # AuthProvider ABC, OAuthProxyConfig, UserIdentity — extension point for future IdPs
│   ├── registry.py          # get_provider() factory
│   ├── auth_registry.py     # get_auth_provider() factory
│   ├── gitlab/
│   │   ├── provider.py      # GitLab implementation of RepositoryProvider
│   │   ├── webhook.py       # GitLab webhook verification + event parsing
│   │   ├── toolkit.py       # GitLab ProviderToolkit implementation
│   │   └── auth.py          # GitLabAuthProvider — the only AuthProvider implemented initially
│   └── github/              # Placeholder — structure mirrors gitlab/
│       ├── provider.py
│       ├── webhook.py
│       └── toolkit.py       # auth.py added here when GitHub provider is implemented
├── worker/
│   ├── main.py              # Entry point for K8s Job pods
│   ├── agent.py             # Agent class: LLM loop, tool dispatch, event emission, message queues
│   ├── agent_runner.py      # Agent initialisation, tool wiring, session mode branching
│   ├── agent_logger.py      # Structured logger: wraps Agent, streams events to gateway
│   └── tools/
│       └── toolkit_base.py  # ProviderToolkit ABC — defines the tool contract
├── dashboard/
│   └── index.html           # React SPA served by gateway (active agents, history, log viewer, session UI)
├── shared/
│   └── models.py            # Shared Pydantic models (TaskSpec, LogEvent, JobRecord, AgentConfig, Session*)
├── k8s/
│   ├── gateway-deployment.yaml
│   ├── rbac.yaml
│   └── secrets.yaml
├── Dockerfile.gateway
├── Dockerfile.worker        # Global base image — used as FROM in project Dockerfiles
├── global-config/
│   ├── skills/              # Globally available skill definitions
│   ├── tools/               # Globally available tool definitions
│   └── agent-config.yml     # Global defaults: base prompt, default skills/tools
│
│   # Example layout inside a GitLab project repo (not part of this repo):
│   # .agents/               ← path controlled by AGENT_CONFIG_DIR gateway env var
│   #   config.yaml          ← project agent config
│   #   Dockerfile           ← optional image override layer
│   #   skills/              ← optional inline skill definitions
│   #   tools/               ← optional inline tool definitions
└── requirements.txt
```

---

## Dependencies

```txt
fastapi>=0.111.0
uvicorn>=0.29.0
kubernetes>=29.0.0
python-gitlab>=4.6.0    # GitLab provider implementation
# PyGithub>=2.3.0        # GitHub provider implementation (when added)
httpx>=0.27.0
pydantic>=2.7.0
pydantic-settings>=2.2.0
openai>=1.30.0             # OpenAI-compatible API client used by the Agent loop
aiosqlite>=0.20.0       # async SQLite for gateway persistence
sse-starlette>=2.1.0    # Server-Sent Events for live log streaming
pyyaml>=6.0.1           # Parsing project config YAML files
```

---

## Implementation

### Shared Models — `shared/models.py`

Pydantic models shared between the gateway and worker covering task specs, log events, job records, and the resolved agent configuration produced by the config loader.

```python
from pydantic import BaseModel
from typing import Any, Literal
from datetime import datetime

class TaskSpec(BaseModel):
    task: str
    project_id: int
    context: dict[str, Any]

class LogEvent(BaseModel):
    job_id: str
    sequence: int
    timestamp: datetime
    event_type: Literal[
        "llm_query",         # prompt sent to the LLM
        "llm_response",      # full LLM response received
        "tool_call",         # agent invoking a tool with arguments
        "tool_result",       # raw result returned from a tool
        "input_request",     # agent suspending to ask the user a question (sessions only)
        "input_received",    # user response received, agent resuming (sessions only)
        "interrupted",       # user sent a redirect message while agent was running
        "gas_updated",       # gas_used updated after an LLM call
        "out_of_gas",        # gas_limit reached; run paused awaiting top-up
        "complete",          # agent finished successfully
        "error",             # unhandled exception or LLM error
    ]
    payload: dict[str, Any]   # event-type-specific data (see below)

class JobRecord(BaseModel):
    id: str
    task: str
    project_id: int
    project_name: str
    status: Literal["pending", "running", "completed", "failed", "cancelled", "out_of_gas"]
    context: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None
    gas_limit: int = 100_000          # token budget allocated to this job
    gas_used: int = 0                 # total tokens consumed (input + output)
    gas_used_input: int = 0           # input tokens consumed so far
    gas_used_output: int = 0          # output tokens consumed so far
    gas_topups: list[int] = []        # record of each top-up amount

class SkillDef(BaseModel):
    name: str
    description: str
    inline: bool = False

class ToolDef(BaseModel):
    name: str
    description: str
    inline: bool = False

class ProjectConfig(BaseModel):
    """Parsed and validated representation of the project agent config."""
    skills: list[SkillDef] = []
    tools: list[ToolDef] = []
    prompt_mode: Literal["append", "override"] = "append"
    prompt: str = ""
    dockerfile: str | None = None   # repo-relative path to project Dockerfile

class AgentConfig(BaseModel):
    """Fully resolved config produced by the config loader — no optional fields."""
    skills: list[SkillDef]          # global + project, deduplicated
    tools: list[ToolDef]            # global + project, deduplicated
    system_prompt: str              # fully composed prompt string
    image: str                      # registry image tag to use for the K8s Job pod

class SessionContext(BaseModel):
    """User-supplied context when creating an interactive session."""
    project_id: int
    project_path: str               # e.g. "group/my-repo"
    branch: str                     # branch the agent will read/write against
    goal: str                       # initial free-text instruction from the user
    mr_iid: int | None = None       # optional: scope session to a specific MR
    skill_overrides: list[str] = [] # skill names to add on top of project config
    tool_overrides: list[str] = []  # tool names to add on top of project config
    gas_limit: int = 100_000        # token budget for this session; overrides system default

class SessionMessage(BaseModel):
    """A single message in an interactive session conversation."""
    session_id: str
    sequence: int
    timestamp: datetime
    role: Literal["user", "agent"]
    content: str
    message_type: Literal[
        "instruction",        # user's initial goal or a follow-up instruction
        "interrupt",          # user redirecting a running agent mid-loop
        "agent_response",     # agent's natural language reply or status update
        "input_request",      # agent asking the user a clarifying question
        "input_response",     # user's answer to an agent clarifying question
    ]

class SessionRecord(BaseModel):
    """Persistent record of an interactive agent session."""
    id: str
    owner: str                        # Normalised username from AuthProvider.extract_user(headers)
    project_id: int
    project_path: str
    branch: str
    mr_iid: int | None
    status: Literal["configuring", "running", "waiting_for_user", "out_of_gas", "complete", "failed", "cancelled"]
    context: SessionContext
    created_at: datetime
    finished_at: datetime | None = None
    gas_limit: int = 100_000          # token budget for this session
    gas_used: int = 0                 # total tokens consumed (input + output)
    gas_used_input: int = 0           # input tokens consumed so far
    gas_used_output: int = 0          # output tokens consumed so far
    gas_topups: list[int] = []        # record of each top-up amount
```

**`LogEvent.payload` shapes by event type:**

| `event_type` | `payload` fields |
|---|---|
| `llm_query` | `messages: list`, `model: str`, `tools: list[str]` |
| `llm_response` | `content: str`, `tool_calls: list`, `input_tokens: int`, `output_tokens: int` |
| `tool_call` | `tool_name: str`, `arguments: dict` |
| `tool_result` | `tool_name: str`, `result: Any`, `duration_ms: int` |
| `complete` | `summary: str`, `total_llm_calls: int`, `total_tool_calls: int` |
| `error` | `message: str`, `traceback: str` |
| `input_request` | `question: str` |
| `input_received` | `response: str` |
| `interrupted` | `redirect_message: str` |
| `gas_updated` | `gas_used: int`, `gas_limit: int`, `input_tokens: int`, `output_tokens: int` |
| `out_of_gas` | `gas_used: int`, `gas_limit: int` |

---

### Provider Abstraction — `providers/base.py`

All repository provider interaction — fetching files, posting comments, creating branches, reporting status — is defined as an abstract interface. The gateway, config loader, and worker all program against this interface. Provider implementations live under `providers/{name}/` and are registered at gateway startup via the `PROVIDER` environment variable.

**Provider-agnostic data models:**

These are plain Pydantic models used throughout the system. No provider SDK types ever cross the boundary into gateway or worker code.

```python
from pydantic import BaseModel
from typing import Any

class MergeRequest(BaseModel):
    iid: int
    title: str
    description: str
    source_branch: str
    target_branch: str
    web_url: str

class Commit(BaseModel):
    sha: str
    title: str
    author: str

class PushEvent(BaseModel):
    branch: str
    commits: list[Commit]
    project_id: int | str

class MREvent(BaseModel):
    mr: MergeRequest
    project_id: int | str
    action: str            # "open", "update", "close", "merge"

class CommentEvent(BaseModel):
    body: str
    project_id: int | str
    mr_iid: int | None
    note_id: int | str

class FileContent(BaseModel):
    path: str
    content: str
    ref: str

class CommitResult(BaseModel):
    sha: str
    branch: str

class MRResult(BaseModel):
    iid: int
    web_url: str
```

**`RepositoryProvider` abstract base class:**

```python
from abc import ABC, abstractmethod

class RepositoryProvider(ABC):
    """
    Abstract interface for all repository provider operations.
    Implementations must not expose provider SDK types in return values —
    all returns must be instances of the shared models above.
    """

    # ── Repo content ──────────────────────────────────────────────────────

    @abstractmethod
    def get_file(self, project_id: int | str, path: str, ref: str) -> FileContent:
        """Read a file at a given ref."""

    @abstractmethod
    def commit_file(
        self, project_id: int | str, branch: str,
        path: str, content: str, message: str
    ) -> CommitResult:
        """Create or update a file on a branch."""

    @abstractmethod
    def get_mr_diff(self, project_id: int | str, mr_iid: int) -> list[dict]:
        """Return the diff hunks for a merge/pull request."""

    # ── Comments ──────────────────────────────────────────────────────────

    @abstractmethod
    def post_mr_comment(
        self, project_id: int | str, mr_iid: int, body: str
    ) -> dict:
        """Post a top-level comment on a merge/pull request."""

    @abstractmethod
    def post_inline_comment(
        self, project_id: int | str, mr_iid: int,
        file_path: str, line: int, body: str
    ) -> dict:
        """Post an inline review comment on a specific diff line."""

    # ── MR / PR management ────────────────────────────────────────────────

    @abstractmethod
    def create_mr(
        self, project_id: int | str,
        source_branch: str, target_branch: str,
        title: str, description: str
    ) -> MRResult:
        """Open a merge/pull request."""

    # ── CI / Pipeline status ──────────────────────────────────────────────

    @abstractmethod
    def update_pipeline_status(
        self, project_id: int | str, sha: str,
        state: str, description: str, context: str = "pi-agent"
    ) -> dict:
        """Post a commit status / check run result."""

    # ── Config and project metadata ───────────────────────────────────────

    @abstractmethod
    def get_file_at_sha(
        self, project_id: int | str, path: str, sha: str
    ) -> FileContent | None:
        """
        Fetch a file at a specific commit SHA.
        Returns None if the file does not exist at that ref.
        Used by the config loader to read .agents/config.yaml at event SHA.
        """

    @abstractmethod
    def search_projects(self, query: str, user_token: str) -> list[dict]:
        """Search for projects accessible to the user identified by user_token."""

    @abstractmethod
    def list_branches(self, project_id: int | str, user_token: str) -> list[str]:
        """List branches for a project."""

    @abstractmethod
    def list_open_mrs(self, project_id: int | str, user_token: str) -> list[MergeRequest]:
        """List open merge/pull requests for a project."""

    # ── Webhook verification ──────────────────────────────────────────────

    @abstractmethod
    def verify_webhook(self, headers: dict, body: bytes, secret: str) -> bool:
        """Return True if the webhook signature is valid."""

    @abstractmethod
    def parse_webhook_event(
        self, headers: dict, body: dict
    ) -> PushEvent | MREvent | CommentEvent | None:
        """
        Parse a raw webhook payload into a provider-agnostic event model.
        Returns None for event types the system does not handle.
        """
```

**Provider registry — `providers/registry.py`:**

```python
import os
from providers.base import RepositoryProvider

def get_provider() -> RepositoryProvider:
    """
    Return the configured provider instance.
    The PROVIDER env var selects the implementation; credentials
    are read from provider-specific env vars by each implementation.
    """
    provider_name = os.getenv("PROVIDER", "gitlab")
    match provider_name:
        case "gitlab":
            from providers.gitlab.provider import GitLabProvider
            return GitLabProvider(
                url=os.getenv("GITLAB_URL", "https://gitlab.com"),
                token=os.getenv("GITLAB_TOKEN"),
            )
        case "github":
            from providers.github.provider import GitHubProvider
            return GitHubProvider(
                token=os.getenv("GITHUB_TOKEN"),
            )
        case _:
            raise ValueError(f"Unknown provider: {provider_name!r}")
```

The gateway and worker both call `get_provider()` once at startup and hold the instance for the lifetime of the process. No code outside the `providers/` directory ever imports from a concrete provider module directly.

---

### Provider Abstraction — `providers/gitlab/provider.py`

The GitLab implementation of `RepositoryProvider`. Uses `python-gitlab` internally but returns only the shared Pydantic models defined in `providers/base.py`. The methods translate between GitLab API shapes and the shared Pydantic models defined in `providers/base.py`, ensuring callers never see GitLab SDK types.

---

### Provider Abstraction — `providers/gitlab/webhook.py`

Implements `verify_webhook` (HMAC comparison against `X-Gitlab-Token`) and `parse_webhook_event` (maps `X-Gitlab-Event` header + payload to `PushEvent`, `MREvent`, or `CommentEvent`). The gateway's webhook endpoint calls this rather than containing any GitLab-specific parsing logic.

---

### Provider Abstraction — `providers/gitlab/toolkit.py` and `worker/tools/toolkit_base.py`

**`ProviderToolkit` ABC** (`worker/tools/toolkit_base.py`):

```python
from abc import ABC, abstractmethod

class ProviderToolkit(ABC):
    """
    Produces the list of tool definitions for a given provider.
    Each tool wraps a RepositoryProvider method with a name, description,
    and parameter schema suitable for LLM tool-calling.
    """

    @abstractmethod
    def get_tools(self) -> list[dict]:
        """
        Return tool definitions in the format expected by Agent.
        Tool execute functions must call self.provider methods only —
        no direct SDK calls.
        """
```

**`GitLabToolkit`** (now `providers/gitlab/toolkit.py`) subclasses `ProviderToolkit` and implements `get_tools()` by wrapping `RepositoryProvider` method calls. The tool names, descriptions, and parameter schemas remain identical — only the internal implementation references `self.provider` rather than the `python-gitlab` SDK directly.

The worker instantiates the toolkit via a factory function that reads the `PROVIDER` env var:

```python
# worker/tools/toolkit_factory.py
import os
from providers.registry import get_provider

def get_toolkit(project_id: int | str) -> ProviderToolkit:
    provider = get_provider()
    provider_name = os.getenv("PROVIDER", "gitlab")
    match provider_name:
        case "gitlab":
            from providers.gitlab.toolkit import GitLabToolkit
            return GitLabToolkit(provider=provider, project_id=project_id)
        case "github":
            from providers.github.toolkit import GitHubToolkit
            return GitHubToolkit(provider=provider, project_id=project_id)
        case _:
            raise ValueError(f"No toolkit for provider: {provider_name!r}")
```

---

### Project Configuration — `.agents/config.yaml`

Each GitLab project can place an `.agents/config.yaml` file in its repository. The directory name `.agents/` is the default and is configurable per gateway deployment via the `AGENT_CONFIG_DIR` environment variable — operators can set it to any repo-relative path (e.g. `.gitlab/agents/` or `config/pi-agent/`). The config file and all associated assets (skills, tools, Dockerfile) live under this single directory. The file is optional — if absent, the agent runs entirely with global defaults.

**Schema:**

```yaml
# .agents/config.yaml

# Additional skills to load on top of the global skill set.
# Values are skill identifiers registered in the global-config/skills/ directory
# or inline skill definitions.
skills:
  - python-testing          # global skill by name
  - security-scanning       # global skill by name
  - name: custom-linter
    description: "Run the project-specific ESLint config and report violations"
    inline: true            # defined here rather than in global registry

# Additional tools to expose to the agent, merged with the global tool set.
# Same format as skills — reference global tools by name or define inline.
tools:
  - notify-slack            # global tool by name
  - name: run-tests
    description: "Execute the project test suite via the CI API"
    inline: true

# Token budget for agent jobs triggered from this project.
# Overrides the system default (DEFAULT_JOB_GAS_LIMIT).
# Sessions have their own gas_limit set in the session launcher.
gas_limit: 150000

# System prompt behaviour.
# "append" adds text after the global base prompt (default).
# "override" replaces the global prompt entirely (use with caution).
prompt_mode: append
prompt: |
  This repository uses Python 3.12 and follows the Google style guide.
  All MR reviews must check for missing type annotations.
  Never suggest changes to files under legacy/.

# Custom agent runtime image.
# If specified, the gateway builds a derived image using the global worker
# image as the base (FROM pi-agent-worker:latest) and this Dockerfile as
# the override layer. The resulting image is used for all agent jobs in
# this project.
dockerfile: Dockerfile   # relative to the agent config directory, i.e. .agents/Dockerfile
```

**Dockerfile override example** — `project-repo/.agents/Dockerfile`:

```dockerfile
# The global worker image is always the base — projects cannot change this FROM
ARG BASE_IMAGE=your-registry/pi-agent-worker:latest
FROM ${BASE_IMAGE}

# Projects can install additional dependencies needed by their custom tools or skills
RUN pip install --no-cache-dir pandas==2.2.0 scipy==1.13.0

# Projects can add files, scripts, or config needed by their inline tools
COPY .agents/scripts/ /app/project-scripts/
```

---

### Gateway — Config Loader — `gateway/config_loader.py`

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

**Image build flow** (when `dockerfile` is present):

The config loader computes a cache key from `project_id` + the git blob SHA of the project's Dockerfile. If a registry image already exists for this cache key, it is returned immediately without a build. If not, the gateway creates a Kubernetes Job running **Kaniko** to build and push the derived image, waits for completion (with a timeout), then returns the new image tag. The agent job is only spawned after the image is ready.

```
cache key = f"{project_id}-{dockerfile_blob_sha}"
image tag = f"your-registry/pi-agent-project:{cache_key}"
```

This means a project's custom image is only ever built once per unique Dockerfile content, regardless of how many agent runs trigger it.

---

### Shared Models — `shared/models.py` (updated)

### Gateway — Event Mapper — `gateway/event_mapper.py`

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

### Gateway — Persistence — `gateway/db.py`

An `aiosqlite`-backed store with two tables. The gateway writes to this on every job spawn, status update, and incoming log event from workers.

The `jobs` table stores one row per agent run: id, task, project details, status, start/finish timestamps, and the original task context. The `log_events` table stores every structured log event emitted by any worker, indexed by `job_id` and `sequence` for ordered replay.

Key methods:

- `create_job(job: JobRecord)` — called by the gateway when a K8s Job is spawned
- `update_job_status(job_id, status, finished_at?)` — called on worker completion/failure callbacks
- `append_log_event(event: LogEvent)` — called by the internal log ingest endpoint
- `get_job(job_id) → JobRecord` — used by the dashboard API
- `list_jobs(status?, limit?, offset?) → list[JobRecord]` — drives the active and history views
- `get_log_events(job_id) → list[LogEvent]` — full replay for historical jobs

---

### Gateway — Kubernetes Job Spawner — `gateway/kube_client.py`

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


### Gateway — Session Broker — `gateway/session_broker.py`

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
   (agent asks question)  (agent loop     (gas_used >= gas_limit)
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

### Gateway — FastAPI Server — `gateway/main.py`

Receives GitLab webhooks and manual trigger requests, validates webhook tokens, delegates to the Kubernetes job spawner, and serves the dashboard API and the React SPA.

**Endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/{provider}` | Receives provider webhook events (e.g. `/webhook/gitlab`, `/webhook/github`); provider verifies signature and parses to a shared event model before dispatch |
| `POST` | `/trigger` | Manual trigger: accepts a `TaskSpec`, spawns an agent job |
| `POST` | `/internal/log` | Called by worker pods to ingest structured `LogEvent` records |
| `POST` | `/internal/jobs/{id}/status` | Called by worker pods on completion or failure |
| `GET` | `/agents` | List active jobs (status `pending` or `running`) |
| `GET` | `/agents/history` | Paginated list of completed/failed/cancelled jobs |
| `GET` | `/agents/{id}` | Single job record with metadata |
| `GET` | `/agents/{id}/logs` | Full log event list for a job (for history replay) |
| `GET` | `/agents/{id}/logs/stream` | SSE stream of log events for a live running job |
| `POST` | `/agents/{id}/cancel` | Deletes the K8s Job and marks the DB record cancelled |
| `GET` | `/agents/{id}/gas` | Return gas usage and top-up history for a job |
| `POST` | `/agents/{id}/gas` | Add tokens to a job's gas limit; resumes `out_of_gas` jobs |
| `POST` | `/sessions` | Create a new interactive session: accepts `SessionContext`, resolves config, spawns K8s Job, returns `SessionRecord` |
| `GET` | `/sessions` | List the authenticated user's sessions (active and recent) |
| `GET` | `/sessions/{id}` | Single session record with metadata and status |
| `GET` | `/sessions/{id}/messages` | Full conversation history for a session |
| `GET` | `/sessions/{id}/stream` | SSE stream delivering both `SessionMessage` and `LogEvent` records interleaved in real time |
| `POST` | `/sessions/{id}/messages` | Send a user message to a running session (instruction, interrupt, or input response) |
| `POST` | `/sessions/{id}/cancel` | Cancel a running session |
| `GET` | `/sessions/{id}/gas` | Return gas usage and top-up history for a session |
| `POST` | `/sessions/{id}/gas` | Add tokens to a session's gas limit; resumes `out_of_gas` sessions |
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

### Worker — Provider Toolkit — `providers/gitlab/toolkit.py`

The GitLab implementation of `ProviderToolkit`. Exposes repository operations as tools by wrapping calls to a `RepositoryProvider` instance. The agent runner never imports it directly — it uses `toolkit_factory.get_toolkit()` to obtain the correct implementation for the active provider.

```python
from providers.base import RepositoryProvider
from worker.tools.toolkit_base import ProviderToolkit
from typing import Any


class GitLabToolkit(ProviderToolkit):
    def __init__(self, provider: RepositoryProvider, project_id: int | str):
        self.provider = provider
        self.project_id = project_id

    def get_tools(self) -> list[dict]:
        return [
            {
                "name": "get_mr_diff",
                "description": "Get the diff of a merge request",
                "parameters": {"mr_iid": {"type": "integer"}},
                "execute": self.get_mr_diff,
            },
            {
                "name": "post_mr_comment",
                "description": "Post a general comment on a merge request",
                "parameters": {"mr_iid": {"type": "integer"}, "body": {"type": "string"}},
                "execute": self.post_mr_comment,
            },
            {
                "name": "post_inline_comment",
                "description": "Post an inline comment on a specific line in an MR diff",
                "parameters": {
                    "mr_iid": {"type": "integer"},
                    "file_path": {"type": "string"},
                    "line": {"type": "integer"},
                    "body": {"type": "string"},
                },
                "execute": self.post_inline_comment,
            },
            {
                "name": "get_file_content",
                "description": "Read a file from the repository at a given ref",
                "parameters": {"file_path": {"type": "string"}, "ref": {"type": "string"}},
                "execute": self.get_file_content,
            },
            {
                "name": "commit_file",
                "description": "Create or update a file in a branch via a commit",
                "parameters": {
                    "branch": {"type": "string"},
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                    "commit_message": {"type": "string"},
                },
                "execute": self.commit_file,
            },
            {
                "name": "create_mr",
                "description": "Open a new merge request",
                "parameters": {
                    "source_branch": {"type": "string"},
                    "target_branch": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
                "execute": self.create_mr,
            },
            {
                "name": "update_pipeline_status",
                "description": "Post a commit status (pending / running / success / failed)",
                "parameters": {
                    "sha": {"type": "string"},
                    "state": {"type": "string"},
                    "description": {"type": "string"},
                },
                "execute": self.update_pipeline_status,
            },
        ]

    def get_mr_diff(self, mr_iid: int) -> list[dict[str, Any]]:
        return self.provider.get_mr_diff(self.project_id, mr_iid)

    def post_mr_comment(self, mr_iid: int, body: str) -> dict:
        return self.provider.post_mr_comment(self.project_id, mr_iid, body)

    def post_inline_comment(self, mr_iid: int, file_path: str, line: int, body: str) -> dict:
        return self.provider.post_inline_comment(self.project_id, mr_iid, file_path, line, body)

    def get_file_content(self, file_path: str, ref: str) -> str:
        return self.provider.get_file(self.project_id, file_path, ref).content

    def commit_file(self, branch: str, file_path: str, content: str, commit_message: str) -> dict:
        result = self.provider.commit_file(self.project_id, branch, file_path, content, commit_message)
        return {"committed": True, "sha": result.sha}

    def create_mr(self, source_branch: str, target_branch: str, title: str, description: str) -> dict:
        result = self.provider.create_mr(self.project_id, source_branch, target_branch, title, description)
        return {"mr_iid": result.iid, "url": result.web_url}

    def update_pipeline_status(self, sha: str, state: str, description: str) -> dict:
        return self.provider.update_pipeline_status(self.project_id, sha, state, description)
```

---

### Worker — Agent Logger — `worker/agent_logger.py`

`AgentLogger` is passed to the `Agent` as its `event_handler`. It receives every `AgentEvent` emitted by the loop and forwards each one as a typed `LogEvent` to the gateway via `POST /internal/log`. It is responsible for the full observability of an agent run.

Events emitted in order during a typical run:

1. `llm_query` — immediately before each LLM call, capturing the full message history and list of available tool names
2. `llm_response` — immediately after, capturing the response content, any tool call decisions, and token counts
3. `tool_call` — for each tool the LLM decides to invoke, capturing the tool name and arguments before execution
4. `tool_result` — after tool execution completes, capturing the return value and wall-clock duration
5. Steps 1–4 repeat for each iteration of the agent loop
6. `complete` — when the agent exits cleanly, with a summary and aggregate counts
7. `error` — if an unhandled exception occurs at any point, with full traceback

All events are fire-and-forget over HTTP with a short timeout so a slow gateway never blocks agent execution. Events include a monotonically incrementing `sequence` number so the dashboard can order them correctly even if delivery is slightly out of order.

---

### Worker — Agent — `worker/agent.py`

The `Agent` class is a Python implementation of a minimal LLM tool-use loop. Its design draws on the ideas behind small agentic loop libraries: call the LLM, execute whatever tools it requests, feed results back, repeat until done.

**Design:**

The loop runs until the LLM returns a message with no tool calls:

```
1. Check steer queue — if a message is pending, prepend it to
   the conversation and clear the queue
2. Call LLM with current conversation history + tool schemas
3. Emit AgentEvent(type='llm_query', ...)
4. Consume the streaming response, accumulating text and tool calls
5. Emit AgentEvent(type='llm_response', ...)
6. For each tool call:
   a. Emit AgentEvent(type='tool_call', ...)
   b. Dispatch to the matching tool function
   c. Append tool result to conversation history
   d. Emit AgentEvent(type='tool_result', ...)
   e. Check steer queue — steer messages interrupt after the
      current tool, before any remaining tools in the same turn
7. If tool calls were made: loop back to step 1
8. If no tool calls:
   a. Append assistant message to conversation history
   b. Check follow-up queue — if a message is waiting, inject
      it and loop back to step 1
   c. Otherwise: emit AgentEvent(type='complete', ...) and return
```

**Gas tracking:**

Each `llm_response` event includes `input_tokens` and `output_tokens` from the LLM response. The `Agent` accumulates these into `self.tokens_used` and emits a `gas_updated` event after every LLM call. If `tokens_used >= gas_limit`, the agent emits an `out_of_gas` event and suspends — it does not start another LLM call. The loop resumes only when `agent.add_gas(amount)` is called, which increases `gas_limit` by `amount` and re-enters the loop.

**Two message queues:**

- **Steer queue** (`agent.steer(message)`) — delivers a message after the current tool finishes, interrupting any remaining tools in the same turn. Used for user redirects in interactive sessions.
- **Follow-up queue** (`agent.follow_up(message)`) — delivers a message only once the agent is fully idle. Used for answering agent clarifying questions in interactive sessions.

**Interface:**

```python
@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict        # JSON Schema for the tool arguments
    execute: Callable       # async function invoked when the tool is called

@dataclass
class AgentEvent:
    type: str               # llm_query | llm_response | tool_call |
                            # tool_result | complete | error
    payload: dict

class Agent:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        tools: list[ToolDef],
        system_prompt: str,
        event_handler: Callable[[AgentEvent], Awaitable[None]],
    ): ...

    async def run(self, initial_message: str) -> None: ...

    def steer(self, message: str) -> None:
        ...

    def follow_up(self, message: str) -> None:
        ...

    def add_gas(self, amount: int) -> None:
        ...

    @property
    def gas_used(self) -> int: ...
```

The `event_handler` callback is how `AgentLogger` attaches to the loop — the agent emits; the logger persists and streams to the gateway. This keeps the agent loop free of any I/O concerns beyond the LLM API call itself.

---

### Worker — Agent Runner — `worker/agent_runner.py`

Initialises an `Agent` instance using the fully resolved `AgentConfig` injected into the pod by the job spawner. The system prompt is already composed (base + project extension) and the skill and tool lists are already merged — the runner does not need to know about global vs project config. It obtains a provider toolkit via `toolkit_factory.get_toolkit()` — which reads the `PROVIDER` env var and returns the appropriate `ProviderToolkit` subclass — and passes it to the `Agent` alongside the task message. The runner is entirely provider-agnostic.

The runner operates in one of two modes, determined by the `SESSION_ID` environment variable:

**Job mode** (no `SESSION_ID`) — standard webhook-triggered or CI-triggered run. The runner executes the agent loop to completion without any user interaction.

**Session mode** (`SESSION_ID` is set) — interactive user-initiated run. The runner wraps each agent loop iteration with two additional behaviours:

- **Interrupt check** — at the start of each iteration, the runner calls `POST /internal/sessions/{id}/interrupt-check`. If an interrupt is pending, it prepends the redirect message to the LLM context before the next call, allowing the user to steer the agent mid-execution without waiting for the current task to complete.
- **Input suspension** — if the agent emits an `input_request` event (i.e. it determines it needs user clarification), the runner calls `POST /internal/sessions/{id}/await-input` with the question. This call blocks until the gateway broker receives a user response, then returns the answer for injection into the LLM context. The agent loop resumes transparently.

```python
import os
from worker.agent import Agent


def build_system_prompt(task: str) -> str:
    return f"""You are an autonomous software engineering agent integrated with a Git repository.
You have tools to read code, post comments, create commits, and open merge requests.
Current task type: {task}.
Always be concise in comments. Never force-push to protected branches.
When making code changes, always create a new branch and open an MR — never commit directly to main."""


def build_task_message(task: str, context: dict) -> str:
    match task:
        case "review_mr":
            return (
                f"Review MR !{context['mr_iid']} "
                f"(merging `{context['source_branch']}` → `{context['target_branch']}`). "
                f"Fetch the diff, check for bugs, security issues, and style problems. "
                f"Post a summary comment and inline notes where relevant."
            )
        case "handle_comment":
            return (
                f"A user left this comment on MR !{context['mr_iid']}: "
                f"\"{context['note_body']}\". "
                f"Interpret the request and act on it. "
                f"If they asked for a fix, implement it and commit the change."
            )
        case "analyze_push":
            return (
                f"Analyze the recent push to {context['branch']}. "
                f"Commits: {context['commits']}. "
                f"Flag any suspicious changes, broken patterns, or missing tests."
            )
        case _:
            return f"Execute task: {task} with context: {context}"


def run_agent(task: str, project_id: int, context: dict) -> None:
    from worker.tools.toolkit_factory import get_toolkit

    toolkit = get_toolkit(project_id=project_id)

    agent = Agent(
        endpoint=os.environ["LLM_ENDPOINT"],
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("LLM_MODEL", "gpt-4o"),
        tools=toolkit.get_tools(),
        system_prompt=build_system_prompt(task),
        event_handler=logger.handle_event,
    )

    await agent.run(build_task_message(task, context))
```

---

### Worker — Entry Point — `worker/main.py`

```python
import os
import json
from worker.agent_runner import run_agent

if __name__ == "__main__":
    run_agent(
        task=os.environ["TASK"],
        project_id=int(os.environ["PROJECT_ID"]),
        context=json.loads(os.environ["TASK_CONTEXT"]),
    )
```

---

## Gas System

### Overview

The gas system gives operators and users explicit control over how many LLM tokens an agent is permitted to consume on a task or session. It is the primary mechanism for controlling cost and preventing runaway agent loops.

Gas is measured in **tokens** — the sum of input and output tokens across all LLM calls made during a run. Every job and session has a `gas_limit` (the budget) and `gas_used` (running total). When `gas_used` reaches `gas_limit` the agent pauses in an `out_of_gas` state: all context is preserved, no further LLM calls are made, and the run is fully resumable once additional gas is allocated.

### Default Gas Limits

Default gas limits are configured at the gateway level via environment variables and can be overridden per project in `.agents/config.yaml` and per session in the session launcher:

| Level | Config key | Default |
|---|---|---|
| System default (jobs) | `DEFAULT_JOB_GAS_LIMIT` env var | `100,000` tokens |
| System default (sessions) | `DEFAULT_SESSION_GAS_LIMIT` env var | `200,000` tokens |
| Project override (jobs) | `gas_limit` in `.agents/config.yaml` | inherits system default |
| Session override | `gas_limit` in `SessionContext` | inherits system default |

### Gas Flow

```
Agent makes LLM call
        ↓
LLM response received — input_tokens + output_tokens extracted
        ↓
Agent emits gas_updated event: {gas_used, gas_limit, tokens_this_call}
        ↓
AgentLogger forwards to gateway → gateway updates gas_used in DB
        ↓
gas_used >= gas_limit?
    NO  → continue agent loop normally
    YES → Agent emits out_of_gas event
          Agent suspends loop (does not make another LLM call)
          Gateway sets job/session status → out_of_gas
          Dashboard shows gas meter at 100%, prompts user to top up
        ↓
User reviews run in dashboard, clicks "Add gas", enters additional tokens
        ↓
POST /agents/{id}/gas or POST /sessions/{id}/gas with {"amount": N}
        ↓
Gateway increments gas_limit by N in DB
Gateway calls agent.add_gas(N) via POST /internal/jobs/{id}/add-gas
        ↓
Agent increments gas_limit, re-enters loop from where it paused
Status → running, agent continues
```

### Gas in the Agent Class

The `Agent` class tracks gas internally:

- `self._gas_used` — accumulated token count across all LLM calls
- `self._gas_limit` — current limit; increases when `add_gas()` is called
- After each LLM call: `self._gas_used += input_tokens + output_tokens`
- Before each new LLM call: if `self._gas_used >= self._gas_limit`, emit `out_of_gas` event and `await self._gas_event.wait()` — an `asyncio.Event` that is set by `add_gas()`
- `add_gas(amount)` increments `self._gas_limit` and calls `self._gas_event.set()`, which unblocks the suspended loop

### Gas API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/agents/{id}/gas` | Add gas to a job; body: `{"amount": N}` |
| `POST` | `/sessions/{id}/gas` | Add gas to a session; body: `{"amount": N}` |
| `GET` | `/agents/{id}/gas` | Return current `gas_used`, `gas_limit`, `topup_history` |
| `GET` | `/sessions/{id}/gas` | Return current `gas_used`, `gas_limit`, `topup_history` |

The `POST` endpoints are accessible to any authenticated user. In a future access control layer they could be restricted to job/session owners or operators.

### Gas in the Dashboard

Every job card and session card in the dashboard displays a **gas meter** — a linear progress bar showing `gas_used / gas_limit`. The meter updates live via the SSE stream as `gas_updated` events arrive.

When a job or session reaches `out_of_gas` status:
- The gas meter fills to 100% and turns amber
- A banner appears: *"Agent paused — out of gas. Review the execution trace below and add more tokens to continue."*
- A numeric input and **Add Gas** button appear, pre-populated with the system default top-up amount
- Submitting calls `POST /agents/{id}/gas` or `POST /sessions/{id}/gas`; the status transitions back to `running` and the meter resets to the new ratio

The full execution trace (all log events up to the pause point) remains visible while the run is `out_of_gas`, so the user has full context before deciding whether to continue.

---

## Control Plane Dashboard — `dashboard/index.html`

A single-page React application served directly by the gateway at `/`. It communicates with the gateway's REST and SSE endpoints only — it has no direct access to Kubernetes or GitLab.

### Active Agents View

Shows all jobs with status `pending` or `running`. Each agent is displayed as a card showing:

- Task type, project name, and job ID
- Animated status indicator (pulsing for running, static for pending)
- Elapsed running time, updated every second in the browser
- Most recent log line as a live preview
- A **Cancel** button that calls `POST /agents/{id}/cancel`

Clicking a card expands an inline **Log Panel** (see below).

### History View

A paginated, searchable, filterable table of all completed, failed, and cancelled jobs. Columns: task type, project, status, duration, and time since completion. Each row has a **Logs** button to open the full execution trace and a **Retry** button (failed jobs only) that re-POSTs the original `TaskSpec` to `/trigger`.

Supports filtering by status and free-text search across project name and task type.

### Agent Session Interface

The session interface is the primary new addition to the dashboard. It is accessible via a **New Session** button in the top navigation and consists of two phases: session configuration and the live session workspace.

**Session Launcher (configuration phase)**

A focused modal or full-page form where the user configures their session before launching:

- **Project picker** — a search-as-you-type input that calls `GET /projects/search` and shows matching projects the user has access to across all connected providers. Projects are shown with their provider, namespace, and a recent activity indicator. Users can also type a full project path manually (e.g. `group/subgroup/repo`) and skip the search.
- **Branch selector** — populated via `GET /projects/{id}/branches` once a project is selected. Defaults to the project's default branch. Supports free-text entry for branches not yet in the list.
- **Target MR** (optional) — a dropdown populated via `GET /projects/{id}/mrs` showing open merge requests. Selecting one scopes the agent's context to that MR.
- **Skill / tool overrides** — a multi-select showing available global and project-level skills and tools. Pre-populated from the project's resolved `AgentConfig`; the user can add or remove items to customise this session without editing the repo.
- **Goal** — a large free-text area for the initial instruction. Placeholder examples: *"Review the security of the authentication module"*, *"Refactor the data pipeline for readability and add missing type annotations"*, *"Investigate why the integration tests are flaky on CI"*.
- **Launch** button — calls `POST /sessions`, receives a `SessionRecord`, and transitions to the session workspace.

**Session Workspace (live phase)**

A split-pane workspace that combines a conversation thread with the live agent execution trace:

Left pane — **Conversation thread**: displays the full `SessionMessage` history in a chat-style layout. User messages (instructions, interrupts, input responses) appear on the right; agent messages (responses, questions) appear on the left. The agent's messages include a status indicator showing whether the agent is currently running, waiting, or finished. A persistent text input at the bottom allows the user to send new messages at any time:
  - If the session status is `waiting_for_user` (agent asked a question), the input is highlighted and labelled *"Agent is waiting for your answer"* — sending a message resumes the agent
  - If the session status is `running`, sending a message delivers an interrupt — the input is labelled *"Redirect the agent"* with a warning that the current step will be allowed to finish before the redirect takes effect
  - If the session is `complete` or `failed`, the input is disabled and replaced with a **New Session** button pre-populated with the same project and branch

Right pane — **Execution trace**: the same structured log panel used for jobs (LLM queries, tool calls, tool results), streamed live via `GET /sessions/{id}/stream`. The two panes are synchronised: when the agent emits an `input_request` log event, both panes update simultaneously — the left pane shows the agent's question as a chat bubble, and the right pane shows the `input_request` log event.

A **session header bar** shows the project name, branch, target MR (if set), elapsed time, and current status with the animated indicator. A **Cancel** button is available while the session is active.

---

### Log Panel

The log panel opens inline below an agent card (active) or in a modal (history). It renders the structured `LogEvent` stream with each event type visually distinct:

| Event type | Rendering |
|---|---|
| `llm_query` | Collapsible block showing message count and tool list |
| `llm_response` | LLM output text with token count badge; tool call decisions highlighted |
| `tool_call` | Tool name in accent colour with formatted argument key/value pairs |
| `tool_result` | Return value in a monospace block with duration badge |
| `complete` | Summary banner with aggregate stats (LLM calls, tool calls, total time) |
| `error` | Red error block with message and collapsible traceback |

For **active agents**, the panel subscribes to `GET /agents/{id}/logs/stream` (SSE) and appends events as they arrive. Auto-scroll follows the latest event but pauses if the user scrolls up, resuming when they scroll back to the bottom.

For **historical agents**, the panel fetches `GET /agents/{id}/logs` once and renders all events immediately, with the option to replay them in sequence at real speed.

---

## Docker Images

### `Dockerfile.gateway`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway/ ./gateway/
COPY shared/ ./shared/
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "3000"]
```

### `Dockerfile.worker`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker/ ./worker/
COPY shared/ ./shared/
CMD ["python", "-m", "worker.main"]
```

---

## Kubernetes Manifests

### `k8s/gateway-deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pi-agent-gateway
  namespace: pi-agents
spec:
  replicas: 2
  selector:
    matchLabels:
      app: pi-agent-gateway
  template:
    metadata:
      labels:
        app: pi-agent-gateway
    spec:
      serviceAccountName: pi-agent-gateway
      containers:
        - name: gateway
          image: your-registry/pi-agent-gateway:latest
          ports:
            - containerPort: 3000
          livenessProbe:
            httpGet:
              path: /healthz
              port: 3000
          env:
            - name: GITLAB_WEBHOOK_SECRET
              valueFrom:
                secretKeyRef:
                  name: gitlab-creds
                  key: webhook-secret
            - name: PI_AGENT_IMAGE
              value: your-registry/pi-agent-worker:latest
            - name: LLM_ENDPOINT
              value: https://api.openai.com/v1
            - name: AGENT_CONFIG_DIR
              value: .agents   # override per deployment; must be repo-relative, no leading slash
---
apiVersion: v1
kind: Service
metadata:
  name: pi-agent-gateway
  namespace: pi-agents
spec:
  selector:
    app: pi-agent-gateway
  ports:
    - port: 80
      targetPort: 3000
```

### `k8s/rbac.yaml`

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: pi-agent-gateway
  namespace: pi-agents
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: job-spawner
  namespace: pi-agents
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "watch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: gateway-job-spawner
  namespace: pi-agents
subjects:
  - kind: ServiceAccount
    name: pi-agent-gateway
roleRef:
  kind: Role
  name: job-spawner
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: pi-agent-worker
  namespace: pi-agents
```

### `k8s/secrets.yaml`

```yaml
# Apply with: kubectl apply -f k8s/secrets.yaml
# Values should be base64-encoded: echo -n "value" | base64
apiVersion: v1
kind: Secret
metadata:
  name: gitlab-creds
  namespace: pi-agents
data:
  token: <base64-encoded-gitlab-token>
  webhook-secret: <base64-encoded-webhook-secret>
---
apiVersion: v1
kind: Secret
metadata:
  name: llm-creds
  namespace: pi-agents
data:
  api-key: <base64-encoded-llm-api-key>
```

---

## GitLab Configuration

### Webhook Setup

In your GitLab project, go to **Settings → Webhooks → Add new webhook**:

| Field | Value |
|---|---|
| URL | `https://pi-agent-gateway.your-domain.com/webhook/gitlab` |
| Secret token | matches `GITLAB_WEBHOOK_SECRET` in the K8s secret |
| Trigger: Push events | ✅ |
| Trigger: Merge request events | ✅ |
| Trigger: Comments | ✅ |

### Manual Trigger via CI — `.gitlab-ci.yml`

```yaml
trigger-pi-agent:
  stage: review
  when: manual
  variables:
    TASK: "handle_comment"
    CONTEXT: '{"instruction": "Refactor the auth module for readability"}'
  script:
    - |
      curl -sf -X POST https://pi-agent-gateway.your-domain.com/trigger \
        -H "Content-Type: application/json" \
        -d "{\"task\": \"$TASK\", \"project_id\": $CI_PROJECT_ID, \"context\": $CONTEXT}"
```

---

## End-to-End Flow

```
1. GitLab event fires (MR opened, comment posted, push, or manual CI job)
        ↓
2. Gateway receives webhook, validates secret token
        ↓
3. Gateway maps event → TaskSpec, calls config loader:
   a. Fetches {AGENT_CONFIG_DIR}/config.yaml (default .agents/config.yaml) from project repo at event commit SHA via GitLab API
   b. Merges with global defaults → resolves AgentConfig (skills, tools, prompt, image)
   c. If project has a custom Dockerfile: checks image cache by Dockerfile blob SHA;
      builds derived image via Kaniko Job if not cached; waits for push to registry
        ↓
4. Gateway creates JobRecord in DB (status: pending), spawns ephemeral K8s Job
   using resolved AgentConfig image tag and composed system prompt
        ↓
5. Worker pod boots, AgentLogger initialised with gateway callback URL + job ID
        ↓
6. Worker POSTs status update → gateway sets job to running in DB
        ↓
7. Agent calls LLM:
   AgentLogger emits llm_query event → gateway persists + fans out to SSE subscribers
        ↓
8. LLM responds with tool call decision:
   AgentLogger emits llm_response event → gateway persists + fans out
        ↓
9. Agent executes tool (e.g. get_mr_diff, post_mr_comment, commit_file):
   AgentLogger emits tool_call event → gateway persists + fans out
   Tool runs against GitLab API
   AgentLogger emits tool_result event → gateway persists + fans out
        ↓
10. Steps 7–9 repeat for each agent loop iteration
        ↓
    After each LLM call: Agent emits gas_updated event → gateway updates gas_used in DB
    If gas_used >= gas_limit: Agent emits out_of_gas → gateway sets status out_of_gas
    Dashboard shows full trace + top-up prompt; user can add gas to resume
        ↓
11. Agent loop completes:
    AgentLogger emits complete event → worker POSTs status: completed to gateway
        ↓
12. Pod exits cleanly, Kubernetes TTL cleans up Job after 5 minutes
        ↓
13. Dashboard reflects final state; log panel shows full execution trace for replay
```

---

## Interactive Session Flow

This supplements the webhook-triggered end-to-end flow above, describing the lifecycle of a user-initiated interactive session.

```
1. User opens "New Session" in the dashboard, selects project + branch + goal
        ↓
2. Dashboard calls GET /projects/search, /branches, /mrs to populate launcher
        ↓
3. User clicks Launch → POST /sessions with SessionContext
        ↓
4. Gateway resolves AgentConfig (config loader: fetch .agents/config.yaml,
   merge skills/tools with session overrides, compose prompt, resolve image)
        ↓
5. Gateway creates SessionRecord in DB (status: configuring),
   spawns K8s Job with SESSION_ID + GATEWAY_URL env vars
        ↓
6. Worker pod boots in session mode, connects to gateway,
   updates session status → running
        ↓
7. Agent loop begins. At the top of each iteration:
   Worker calls POST /internal/sessions/{id}/interrupt-check
   → if an interrupt is pending, it is injected into LLM context
        ↓
8. Agent calls LLM with goal + conversation history:
   AgentLogger emits llm_query → gateway persists, SSE fans out to dashboard
        ↓
9. LLM responds — either:
   a) Tool call decision → tool executes, result logged, loop continues (→ step 7)
   b) Natural language response → emitted as agent_response SessionMessage,
      appears in dashboard conversation thread
   c) Input request → agent calls POST /internal/sessions/{id}/await-input
      with question; gateway transitions session → waiting_for_user;
      question appears in conversation thread; worker blocks
        ↓
10. (If waiting_for_user) User types answer → POST /sessions/{id}/messages
    Gateway enqueues message, transitions session → running,
    await-input call returns with user's answer → agent loop resumes (→ step 7)
        ↓
11. (If running) User sends an interrupt → POST /sessions/{id}/messages
    Gateway enqueues interrupt; picked up at next iteration (→ step 7)
        ↓
12. Agent determines goal is complete, emits complete event
    Worker POSTs status: complete to gateway
        ↓
13. Session status → complete, conversation input disabled,
    full execution trace available for replay
```

---

## Authentication

### Overview

Authentication is handled by **oauth2-proxy** sitting in front of the gateway, but the specific IdP configuration — which OAuth2 provider to use, how to restrict access, and how to interpret forwarded identity headers — is driven by an `AuthProvider` abstraction that mirrors the `RepositoryProvider` pattern.

This means:
- Switching repo providers does not force a change in IdP (a GitHub-backed deployment can still authenticate via GitLab or Keycloak)
- Each repo provider ships a default `AuthProvider` that uses its own OAuth2 flow, so the common case requires no extra configuration
- Organisations with a centralised IdP (Keycloak, Okta, Azure AD) can override the auth provider independently of the repo provider

### AuthProvider Abstraction — `providers/auth_base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class OAuthProxyConfig:
    provider_flag: str          # value for --provider (e.g. "gitlab", "github", "oidc")
    oidc_issuer_url: str | None # value for --oidc-issuer-url (OIDC providers only)
    extra_flags: list[str]      # provider-specific flags, e.g.:
                                # ["--gitlab-group=my-group"]
                                # ["--github-org=my-org", "--github-team=agents"]

@dataclass
class UserIdentity:
    username: str               # canonical stable username for session ownership
    email: str
    groups: list[str]           # group/org/team memberships for future authz use

class AuthProvider(ABC):

    @abstractmethod
    def oauth_proxy_config(self) -> OAuthProxyConfig:
        """Return the oauth2-proxy flags for this IdP."""

    @abstractmethod
    def extract_user(self, headers: dict[str, str]) -> UserIdentity:
        """
        Extract a normalised UserIdentity from the forwarded headers
        set by oauth2-proxy after successful authentication.
        Different IdPs use different header names and value formats.
        """
```

### Initial AuthProvider Implementation

**`providers/gitlab/auth.py` — `GitLabAuthProvider`** is the only implementation built initially, since GitLab is the initial repository provider.

It implements `oauth_proxy_config()` returning `--provider=gitlab` and `--gitlab-group=<group>` in `extra_flags`, and `extract_user()` reading `X-Forwarded-User` (username), `X-Forwarded-Email`, and `X-Forwarded-Groups` — the headers oauth2-proxy sets in GitLab mode.

Additional implementations are deferred until their corresponding repo providers are built (see Phase 8 in the implementation plan). The expected future implementations and their header mappings are documented in `providers/auth_base.py` as comments alongside the ABC, so the contract is clear when the time comes:

- **`GitHubAuthProvider`** — `--provider=github`, `--github-org`, reads `X-Forwarded-User` (login handle) and `X-Forwarded-Groups`
- **`OIDCAuthProvider`** — `--provider=oidc`, configurable issuer URL, reads `X-Auth-Request-User` / `X-Auth-Request-Email` / `X-Auth-Request-Groups` (different header names from the provider-specific modes)

### Auth Provider Registry — `providers/auth_registry.py`

```python
import os
from providers.auth_base import AuthProvider

def get_auth_provider() -> AuthProvider:
    # Defaults to PROVIDER value so operators only need one env var in the common case.
    # Override AUTH_PROVIDER independently when repo and IdP differ (future use).
    auth_name = os.getenv("AUTH_PROVIDER", os.getenv("PROVIDER", "gitlab"))
    match auth_name:
        case "gitlab":
            from providers.gitlab.auth import GitLabAuthProvider
            return GitLabAuthProvider(
                group=os.getenv("GITLAB_AUTH_GROUP"),
                url=os.getenv("GITLAB_URL", "https://gitlab.com"),
            )
        # "github" and "oidc" cases added in Phase 8 when those providers are implemented
        case _:
            raise ValueError(f"Unknown auth provider: {auth_name!r}")
```

`AUTH_PROVIDER` defaults to the value of `PROVIDER`. The registry is structured so additional cases are added without touching any other code when new providers are implemented.

### How the Gateway Uses AuthProvider

The gateway calls `get_auth_provider()` once at startup and holds the instance for the lifetime of the process. It is used in two places:

**Identity extraction** — every authenticated request calls `auth_provider.extract_user(request.headers)` to obtain a `UserIdentity`. The `username` field is stored as `owner` on `SessionRecord` and `triggered_by` on `JobRecord`. Because `extract_user` is provider-specific, the correct header is always read regardless of IdP — the gateway never references `X-Forwarded-User` directly.

**oauth2-proxy configuration** — the gateway exposes `GET /internal/oauth2-proxy-config` which renders `auth_provider.oauth_proxy_config()` as oauth2-proxy CLI args. A Helm chart or init container consumes this at install time to generate the correct Deployment manifest. Constant flags (skip-auth routes, cookie settings, upstream URL, redirect URL) are set statically; only IdP-specific flags come from `AuthProvider`.

### GitLab OAuth2 Application Setup (Default Case)

When `AUTH_PROVIDER=gitlab` (the default), create a GitLab OAuth2 application:

**Group-level:** GitLab Group → Settings → Applications → Add new application
**Instance-level (self-hosted):** Admin → Applications → New application

| Field | Value |
|---|---|
| Name | `Agent Control Plane` |
| Redirect URI | `https://pi-agent.your-domain.com/oauth2/callback` |
| Scopes | `openid`, `profile`, `email`, `read_user` |
| Confidential | ✅ |

Store the Application ID and Secret in the `oauth2-proxy-creds` K8s Secret alongside `GITLAB_AUTH_GROUP`.

### Ingress Routing

Webhook and internal paths bypass oauth2-proxy and route directly to the gateway. All browser traffic goes through oauth2-proxy. The ingress configuration is IdP-agnostic — oauth2-proxy presents a consistent interface to the Ingress regardless of which backend IdP is in use.

### When to Override AUTH_PROVIDER

Initially only the first row applies. The table documents the intended future behaviour so the design intent is clear when additional providers are implemented.

| Scenario | `PROVIDER` | `AUTH_PROVIDER` | Status |
|---|---|---|---|
| GitLab repos, GitLab auth | `gitlab` | _(inherits)_ | ✅ Implemented |
| GitHub repos, GitHub auth | `github` | _(inherits)_ | ⏳ Phase 8 |
| GitLab repos, Keycloak/Okta | `gitlab` | `oidc` | ⏳ Phase 8 |
| GitHub repos, Keycloak/Okta | `github` | `oidc` | ⏳ Phase 8 |
| Multiple providers, single IdP | _(per deployment)_ | `oidc` | ⏳ Phase 8 |

---

## Security Considerations

| Concern | Mitigation |
|---|---|
| Webhook authenticity | HMAC token comparison via `hmac.compare_digest` |
| GitLab API credentials | Injected via K8s Secret, never in env files or CI vars |
| LLM API key | Injected via K8s Secret |
| Worker cluster permissions | Dedicated `ServiceAccount` with no K8s API access |
| Gateway cluster permissions | `Role` scoped to `batch/jobs` in `pi-agents` namespace only |
| Branch protection | System prompt instructs agent never to commit to `main` directly |
| Internal log endpoints | `/internal/*` routes protected by shared secret, excluded from Ingress auth |
| Dashboard authentication | oauth2-proxy configured by `AuthProvider.oauth_proxy_config()`; IdP and group/org restriction are provider-specific |
| Identity header extraction | Gateway never hardcodes `X-Forwarded-User`; always calls `auth_provider.extract_user(headers)` so correct headers are read for any IdP |
| AUTH_PROVIDER / PROVIDER decoupling | The two env vars default to the same value but can be set independently, allowing any combination of repo provider and IdP |
| Webhook bypass | `/webhook/*` and `/internal/*` routed directly to gateway, bypassing oauth2-proxy |
| User attribution | `X-Forwarded-User` header used to record which operator triggered manual runs |
| Log data sensitivity | LLM prompts and tool results may contain code and secrets — group restriction limits exposure to GitLab group members only |
| Project Dockerfile trust | Project Dockerfiles run as a layer on the global base image — the base image is controlled by operators; projects cannot replace it or escalate privileges |
| Config fetch credentials | Gateway uses the provider's service token to fetch `{AGENT_CONFIG_DIR}/config.yaml` via `provider.get_file_at_sha()` — no additional credentials required |
| Invalid project config | Config loader validates `config.yaml` with Pydantic; malformed files fall back to global defaults with a logged warning rather than failing the agent run |
| Image build isolation | Kaniko builds run in a dedicated namespace with no access to the host Docker socket, and images are pushed directly to the registry without a local daemon |
| Session ownership | Sessions are scoped to the `X-Forwarded-User` identity; the gateway rejects requests to view or message sessions owned by a different user |
| Session worker access | Session workers can only access GitLab projects the launching user has access to — the agent is spawned with a scoped token, not the global service token |
| Project search proxy | `GET /projects/search` proxies to the provider via `provider.search_projects(user_token=...)`, ensuring users cannot enumerate projects they lack access to |
| Interrupt safety | Interrupts are delivered at iteration boundaries, never mid-tool-execution, preventing partial writes or inconsistent repo state |
| Gas top-up safety | `add_gas` is processed only between LLM calls — never mid-tool-execution; the agent always finishes the current tool before checking the gas limit |
| Gas limit enforcement | Gas limit is enforced inside the `Agent` class before each new LLM call; it cannot be bypassed by the worker or toolkit code |


---

## Implementation Plan

The implementation is broken into seven phases, each independently deployable and testable. Each phase begins with tests written against the specified contracts before any implementation code is written — this allows an AI agent iterating on the codebase to run the test suite after each change and get unambiguous pass/fail signal.

Phases are ordered so that every phase produces a working, observable system. Infrastructure comes first, then the core agent loop, then observability, then configuration, then authentication, then the dashboard, and finally interactive sessions.

---

### Phase 1 — Infrastructure Foundation

**Goal:** A working Kubernetes namespace with the gateway running, able to receive webhook events and spawn Jobs.

**Scope:**
- `shared/models.py` — `TaskSpec`, `JobRecord` only
- `gateway/db.py` — jobs table only (no log events yet)
- `gateway/kube_client.py` — job spawning, no config resolution yet; uses global image unconditionally
- `providers/base.py` — `RepositoryProvider` ABC, `ProviderToolkit` ABC, all shared event and data models
- `providers/auth_base.py` — `AuthProvider` ABC, `OAuthProxyConfig`, `UserIdentity` data classes (the abstraction layer; no additional implementations yet)
- `providers/gitlab/provider.py` — GitLab implementation (file fetch, commit, MR, comments, status, webhook verify, event parse)
- `providers/gitlab/webhook.py` — webhook signature verification and `parse_webhook_event`
- `providers/gitlab/auth.py` — `GitLabAuthProvider`: `oauth_proxy_config()` and `extract_user()`
- `providers/registry.py` — provider factory, reads `PROVIDER` env var
- `providers/auth_registry.py` — auth provider factory; initially only `gitlab` case; structured for future extension
- `gateway/event_mapper.py` — maps provider-agnostic events to `TaskSpec`; all three event types
- `gateway/main.py` — `/webhook/gitlab`, `/trigger`, `/healthz`, `/agents`, `/agents/history`
- `k8s/` — namespace, gateway Deployment, RBAC, Secrets, Service, Ingress (no oauth2-proxy yet)

**Tests to write first:**
- Unit: `GitLabProvider.parse_webhook_event` maps all three raw GitLab payloads to the correct shared event models; returns `None` for unknown event types
- Unit: `GitLabProvider.verify_webhook` returns `True` for valid HMAC, `False` for invalid
- Unit: `event_mapper` correctly maps `MREvent`, `CommentEvent`, `PushEvent` to `TaskSpec`; returns `None` for `None` input
- Unit: `event_mapper` handles missing optional fields gracefully (e.g. `CommentEvent` with no `mr_iid`)
- Unit: `get_provider()` returns `GitLabProvider` when `PROVIDER=gitlab`; raises `ValueError` for unknown provider
- Unit: `get_auth_provider()` returns `GitLabAuthProvider` when `AUTH_PROVIDER=gitlab`
- Unit: `get_auth_provider()` defaults to `PROVIDER` value when `AUTH_PROVIDER` is unset
- Unit: `get_auth_provider()` raises `ValueError` for unknown auth provider names
- Unit: `GitLabAuthProvider.extract_user()` reads `X-Forwarded-User`, `X-Forwarded-Email`, `X-Forwarded-Groups` and returns a `UserIdentity`
- Unit: `GitLabAuthProvider.oauth_proxy_config()` returns `provider_flag="gitlab"` and `--gitlab-group` in `extra_flags`
- Unit: `GET /internal/oauth2-proxy-config` renders `auth_provider.oauth_proxy_config()` as CLI args correctly
- Unit: `db.py` — `create_job`, `update_job_status`, `list_jobs`, `get_job` round-trip correctly
- Unit: `kube_client` — mock the K8s batch API; assert correct Job manifest shape (env vars, image, restart policy, TTL)
- Integration: `POST /webhook/gitlab` with a valid token and MR Hook payload returns `{"job_name": ...}` and creates a DB record
- Integration: `POST /webhook/gitlab` with an invalid token returns 401
- Integration: `POST /trigger` spawns a job and persists a `JobRecord`
- Integration: `GET /agents` returns only `pending`/`running` jobs
- Integration: `GET /agents/history` returns completed/failed/cancelled jobs with pagination
- E2E (staging): post a real GitLab webhook; verify K8s Job appears in cluster; verify DB record created

**Definition of done:** Gateway runs in cluster, receives a real GitLab MR webhook, creates a K8s Job, and the Job record is visible via `GET /agents`.

---

### Phase 2 — Core Agent Worker

**Goal:** A worker pod that boots, runs the `Agent` loop against a real GitLab project, and reports completion back to the gateway.

**Scope:**
- `worker/tools/toolkit_base.py` — `ProviderToolkit` ABC
- `providers/gitlab/toolkit.py` — GitLab `ProviderToolkit` implementation, all seven tools wrapping `RepositoryProvider`
- `worker/tools/toolkit_factory.py` — `get_toolkit()` factory function
- `worker/agent_runner.py` — job mode only (no session mode yet)
- `worker/main.py` — reads env vars, calls `run_agent`
- `gateway/main.py` — add `POST /internal/jobs/{id}/status` endpoint
- `Dockerfile.worker`

**Tests to write first:**
- Unit: each `GitLabProvider` method — mock `python-gitlab`; assert correct API calls and that return values are shared model instances (`FileContent`, `CommitResult`, `MRResult`), not SDK types
- Unit: `GitLabProvider.commit_file` falls back to `create` when `update` raises `GitlabGetError`
- Unit: each `GitLabToolkit` tool's `execute` function calls the correct `RepositoryProvider` method with correct arguments — mock `RepositoryProvider`, not `python-gitlab` directly
- Unit: `get_toolkit()` returns `GitLabToolkit` when `PROVIDER=gitlab`
- Unit: `build_system_prompt` includes the task type string
- Unit: `build_task_message` produces correct strings for all three task types; default case for unknown task
- Unit: `Agent` calls LLM with correct message history and tool schemas
- Unit: `Agent` executes tool calls and appends results to conversation history
- Unit: `Agent` loops until LLM returns a response with no tool calls
- Unit: `Agent.steer()` message is injected after the current tool, before remaining tools in the same turn
- Unit: `Agent.follow_up()` message is injected only after the agent is fully idle
- Unit: `Agent` calls `event_handler` in correct order: llm_query -> llm_response -> gas_updated -> tool_call -> tool_result (repeat) -> complete
- Unit: `Agent` emits `out_of_gas` and suspends when `gas_used >= gas_limit` before the next LLM call
- Unit: `Agent.add_gas(N)` increments `gas_limit` and resumes the suspended loop
- Unit: gas is checked before each LLM call, not mid-tool — tool execution is never interrupted by an out-of-gas condition
- Unit: `run_agent` constructs `Agent` with correct arguments — mock `Agent.run`
- Integration: `POST /internal/jobs/{id}/status` with `{"status": "completed"}` updates DB and returns 200; unknown job ID returns 404
- Integration: worker `main.py` reads `TASK`, `PROJECT_ID`, `TASK_CONTEXT` env vars and calls `run_agent` with correct arguments
- E2E (staging): trigger a `review_mr` job against a test GitLab project; verify agent posts a comment on the MR

**Definition of done:** End-to-end webhook → worker → GitLab comment flow works against a real project.

---

### Phase 3 — Structured Logging and Observability

**Goal:** Every agent run emits typed log events that are persisted in the gateway and visible via API.

**Scope:**
- `shared/models.py` — add `LogEvent`
- `gateway/db.py` — add `log_events` table; `append_log_event`, `get_log_events`
- `gateway/main.py` — add `POST /internal/log`, `GET /agents/{id}/logs`, `GET /agents/{id}/logs/stream` (SSE)
- `worker/agent_logger.py` — `AgentEvent` handler; emits all six event types to the gateway
- `Agent` already calls `event_handler` for every event; pass `AgentLogger.handle_event` as the handler — no changes to `Agent` or `agent_runner.py` required

**Tests to write first:**
- Unit: `AgentLogger` emits `llm_query` before LLM call with correct `messages`, `model`, `tools` payload
- Unit: `AgentLogger` emits `llm_response` after LLM call with `content`, `tool_calls`, token counts
- Unit: `AgentLogger` emits `tool_call` before tool execution with `tool_name`, `arguments`
- Unit: `AgentLogger` emits `tool_result` after tool execution with `tool_name`, `result`, `duration_ms`
- Unit: `AgentLogger` emits `complete` on clean exit with aggregate counts
- Unit: `AgentLogger` emits `error` with `message` and `traceback` when an exception is raised
- Unit: `AgentLogger` fire-and-forget HTTP — a slow gateway response does not block agent execution; timeout is enforced
- Unit: `LogEvent` sequence numbers are monotonically increasing across concurrent emits
- Unit: `db.py` — `append_log_event`, `get_log_events` ordered by sequence
- Integration: `POST /internal/log` persists a `LogEvent` and returns 200; malformed payload returns 422
- Integration: `GET /agents/{id}/logs` returns all events for a job in sequence order
- Integration: `GET /agents/{id}/logs/stream` — connect SSE client; post log events via internal endpoint; assert events are received in order with correct `event_type` fields
- E2E (staging): run a full agent job; assert all six event types appear in `GET /agents/{id}/logs`

**Definition of done:** A completed agent job has a full structured execution trace retrievable via API and streamable via SSE.

---

### Phase 4 — Project Configuration

**Goal:** Projects can place a `.agents/config.yaml` in their repo and the agent will use their custom skills, tools, prompt, and image.

**Scope:**
- `shared/models.py` — add `SkillDef`, `ToolDef`, `ProjectConfig`, `AgentConfig`
- `gateway/config_loader.py` — full implementation: fetch, parse, merge, Kaniko build
- `global-config/` — base `agent-config.yml`, initial skills and tools directories
- Update `gateway/kube_client.py` to accept `AgentConfig` and use resolved image + prompt
- Update `gateway/main.py` — call config loader before spawning every job
- `AGENT_CONFIG_DIR` env var wired through gateway Deployment manifest

**Tests to write first:**
- Unit: `config_loader` returns global defaults when project has no `.agents/config.yaml`
- Unit: `config_loader` returns global defaults (with warning log) when `.agents/config.yaml` is malformed YAML
- Unit: `config_loader` returns global defaults (with warning log) when `.agents/config.yaml` fails Pydantic validation
- Unit: skill merging — project skills are appended after global skills; duplicates (by name) are deduplicated, keeping the project definition
- Unit: tool merging — same deduplication behaviour as skills
- Unit: `prompt_mode: append` — project prompt is appended to global base with a newline separator
- Unit: `prompt_mode: override` — project prompt completely replaces global base
- Unit: `AGENT_CONFIG_DIR` env var changes the path used to fetch config (default `.agents`)
- Unit: Kaniko cache key is `f"{project_id}-{dockerfile_blob_sha}"`; same key returns cached image without spawning a build Job
- Unit: Kaniko job is spawned when cache key is absent; returned image tag follows the expected pattern
- Unit: config is fetched at event commit SHA, not HEAD
- Integration: spawn a job for a project with a valid `.agents/config.yaml`; assert `AgentConfig` passed to `kube_client` has merged skills/tools and composed prompt
- Integration: spawn a job for a project with a custom Dockerfile; assert Kaniko Job is created; after mock completion, assert derived image tag is used in worker Job manifest
- E2E (staging): create a test project with a `.agents/config.yaml` adding a custom skill; trigger an agent run; verify custom skill is available in the worker

**Definition of done:** A project with `.agents/config.yaml` runs an agent with its custom configuration; a project without the file runs identically to Phase 2/3.

---

### Phase 5 — Authentication

**Goal:** The dashboard and API are accessible only to authenticated GitLab users who are members of the configured group.

**Scope:**
- `k8s/oauth2-proxy-deployment.yaml`
- `k8s/ingress.yaml` — split routing (webhook/internal bypass, everything else through proxy)
- `k8s/secrets.yaml` — add `oauth2-proxy-creds` secret
- `gateway/main.py` — call `auth_provider.extract_user(headers)` on every authenticated request; attach `UserIdentity.username` to `JobRecord` and `SessionRecord`
- `gateway/main.py` — add `GET /internal/oauth2-proxy-config` endpoint
- GitLab OAuth2 application setup (documented steps)

**Tests to write first:**
- Integration: requests to `/` without a valid session cookie are redirected to GitLab OAuth2 login
- Integration: requests to `/webhook/gitlab` bypass oauth2-proxy and reach the gateway directly (no redirect)
- Integration: requests to `/internal/log` bypass oauth2-proxy and reach the gateway directly
- Integration: `POST /trigger` with valid session calls `auth_provider.extract_user()` and sets `triggered_by` on `JobRecord` to the returned `username`
- Integration: `POST /trigger` without forwarded headers (direct cluster call) sets `triggered_by` to `"system"`
- Integration: `GET /internal/oauth2-proxy-config` returns correct CLI args for `GitLabAuthProvider`
- Unit: gateway uses `auth_provider.extract_user()` and never directly reads `X-Forwarded-User` header — enforced by asserting no string literal `'X-Forwarded-User'` appears outside `providers/`
- E2E (staging): log in via GitLab OAuth; assert dashboard is accessible; log out; assert redirect to login

**Definition of done:** Dashboard requires GitLab login; webhook and internal endpoints are unaffected; operator identity is recorded on manual triggers.

---

### Phase 6 — Control Plane Dashboard

**Goal:** A fully functional browser dashboard for monitoring jobs, viewing history, and inspecting execution traces.

**Scope:**
- `dashboard/index.html` — full React SPA
  - Active agents view (polling or SSE-driven job list)
  - History view (paginated, filterable, searchable)
  - Log panel (SSE live stream for active jobs, full replay for history)
  - Cancel and retry actions
  - Gas meter per job/session card (live via SSE)
  - Out-of-gas banner with top-up input and Add Gas button

**Tests to write first:**
- Unit (React): `StatusPill` renders correct colour and pulse animation for each status value
- Unit (React): `AgentCard` displays task type, project name, elapsed time, and most recent log line
- Unit (React): `AgentCard` cancel button calls `POST /agents/{id}/cancel` and updates local state optimistically
- Unit (React): `LogPanel` renders each of the six event types with distinct visual treatment
- Unit (React): `LogPanel` auto-scrolls to bottom on new events; pauses when user scrolls up; resumes on scroll to bottom
- Unit (React): `LogPanel` fetches full log history via `GET /agents/{id}/logs` for completed jobs
- Unit (React): `HistoryRow` retry button is visible only for `failed` jobs; calls `POST /trigger` with original context
- Unit (React): history search filters rows by project name and task type; status filter works independently
- Integration: `POST /agents/{id}/cancel` deletes the K8s Job and sets DB status to `cancelled`
- Integration: `POST /agents/{id}/gas` increments `gas_limit` in DB and calls internal add-gas endpoint; returns updated gas state
- Integration: `POST /agents/{id}/gas` on a non-`out_of_gas` job still increments limit (pre-emptive top-up) without triggering a resume
- Unit (React): gas meter renders correct fill ratio from `gas_used / gas_limit`
- Unit (React): gas meter turns amber and out-of-gas banner appears when status is `out_of_gas`
- Unit (React): Add Gas button calls `POST /agents/{id}/gas` and optimistically updates meter
- E2E (browser): navigate to dashboard; verify active jobs appear; expand log panel; verify events stream in; cancel a job; verify status updates

**Definition of done:** Operators can monitor all agent activity, inspect full execution traces, cancel running jobs, and retry failed ones entirely from the browser.

---

### Phase 7 — Interactive Sessions

**Goal:** Users can launch an ad hoc agent session against any GitLab project they have access to, converse with it in real time, steer it mid-run, and have the agent ask clarifying questions.

**Scope:**
- `shared/models.py` — add `SessionContext`, `SessionMessage`, `SessionRecord`
- `shared/models.py` — add `input_request`, `input_received`, `interrupted` log event types
- `gateway/db.py` — add `sessions` and `session_messages` tables
- `gateway/session_broker.py` — full implementation
- `gateway/main.py` — all session endpoints, project proxy endpoints, internal session endpoints
- `worker/agent_runner.py` — session mode (interrupt check, input suspension)
- `dashboard/index.html` — session launcher, session workspace (split-pane conversation + trace)

This phase has the most internal dependencies and should be implemented in the following sub-order:

**7a — Session data layer and broker**
- DB schema and methods for sessions and messages
- `session_broker.py` core: queue management, state transitions, `send_to_agent`, `await_user_input`, `check_interrupt`
- Session CRUD endpoints: `POST /sessions`, `GET /sessions`, `GET /sessions/{id}`, `GET /sessions/{id}/messages`
- Internal session endpoints: `POST /internal/sessions/{id}/await-input`, `POST /internal/sessions/{id}/interrupt-check`

**7b — Worker session mode**
- Update `agent_runner.py` with session mode branch — wire `Agent.steer()` and `Agent.follow_up()` to the broker endpoints
- Update `AgentLogger` with `input_request`, `input_received`, `interrupted` event types
- `GET /sessions/{id}/stream` SSE endpoint (interleaved messages and log events)

**7c — Session messaging**
- `POST /sessions/{id}/messages` endpoint
- End-to-end test: full session lifecycle via API only (no UI)

**7d — GitLab proxy endpoints and session launcher UI**
- `GET /projects/search`, `GET /projects/{id}/branches`, `GET /projects/{id}/mrs`
- Session launcher form in dashboard

**7e — Session workspace UI**
- Split-pane workspace: conversation thread + execution trace
- Context-aware input (waiting vs redirect mode)
- Session header bar with status and cancel

**Tests to write first (7a):**
- Unit: `session_broker.send_to_agent` enqueues a message and transitions `waiting_for_user → running`
- Unit: `session_broker.await_user_input` transitions session to `waiting_for_user`; blocks until message enqueued; returns message content
- Unit: `session_broker.check_interrupt` returns pending interrupt and clears it; returns `None` when none pending
- Unit: `session_broker` cleans up queue on terminal state transition
- Unit: `db.py` — session and message CRUD round-trips correctly
- Integration: `POST /sessions` creates a `SessionRecord` in `configuring` status, spawns K8s Job with `SESSION_ID` env var
- Integration: `POST /internal/sessions/{id}/await-input` blocks until `POST /sessions/{id}/messages` is called; returns message content
- Integration: `POST /internal/sessions/{id}/interrupt-check` returns pending interrupt; returns empty on second call

**Tests to write first (7b):**
- Unit: worker in session mode calls `interrupt-check` at the start of each loop iteration
- Unit: worker injects interrupt message into LLM context when interrupt is returned
- Unit: worker calls `await-input` when agent emits `input_request`; LLM context updated with response on resume
- Unit: `AgentLogger` emits `input_request` event with question payload
- Unit: `AgentLogger` emits `input_received` event with response payload
- Unit: `AgentLogger` emits `interrupted` event with redirect message payload
- Integration: `GET /sessions/{id}/stream` SSE delivers `SessionMessage` and `LogEvent` records interleaved in arrival order

**Tests to write first (7c–7e):**
- Integration: full session lifecycle via API — create session, simulate worker calling await-input, send user message, verify session resumes, verify complete status
- Integration: `GET /projects/search` returns only projects the authenticated user can access (mock GitLab API)
- Unit (React): session launcher project search calls `/projects/search` on input; renders results with namespace
- Unit (React): branch selector populates from `/projects/{id}/branches`; defaults to default branch
- Unit (React): conversation thread renders user and agent messages with correct alignment and `message_type` labels
- Unit (React): input box label changes between "Redirect the agent" (running) and "Agent is waiting for your answer" (waiting_for_user)
- Unit (React): input is disabled when session is in terminal state
- E2E (browser + staging): launch a session against a test project; send initial goal; receive agent response; send interrupt; verify agent redirects; agent asks question; send answer; session completes

**Definition of done:** A user can launch a session from the browser, have a real conversation with an agent running against their GitLab project, steer it mid-run, answer its questions, and see the full execution trace alongside the conversation — with no local setup required.

---

### Phase 8 — Additional Auth and Repository Providers (Deferred)

**Goal:** Extend the system to support additional repository providers (GitHub, Bitbucket, etc.) and additional IdPs (GitHub OAuth, Keycloak/OIDC, Okta), each slotting into the existing abstraction without changes to the gateway, worker, dashboard, or session broker.

**Scope (per new provider added):**
- `providers/{name}/provider.py` — implement all `RepositoryProvider` abstract methods
- `providers/{name}/webhook.py` — implement `verify_webhook` and `parse_webhook_event`
- `providers/{name}/toolkit.py` — implement `ProviderToolkit`
- `providers/{name}/auth.py` — implement `AuthProvider` (`oauth_proxy_config` + `extract_user`)
- Register new cases in `providers/registry.py` and `providers/auth_registry.py`
- Add provider-specific credential env vars to the K8s Secret and gateway Deployment manifest
- For OIDC IdPs: implement `providers/auth_oidc.py` — `OIDCAuthProvider` using `--provider=oidc` and `X-Auth-Request-*` headers

**No changes required to:** `event_mapper.py`, `agent_runner.py`, `config_loader.py`, `session_broker.py`, `kube_client.py`, `db.py`, or any dashboard code.

**Tests follow the same pattern as Phase 0** — one set of provider unit tests and one set of auth unit tests per new provider, plus integration tests for the webhook path and a full E2E test for the session launcher showing projects from the new provider appearing alongside GitLab projects.

---

### Phase Summary

| Phase | Deliverable | External dependency |
|---|---|---|
| 0 | Provider abstraction layer (`providers/base.py`, `GitLabProvider`, registry) | None (pure Python) |
| 1 | Gateway + K8s Job spawning | Kubernetes cluster, provider webhook |
| 2 | Working agent worker | GitLab API, OpenAI-compatible LLM |
| 3 | Structured logging + SSE | None (internal) |
| 4 | Per-project configuration | GitLab API (config fetch), container registry (Kaniko) |
| 5 | Authentication | GitLab OAuth2 application |
| 6 | Control plane dashboard | None (consumes existing API) |
| 7 | Interactive sessions | All prior phases |
| 8 | Additional providers + IdPs (deferred) | Per-provider credentials |

Each phase is a mergeable increment. Phase 0 must be completed first as all subsequent phases depend on the provider abstraction. Phases 1–3 can then be developed largely in parallel by different engineers. Phase 4 depends on Phase 1. Phase 5 depends on Phase 1. Phase 6 depends on Phases 1–3 and 5. Phase 7 depends on all prior phases.

---

## Extending the Integration

Adding new agent capabilities follows a consistent pattern:

1. **New provider action** → add a method to `RepositoryProvider` ABC + implement in all provider classes; add a corresponding tool definition to each `ProviderToolkit` subclass
2. **New event type** → add a new model to `providers/base.py`; implement `parse_webhook_event` in each provider; add a `case` to `event_mapper.py`
3. **New task behaviour** → add a `case` to `build_task_message()` in `agent_runner.py` — no provider-specific changes needed
4. **New log event type** → add to the `Literal` union in `LogEvent`, emit from `AgentLogger`, add a renderer in the dashboard log panel
5. **New dashboard view** → add a route and fetch against the existing gateway REST API

No changes to the Kubernetes manifests or job spawner are required for most extensions.

**Adding a new provider** (e.g. GitHub) follows a fixed, contained pattern:

For the `AuthProvider` (see Phase 8 for deferred implementations):
1. Implement `AuthProvider` in `providers/{name}/auth.py` — `oauth_proxy_config()` with the correct `--provider` flag and restriction flags, and `extract_user()` mapping the IdP's header names to `UserIdentity`
2. Register it in `providers/auth_registry.py`
3. Add `AUTH_PROVIDER={name}` and credential env vars to the K8s Secret and gateway Deployment

For the `RepositoryProvider`:

1. Create `providers/github/` with `provider.py` (implement all `RepositoryProvider` abstract methods), `webhook.py` (implement `verify_webhook` and `parse_webhook_event`), and `toolkit.py` (implement `ProviderToolkit`)
2. Register it in `providers/registry.py`
3. Add `PROVIDER=github` and `GITHUB_TOKEN` to the K8s Secret and gateway Deployment
4. No changes to `event_mapper.py`, `agent_runner.py`, `config_loader.py`, `session_broker.py`, or any dashboard code

Additionally, the project configuration layer is independently extensible:

6. **New global skill or tool** → add a definition to `global-config/skills/` or `global-config/tools/` and update `global-config/agent-config.yml` — available to all projects immediately
7. **New `config.yaml` field** → add to `ProjectConfig`, handle in `config_loader.py`, pass through `AgentConfig` — backward compatible since unrecognised fields are ignored by existing projects
8. **New image build strategy** → implement alongside the Kaniko builder in `config_loader.py` — the rest of the stack only cares about the resolved image tag
9. **New session message type** → add to the `message_type` Literal in `SessionMessage`, handle in `session_broker.py`, add a renderer in the conversation thread UI
10. **New launcher context field** → add to `SessionContext`, surface in the session launcher form, pass through to the worker via env var — no changes to the broker or message protocol required
11. **Gas limit policy change** → adjust `DEFAULT_JOB_GAS_LIMIT` / `DEFAULT_SESSION_GAS_LIMIT` env vars or project-level `gas_limit` in `.agents/config.yaml` — no code changes required
