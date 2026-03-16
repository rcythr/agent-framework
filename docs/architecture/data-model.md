# Data Model

## Shared Models — `shared/models.py`

Pydantic models shared between the gateway and worker covering task specs, log events, job records, and the resolved agent configuration produced by the config loader.

```python
from pydantic import BaseModel
from typing import Any, Literal
from datetime import datetime

class TaskSpec(BaseModel):
    task: str
    project_id: int
    context: dict[str, Any]

class LogEvent(BaseModel):
    job_id: str
    sequence: int
    timestamp: datetime
    event_type: Literal[
        "llm_query",         # prompt sent to the LLM
        "llm_response",      # full LLM response received
        "tool_call",         # agent invoking a tool with arguments
        "tool_result",       # raw result returned from a tool
        "input_request",     # agent suspending to ask the user a question (sessions only)
        "input_received",    # user response received, agent resuming (sessions only)
        "interrupted",       # user sent a redirect message while agent was running
        "gas_updated",       # gas_used updated after an LLM call
        "out_of_gas",        # input or output gas_limit reached; run paused awaiting top-up
        "complete",          # agent finished successfully
        "error",             # unhandled exception or LLM error
    ]
    payload: dict[str, Any]   # event-type-specific data (see below)

class JobRecord(BaseModel):
    id: str
    task: str
    project_id: int
    project_name: str
    status: Literal["pending", "running", "completed", "failed", "cancelled", "out_of_gas"]
    context: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None
    gas_limit_input: int = 80_000     # input token budget allocated to this job
    gas_limit_output: int = 20_000    # output token budget allocated to this job
    gas_used_input: int = 0           # input tokens consumed so far
    gas_used_output: int = 0          # output tokens consumed so far
    gas_topups: list[dict] = []       # record of each top-up: {"input": N, "output": M}
    result: str | None = None         # final text response from the agent (None if not yet finished)

class SkillDef(BaseModel):
    name: str
    description: str
    inline: bool = False

class ToolDef(BaseModel):
    name: str
    description: str
    inline: bool = False

class ProjectConfig(BaseModel):
    """Parsed and validated representation of the project agent config."""
    skills: list[SkillDef] = []
    tools: list[ToolDef] = []
    prompt_mode: Literal["append", "override"] = "append"
    prompt: str = ""
    dockerfile: str | None = None        # repo-relative path to project Dockerfile
    gas_limit_input: int | None = None   # overrides DEFAULT_JOB_INPUT_GAS_LIMIT if set
    gas_limit_output: int | None = None  # overrides DEFAULT_JOB_OUTPUT_GAS_LIMIT if set
    allowed_users: list[str] = []        # usernames permitted to trigger agent dispatch;
                                         # empty list = all users blocked (deny-by-default)

class AgentConfig(BaseModel):
    """Fully resolved config produced by the config loader — no optional fields."""
    skills: list[SkillDef]          # global + project, deduplicated
    tools: list[ToolDef]            # global + project, deduplicated
    system_prompt: str              # fully composed prompt string
    image: str                      # registry image tag to use for the K8s Job pod
    gas_limit_input: int            # resolved input token budget
    gas_limit_output: int           # resolved output token budget

class SessionContext(BaseModel):
    """User-supplied context when creating an interactive session."""
    project_id: int
    project_path: str               # e.g. "group/my-repo"
    branch: str                     # branch the agent will read/write against
    goal: str                       # initial free-text instruction from the user
    mr_iid: int | None = None       # optional: scope session to a specific MR
    skill_overrides: list[str] = [] # skill names to add on top of project config
    tool_overrides: list[str] = []  # tool names to add on top of project config
    gas_limit_input: int = 160_000  # input token budget for this session; overrides system default
    gas_limit_output: int = 40_000  # output token budget for this session; overrides system default

class SessionMessage(BaseModel):
    """A single message in an interactive session conversation."""
    session_id: str
    sequence: int
    timestamp: datetime
    role: Literal["user", "agent"]
    content: str
    message_type: Literal[
        "instruction",        # user's initial goal or a follow-up instruction
        "interrupt",          # user redirecting a running agent mid-loop
        "agent_response",     # agent's natural language reply or status update
        "input_request",      # agent asking the user a clarifying question
        "input_response",     # user's answer to an agent clarifying question
    ]

class SessionRecord(BaseModel):
    """Persistent record of an interactive agent session."""
    id: str
    owner: str                        # Normalised username from AuthProvider.extract_user(headers)
    project_id: int
    project_path: str
    branch: str
    mr_iid: int | None
    status: Literal["configuring", "running", "waiting_for_user", "out_of_gas", "complete", "failed", "cancelled"]
    context: SessionContext
    created_at: datetime
    finished_at: datetime | None = None
    gas_limit_input: int = 160_000    # input token budget for this session
    gas_limit_output: int = 40_000    # output token budget for this session
    gas_used_input: int = 0           # input tokens consumed so far
    gas_used_output: int = 0          # output tokens consumed so far
    gas_topups: list[dict] = []       # record of each top-up: {"input": N, "output": M}
```

