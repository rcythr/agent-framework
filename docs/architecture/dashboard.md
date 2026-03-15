## Control Plane Dashboard — `dashboard/index.html`

A single-page React application served directly by the gateway at `/`. It communicates with the gateway's REST and SSE endpoints only — it has no direct access to Kubernetes or GitLab.

## Active Agents View

Shows all jobs with status `pending` or `running`. Each agent is displayed as a card showing:

- Task type, project name, and job ID
- Animated status indicator (pulsing for running, static for pending)
- Elapsed running time, updated every second in the browser
- Most recent log line as a live preview
- A **Cancel** button that calls `POST /agents/{id}/cancel`

Clicking a card expands an inline **Log Panel** (see below).

## History View

A paginated, searchable, filterable table of all completed, failed, and cancelled jobs. Columns: task type, project, status, duration, and time since completion. Each row has a **Logs** button to open the full execution trace and a **Retry** button (failed jobs only) that re-POSTs the original `TaskSpec` to `/trigger`.

Supports filtering by status and free-text search across project name and task type.

## Agent Session Interface

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

## Log Panel

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
