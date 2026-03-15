# AGENTS.md — Phalanx Codebase Guide for AI Agents

This file is written for AI agents (Claude, GPT-4, Gemini, etc.) that are working inside this repository. Read it before making changes.

---

## What this project does

Phalanx is an autonomous agent system for Git repositories. It listens to repository events (pushes, merge requests, comments) and spawns AI agents as ephemeral Kubernetes Jobs to review code, implement changes, and post inline feedback. A persistent gateway coordinates everything; a React SPA dashboard lets humans watch and intervene in real time.

---

## Repository layout (what lives where)

```
phalanx/
├── gateway/            # FastAPI service — the persistent control plane
├── worker/             # Python entrypoint for K8s Job pods
├── providers/          # Git provider abstraction + implementations
├── shared/             # Pydantic models shared between gateway and worker
├── dashboard/          # Single-file React SPA (index.html — no build step)
├── global-config/      # Default agent config, global skills and tools
├── k8s/                # Raw Kubernetes manifests (used for local dev)
├── helm/               # Helm chart for production deployment
├── kind/               # KIND cluster config for local development
├── scripts/            # Shell scripts for local dev (cluster-up, seed, etc.)
├── harness/            # Multi-phase test harness CLI
├── tests/              # Pytest test suite (unit + E2E)
└── docs/               # Architecture and usage documentation
```

---

## Core abstractions

### RepositoryProvider (`providers/base.py`)

The single integration boundary between Phalanx and any Git hosting platform. All gateway and worker code interacts with `RepositoryProvider` — never with a platform SDK directly.

Key methods:
- `get_file(project_id, path, ref)` — fetch file contents at a ref
- `commit_file(project_id, path, content, branch, message)` — write a file
- `create_mr(...)` / `post_mr_comment(...)` / `post_inline_comment(...)`
- `verify_webhook(payload, headers)` / `parse_webhook_event(payload, headers)`

Adding a new provider = create `providers/{name}/provider.py` implementing this ABC, plus `webhook.py`, `toolkit.py`, `auth.py`. Register in `providers/registry.py` and `providers/auth_registry.py`.

### ProviderToolkit (`worker/tools/toolkit_base.py`)

The set of tools exposed to the agent's LLM loop. Each tool is a Python async function; the toolkit maps function names to callables. The agent calls tools by name in its loop.

### Agent (`worker/agent.py`)

The core LLM interaction loop. Sends messages to an OpenAI-compatible API, dispatches tool calls, tracks gas (token) usage, and handles interrupts from the session broker. Does not know about Kubernetes, providers, or logging — those concerns live in `agent_runner.py`.

### Gateway (`gateway/main.py`)

FastAPI application. Key responsibilities:
- Receive webhooks from providers and spawn K8s Jobs
- Persist jobs, log events, and sessions to SQLite (`gateway/db.py`)
- Serve SSE streams for the dashboard
- Handle interactive session messages via the in-memory broker (`gateway/session_broker.py`)
- Provide REST endpoints for the dashboard (`/api/...`)

---

## Data flow

```
Provider webhook → gateway/main.py → event_mapper.py → TaskSpec
    → kube_client.py spawns K8s Job
        → worker/main.py → agent_runner.py → agent.py (LLM loop)
            → tools → provider toolkit → provider API
            → agent_logger.py → POST /internal/log → gateway DB
    → dashboard SSE stream ← gateway/main.py
```

---

## Environment variables (workers inherit these from K8s Job spec)

| Variable | Purpose |
|---|---|
| `PROVIDER` | Which provider to load (`gitlab`, `github`, `bitbucket`, `gitea`) |
| `GATEWAY_URL` | Internal URL of the gateway for posting log events |
| `JOB_ID` | Identifier of this job (for logging) |
| `SESSION_ID` | If set, run in session mode rather than job mode |
| `LLM_API_BASE` | OpenAI-compatible API base URL |
| `LLM_API_KEY` | API key for the LLM |
| `LLM_MODEL` | Model name to use |
| `GAS_LIMIT_INPUT` | Max input tokens for this run |
| `GAS_LIMIT_OUTPUT` | Max output tokens for this run |
| `REQUESTS_CA_BUNDLE` | Path to custom CA bundle (set automatically when `customCACerts` is configured) |
| `SSL_CERT_FILE` | Same as above — for libraries that use this variable |

