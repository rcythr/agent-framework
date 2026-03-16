from pydantic import BaseModel
from typing import Any, Literal
from datetime import datetime


class ActivationRecord(BaseModel):
    """Persistent record of a self-service webhook registration for a repository."""
    project_id: str
    webhook_id: str       # Provider-assigned ID; needed to deregister the webhook
    secret: str           # HMAC secret generated at activation time
    activated_by: str
    activated_at: datetime


class TaskSpec(BaseModel):
    task: str
    project_id: int | str
    project_path: str = ""   # e.g. "group/repo" — used to clone workspace
    context: dict[str, Any]


class LogEvent(BaseModel):
    job_id: str
    sequence: int
    timestamp: datetime
    event_type: Literal[
        "llm_query",
        "llm_response",
        "tool_call",
        "tool_result",
        "input_request",
        "input_received",
        "interrupted",
        "gas_updated",
        "out_of_gas",
        "complete",
        "error",
    ]
    payload: dict[str, Any]


class JobRecord(BaseModel):
    id: str
    task: str
    project_id: int | str
    project_name: str
    status: Literal["pending", "running", "completed", "failed", "cancelled", "out_of_gas"]
    context: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None
    triggered_by: str = "system"
    gas_limit_input: int = 80_000
    gas_limit_output: int = 20_000
    gas_used_input: int = 0
    gas_used_output: int = 0
    gas_topups: list[dict] = []


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
    dockerfile: str | None = None
    gas_limit_input: int | None = None
    gas_limit_output: int | None = None
    allowed_users: list[str] = []


class AgentConfig(BaseModel):
    """Fully resolved config produced by the config loader — no optional fields."""
    skills: list[SkillDef]
    tools: list[ToolDef]
    system_prompt: str
    image: str
    gas_limit_input: int
    gas_limit_output: int
    allowed_users: list[str]


class SessionContext(BaseModel):
    """User-supplied context when creating an interactive session."""
    project_id: int | str
    project_path: str
    branch: str
    goal: str
    mr_iid: int | None = None
    skill_overrides: list[str] = []
    tool_overrides: list[str] = []
    gas_limit_input: int = 160_000
    gas_limit_output: int = 40_000


class SessionMessage(BaseModel):
    """A single message in an interactive session conversation."""
    session_id: str
    sequence: int
    timestamp: datetime
    role: Literal["user", "agent"]
    content: str
    message_type: Literal[
        "instruction",
        "interrupt",
        "agent_response",
        "input_request",
        "input_response",
    ]


class SessionRecord(BaseModel):
    """Persistent record of an interactive agent session."""
    id: str
    owner: str
    project_id: int | str
    project_path: str
    branch: str
    mr_iid: int | None
    status: Literal["configuring", "running", "waiting_for_user", "out_of_gas", "complete", "failed", "cancelled"]
    context: SessionContext
    created_at: datetime
    finished_at: datetime | None = None
    gas_limit_input: int = 160_000
    gas_limit_output: int = 40_000
    gas_used_input: int = 0
    gas_used_output: int = 0
    gas_topups: list[dict] = []
