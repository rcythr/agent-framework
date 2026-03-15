# Phase 7 — Interactive Sessions

## Goal
Users can launch an ad hoc agent session against any GitLab project they have access to, converse with it in real time, steer it mid-run, and have the agent ask clarifying questions — with no local setup required.

## Prerequisites
- **All prior phases complete** (Phase 0–6)

This phase has the most internal dependencies and is broken into five sub-tasks (7a–7e) with a defined implementation order. Sub-tasks 7d and 7e can be parallelised once 7a–7c are merged.

---

## Sub-task 7a — Session Data Layer and Broker

### Deliverables

#### `shared/models.py` — add session models
```python
class SessionContext(BaseModel):
    project_id: int
    project_path: str
    branch: str
    goal: str
    mr_iid: int | None = None
    skill_overrides: list[str] = []
    tool_overrides: list[str] = []
    gas_limit: int = 100_000

class SessionMessage(BaseModel):
    session_id: str
    sequence: int
    timestamp: datetime
    role: Literal["user", "agent"]
    content: str
    message_type: Literal[
        "instruction", "interrupt", "agent_response",
        "input_request", "input_response"
    ]

class SessionRecord(BaseModel):
    id: str
    owner: str
    project_id: int
    project_path: str
    branch: str
    mr_iid: int | None
    status: Literal[
        "configuring", "running", "waiting_for_user",
        "out_of_gas", "complete", "failed", "cancelled"
    ]
    context: SessionContext
    created_at: datetime
    finished_at: datetime | None = None
    gas_limit: int = 100_000
    gas_used: int = 0
    gas_topups: list[int] = []
```

Also add `input_request`, `input_received`, `interrupted` to `LogEvent.event_type` Literal in `shared/models.py` if not already present.

#### `gateway/db.py` — add session tables
Add two new tables:
- `sessions` — one row per session; mirrors `SessionRecord` fields
- `session_messages` — one row per `SessionMessage`

New methods:
- `create_session(session: SessionRecord)`
- `update_session_status(session_id, status, finished_at?)`
- `get_session(session_id) → SessionRecord`
- `list_sessions(owner, status?, limit?, offset?) → list[SessionRecord]`
- `append_session_message(message: SessionMessage)`
- `get_session_messages(session_id) → list[SessionMessage]`

#### `gateway/session_broker.py`
In-memory message queue and state management for active sessions. State is not persisted here — the DB is the source of truth; the broker holds only live in-flight data.

```python
class SessionBroker:
    async def register(self, session_id: str) -> None:
        """Called when a new session worker connects."""
    
    async def send_to_agent(self, session_id: str, message: str, message_type: str) -> None:
        """Enqueue a user message; transition waiting_for_user → running if applicable."""
    
    async def await_user_input(self, session_id: str, question: str) -> str:
        """
        Called by the worker when agent emits input_request.
        Transitions session to waiting_for_user.
        Blocks until send_to_agent is called.
        Returns the user's response string.
        """
    
    def check_interrupt(self, session_id: str) -> str | None:
        """
        Called by worker at start of each loop iteration.
        Returns pending interrupt message and clears it, or None.
        """
    
    async def cleanup(self, session_id: str) -> None:
        """Called on terminal state; removes queues."""
```

#### `gateway/main.py` — session CRUD and internal endpoints