---

## Shared models (`shared/models.py`)

Import these rather than defining new ones when working across gateway/worker:

- `JobRecord` — persisted job state (status, gas limits/usage, timestamps)
- `LogEvent` — one structured event in the execution trace (LLM query, tool call, result, error, etc.)
- `SessionRecord`, `SessionMessage` — interactive session persistence
- `ProjectConfig`, `AgentConfig` — merged configuration for a run
- `TaskSpec` — provider-agnostic description of a task to run
- `ActivationRecord`, `WebhookRegistration`

---

## Per-project configuration (`.agents/config.yaml`)

Any repository that Phalanx manages can override agent behaviour by placing `.agents/config.yaml` at its root. The gateway fetches this file at the exact commit SHA that triggered the event. Fields:

```yaml
allowed_users: [alice, bob]       # who can trigger automatic dispatch
skills: [python-testing]          # skill IDs to load
tools: [notify-slack]             # tool IDs to load
gas_limit_input: 120000
gas_limit_output: 30000
prompt_mode: append               # or "replace"
prompt: "Project-specific context..."
dockerfile: Dockerfile            # optional custom worker image
```

---

## Gas system

Token budgets are tracked per-job/session. `agent.py` increments `gas_used_input` and `gas_used_output` after every LLM call. When either limit is reached the agent pauses, serialises state, and returns a `PAUSED` status. The gateway stores the full context; a top-up via the dashboard API resumes the agent from the exact same point.

---

## Testing

```bash
# Unit tests (no cluster required)
pytest tests/

# E2E tests (requires running local cluster)
source .env.test
pytest tests/e2e/
```

Test files are in `tests/`. Most unit tests mock the provider and K8s client. E2E tests use the seeded GitLab instance created by `scripts/cluster-up.sh`.

---

## Local development

```bash
./scripts/cluster-up.sh    # ~5–8 min first run; creates cluster, GitLab, registry
./scripts/load-images.sh   # < 1 min; rebuild images after code changes
./scripts/cluster-down.sh  # teardown
```

After `cluster-up.sh`, the dashboard is at `http://phalanx.localhost:8080` and GitLab is at `http://gitlab.localhost:8080` (root / changeme-local-only).

---

## Common patterns to follow

**Adding a new API endpoint** — add it to `gateway/main.py`. Use the existing SQLite helpers from `gateway/db.py` for persistence.

**Adding a new agent tool** — add an async function to the relevant toolkit in `providers/{name}/toolkit.py` and register it in `worker/tools/toolkit_factory.py`.

**Adding a new provider** — implement all four files in `providers/{name}/` following the ABC contracts in `providers/base.py` and `worker/tools/toolkit_base.py`. Register in both registry files.

**Modifying the dashboard** — all frontend code lives in `dashboard/index.html`. It uses React 18 loaded from CDN with Babel transpilation at runtime. No build step.

**Adding a new Helm value** — add it to `helm/phalanx/values.yaml` with a sensible default, then reference it in the appropriate template in `helm/phalanx/templates/`.

---

## What NOT to do

- Do not import provider-specific SDKs (`python-gitlab`, `PyGithub`, etc.) in `gateway/` or `worker/` code — only in `providers/{name}/`.
- Do not log secrets. The logger (`worker/agent_logger.py`) filters environment variables, but be careful with raw exception messages that might include headers or tokens.
- Do not add state to worker pods. All persistent state lives in the gateway's SQLite database.
- Do not bypass the `RepositoryProvider` ABC — even in tests, mock the ABC not the underlying SDK.
- Do not modify `shared/models.py` Pydantic model field names without updating every serialisation point in both gateway and worker.
