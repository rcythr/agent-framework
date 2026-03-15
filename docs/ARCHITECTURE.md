# Phalanx Architecture

## Overview

Phalanx is an **autonomous agent system** that integrates with **Git repository hosting platforms**, deployed on **Kubernetes**. It is designed around a provider abstraction layer so that support for GitLab, GitHub, Bitbucket, Gitea, and others can be added without changes to the core agent loop, gateway, or dashboard. The agent loop is implemented in Python as the `Agent` class — an LLM call loop with tool dispatch, event emission, and a two-queue message model for steering and follow-ups. The system autonomously reacts to repository events, performs code review, implements changes, and can be triggered manually via the dashboard or API.

---

## Goals

- React to repository events (push, merge/pull requests, comments) in real time, via a provider-agnostic webhook ingestion layer
- Act as an autonomous coding and review agent on repositories hosted on any supported provider
- Support manual triggering from the dashboard UI and provider CI pipelines
- Read and write code, open merge requests, and commit changes
- Post comments and inline review notes on merge requests and issues
- Report pipeline status back to the provider
- Provide a human-facing control plane dashboard to monitor active agents, view history, and inspect full execution traces
- Stream structured agent logs (LLM queries, tool calls, tool outputs) in real time to the dashboard
- Allow individual projects to configure their agent environment — skills, tools, system prompt, and runtime image — via a configurable project directory (defaulting to `.agents/`), extending global defaults rather than replacing them
- Provide an interactive **Agent Session** interface in the dashboard where users can launch an ad hoc agent against any project, converse with it in real time, steer it mid-run, and have the agent ask clarifying questions — without any local setup

---

## System architecture

The system is composed of three distinct layers:

1. **Gateway Service** — a persistent, always-on FastAPI server that receives provider webhooks and manual trigger requests, spawns ephemeral worker jobs, persists job and log state, serves the dashboard API, and brokers bidirectional messaging between users and interactive agent sessions
2. **Worker Jobs** — short-lived Kubernetes Jobs, one per agent task or session, each running an `Agent` instance with a provider-supplied tool suite and a structured logger that streams events back to the gateway
3. **Control Plane Dashboard** — a browser-based React UI served by the gateway, providing real-time agent monitoring, log streaming, history browsing, agent management actions, and an interactive Agent Session interface for ad hoc work

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

### Design Principles

- **Gateway owns all state** — job records and log events are persisted in the gateway's SQLite database (swappable for Postgres in production), making the dashboard independent of running pods
- **Workers stream logs in real time** — the `AgentLogger` wraps the `Agent` loop and POSTs structured log events to the gateway as they occur, so the dashboard reflects live progress
- **Log events are typed and structured** — every event has an explicit type (`llm_query`, `llm_response`, `tool_call`, `tool_result`, `complete`, `error`) enabling the dashboard to render each differently rather than as raw text
- **Dashboard uses SSE for live updates** — the gateway exposes a `/agents/{id}/logs/stream` Server-Sent Events endpoint; the dashboard subscribes per agent and appends events as they arrive
- **Workers are fully ephemeral** — isolated per task, auto-cleaned via `ttlSecondsAfterFinished`; all observable state lives in the gateway DB, not the pod
- **Provider abstraction is the integration boundary** — all repository provider API interaction is encapsulated behind a `RepositoryProvider` abstract base class; the agent runtime, gateway, and config loader program against this interface exclusively and are unaware of which provider is in use
- **Secrets never leave Kubernetes** — provider tokens and LLM API keys are injected via K8s Secrets, not environment files or CI variables
- **Project config is fetched at spawn time** — the gateway reads the project config directory (default `.agents/`) from the project repo via the provider API immediately before creating the K8s Job, so config changes take effect on the next agent run with no redeployment
- **Global defaults are always present** — project config extends the global skill and tool set; it cannot remove globally registered tools, ensuring baseline capabilities are always available
- **Custom images are layered, not replaced** — project Dockerfiles use the global worker image as their `FROM` base; derived images are built by a Kaniko sidecar at spawn time, tagged by project ID and Dockerfile commit SHA, and cached in the registry
- **Sessions and jobs share the same worker** — interactive sessions are K8s Jobs running the same worker image as webhook-triggered jobs; the difference is behavioural: session workers hold a long-lived connection to the gateway's message broker and can suspend their loop waiting for user input
- **User messages are queued, not pushed** — the gateway holds an in-memory message queue per session; the agent polls it between loop iterations, ensuring interrupts and clarifications are handled safely at iteration boundaries rather than mid-tool-execution
- **Sessions are scoped to the authenticated user** — each session is owned by the `X-Forwarded-User` identity from oauth2-proxy; users can only view and interact with their own sessions

