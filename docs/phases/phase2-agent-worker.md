# Phase 2 — Core Agent Worker

## Goal
A worker pod that boots, runs the `Agent` loop against a real GitLab project, and reports completion back to the gateway. This completes the end-to-end webhook → worker → GitLab comment flow.

## Prerequisites
- **Phase 0 complete** — provider abstraction, shared models
- **Phase 1 complete** — gateway running, able to spawn Jobs and receive status callbacks

---

## Deliverables

### `worker/agent.py` — `Agent` class
The LLM call loop with tool dispatch, event emission, and a two-queue message model.

```python
class Agent:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        tools: list[dict],
        system_prompt: str,
        event_handler: Callable[[AgentEvent], Awaitable[None]],
        gas_limit_input: int = 80_000,
        gas_limit_output: int = 20_000,
    ): ...

    async def run(self, initial_message: str) -> None: ...
    def steer(self, message: str) -> None: ...                          # inject into steering queue
    def follow_up(self, message: str) -> None: ...                      # inject into follow-up queue
    def add_gas(self, input_amount: int = 0, output_amount: int = 0) -> None: ...  # increment limit(s), unblock loop

    @property
    def gas_used_input(self) -> int: ...

    @property
    def gas_used_output(self) -> int: ...
```

**Loop behaviour:**
1. Before each LLM call: check `gas_used_input >= gas_limit_input` or `gas_used_output >= gas_limit_output`; if either, emit `out_of_gas` and `await self._gas_event.wait()`
2. Call LLM with full message history and tool schemas
3. Emit `llm_query` before the call, `llm_response` after
4. After response: `gas_used_input += input_tokens; gas_used_output += output_tokens`; emit `gas_updated`
5. If response has tool calls: emit `tool_call`, execute, emit `tool_result`, repeat
6. Steer queue messages are injected after the current tool finishes, before remaining tools in the same turn
7. Follow-up queue messages are injected only after the agent is fully idle (no pending tool calls)
8. Loop until LLM returns a response with no tool calls
9. Emit `complete` with aggregate stats

**Gas implementation:**
- `self._gas_used_input` — accumulated input token count
- `self._gas_used_output` — accumulated output token count
- `self._gas_limit_input` — input token limit; starts at constructor arg
- `self._gas_limit_output` — output token limit; starts at constructor arg
- `self._gas_event = asyncio.Event()` — set by `add_gas()`
- `add_gas(input_amount=0, output_amount=0)` increments the specified limit(s) and calls `_gas_event.set()` then `_gas_event.clear()`

**`AgentEvent` type:**
```python
@dataclass
class AgentEvent:
    event_type: Literal[
        "llm_query", "llm_response", "tool_call", "tool_result",
        "input_request", "input_received", "interrupted",
        "gas_updated", "out_of_gas", "complete", "error"
    ]
    payload: dict
```

### `providers/gitlab/toolkit.py` — `GitLabToolkit`
Subclasses `ProviderToolkit`. Implements `get_tools()` returning tool definitions in the LLM tool-calling format. Each tool's `execute` function calls the appropriate `RepositoryProvider` method — **no direct `python-gitlab` SDK calls**.

Tools to implement:
1. `get_file` — calls `provider.get_file(project_id, path, ref)`
2. `commit_file` — calls `provider.commit_file(project_id, branch, path, content, message)`
3. `create_mr` — calls `provider.create_mr(project_id, source_branch, target_branch, title, description)`
4. `post_mr_comment` — calls `provider.post_mr_comment(project_id, mr_iid, body)`
5. `post_inline_comment` — calls `provider.post_inline_comment(project_id, mr_iid, path, line, body)`
6. `get_mr_diff` — calls `provider.get_mr_diff(project_id, mr_iid)`
7. `update_pipeline_status` — calls `provider.update_pipeline_status(project_id, sha, state, description)`

### `worker/tools/toolkit_factory.py`
```python
def get_toolkit(project_id: int | str) -> ProviderToolkit:
    provider = get_provider()
    provider_name = os.getenv("PROVIDER", "gitlab")
    match provider_name:
        case "gitlab":
            from providers.gitlab.toolkit import GitLabToolkit
            return GitLabToolkit(provider=provider, project_id=project_id)
        case _:
            raise ValueError(f"No toolkit for provider: {provider_name!r}")
```

