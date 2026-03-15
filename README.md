# Phalanx

Phalanx is an autonomous agent system for Git repositories. It listens to repository events — pushes, merge requests, comments — and dispatches fleets of AI agents to review code, implement changes, open merge requests, and post inline feedback. Agents run as ephemeral Kubernetes Jobs, coordinate through a persistent gateway, and can be monitored, steered, and interacted with in real time from a browser dashboard.

The name comes from the ancient Greek battle formation: many units acting in tight coordination, each knowing its role, the whole more capable than any individual part.

---

## What it does

**Reacts to your repository automatically.** When a merge request is opened, Phalanx reviews it. When a comment asks for a change, Phalanx implements it. When a push lands on a branch, Phalanx checks for issues. No pipeline configuration required — just a webhook.

**Runs agents as isolated Kubernetes Jobs.** Every task gets its own pod. Agents are fully ephemeral: they do their work, post their results, and disappear. All state — job records, execution traces, token usage — lives in the gateway, not the pods.

**Streams everything in real time.** The dashboard shows every LLM call, every tool invocation, every result, as it happens. You can watch an agent think.

**Lets you intervene mid-run.** Through interactive sessions, you can launch an agent against any project you have access to, give it a goal, and then redirect it, answer its questions, or cancel it — all from the browser while it's running.

**Respects your token budget.** Every job and session has a gas limit measured in LLM tokens. When the budget runs out, the agent pauses and preserves all context. You review the trace, add more tokens, and it resumes exactly where it stopped.

**Stays provider-agnostic.** The initial implementation targets GitLab. GitHub, Bitbucket, and others can be added without touching the gateway, agent loop, or dashboard — only a new provider implementation is needed.

---

## Architecture

Phalanx has three layers:

```
                                          Browser
                                             │
                              dashboard /    │    / webhook (no auth)
                                             ▼
Provider OAuth2 ◀─ authn ──▶  oauth2-proxy (K8s Deployment)
                                             │
                               sets X-Forwarded-User header
                                             ▼
Provider ──webhook/API──▶  Gateway (persistent K8s Deployment)
                               │       │         │
                    ┌──────────┘       │         └──────────────┐
                    ▼                  ▼                         ▼
              K8s Job Spawner      SQLite DB              Dashboard API
                    │             (jobs + logs)           (REST + SSE)
                    │                  ▲                         ▲
                    ▼                  │                         │
              Worker Pod ──log events──▶ POST /internal/log      │
             (Agent +                                            │
           AgentLogger +                                Browser Dashboard
          ProviderToolkit)
                    │
                    ▼
         OpenAI-compatible API + Provider API
```

**Gateway** — a persistent FastAPI service. Receives webhooks, spawns worker Jobs, persists all state, serves the dashboard, and brokers messages between users and running sessions.

**Workers** — short-lived Kubernetes Jobs, one per task or session. Each runs an `Agent` loop wired to a provider toolkit and a structured logger that streams events back to the gateway as they occur.

**Dashboard** — a React SPA served by the gateway. Shows active agents, job history, full execution traces, and the interactive session interface.

---

## Key design decisions

**Gateway owns all state.** Job records and log events are persisted in SQLite (swappable for Postgres). The dashboard is completely independent of running pods — a pod can die and the trace is still there.

**Provider abstraction is the integration boundary.** All repository interaction goes through a `RepositoryProvider` abstract base class. The gateway, agent runner, and config loader never import a provider directly. Adding GitHub means writing `providers/github/` — nothing else changes.

**Per-project configuration from the repo itself.** Projects can place a `.agents/config.yaml` in their repository to extend the global agent configuration with custom skills, tools, system prompt additions, and even a custom worker image. The gateway fetches this file at the commit SHA that triggered the event — configuration always matches the code being acted on.

**Webhook dispatch is deny-by-default.** Projects must explicitly list the usernames permitted to trigger automatic agent dispatch in `allowed_users`. If no config file exists or the list is empty, no agents are spawned in response to webhook events — only users with access to the dashboard can launch sessions manually.

**Custom images are layered, not replaced.** Project Dockerfiles must use the global worker image as their `FROM` base. Derived images are built by a Kaniko sidecar, tagged by project ID and Dockerfile content hash, and cached. Projects can add dependencies; they cannot remove the baseline capabilities.

**Interrupts are safe.** User messages to running sessions are delivered at iteration boundaries — never mid-tool-execution. An agent will always finish its current tool call before acting on a redirect or answering a question, preventing partial writes or inconsistent repository state.