---

## Project structure

```
phalanx/
├── gateway/
│   ├── main.py              # FastAPI server: webhooks, triggers, dashboard API, SSE, session endpoints
│   ├── kube_client.py       # K8s Job spawner
│   ├── event_mapper.py      # Provider event → TaskSpec
│   ├── db.py                # SQLite persistence (jobs, log events, sessions, messages)
│   ├── config_loader.py     # Fetches + merges project config dir with global defaults
│   └── session_broker.py    # In-memory message queues + session state for interactive sessions
├── providers/
│   ├── base.py              # RepositoryProvider ABC + shared data models (MR, Commit, etc.)
│   ├── auth_base.py         # AuthProvider ABC, OAuthProxyConfig, UserIdentity
│   ├── registry.py          # get_provider() factory
│   ├── auth_registry.py     # get_auth_provider() factory
│   ├── gitlab/              # GitLab implementation
│   ├── github/              # GitHub implementation
│   ├── bitbucket/           # Bitbucket implementation
│   └── gitea/               # Gitea implementation
├── worker/
│   ├── main.py              # Entry point for K8s Job pods
│   ├── agent.py             # Agent class: LLM loop, tool dispatch, event emission, message queues
│   ├── agent_runner.py      # Agent initialisation, tool wiring, session mode branching
│   ├── agent_logger.py      # Structured logger: wraps Agent, streams events to gateway
│   └── tools/
│       ├── toolkit_base.py  # ProviderToolkit ABC — defines the tool contract
│       └── toolkit_factory.py
├── dashboard/
│   └── index.html           # React SPA served by gateway
├── shared/
│   └── models.py            # Shared Pydantic models (TaskSpec, LogEvent, JobRecord, AgentConfig, Session*)
├── helm/
│   └── phalanx/             # Helm chart for production deployment
├── k8s/                     # Raw Kubernetes manifests (local development)
├── global-config/
│   ├── skills/
│   ├── tools/
│   └── agent-config.yml     # Global defaults: base prompt, default skills/tools
├── Dockerfile.gateway
└── Dockerfile.worker
```

---

## Dependencies

```
fastapi>=0.111.0
uvicorn>=0.29.0
kubernetes>=29.0.0
python-gitlab>=4.6.0    # GitLab provider
PyGithub>=2.3.0         # GitHub provider
httpx>=0.27.0           # Bitbucket + Gitea providers, internal HTTP
pydantic>=2.7.0
pydantic-settings>=2.2.0
openai>=1.30.0          # OpenAI-compatible API client used by the Agent loop
aiosqlite>=0.20.0       # async SQLite for gateway persistence
sse-starlette>=2.1.0    # Server-Sent Events for live log streaming
pyyaml>=6.0.1           # Parsing project config YAML files
```

---

## Detailed documentation

| Document | Contents |
|---|---|
| [Data model](architecture/data-model.md) | Shared Pydantic models, per-project configuration schema |
| [Providers](architecture/providers.md) | `RepositoryProvider` ABC, data models, registry, toolkit abstraction |
| [Gateway](architecture/gateway.md) | Config loader, event mapper, persistence, job spawner, session broker, API endpoints |
| [Worker](architecture/worker.md) | Provider toolkit, agent logger, agent loop, agent runner, entry point |
| [Gas system](architecture/gas-system.md) | Token budgeting, limits, top-up flow, dashboard integration |
| [Dashboard](architecture/dashboard.md) | Active agents view, history, session interface, log panel |
| [Deployment](architecture/deployment.md) | Docker images, Kubernetes manifests, provider webhook setup |
| [Flows](architecture/flows.md) | End-to-end webhook flow, interactive session flow |
| [Authentication](architecture/authentication.md) | `AuthProvider` abstraction, oauth2-proxy configuration, IdP override |
| [Security](architecture/security.md) | Security considerations and mitigations |
| [Extending](architecture/extending.md) | How to add new provider actions, event types, and providers |
