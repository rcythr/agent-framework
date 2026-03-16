# Worker

## Worker — Provider Toolkit — `providers/gitlab/toolkit.py`

The GitLab implementation of `ProviderToolkit`. Exposes repository operations as tools by wrapping calls to a `RepositoryProvider` instance. The agent runner never imports it directly — it uses `toolkit_factory.get_toolkit()` to obtain the correct implementation for the active provider.

```python
from providers.base import RepositoryProvider
from worker.tools.toolkit_base import ProviderToolkit
from typing import Any


class GitLabToolkit(ProviderToolkit):
    def __init__(self, provider: RepositoryProvider, project_id: int | str):
        self.provider = provider
        self.project_id = project_id

    def get_tools(self) -> list[dict]:
        return [
            {
                "name": "get_mr_diff",
                "description": "Get the diff of a merge request",
                "parameters": {"mr_iid": {"type": "integer"}},
                "execute": self.get_mr_diff,
            },
            {
                "name": "post_mr_comment",
                "description": "Post a general comment on a merge request",
                "parameters": {"mr_iid": {"type": "integer"}, "body": {"type": "string"}},
                "execute": self.post_mr_comment,
            },
            {
                "name": "post_inline_comment",
                "description": "Post an inline comment on a specific line in an MR diff",
                "parameters": {
                    "mr_iid": {"type": "integer"},
                    "file_path": {"type": "string"},
                    "line": {"type": "integer"},
                    "body": {"type": "string"},
                },
                "execute": self.post_inline_comment,
            },
            {
                "name": "get_file_content",
                "description": "Read a file from the repository at a given ref",
                "parameters": {"file_path": {"type": "string"}, "ref": {"type": "string"}},
                "execute": self.get_file_content,
            },
            {
                "name": "commit_file",
                "description": "Create or update a file in a branch via a commit",
                "parameters": {
                    "branch": {"type": "string"},
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                    "commit_message": {"type": "string"},
                },
                "execute": self.commit_file,
            },
            {
                "name": "create_mr",
                "description": "Open a new merge request",
                "parameters": {
                    "source_branch": {"type": "string"},
                    "target_branch": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
                "execute": self.create_mr,
            },
            {
                "name": "update_pipeline_status",
                "description": "Post a commit status (pending / running / success / failed)",
                "parameters": {
                    "sha": {"type": "string"},
                    "state": {"type": "string"},
                    "description": {"type": "string"},
                },
                "execute": self.update_pipeline_status,
            },
        ]

    def get_mr_diff(self, mr_iid: int) -> list[dict[str, Any]]:
        return self.provider.get_mr_diff(self.project_id, mr_iid)

    def post_mr_comment(self, mr_iid: int, body: str) -> dict:
        return self.provider.post_mr_comment(self.project_id, mr_iid, body)

    def post_inline_comment(self, mr_iid: int, file_path: str, line: int, body: str) -> dict:
        return self.provider.post_inline_comment(self.project_id, mr_iid, file_path, line, body)

    def get_file_content(self, file_path: str, ref: str) -> str:
        return self.provider.get_file(self.project_id, file_path, ref).content

    def commit_file(self, branch: str, file_path: str, content: str, commit_message: str) -> dict:
        result = self.provider.commit_file(self.project_id, branch, file_path, content, commit_message)
        return {"committed": True, "sha": result.sha}

    def create_mr(self, source_branch: str, target_branch: str, title: str, description: str) -> dict:
        result = self.provider.create_mr(self.project_id, source_branch, target_branch, title, description)
        return {"mr_iid": result.iid, "url": result.web_url}

    def update_pipeline_status(self, sha: str, state: str, description: str) -> dict:
        return self.provider.update_pipeline_status(self.project_id, sha, state, description)
```

---

## Worker — Agent Logger — `worker/agent_logger.py`

`AgentLogger` is passed to the `Agent` as its `event_handler`. It receives every `AgentEvent` emitted by the loop and forwards each one as a typed `LogEvent` to the gateway via `POST /internal/log`. It is responsible for the full observability of an agent run.

