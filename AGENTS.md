# AGENTS.md — Phalanx Codebase Guide for AI Agents

This file is written for AI agents working inside this repository. Read it before making changes. Every claim below links to the exact file and line where you can verify it.

---

## What this project does

Phalanx is an autonomous agent system for Git repositories. It listens to repository events (pushes, merge requests, comments) and spawns AI agents as ephemeral Kubernetes Jobs to review code, implement changes, and post inline feedback. A persistent gateway coordinates everything; a Vue 3 SPA dashboard lets humans watch and intervene in real time.

Full narrative: [`docs/ARCHITECTURE.md:1-50`](docs/ARCHITECTURE.md)

---

## Repository layout

```
phalanx/
├── gateway/            # FastAPI service — the persistent control plane
├── worker/             # Python entrypoint for K8s Job pods
├── providers/          # Git provider abstraction + implementations
├── shared/             # Pydantic models shared between gateway and worker
├── dashboard/          # Vue 3 + Vite SPA (src/ → dist/ via npm run build)
├── global-config/      # Default agent config, global skills and tools
│   ├── agent-config.yml          # Base prompt + lists active global tools and skills
│   ├── tools/                    # Core tool implementations (*.py, each exports get_tool())
│   │   ├── read.py               # Read a local file
│   │   ├── write.py              # Write a local file
│   │   ├── edit.py               # Search-and-replace in a local file
│   │   ├── bash.py               # Run a shell command
│   │   ├── spawn_subagent.py     # POST /trigger to spawn a child job
│   │   └── rag_query.py          # Query a LangIndex-compatible RAG API
│   └── skills/                   # Skill prompt snippets (*.yml, each has name/description/prompt)
│       ├── python-testing.yml
│       ├── security-review.yml
│       ├── conventional-commits.yml
│       └── linting.yml
├── k8s/                # Raw Kubernetes manifests (local dev)
├── helm/               # Helm chart for production deployment
├── kind/               # KIND cluster config for local development
├── scripts/            # Shell scripts for local dev (cluster-up, seed, etc.)
├── tests/              # Pytest test suite (unit + E2E)
└── docs/               # Architecture and usage documentation
```

Detailed layout with annotations: [`docs/ARCHITECTURE.md:91-128`](docs/ARCHITECTURE.md)

---

## Source files — where everything lives

### Gateway (`gateway/`)

| File | Lines | Purpose |
|---|---|---|
| `gateway/main.py` | 1–680 | FastAPI app — all HTTP endpoints, SSE streams, lifespan startup |
| `gateway/db.py` | 1–453 | SQLite persistence via aiosqlite; `Database` class at line 11 |
| `gateway/kube_client.py` | 1–272 | Kubernetes Job spawner; `KubeClient` class at line 11 |
| `gateway/config_loader.py` | 1–233 | Fetches + merges per-project config; `ConfigLoader` class at line 58 |
| `gateway/event_mapper.py` | 1–53 | Maps provider events → `TaskSpec` |
| `gateway/session_broker.py` | 1–66 | In-memory message queues for interactive sessions; `SessionBroker` at line 5 |

Key gateway endpoint groups in `gateway/main.py`:
- Webhook ingestion: lines 99–160
- Job API (`/agents/...`): lines 178–329
- Session API (`/sessions/...`): lines 330–555
- Internal cluster endpoints (`/internal/...`): lines 194–577
- Project proxy endpoints (`/projects/...`): lines 578–680
- Dashboard SPA: line 683 (serves `dashboard/dist/index.html`; `/assets` mounted at line 66)

### Worker (`worker/`)

| File | Lines | Purpose |
|---|---|---|
| `worker/main.py` | 1–21 | K8s Job entrypoint; routes to job or session mode |
| `worker/agent.py` | 1–234 | `AgentEvent` dataclass at line 10; `Agent` class at line 19 |
| `worker/agent_runner.py` | 1–230 | `build_system_prompt` at line 11; `run_agent` at line 52; `run_session` at line 171 |
| `worker/agent_logger.py` | 1–145 | `AgentLogger` class at line 15; streams events to gateway |
| `worker/tools/toolkit_base.py` | 1–17 | `ProviderToolkit` ABC |
| `worker/tools/toolkit_factory.py` | 1–15 | Factory: reads `PROVIDER` env var, returns correct toolkit |
| `worker/tools/global_tools_loader.py` | 1–43 | `load_global_tools()` — dynamically imports `global-config/tools/*.py` and returns their tool dicts |