**Secrets never leave Kubernetes.** Provider tokens and LLM API keys are injected via K8s Secrets. They are not logged, not returned by the API, and not visible in the dashboard.

---

## Repository layout

```
phalanx/
├── gateway/
│   ├── main.py              # FastAPI: webhooks, job API, dashboard API, SSE, session endpoints
│   ├── kube_client.py       # Kubernetes Job spawner
│   ├── event_mapper.py      # Provider event → TaskSpec
│   ├── db.py                # SQLite persistence
│   ├── config_loader.py     # Fetches and merges per-project config
│   └── session_broker.py    # In-memory message queues for interactive sessions
├── providers/
│   ├── base.py              # RepositoryProvider ABC + shared data models
│   ├── auth_base.py         # AuthProvider ABC
│   ├── registry.py          # Provider factory
│   ├── auth_registry.py     # Auth provider factory
│   ├── gitlab/              # GitLab implementation
│   └── github/              # GitHub (placeholder, Phase 8)
├── worker/
│   ├── main.py              # K8s Job entrypoint
│   ├── agent.py             # Agent loop: LLM calls, tool dispatch, gas tracking
│   ├── agent_runner.py      # Wires agent to provider toolkit; job and session modes
│   ├── agent_logger.py      # Streams structured log events to the gateway
│   └── tools/
│       ├── toolkit_base.py  # ProviderToolkit ABC
│       └── toolkit_factory.py
├── dashboard/
│   └── index.html           # React SPA (single file)
├── shared/
│   └── models.py            # Pydantic models shared between gateway and worker
├── global-config/
│   ├── agent-config.yml     # Global base prompt, default skills and tools
│   ├── skills/
│   └── tools/
├── k8s/
│   ├── namespace.yaml
│   ├── gateway-deployment.yaml
│   ├── rbac.yaml
│   ├── secrets.yaml
│   ├── ingress.yaml
│   └── gitlab/              # GitLab CE deployment (local development)
├── kind/
│   ├── cluster-config.yaml
│   └── registry-configmap.yaml
├── scripts/
│   ├── cluster-up.sh        # Stand up the full local environment
│   ├── cluster-down.sh
│   ├── load-images.sh       # Rebuild and redeploy without recreating the cluster
│   ├── reseed-gitlab.sh
│   └── seed-gitlab.sh       # Create test project, token, and webhook in GitLab
├── Dockerfile.gateway
├── Dockerfile.worker
└── requirements.txt
```

---

## Local development