Session endpoints:
| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions` | Create `SessionRecord` in `configuring` status; spawn K8s Job with `SESSION_ID` env var |
| `GET` | `/sessions` | List sessions owned by the authenticated user |
| `GET` | `/sessions/{id}` | Get a single session; enforce ownership |
| `GET` | `/sessions/{id}/messages` | Return all `SessionMessage` records for session |

Internal session endpoints (no auth, cluster-only):
| Method | Path | Description |
|---|---|---|
| `POST` | `/internal/sessions/{id}/await-input` | Worker suspends here; blocks until user sends a message; returns message content |
| `POST` | `/internal/sessions/{id}/interrupt-check` | Worker polls here at loop start; returns interrupt if pending, else empty |

Session gas endpoints:
| Method | Path | Description |
|---|---|---|
| `POST` | `/sessions/{id}/gas` | Add gas to a session; body: `{"amount": N}` |
| `GET` | `/sessions/{id}/gas` | Return `gas_used`, `gas_limit`, `topup_history` |

### Tests (7a)
- Unit: `session_broker.send_to_agent` enqueues a message and transitions `waiting_for_user → running`
- Unit: `session_broker.await_user_input` transitions session to `waiting_for_user`; blocks until message enqueued; returns message content
- Unit: `session_broker.check_interrupt` returns pending interrupt and clears it; returns `None` when none pending
- Unit: `session_broker` cleans up queue on terminal state transition
- Unit: `db.py` — session and message CRUD round-trips correctly
- Integration: `POST /sessions` creates a `SessionRecord` in `configuring` status, spawns K8s Job with `SESSION_ID` env var
- Integration: `POST /internal/sessions/{id}/await-input` blocks until `POST /sessions/{id}/messages` is called; returns message content
- Integration: `POST /internal/sessions/{id}/interrupt-check` returns pending interrupt; returns empty on second call

---

## Sub-task 7b — Worker Session Mode

### Deliverables

#### `worker/agent_runner.py` — session mode branch
Add session mode to the existing runner. Determined by presence of `SESSION_ID` env var.

**Session mode additions:**

*Interrupt check* — at the start of each loop iteration, call `POST /internal/sessions/{id}/interrupt-check`. If a redirect message is returned, call `agent.steer(message)` to inject it into the LLM context before the next call.

*Input suspension* — if the agent emits `input_request`, call `POST /internal/sessions/{id}/await-input` with the question. This call **blocks** until the gateway broker returns the user's answer. Inject the answer into LLM context via `agent.follow_up(answer)`.

#### `worker/agent_logger.py` — add session event types
Emit three additional event types:
- `input_request` — payload: `{"question": str}` — emitted when agent suspends for user input
- `input_received` — payload: `{"response": str}` — emitted when user answer is received
- `interrupted` — payload: `{"redirect_message": str}` — emitted when interrupt is injected

#### `gateway/main.py` — session SSE stream
```
GET /sessions/{id}/stream
```
SSE endpoint that delivers `SessionMessage` and `LogEvent` records interleaved in arrival order. On connect: replay all existing session messages and log events, then keep connection alive.

### Tests (7b)
- Unit: worker in session mode calls `interrupt-check` at the start of each loop iteration
- Unit: worker injects interrupt message into LLM context when interrupt is returned
- Unit: worker calls `await-input` when agent emits `input_request`; LLM context updated with response on resume
- Unit: `AgentLogger` emits `input_request` event with question payload
- Unit: `AgentLogger` emits `input_received` event with response payload
- Unit: `AgentLogger` emits `interrupted` event with redirect message payload
- Integration: `GET /sessions/{id}/stream` SSE delivers `SessionMessage` and `LogEvent` records interleaved in arrival order

---

## Sub-task 7c — Session Messaging

### Deliverables

#### `gateway/main.py` — user message endpoint
```
POST /sessions/{id}/messages
Body: {"content": str, "message_type": "instruction" | "interrupt" | "input_response"}
```
- Persist the `SessionMessage` to DB
- Call `session_broker.send_to_agent(session_id, content, message_type)`
- For `interrupt`: set an interrupt flag in the broker instead of enqueuing a regular message
- Return the persisted `SessionMessage`

### Tests (7c)
- Integration: full session lifecycle via API — create session, simulate worker calling `await-input`, send user message, verify session resumes, verify `complete` status

---

## Sub-task 7d — Project Proxy Endpoints and Session Launcher UI

### Deliverables

#### `gateway/main.py` — project proxy endpoints
These proxy to the provider using the **authenticated user's token**, not the global service token, ensuring users can only access projects they have permission to:

| Method | Path | Description |
|---|---|---|
| `GET` | `/projects/search?q={query}` | Proxy to `provider.search_projects(query, user_token)` |
| `GET` | `/projects/{id}/branches` | Proxy to `provider.list_branches(project_id, user_token)` |
| `GET` | `/projects/{id}/mrs` | Proxy to `provider.list_open_mrs(project_id, user_token)` |

#### `dashboard/index.html` — session launcher
Add the **New Session** launcher form (replacing the Phase 6 placeholder):

- **Project picker** — search-as-you-type calling `GET /projects/search`; shows provider, namespace, and recent activity indicator; supports manual full-path entry
- **Branch selector** — populated via `GET /projects/{id}/branches`; defaults to project's default branch; supports free-text entry
- **Target MR** (optional) — dropdown from `GET /projects/{id}/mrs`
- **Skill / tool overrides** — multi-select from project's resolved `AgentConfig`
- **Goal** — large free-text area for initial instruction
- **Gas limit** — numeric input, defaults to `DEFAULT_SESSION_GAS_LIMIT`
- **Launch** button — calls `POST /sessions`; on success, transitions to Session Workspace

### Tests (7d)
- Integration: `GET /projects/search` returns only projects the authenticated user can access (mock GitLab API)
- Unit (React): session launcher project search calls `/projects/search` on input; renders results with namespace
- Unit (React): branch selector populates from `/projects/{id}/branches`; defaults to default branch

---

## Sub-task 7e — Session Workspace UI

### Deliverables

#### `dashboard/index.html` — session workspace
Split-pane workspace combining a conversation thread with the live execution trace. Activated after a session is launched.

**Left pane — Conversation thread:**
- Chat-style layout: user messages on the right, agent messages on the left
- Agent messages include a status indicator (running / waiting / finished)
- Context-aware input at the bottom:
  - Status `waiting_for_user`: highlighted, labelled *"Agent is waiting for your answer"*, sends `input_response`
  - Status `running`: labelled *"Redirect the agent"* with a warning; sends `interrupt`
  - Status `complete`/`failed`: input disabled, replaced by **New Session** button pre-populated with same project/branch

**Right pane — Execution trace:**
- Same `LogPanel` component from Phase 6
- Subscribed to `GET /sessions/{id}/stream`
- Synchronised with left pane: `input_request` log event causes both panes to update simultaneously

**Session header bar:**
- Project name, branch, target MR (if set), elapsed time, current status with animated indicator
- **Cancel** button (active sessions only)

**Session gas meter:**
- Same gas meter component as Phase 6, using session-specific endpoints
- `out_of_gas` banner with Add Gas input for sessions

### Tests (7e)
- Unit (React): conversation thread renders user and agent messages with correct alignment and `message_type` labels
- Unit (React): input box label changes between "Redirect the agent" (running) and "Agent is waiting for your answer" (waiting_for_user)
- Unit (React): input is disabled when session is in terminal state
- E2E (browser + KIND cluster): launch a session against a test project; send initial goal; receive agent response; send interrupt; verify agent redirects; agent asks question; send answer; session completes

---

## Definition of Done
A user can launch a session from the browser, have a real conversation with an agent running against their GitLab project, steer it mid-run, answer its questions, and see the full execution trace alongside the conversation — with no local setup required.

## Dependencies
- **Blocked by:** All of Phases 0–6
- **Internal order:** 7a → 7b → 7c → then 7d and 7e can proceed in parallel