Events emitted in order during a typical run:

1. `llm_query` — immediately before each LLM call, capturing the full message history and list of available tool names
2. `llm_response` — immediately after, capturing the response content, any tool call decisions, and token counts
3. `tool_call` — for each tool the LLM decides to invoke, capturing the tool name and arguments before execution
4. `tool_result` — after tool execution completes, capturing the return value and wall-clock duration
5. Steps 1–4 repeat for each iteration of the agent loop
6. `complete` — when the agent exits cleanly, with a summary and aggregate counts
7. `error` — if an unhandled exception occurs at any point, with full traceback

All events are fire-and-forget over HTTP with a short timeout so a slow gateway never blocks agent execution. Events include a monotonically incrementing `sequence` number so the dashboard can order them correctly even if delivery is slightly out of order.

---

## Worker — Agent — `worker/agent.py`

The `Agent` class is a Python implementation of a minimal LLM tool-use loop. Its design draws on the ideas behind small agentic loop libraries: call the LLM, execute whatever tools it requests, feed results back, repeat until done.

**Design:**

The loop runs until the LLM returns a message with no tool calls:

```
1. Check steer queue — if a message is pending, prepend it to
   the conversation and clear the queue
2. Call LLM with current conversation history + tool schemas
3. Emit AgentEvent(type='llm_query', ...)
4. Consume the streaming response, accumulating text and tool calls
5. Emit AgentEvent(type='llm_response', ...)
6. For each tool call:
   a. Emit AgentEvent(type='tool_call', ...)
   b. Dispatch to the matching tool function
   c. Append tool result to conversation history
   d. Emit AgentEvent(type='tool_result', ...)
   e. Check steer queue — steer messages interrupt after the
      current tool, before any remaining tools in the same turn
7. If tool calls were made: loop back to step 1
8. If no tool calls:
   a. Append assistant message to conversation history
   b. Check follow-up queue — if a message is waiting, inject
      it and loop back to step 1
   c. Otherwise: emit AgentEvent(type='complete', ...) and return
```

**Gas tracking:**

Each `llm_response` event includes `input_tokens` and `output_tokens` from the LLM response. The `Agent` accumulates these into `self._gas_used_input` and `self._gas_used_output` separately and emits a `gas_updated` event after every LLM call. If either counter reaches its corresponding limit (`gas_limit_input` or `gas_limit_output`), the agent emits an `out_of_gas` event indicating which limit was exhausted and suspends — it does not start another LLM call. The loop resumes only when `agent.add_gas(input_amount, output_amount)` is called, which increases one or both limits and re-enters the loop.

**Two message queues:**

- **Steer queue** (`agent.steer(message)`) — delivers a message after the current tool finishes, interrupting any remaining tools in the same turn. Used for user redirects in interactive sessions.
- **Follow-up queue** (`agent.follow_up(message)`) — delivers a message only once the agent is fully idle. Used for answering agent clarifying questions in interactive sessions.

**Interface:**

```python
@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict        # JSON Schema for the tool arguments
    execute: Callable       # async function invoked when the tool is called

@dataclass
class AgentEvent:
    type: str               # llm_query | llm_response | tool_call |
                            # tool_result | complete | error
    payload: dict

class Agent:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        tools: list[ToolDef],
        system_prompt: str,
        event_handler: Callable[[AgentEvent], Awaitable[None]],
        gas_limit_input: int = 80_000,
        gas_limit_output: int = 20_000,
    ): ...

    async def run(self, initial_message: str) -> None: ...

    def steer(self, message: str) -> None:
        ...

    def follow_up(self, message: str) -> None:
        ...

    def add_gas(self, input_amount: int = 0, output_amount: int = 0) -> None:
        ...

    @property
    def gas_used_input(self) -> int: ...

    @property
    def gas_used_output(self) -> int: ...

    @property
    def last_response(self) -> str:
        """The final text response from the LLM before the agent loop exited.
        Empty string before run() is called or if the agent exited via an exception."""
        ...
```

