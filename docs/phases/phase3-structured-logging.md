# Phase 3 — Structured Logging and Observability

## Goal
Every agent run emits typed log events that are persisted in the gateway and visible via API and live SSE stream. The `Agent` class already calls `event_handler` for every event — this phase wires the logger consumer to that hook and adds the gateway-side persistence and streaming.

## Prerequisites
- **Phase 0 complete** — shared models
- **Phase 1 complete** — gateway running, DB exists
- **Phase 2 complete (or in progress)** — `Agent` class must implement `event_handler` callback; `AgentLogger` wraps it

> Note: Phase 3 can be developed largely in parallel with Phase 2. The `Agent.event_handler` contract is defined in Phase 2's spec; the logger can be written and tested against that contract before Phase 2 is fully merged, as long as the `AgentEvent` dataclass is agreed upon.

---

## Deliverables

### `shared/models.py` — add `LogEvent`
```python
class LogEvent(BaseModel):
    job_id: str
    sequence: int
    timestamp: datetime
    event_type: Literal[
        "llm_query", "llm_response", "tool_call", "tool_result",
        "input_request", "input_received", "interrupted",
        "gas_updated", "out_of_gas", "complete", "error"
    ]
    payload: dict[str, Any]
```

`payload` shapes by event type:
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
| `gas_updated` | `gas_used: int`, `gas_limit: int`, `tokens_this_call: int` |
| `out_of_gas` | `gas_used: int`, `gas_limit: int` |

### `gateway/db.py` — add `log_events` table
Add to the existing `db.py`:
- `log_events` table: `job_id`, `sequence`, `timestamp`, `event_type`, `payload` (JSON)
- `append_log_event(event: LogEvent)` — insert a row
- `get_log_events(job_id: str) → list[LogEvent]` — return all events for a job ordered by `sequence`

### `gateway/main.py` — add logging endpoints
| Method | Path | Description |
|---|---|---|
| `POST` | `/internal/log` | Accept `LogEvent` body; persist to DB; broadcast to active SSE subscribers for that `job_id`; return 200 |
| `GET` | `/agents/{id}/logs` | Return full list of `LogEvent` for job, ordered by sequence |
| `GET` | `/agents/{id}/logs/stream` | SSE endpoint — stream new log events as they arrive via `/internal/log`; on connect, replay all existing events first, then keep connection alive for new ones |

SSE implementation notes:
- Use `sse-starlette` for the SSE endpoint
- Maintain an in-memory subscriber registry: `dict[job_id, list[asyncio.Queue]]`
- On `POST /internal/log`: persist event, then put it on all queues for that `job_id`
- On `GET /agents/{id}/logs/stream`: replay existing events from DB, then subscribe to the queue
- Clean up subscriber queues when the job reaches a terminal state

### `worker/agent_logger.py` — `AgentLogger`
Wraps the `Agent` event stream. Listens to `AgentEvent` callbacks and POSTs `LogEvent` records to the gateway's `/internal/log` endpoint.

```python
class AgentLogger:
    def __init__(self, job_id: str, gateway_url: str):
        self._job_id = job_id
        self._gateway_url = gateway_url
        self._sequence = 0

    async def handle_event(self, event: AgentEvent) -> None:
        """Called by Agent for every event. Fire-and-forget POST to gateway."""
```

Implementation requirements:
- Translate `AgentEvent` → `LogEvent` with correct `payload` shape for each type
- Sequence numbers must be monotonically increasing; use an `asyncio.Lock` for thread safety
- HTTP POST must be **fire-and-forget** — a slow or failed gateway response must not block agent execution
- Enforce a per-request timeout (e.g. 5 seconds); swallow timeout errors after logging locally

Pass `AgentLogger.handle_event` as the `event_handler` argument when constructing `Agent` in `agent_runner.py`. **No changes to `Agent` or `agent_runner.py` are required** — the logger attaches via the existing callback.

---

## Tests to Write First (TDD)

### Unit tests — `worker/agent_logger.py`
- `AgentLogger` emits `llm_query` before LLM call with correct `messages`, `model`, `tools` payload
- `AgentLogger` emits `llm_response` after LLM call with `content`, `tool_calls`, token counts
- `AgentLogger` emits `tool_call` before tool execution with `tool_name`, `arguments`
- `AgentLogger` emits `tool_result` after execution with `tool_name`, `result`, `duration_ms`
- `AgentLogger` emits `complete` on clean exit with aggregate counts
- `AgentLogger` emits `error` with `message` and `traceback` when an exception is raised
- `AgentLogger` emits `gas_updated` with correct `gas_used`, `gas_limit`, `tokens_this_call`
- `AgentLogger` fire-and-forget HTTP — a slow gateway response does not block agent execution; timeout is enforced
- Sequence numbers are monotonically increasing across concurrent emits

### Unit tests — `gateway/db.py`
- `append_log_event` inserts a row correctly
- `get_log_events` returns events ordered by `sequence`

### Integration tests
- `POST /internal/log` persists a `LogEvent` and returns 200
- `POST /internal/log` with malformed payload returns 422
- `GET /agents/{id}/logs` returns all events for a job in sequence order
- `GET /agents/{id}/logs/stream` — connect SSE client; post log events via internal endpoint; assert events are received in order with correct `event_type` fields

### E2E test (KIND cluster)
- Run a full agent job
- Assert all event types (`llm_query`, `llm_response`, `tool_call`, `tool_result`, `gas_updated`, `complete`) appear in `GET /agents/{id}/logs`

---

## Definition of Done
A completed agent job has a full structured execution trace retrievable via `GET /agents/{id}/logs` and streamable live via `GET /agents/{id}/logs/stream` SSE.

## Dependencies
- **Blocked by:** Phase 0 (shared models)
- **Blocked by:** Phase 1 (gateway, DB)
- **Parallel with:** Phase 2 — can be developed against the agreed `AgentEvent` contract; merge order does not matter as long as both pass before E2E