### Providers (`providers/`)

| File | Lines | Purpose |
|---|---|---|
| `providers/base.py` | 1–175 | Shared event/data models (lines 6–62); `RepositoryProvider` ABC at line 65 |
| `providers/registry.py` | 1–37 | `get_provider()` factory |
| `providers/auth_base.py` | 1–27 | `OAuthProxyConfig`, `UserIdentity`, `AuthProvider` ABC |
| `providers/auth_registry.py` | 1–28 | `get_auth_provider()` factory |
| `providers/gitlab/` | — | Full GitLab implementation (`provider.py`, `webhook.py`, `toolkit.py`, `auth.py`) |
| `providers/github/` | — | GitHub implementation |
| `providers/bitbucket/` | — | Bitbucket implementation |
| `providers/gitea/` | — | Gitea implementation |

### Dashboard (`dashboard/`)

Built with Vue 3 and Vite. Source lives in `dashboard/src/`; the compiled output served by the gateway is `dashboard/dist/`.

| File | Purpose |
|---|---|
| `dashboard/index.html` | Vite HTML entry point |
| `dashboard/vite.config.js` | Vite config; dev-server proxies `/agents`, `/sessions`, `/projects` to gateway port 3000 |
| `dashboard/src/main.js` | Creates and mounts the Vue app |
| `dashboard/src/App.vue` | Root component — nav bar and view switching |
| `dashboard/src/styles/global.css` | CSS variables, resets, and all shared utility classes |
| `dashboard/src/api/index.js` | `apiFetch()` — thin wrapper around `fetch()` |
| `dashboard/src/utils/time.js` | `elapsed()`, `duration()`, `timeAgo()`, `copyText()` |
| `dashboard/src/components/StatusPill.vue` | Coloured status badge |
| `dashboard/src/components/GasMeter.vue` | Token-budget progress bars + top-up form |
| `dashboard/src/components/LogEvent.vue` | Single structured log event (collapsible) |
| `dashboard/src/components/LogPanel.vue` | Scrolling log list; streams via SSE for active jobs, fetches history for finished ones |
| `dashboard/src/components/AgentCard.vue` | Running-agent card with live log preview and cancel button |
| `dashboard/src/views/ActiveAgentsView.vue` | Polls `/agents` every 5 s and renders `AgentCard` list |
| `dashboard/src/views/HistoryView.vue` | Paginated, searchable, filterable history table |
| `dashboard/src/views/HistoryRow.vue` | Single history table row with Logs / Retry actions |
| `dashboard/src/views/SessionLauncher.vue` | New-session configuration form (project search, branch, goal, gas limits) |
| `dashboard/src/views/SessionWorkspace.vue` | Split-pane live session: conversation thread (left) + execution trace (right) |
| `dashboard/src/views/NewSessionView.vue` | Switches between `SessionLauncher` and `SessionWorkspace` |

Build: `cd dashboard && npm install && npm run build` (output → `dashboard/dist/`).
Dev server: `npm run dev` in `dashboard/` — serves on port 5173 with API proxy to the gateway.

### Shared models (`shared/models.py`)

| Class | Line | Purpose |
|---|---|---|
| `ActivationRecord` | 6 | Webhook registration record |
| `TaskSpec` | 15 | Provider-agnostic task description passed to worker |
| `LogEvent` | 21 | One structured event in the execution trace |
| `JobRecord` | 41 | Persisted job state (status, gas limits/usage, timestamps) |
| `SkillDef` / `ToolDef` | 58 / 64 | Skill and tool definitions |
| `ProjectConfig` | 70 | Parsed `.agents/config.yaml` |
| `AgentConfig` | 82 | Fully resolved config produced by config loader |
| `SessionContext` | 93 | User-supplied session parameters |
| `SessionMessage` | 106 | One message in the conversation thread |
| `SessionRecord` | 122 | Persisted interactive session state |

---

## Documentation — what to read for each topic

### Architecture