The `event_handler` callback is how `AgentLogger` attaches to the loop — the agent emits; the logger persists and streams to the gateway. This keeps the agent loop free of any I/O concerns beyond the LLM API call itself.

---

## Worker — Agent Runner — `worker/agent_runner.py`

Initialises an `Agent` instance using the fully resolved `AgentConfig` injected into the pod by the job spawner. The system prompt is already composed (base + project extension) and the skill and tool lists are already merged — the runner does not need to know about global vs project config. It obtains a provider toolkit via `toolkit_factory.get_toolkit()` — which reads the `PROVIDER` env var and returns the appropriate `ProviderToolkit` subclass — and passes it to the `Agent` alongside the task message. The runner is entirely provider-agnostic.

The runner operates in one of two modes, determined by the `SESSION_ID` environment variable:

**Job mode** (no `SESSION_ID`) — standard webhook-triggered or CI-triggered run. The runner executes the agent loop to completion without any user interaction. On completion, the runner reads `agent.last_response` (the agent's final text output) and includes it in the `POST /internal/jobs/{id}/status` call as a `result` field. The gateway stores this in `JobRecord.result` and notifies any parent agents waiting on `GET /internal/jobs/{id}/await-result`.

**Session mode** (`SESSION_ID` is set) — interactive user-initiated run. The runner wraps each agent loop iteration with two additional behaviours:

- **Interrupt check** — at the start of each iteration, the runner calls `POST /internal/sessions/{id}/interrupt-check`. If an interrupt is pending, it prepends the redirect message to the LLM context before the next call, allowing the user to steer the agent mid-execution without waiting for the current task to complete.
- **Input suspension** — if the agent emits an `input_request` event (i.e. it determines it needs user clarification), the runner calls `POST /internal/sessions/{id}/await-input` with the question. This call blocks until the gateway broker receives a user response, then returns the answer for injection into the LLM context. The agent loop resumes transparently.

```python
import os
from worker.agent import Agent


def build_system_prompt(task: str) -> str:
    return f"""You are an autonomous software engineering agent integrated with a Git repository.
You have tools to read code, post comments, create commits, and open merge requests.
Current task type: {task}.
Always be concise in comments. Never force-push to protected branches.
When making code changes, always create a new branch and open an MR — never commit directly to main."""


def build_task_message(task: str, context: dict) -> str:
    match task:
        case "review_mr":
            return (
                f"Review MR !{context['mr_iid']} "
                f"(merging `{context['source_branch']}` → `{context['target_branch']}`). "
                f"Fetch the diff, check for bugs, security issues, and style problems. "
                f"Post a summary comment and inline notes where relevant."
            )
        case "handle_comment":
            return (
                f"A user left this comment on MR !{context['mr_iid']}: "
                f"\"{context['note_body']}\". "
                f"Interpret the request and act on it. "
                f"If they asked for a fix, implement it and commit the change."
            )
        case "analyze_push":
            return (
                f"Analyze the recent push to {context['branch']}. "
                f"Commits: {context['commits']}. "
                f"Flag any suspicious changes, broken patterns, or missing tests."
            )
        case _:
            return f"Execute task: {task} with context: {context}"


def run_agent(task: str, project_id: int, context: dict) -> None:
    from worker.tools.toolkit_factory import get_toolkit

    toolkit = get_toolkit(project_id=project_id)

    agent = Agent(
        endpoint=os.environ["LLM_ENDPOINT"],
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("LLM_MODEL", "gpt-4o"),
        tools=toolkit.get_tools(),
        system_prompt=build_system_prompt(task),
        event_handler=logger.handle_event,
    )

    await agent.run(build_task_message(task, context))
```

---

## Worker — Entry Point — `worker/main.py`

```python
import os
import json
from worker.agent_runner import run_agent

if __name__ == "__main__":
    run_agent(
        task=os.environ["TASK"],
        project_id=int(os.environ["PROJECT_ID"]),
        context=json.loads(os.environ["TASK_CONTEXT"]),
    )
```

---