The full development environment — Kubernetes cluster, GitLab CE, image registry, and the gateway itself — runs locally inside [KIND](https://kind.sigs.k8s.io/) (Kubernetes IN Docker). No cloud account, no external services, no tunnels.

### Prerequisites

| Tool | Minimum version |
|---|---|
| Docker | 24+ |
| kind | 0.23+ |
| kubectl | 1.28+ |
| helm | 3.14+ |

Docker must have at least **8 GB RAM** allocated. GitLab CE is the hungry one.

### Start everything

```bash
./scripts/cluster-up.sh
```

This takes 5–8 minutes on first run (mostly waiting for GitLab). It:

1. Starts a local Docker registry at `localhost:5001`
2. Creates a 3-node KIND cluster with host port mappings
3. Installs the nginx ingress controller
4. Deploys GitLab CE via Helm
5. Builds and pushes the gateway and worker images
6. Applies all Kubernetes manifests
7. Seeds GitLab with a test group, project, service token, and webhook

When it finishes:

| Service | URL |
|---|---|
| GitLab | `http://gitlab.localhost:8080` (root / changeme-local-only) |
| Gateway | `http://phalanx.localhost:8080` |

Because GitLab runs inside the same cluster as the gateway, webhooks are delivered over in-cluster DNS — no tunnel needed.

### Rebuild after code changes

```bash
./scripts/load-images.sh
```

Rebuilds both images, pushes to the local registry, and does a rolling restart of the gateway deployment. Takes under a minute.

### Tear down

```bash
./scripts/cluster-down.sh
```

---

## Per-project configuration

Any repository can place a `.agents/` directory at its root to customise how Phalanx behaves when acting on that project.

```
your-repo/
└── .agents/
    ├── config.yaml      # Skills, tools, prompt, gas limit, custom image
    ├── Dockerfile       # Optional: extends the global worker image
    ├── skills/          # Optional: inline skill definitions
    └── tools/           # Optional: inline tool definitions
```

**`config.yaml` example:**

```yaml
# Who can trigger automatic agent dispatch via push, MR, or comment webhooks.
# Empty or absent = no automatic dispatch for any user (deny-by-default).
# Manual sessions launched from the dashboard are controlled by dashboard auth.
allowed_users:
  - alice
  - bob
  - ci-bot

skills:
  - python-testing
  - name: custom-linter
    description: "Run the project ESLint config and report violations"
    inline: true

tools:
  - notify-slack

gas_limit_input: 120000
gas_limit_output: 30000

prompt_mode: append
prompt: |
  This repository uses Python 3.12 and follows the Google style guide.
  Never suggest changes to files under legacy/.

dockerfile: Dockerfile
```

The gateway fetches this file at the exact commit SHA that triggered the agent run — configuration always reflects the code being acted on. Project config extends global defaults; it cannot remove globally registered tools.

If no `.agents/config.yaml` is present (or if `allowed_users` is empty), no automatic dispatch will occur for that project.

---

## Gas system

Phalanx tracks LLM token consumption per job and session. Input tokens and output tokens are budgeted separately — every run has a `gas_limit_input` and a `gas_limit_output`, with corresponding `gas_used_input` and `gas_used_output` counters that increment after each LLM call.

When either counter reaches its limit, the agent pauses. All context is preserved. The dashboard shows the full execution trace up to the pause point, displays both gas meters, and offers top-up inputs for whichever limit needs refilling. Once you add gas, the agent resumes from exactly where it stopped — no re-execution.

Default limits are configured at the gateway level and can be overridden per project in `.agents/config.yaml` or per session in the session launcher.

| Level | Input default | Output default |
|---|---|---|
| Jobs (system default) | 80,000 tokens | 20,000 tokens |
| Sessions (system default) | 160,000 tokens | 40,000 tokens |
| Project override | Set `gas_limit_input` / `gas_limit_output` in `.agents/config.yaml` | ← |
| Session override | Set in the session launcher form | ← |

Input and output tokens are budgeted separately because they have different per-token costs on most LLM providers. The agent pauses when either limit is reached.

---

## Interactive sessions

Beyond reacting to repository events, Phalanx supports ad hoc interactive sessions launched directly from the dashboard.

Pick a project, a branch, an optional target MR, and give the agent a goal. The session workspace gives you a split view: a conversation thread on the left and the live execution trace on the right.

While the agent is running you can redirect it mid-task. If the agent needs clarification, it pauses and asks — the input box changes to indicate it's waiting for your answer. When the session ends, the full trace is preserved in history.

Sessions run on the same worker infrastructure as webhook-triggered jobs. The difference is purely behavioural: session workers hold a long-lived connection to the gateway's message broker and can suspend their loop at iteration boundaries to wait for user input.

---

## Authentication

The dashboard and API are protected by [oauth2-proxy](https://github.com/oauth2-proxy/oauth2-proxy) in front of the gateway. GitLab OAuth2 is the default identity provider — any user who is a member of the configured GitLab group can log in.

Webhook and internal cluster endpoints bypass the proxy and require no authentication.

---

## Adding a new provider

Adding GitHub, Bitbucket, or any other Git hosting platform requires only a new `providers/{name}/` directory:

```
providers/github/
├── provider.py    # Implement RepositoryProvider ABC
├── webhook.py     # Implement verify_webhook + parse_webhook_event
├── toolkit.py     # Implement ProviderToolkit
└── auth.py        # Implement AuthProvider
```

Register the new cases in `providers/registry.py` and `providers/auth_registry.py`, add credentials to the K8s Secret, and set `PROVIDER=github`. Nothing else in the gateway, worker, or dashboard needs to change.

---

## Technology

| Component | Technology |
|---|---|
| Gateway | Python, FastAPI, aiosqlite, sse-starlette |
| Worker / Agent loop | Python, asyncio, OpenAI-compatible API client |
| Dashboard | React (single-file SPA) |
| Orchestration | Kubernetes, ephemeral Jobs |
| Auth | oauth2-proxy, GitLab OAuth2 |
| Custom image builds | Kaniko |
| Local development | KIND, Helm, nginx ingress |

---

## Implementation status

| Phase | Description | Status |
|---|---|---|
| 0 | Provider abstraction layer | Planned |
| 1 | Infrastructure foundation (KIND + GitLab) | Planned |
| 2 | Core agent worker | Planned |
| 3 | Structured logging and observability | Planned |
| 4 | Per-project configuration | Planned |
| 5 | Authentication | Planned |
| 6 | Control plane dashboard | Planned |
| 7 | Interactive sessions | Planned |
| 8 | Additional providers (GitHub, etc.) | Deferred |