| Topic | File | Lines |
|---|---|---|
| System overview, diagram, design principles | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 1–154 |
| Shared Pydantic models + project config schema | [`docs/architecture/data-model.md`](docs/architecture/data-model.md) | 1–221 |
| `RepositoryProvider` ABC, event models, registry, toolkit | [`docs/architecture/providers.md`](docs/architecture/providers.md) | 1–255 |
| Gateway internals (config loader → API endpoints) | [`docs/architecture/gateway.md`](docs/architecture/gateway.md) | 1–263 |
| Worker internals (toolkit → agent loop → runner) | [`docs/architecture/worker.md`](docs/architecture/worker.md) | 1–303 |
| Gas / token budget system | [`docs/architecture/gas-system.md`](docs/architecture/gas-system.md) | 1–90 |
| Dashboard (views, session UI, log panel) | [`docs/architecture/dashboard.md`](docs/architecture/dashboard.md) | 1–120 |
| Docker images + K8s manifests + webhook setup | [`docs/architecture/deployment.md`](docs/architecture/deployment.md) | 1–179 |
| End-to-end flow + interactive session flow | [`docs/architecture/flows.md`](docs/architecture/flows.md) | 1–99 |
| `AuthProvider` abstraction + oauth2-proxy config | [`docs/architecture/authentication.md`](docs/architecture/authentication.md) | 1–119 |
| Security considerations table | [`docs/architecture/security.md`](docs/architecture/security.md) | 1–29 |
| How to extend: new providers, tools, event types | [`docs/architecture/extending.md`](docs/architecture/extending.md) | 1–34 |

### Usage and deployment

| Topic | File | Lines |
|---|---|---|
| End-to-end usage walkthrough (local dev → Helm prod) | [`docs/walkthrough.md`](docs/walkthrough.md) | 1–302 |
| Provider comparison table + choosing a provider | [`docs/providers/README.md`](docs/providers/README.md) | 1–26 |
| GitLab setup (token, OAuth app, webhooks, Helm) | [`docs/providers/gitlab.md`](docs/providers/gitlab.md) | 1–170 |
| GitHub setup (PAT, OAuth app, webhooks, Helm) | [`docs/providers/github.md`](docs/providers/github.md) | 1–173 |
| Bitbucket setup (app password, Atlassian OIDC, Helm) | [`docs/providers/bitbucket.md`](docs/providers/bitbucket.md) | 1–172 |
| Gitea setup (API token, webhooks, custom CA, Helm) | [`docs/providers/gitea.md`](docs/providers/gitea.md) | 1–184 |
| GitLab OAuth application setup detail | [`docs/gitlab-oauth-setup.md`](docs/gitlab-oauth-setup.md) | 1–52 |

### Helm chart

| File | Lines | Purpose |
|---|---|---|
| `helm/phalanx/Chart.yaml` | 1–17 | Chart metadata |
| `helm/phalanx/values.yaml` | 1–110 | All configurable values with comments |
| `helm/phalanx/templates/gateway-deployment.yaml` | — | Gateway Deployment + Service |
| `helm/phalanx/templates/secrets.yaml` | — | Provider credential Secrets |
| `helm/phalanx/templates/configmap.yaml` | — | Gateway config + custom CA cert ConfigMap |
| `helm/phalanx/templates/ingress.yaml` | — | Bypass + main Ingress |
| `helm/phalanx/templates/oauth2-proxy.yaml` | — | oauth2-proxy Deployment + Service |
| `helm/phalanx/templates/rbac.yaml` | — | ServiceAccounts, Role, RoleBinding |
| `helm/phalanx/templates/pvc.yaml` | — | Gateway data PersistentVolumeClaim |

---

## Data flow

```
Provider webhook
  → gateway/main.py:99  (_process_webhook)
  → gateway/event_mapper.py:1  (map_event_to_task → TaskSpec)
  → gateway/config_loader.py:58  (ConfigLoader.load → AgentConfig)
  → gateway/kube_client.py:11  (KubeClient.spawn_agent_job)
      → worker/main.py:1  (entrypoint)
      → worker/agent_runner.py:52  (run_agent) or :170 (run_session)
          → worker/agent.py:19  (Agent.run — LLM loop)
              → worker/tools/toolkit_factory.py:1  (get_toolkit)
              → providers/{name}/toolkit.py  (tool execution)
          → worker/agent_logger.py:15  (AgentLogger → POST /internal/log)
              → gateway/main.py:209  (post_log → db + SSE fan-out)
```

