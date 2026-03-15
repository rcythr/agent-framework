# Task Dependency Map and Parallelisation Guide

## Dependency Graph

```
Phase 0 (Provider Abstraction)
    │
    ├──────────────────────────────────────────┐
    │                                          │
Phase 1 (Infrastructure)              [Phase 2 and 3 can begin
    │                                  once Phase 0 is done,
    ├──────────┬───────────┐            but need Phase 1 before
    │          │           │            integration tests pass]
Phase 2    Phase 3      Phase 4      Phase 5
(Worker)  (Logging)    (Config)     (Auth)
    │          │           │           │
    └──────────┴───────────┴───────────┘
                           │
                       Phase 6 (Dashboard)
                           │
                       Phase 7a (Session Data Layer)
                           │
                       Phase 7b (Worker Session Mode)
                           │
                       Phase 7c (Session Messaging)
                           │
               ┌───────────┴───────────┐
           Phase 7d               Phase 7e
        (Launcher UI)          (Workspace UI)
               └───────────┬───────────┘
                        Phase 8
                   (Additional Providers)
                      [Deferred]
```

---

## Parallelisation Opportunities

### Wave 1 — Start immediately
| Task | Can start | Notes |
|---|---|---|
| **Phase 0** | Immediately | No dependencies. Must complete before anything else can merge. |

---

### Wave 2 — After Phase 0 merges
All four of these can be worked simultaneously by separate engineers.

| Task | Blocked by | Parallel with |
|---|---|---|
| **Phase 1** (Infrastructure) | Phase 0 | 2, 3, 4, 5 |
| **Phase 2** (Agent Worker) | Phase 0 | 1, 3, 4, 5 |
| **Phase 3** (Structured Logging) | Phase 0 | 1, 2, 4, 5 |
| **Phase 4** (Project Config) | Phase 0 | 2, 3, 5 |
| **Phase 5** (Authentication) | Phase 0 | 2, 3, 4 |

**Important caveat:** Phases 2, 3, 4, and 5 all depend on Phase 1 being deployed before their **integration and E2E tests** can run against a live system. However, unit test development and implementation can proceed in parallel with Phase 1. Plan to merge Phase 1 first, then unblock integration test runs for the other wave-2 phases.

**Phase 2 and Phase 3 coordination:** The `Agent.event_handler` callback is Phase 2's contract; the `AgentLogger` consumer is Phase 3's deliverable. Agree on the `AgentEvent` dataclass shape at the start of Wave 2 so both teams can work independently. No merge-order dependency between them — both just need Phase 1 deployed before integration tests.

---

### Wave 3 — After Phases 1, 2, 3, and 5 merge
| Task | Blocked by | Notes |
|---|---|---|
| **Phase 6** (Dashboard) | 1, 2, 3, 5 | Phase 4 is NOT required for Phase 6 |

Phase 4 (Project Config) can be merged any time after Phase 1 — it does not need to wait for Phase 6.

---

### Wave 4 — After Phase 6 merges (and all of 0–5)
| Task | Notes |
|---|---|
| **Phase 7a** (Session Data Layer) | Must be first within Phase 7 |
| → **Phase 7b** (Worker Session Mode) | After 7a |
| → **Phase 7c** (Session Messaging) | After 7b |
| → **Phase 7d** (Launcher UI) | After 7c — parallel with 7e |
| → **Phase 7e** (Workspace UI) | After 7c — parallel with 7d |

---

### Wave 5 — After Phase 7 is complete
| Task | Notes |
|---|---|
| **Phase 8** (Additional Providers) | Deferred. Multiple providers can be added in parallel. |

---

## Local Development Environment

All development, integration testing, and E2E testing runs against a fully self-contained **KIND (Kubernetes IN Docker)** cluster. No cloud account, external tunnels, or external GitLab instance is required.

The full local environment is brought up with a single command:
```bash
./scripts/cluster-up.sh
```

This creates the KIND cluster, starts a local Docker registry at `localhost:5001`, installs the nginx ingress controller, deploys **GitLab CE** inside the cluster, builds and pushes both Docker images, applies all K8s manifests, and seeds GitLab with a test project and webhook. After it completes:

- GitLab UI: `http://gitlab.localhost:8080`
- Gateway: `http://pi-agent.localhost:8080`
- Test credentials (token, project ID, etc.) written to `.env.test`

Because GitLab runs inside the cluster, it delivers webhooks to the gateway via in-cluster DNS — no tunnel needed. The first run takes 5–8 minutes (mostly waiting for GitLab CE to start). Subsequent `scripts/load-images.sh` runs to rebuild and redeploy the gateway/worker take under a minute.

**Resource requirement:** GitLab CE needs at least 8 GB RAM available to Docker. Adjust Docker Desktop memory limits before running `cluster-up.sh`.

Engineers working on phases that don't require a live cluster (Phase 0 pure Python work, unit tests) can skip this setup entirely.

---

## Recommended Engineer Assignment

For a team of 3–4 engineers working simultaneously:

**Engineer A** — Phase 0 (solo, unblocks everyone)

Once Phase 0 is merged:

**Engineer A** → Phase 1 (Infrastructure — critical path)

**Engineer B** → Phase 2 (Agent Worker) — unit tests first; integration tests once Phase 1 deploys

**Engineer C** → Phase 3 (Structured Logging) — unit tests first; coordinate `AgentEvent` shape with Engineer B

**Engineer D** → Phase 5 (Authentication) — can write K8s manifests and unit tests immediately; integration tests once Phase 1 deploys. Also pick up Phase 4 if bandwidth allows.

Once Phases 1–3 and 5 merge:

**Engineers B + C** → Phase 6 (Dashboard) together, or one takes Phase 4 if not yet done

Once Phase 6 merges:

**All engineers** → Phase 7 sub-tasks in order: 7a → 7b → 7c → 7d/7e in parallel

---

## Critical Path

The minimum-duration sequence through the project:

```
Phase 0 → Phase 1 → Phase 2/3 (parallel) → Phase 5 → Phase 6 → Phase 7a → 7b → 7c → 7d/7e
```

Phase 4 is not on the critical path — it can be slotted in any time between Phase 1 and Phase 7 without affecting overall delivery schedule.

---

## Phase File Index

| File | Phase |
|---|---|
| `phase0-provider-abstraction.md` | Phase 0 — Provider Abstraction Layer |
| `phase1-infrastructure.md` | Phase 1 — Infrastructure Foundation |
| `phase2-agent-worker.md` | Phase 2 — Core Agent Worker |
| `phase3-structured-logging.md` | Phase 3 — Structured Logging and Observability |
| `phase4-project-configuration.md` | Phase 4 — Per-Project Configuration |
| `phase5-authentication.md` | Phase 5 — Authentication |
| `phase6-dashboard.md` | Phase 6 — Control Plane Dashboard |
| `phase7-iteractive-sessions.md` | Phase 7 — Interactive Sessions (sub-tasks 7a–7e) |
| `phase8-additional-auth.md` | Phase 8 — Additional Providers (Deferred) |

---

## Merge Order Constraints (hard rules)

1. **Phase 0 must merge before any other phase.** All phases import from `providers/base.py` and `shared/models.py`.
2. **Phase 1 must merge before Phase 4, 6.** Config loader and dashboard depend on gateway being deployed.
3. **Phase 5 must merge before Phase 6.** Dashboard must be behind auth.
4. **Within Phase 7: 7a → 7b → 7c → (7d ∥ 7e).** Each sub-task builds on the previous.
5. **Phase 6 must be complete before Phase 7.** The session workspace extends the dashboard.

All other ordering is flexible and can be parallelised.