**`LogEvent.payload` shapes by event type:**

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
| `gas_updated` | `gas_used_input: int`, `gas_limit_input: int`, `gas_used_output: int`, `gas_limit_output: int`, `input_tokens: int`, `output_tokens: int` |
| `out_of_gas` | `gas_used_input: int`, `gas_limit_input: int`, `gas_used_output: int`, `gas_limit_output: int`, `exhausted: Literal["input", "output", "both"]` |

---

---

## Project Configuration — `.agents/config.yaml`

Each GitLab project can place an `.agents/config.yaml` file in its repository. The directory name `.agents/` is the default and is configurable per gateway deployment via the `AGENT_CONFIG_DIR` environment variable — operators can set it to any repo-relative path (e.g. `.gitlab/agents/` or `config/pi-agent/`). The config file and all associated assets (skills, tools, Dockerfile) live under this single directory. The file is optional — if absent, the agent runs entirely with global defaults.

**Schema:**

```yaml
# .agents/config.yaml

# Additional skills to load on top of the global skill set.
# Values are skill identifiers registered in the global-config/skills/ directory
# or inline skill definitions.
skills:
  - python-testing          # global skill by name
  - security-scanning       # global skill by name
  - name: custom-linter
    description: "Run the project-specific ESLint config and report violations"
    inline: true            # defined here rather than in global registry

# Additional tools to expose to the agent, merged with the global tool set.
# Same format as skills — reference global tools by name or define inline.
tools:
  - notify-slack            # global tool by name
  - name: run-tests
    description: "Execute the project test suite via the CI API"
    inline: true

# Users permitted to trigger agent dispatch via webhooks (push, MR, comment events).
# Only these usernames will cause an agent to be spawned automatically.
# Empty list = no automatic dispatch (deny-by-default).
# Manual sessions launched from the dashboard are controlled by dashboard auth instead.
allowed_users:
  - alice
  - bob
  - ci-bot

# Token budgets for agent jobs triggered from this project.
# Overrides the system defaults (DEFAULT_JOB_INPUT_GAS_LIMIT / DEFAULT_JOB_OUTPUT_GAS_LIMIT).
# Sessions have their own gas limits set in the session launcher.
gas_limit_input: 120000
gas_limit_output: 30000

# System prompt behaviour.
# "append" adds text after the global base prompt (default).
# "override" replaces the global prompt entirely (use with caution).
prompt_mode: append
prompt: |
  This repository uses Python 3.12 and follows the Google style guide.
  All MR reviews must check for missing type annotations.
  Never suggest changes to files under legacy/.

# Custom agent runtime image.
# If specified, the gateway builds a derived image using the global worker
# image as the base (FROM pi-agent-worker:latest) and this Dockerfile as
# the override layer. The resulting image is used for all agent jobs in
# this project.
dockerfile: Dockerfile   # relative to the agent config directory, i.e. .agents/Dockerfile
```

**Dockerfile override example** — `project-repo/.agents/Dockerfile`:

```dockerfile
# The global worker image is always the base — projects cannot change this FROM
ARG BASE_IMAGE=your-registry/pi-agent-worker:latest
FROM ${BASE_IMAGE}

# Projects can install additional dependencies needed by their custom tools or skills
RUN pip install --no-cache-dir pandas==2.2.0 scipy==1.13.0

# Projects can add files, scripts, or config needed by their inline tools
COPY .agents/scripts/ /app/project-scripts/
```

---