---

## Environment variables (workers inherit from K8s Job spec)

| Variable | Purpose |
|---|---|
| `PROVIDER` | Which provider to load (`gitlab`, `github`, `bitbucket`, `gitea`) |
| `GATEWAY_URL` | Internal URL of the gateway for posting log events |
| `JOB_ID` | Identifier of this job (for logging) |
| `SESSION_ID` | If set, run in session mode (`worker/agent_runner.py:171`) |
| `PROJECT_ID` | Provider project ID (numeric for GitLab, `owner/repo` for others) |
| `PROJECT_PATH` | `owner/repo` path used to clone the workspace |
| `WORKSPACE` | Path where the repository was cloned (`/workspace`) |
| `LLM_API_BASE` | OpenAI-compatible API base URL |
| `LLM_API_KEY` | API key for the LLM |
| `LLM_MODEL` | Model name to use |
| `GAS_LIMIT_INPUT` | Max input tokens for this run |
| `GAS_LIMIT_OUTPUT` | Max output tokens for this run |
| `GITLAB_URL` | GitLab instance URL (passed to init container for authenticated clone) |
| `GITLAB_TOKEN` | GitLab API token (from `gitlab-creds` secret) |
| `GITHUB_TOKEN` | GitHub PAT (from `github-creds` secret, optional) |
| `BB_USERNAME` / `BB_APP_PASSWORD` | Bitbucket credentials (from `bitbucket-creds` secret, optional) |
| `GITEA_URL` / `GITEA_TOKEN` | Gitea credentials (from `gitea-creds` secret, optional) |
| `RAG_API_URL` | URL of a LangIndex-compatible RAG API (enables the `rag_query` tool) |
| `REQUESTS_CA_BUNDLE` | Path to custom CA bundle (set by `scripts/docker-entrypoint.sh` when cert is mounted) |
| `SSL_CERT_FILE` | Same — for libraries that read this variable instead |

Full K8s Job env var injection: `gateway/kube_client.py:11`

---

## Per-project configuration (`.agents/config.yaml`)

The gateway fetches this at the exact commit SHA that triggered the event (`gateway/config_loader.py:58`). Full schema: [`docs/architecture/data-model.md:155-221`](docs/architecture/data-model.md).

```yaml
allowed_users: [alice, bob]
skills: [python-testing]
tools: [notify-slack]
gas_limit_input: 120000
gas_limit_output: 30000
prompt_mode: append        # or "override"
prompt: "Project-specific context..."
dockerfile: Dockerfile     # optional custom worker image layer
```

---

## Gas system

Token budgets are tracked per-job/session. `worker/agent.py:19` increments `_gas_used_input` and `_gas_used_output` after every LLM call. When either limit is reached the agent emits `out_of_gas`, suspends on an `asyncio.Event`, and waits for `add_gas()` to be called. The gateway stores full context; a top-up via `POST /agents/{id}/gas` (line 296) or `POST /sessions/{id}/gas` (line 467) resumes the agent.

Full flow: [`docs/architecture/gas-system.md:22-53`](docs/architecture/gas-system.md)

---

## Testing

```bash
# Unit tests (no cluster required)
pytest tests/

# E2E tests (requires running local cluster)
source .env.test
pytest tests/e2e/
```

Unit tests mock `RepositoryProvider` and `KubeClient`. E2E tests use the seeded GitLab instance from `scripts/cluster-up.sh`.

---

## Local development

```bash
./scripts/cluster-up.sh    # ~5–8 min first run
./scripts/load-images.sh   # rebuild images after code changes
./scripts/cluster-down.sh  # teardown
```

Full walkthrough: [`docs/walkthrough.md:22-90`](docs/walkthrough.md)

---

## Common patterns

**Adding a new API endpoint** — `gateway/main.py` (add route); `gateway/db.py:11` (Database class for persistence).

**Adding a provider-specific agent tool** — `providers/{name}/toolkit.py` (add method + register in `get_tools()`); `worker/tools/toolkit_factory.py:1` (factory stays unchanged unless adding a new provider).

**Adding a global agent tool** — create `global-config/tools/<name>.py` exposing `get_tool() -> dict` (with `name`, `description`, `parameters`, `execute` keys). Register the tool name in `global-config/agent-config.yml` under `tools:`. The loader at `worker/tools/global_tools_loader.py:1` picks it up automatically at runtime.