### `worker/agent_runner.py` — job mode only
Session mode is added in Phase 7. At this phase, implement only job mode.

```python
def build_system_prompt(task: str) -> str: ...
def build_task_message(task: str, context: dict) -> str: ...
async def run_agent(task: str, project_id: int, context: dict) -> None: ...
```

`build_task_message` must handle:
- `"review_mr"` → uses `mr_iid`, `source_branch`, `target_branch`, `description`
- `"handle_comment"` → uses `note_body`, `mr_iid`, `note_id`
- `"analyze_push"` → uses `branch`, `commits`
- default case → generic fallback message

On completion, `run_agent` must call `POST /internal/jobs/{id}/status` with `{"status": "completed"}` (or `"failed"` on exception).

### `worker/main.py`
Entry point for K8s Job pods:
```python
import os, json, asyncio
from worker.agent_runner import run_agent

if __name__ == "__main__":
    asyncio.run(run_agent(
        task=os.environ["TASK"],
        project_id=int(os.environ["PROJECT_ID"]),
        context=json.loads(os.environ["TASK_CONTEXT"]),
    ))
```

### `gateway/main.py` — add status callback endpoint
```
POST /internal/jobs/{id}/status
Body: {"status": "completed" | "failed" | "cancelled"}
```
Updates DB record and sets `finished_at`. Returns 200 on success, 404 if job not found.

### `Dockerfile.worker`
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker/ ./worker/
COPY shared/ ./shared/
COPY providers/ ./providers/
CMD ["python", "-m", "worker.main"]
```

---

## Tests to Write First (TDD)

### Unit tests — `worker/agent.py`
- `Agent` calls LLM with correct message history and tool schemas
- `Agent` executes tool calls and appends results to conversation history
- `Agent` loops until LLM returns a response with no tool calls
- `Agent.steer()` message is injected after the current tool finishes, before remaining tools in the same turn
- `Agent.follow_up()` message is injected only after the agent is fully idle
- `Agent` calls `event_handler` in correct order: `llm_query → llm_response → gas_updated → tool_call → tool_result` (repeat) → `complete`
- `Agent` emits `out_of_gas` and suspends when `gas_used_input >= gas_limit_input` before next LLM call
- `Agent` emits `out_of_gas` and suspends when `gas_used_output >= gas_limit_output` before next LLM call
- `Agent.add_gas(input_amount=N)` increments `gas_limit_input` and resumes the suspended loop
- `Agent.add_gas(output_amount=N)` increments `gas_limit_output` and resumes the suspended loop
- Gas is checked before each LLM call, not mid-tool — tool execution is never interrupted

### Unit tests — `providers/gitlab/toolkit.py`
- Each `GitLabToolkit` tool's `execute` function calls the correct `RepositoryProvider` method with correct arguments
- Mock `RepositoryProvider` (not `python-gitlab` directly)

### Unit tests — `worker/tools/toolkit_factory.py`
- `get_toolkit()` returns `GitLabToolkit` when `PROVIDER=gitlab`

### Unit tests — `worker/agent_runner.py`
- `build_system_prompt` includes the task type string
- `build_task_message` produces correct strings for all three task types
- `build_task_message` handles default case for unknown task
- `run_agent` constructs `Agent` with correct arguments — mock `Agent.run`

### Integration tests
- `POST /internal/jobs/{id}/status` with `{"status": "completed"}` updates DB record and returns 200
- `POST /internal/jobs/{id}/status` with unknown job ID returns 404
- Worker `main.py` reads `TASK`, `PROJECT_ID`, `TASK_CONTEXT` env vars and calls `run_agent` with correct arguments

### E2E test (KIND cluster)
- Trigger a `review_mr` job against a test GitLab project
- Verify the agent posts a comment on the MR

---

## Definition of Done
End-to-end webhook → worker → GitLab comment flow works against a real project.

## Dependencies
- **Blocked by:** Phase 0 (provider abstraction, shared models)
- **Blocked by:** Phase 1 (gateway `/internal/jobs/{id}/status` endpoint, K8s Job spawning)
- **Can proceed in parallel with:** Phase 3 (structured logging) — the `Agent` class must emit `AgentEvent`s but the `AgentLogger` consumer is Phase 3's deliverable
