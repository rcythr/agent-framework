# Phase 6 — Control Plane Dashboard

## Goal
A fully functional browser dashboard for monitoring jobs, viewing history, inspecting execution traces, managing gas budgets, and controlling agent runs — entirely from the browser.

## Prerequisites
- **Phase 1 complete** — job API endpoints exist
- **Phase 2 complete** — agent runs produce completable jobs
- **Phase 3 complete** — log events exist and SSE streaming works
- **Phase 5 complete** — authentication in place (dashboard must be behind auth)

---

## Deliverables

### `dashboard/index.html`
A single-page React application bundled into a single file and served by the gateway at `/`. Uses only the gateway's REST and SSE APIs — no direct K8s or GitLab access.

The app is structured around three main views accessible from a top navigation bar: **Active Agents**, **History**, and **New Session** (New Session is implemented in Phase 7; add the nav item but it can be a placeholder at this phase).

---

#### Active Agents View
Shows all jobs with status `pending` or `running`. Refreshes automatically (either SSE-driven job list or polling `GET /agents`).

Each job is displayed as an **AgentCard** showing:
- Task type and project name
- Job ID (truncated, copyable)
- Animated status indicator: pulsing dot for `running`, static for `pending`
- Elapsed running time, updating every second in the browser
- Most recent log line as a live preview
- Gas meters — two linear progress bars showing `gas_used_input / gas_limit_input` and `gas_used_output / gas_limit_output` (see Gas Meter section)
- **Cancel** button → calls `POST /agents/{id}/cancel`; updates card state optimistically

Clicking a card expands an inline **Log Panel** (see Log Panel section).

**`POST /agents/{id}/cancel` gateway endpoint** (implement in `gateway/main.py`):
- Delete the K8s Job
- Set DB status to `cancelled`, `finished_at` to now
- Return 200

---

#### History View
Paginated, searchable, filterable table of completed, failed, and cancelled jobs.

Columns: task type, project name, status, duration, time since completion.

Controls:
- Free-text search input — filters by project name and task type (client-side)
- Status filter dropdown — `all`, `completed`, `failed`, `cancelled`
- Pagination: previous/next buttons; shows current page and total count

Each row:
- **Logs** button — opens the Log Panel in a modal with full event history
- **Retry** button (failed jobs only) — re-POSTs original `TaskSpec` to `POST /trigger`

---

#### Log Panel
Used both inline (active agents) and in a modal (history). Renders structured `LogEvent` stream with each type visually distinct:

| Event type | Rendering |
|---|---|
| `llm_query` | Collapsible block showing message count and tool list |
| `llm_response` | LLM output text with token count badge; tool calls highlighted |
| `tool_call` | Tool name in accent colour with formatted argument key/value pairs |
| `tool_result` | Return value in monospace block with duration badge |
| `complete` | Summary banner with aggregate stats (LLM calls, tool calls, total time) |
| `error` | Red error block with message and collapsible traceback |
| `gas_updated` | Subtle inline update to gas meter (no separate log item needed) |
| `out_of_gas` | Amber banner (see Gas Meter section) |

For **active agents**: subscribe to `GET /agents/{id}/logs/stream` (SSE) and append events as they arrive. Auto-scroll follows the latest event, but **pauses** when the user scrolls up, and **resumes** when they scroll back to the bottom.

For **historical agents**: fetch `GET /agents/{id}/logs` once and render all events immediately.

---

#### Gas Meter
Present on every AgentCard and in the Log Panel header. Two separate meters are shown — one for input tokens, one for output tokens.

Normal state:
- Two linear progress bars: input fill = `gas_used_input / gas_limit_input`, output fill = `gas_used_output / gas_limit_output`
- Labels: `{gas_used_input.toLocaleString()} / {gas_limit_input.toLocaleString()} input tokens` and `{gas_used_output.toLocaleString()} / {gas_limit_output.toLocaleString()} output tokens`
- Both meters update live via `gas_updated` events from the SSE stream

`out_of_gas` state:
- The exhausted meter fills to 100% and turns amber; the other shows its current fill
- Banner: *"Agent paused — out of gas. Review the execution trace and add more tokens to continue."*
- Two numeric inputs (input tokens / output tokens) pre-populated with default top-up amounts + **Add Gas** button
- Submitting calls `POST /agents/{id}/gas` with `{"input_amount": N, "output_amount": M}` (either field optional)
- On success: status transitions back to `running`, meters reset to new ratios

**`POST /agents/{id}/gas` and `GET /agents/{id}/gas` gateway endpoints** (implement in `gateway/main.py`):
- `POST`: increment the specified limit(s) in DB; call `POST /internal/jobs/{id}/add-gas` to unblock the agent if it is `out_of_gas`; return updated gas state
- `POST` on a non-`out_of_gas` job: still increments limit(s) (pre-emptive top-up), does **not** trigger a resume
- `GET`: return `{"gas_used_input": N, "gas_limit_input": N, "gas_used_output": N, "gas_limit_output": N, "topup_history": [...]}`

**`POST /internal/jobs/{id}/add-gas` gateway endpoint:**
- Forwards `amount` to the running worker pod (worker calls `agent.add_gas(amount)`)
- Only has effect if the agent is currently suspended in `out_of_gas` state

---

## Tests to Write First (TDD)

### Unit tests (React)
- `StatusPill` renders correct colour and pulse animation for each status value
- `AgentCard` displays task type, project name, elapsed time, and most recent log line
- `AgentCard` cancel button calls `POST /agents/{id}/cancel` and updates local state optimistically
- `LogPanel` renders each of the six primary event types with distinct visual treatment
- `LogPanel` auto-scrolls to bottom on new events; pauses when user scrolls up; resumes on scroll to bottom
- `LogPanel` fetches full log history via `GET /agents/{id}/logs` for completed jobs
- `HistoryRow` retry button is visible only for `failed` jobs; calls `POST /trigger` with original context
- History search filters rows by project name and task type; status filter works independently
- Gas meters render correct fill ratios from `gas_used_input / gas_limit_input` and `gas_used_output / gas_limit_output`
- Exhausted gas meter turns amber and out-of-gas banner appears when status is `out_of_gas`
- Add Gas button calls `POST /agents/{id}/gas` with `{"input_amount": N, "output_amount": M}` and optimistically updates meters

### Integration tests
- `POST /agents/{id}/cancel` deletes the K8s Job and sets DB status to `cancelled`
- `POST /agents/{id}/gas` increments specified limit(s) in DB and calls internal add-gas endpoint; returns updated gas state
- `POST /agents/{id}/gas` on a non-`out_of_gas` job increments limit(s) without triggering a resume

### E2E test (browser + KIND cluster)
- Navigate to dashboard; verify active jobs appear
- Expand log panel on an active job; verify events stream in
- Cancel a job; verify status updates to `cancelled`
- Navigate to history; verify cancelled job appears

---

## Definition of Done
Operators can monitor all agent activity, inspect full execution traces, cancel running jobs, retry failed ones, and manage gas budgets — entirely from the browser.

## Dependencies
- **Blocked by:** Phase 1 (job API), Phase 2 (agent runs), Phase 3 (logs/SSE), Phase 5 (auth)
- **Does not block:** Phase 7 (sessions) — Phase 7 extends the dashboard, not the other way around
- 