**Workspace** — every K8s Job gets an `emptyDir` volume mounted at `/workspace`. The `git-clone` init container (`alpine/git`) checks out the relevant branch before the worker starts: `source_branch` for MR reviews and comments, `branch` for push events. The `WORKSPACE=/workspace` env var is set so tools know where to operate. The `read`, `write`, `edit`, and `bash` global tools all work against the local filesystem where the repo is cloned.

**Adding a global skill** — create `global-config/skills/<name>.yml` with `name`, `description`, and `prompt` fields. Add the skill name to `global-config/agent-config.yml` under `skills:` (or to a project's `.agents/config.yaml`). `gateway/config_loader.py` injects the prompt into the agent's system prompt at job spawn time.

**Adding a new provider** — implement `providers/{name}/provider.py`, `webhook.py`, `toolkit.py`, `auth.py` following `providers/base.py:65` (`RepositoryProvider` ABC) and `providers/auth_base.py:1` (`AuthProvider` ABC). Register in `providers/registry.py:1` and `providers/auth_registry.py:1`. See [`docs/architecture/extending.md`](docs/architecture/extending.md) and [`docs/architecture/providers.md`](docs/architecture/providers.md).

**Adding a new Helm value** — `helm/phalanx/values.yaml` (add with default); reference in relevant template under `helm/phalanx/templates/`.

**Modifying the dashboard** — edit Vue SFCs under `dashboard/src/`. Run `npm run build` in `dashboard/` after changes to update `dashboard/dist/` (served by the gateway). Use `npm run dev` for live-reload development on port 5173 (API calls are proxied to the gateway at port 3000). See [`docs/architecture/dashboard.md`](docs/architecture/dashboard.md) for the full component tree.

**Modifying shared models** — `shared/models.py`; check every serialisation point in both `gateway/` and `worker/` afterwards.

---

## Keeping documentation up to date

When you make changes to this codebase, update the documentation as part of the same commit. Do not leave docs describing the old behaviour.

**This file (AGENTS.md)**
- If you add, move, or delete a source file, update the source files table and the repository layout.
- If a class or function moves to a different line, update the `file:line` reference.
- If you add a new pattern, abstraction, or hard rule, add it to the relevant section.

**`docs/ARCHITECTURE.md` and `docs/architecture/`**
- If you change how a component works, update its sub-document (`gateway.md`, `worker.md`, `providers.md`, etc.).
- If you add a new component or remove one, update the overview table in `docs/ARCHITECTURE.md` and the project structure diagram.
- If you change the API surface (`gateway/main.py` endpoints), update `docs/architecture/gateway.md`.
- If you change the data model (`shared/models.py`), update `docs/architecture/data-model.md`.
- If you change how authentication works, update `docs/architecture/authentication.md`.
- If you add or change a security property, update `docs/architecture/security.md`.

**`docs/providers/`**
- If you change credential requirements, OAuth scopes, webhook events, or signature behaviour for a provider, update the relevant file in `docs/providers/`.

**`docs/walkthrough.md`**
- If you change the local dev setup, Helm install steps, or any user-facing workflow, update the walkthrough.

**`helm/phalanx/values.yaml`**
- Every new configurable value must have a comment explaining what it does. The Helm section of `docs/walkthrough.md` should be updated if the install command changes.

**General rules**
- Line references in this file must stay accurate. If a refactor shifts a function to a different line, fix the reference.
- Do not add a doc section for something that does not yet exist in code.
- Do not leave `TODO` or `planned` language in documentation for features that are already implemented.

---

## What NOT to do

- Do not import provider SDKs (`python-gitlab`, `PyGithub`, etc.) outside `providers/{name}/` — the gateway and worker must stay provider-agnostic.
- Do not bypass the `RepositoryProvider` ABC (`providers/base.py:65`) — mock the ABC in tests, not the underlying SDK.
- Do not add state to worker pods — all persistent state lives in `gateway/db.py:11`.
- Do not log secrets — `worker/agent_logger.py:15` filters env vars but raw exception messages can leak headers or tokens.
- Do not modify `shared/models.py` field names without updating every serialisation point in both `gateway/` and `worker/`.
- Do not push to a branch other than `claude/docs-helm-certs-0Xoy1` without explicit permission.
