import json
import os

import httpx

from worker.agent import Agent, AgentEvent
from worker.agent_logger import AgentLogger
from worker.tools.global_tools_loader import load_global_tools
from worker.tools.toolkit_factory import get_toolkit


def build_system_prompt(task: str) -> str:
    return (
        f"You are an autonomous software agent running task: {task}.\n"
        "Use the available tools to complete the task. Be precise and thorough.\n"
        "When you have finished, summarise what you did."
    )


def build_task_message(task: str, context: dict) -> str:
    match task:
        case "review_mr":
            return (
                f"Please review merge request !{context.get('mr_iid')}.\n"
                f"Source branch: {context.get('source_branch')}\n"
                f"Target branch: {context.get('target_branch')}\n"
                f"Description: {context.get('description', '')}\n"
                "Use get_mr_diff to fetch the diff, then post a review comment."
            )
        case "handle_comment":
            return (
                f"A comment was left on merge request !{context.get('mr_iid')} "
                f"(note ID {context.get('note_id')}):\n"
                f"{context.get('note_body', '')}\n"
                "Please address the comment."
            )
        case "analyze_push":
            commits = context.get("commits", [])
            commits_str = json.dumps(commits, indent=2)
            return (
                f"A push was made to branch '{context.get('branch')}'.\n"
                f"Commits:\n{commits_str}\n"
                "Analyse the changes and report any issues."
            )
        case _:
            return (
                f"Task: {task}\n"
                f"Context: {json.dumps(context)}\n"
                "Please complete the task using the available tools."
            )


async def run_agent(task: str, project_id: int | str, context: dict) -> None:
    toolkit = get_toolkit(project_id=project_id)
    tools = toolkit.get_tools() + load_global_tools()

    endpoint = os.getenv("LLM_ENDPOINT", "https://api.openai.com/v1")
    api_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("LLM_MODEL", "gpt-4o")
    gas_limit_input = int(os.getenv("GAS_LIMIT_INPUT", "80000"))
    gas_limit_output = int(os.getenv("GAS_LIMIT_OUTPUT", "20000"))

    job_id = os.getenv("JOB_ID", "")
    gateway_url = os.getenv("GATEWAY_URL", "http://pi-agent-gateway")

    system_prompt = build_system_prompt(task)
    initial_message = build_task_message(task, context)

    agent_logger = AgentLogger(
        job_id=job_id,
        gateway_url=gateway_url,
        model=model,
        tool_names=[t["name"] for t in tools],
    )

    agent = Agent(
        endpoint=endpoint,
        api_key=api_key,
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        event_handler=agent_logger.handle_event,
        gas_limit_input=gas_limit_input,
        gas_limit_output=gas_limit_output,
    )

    final_status = "completed"
    try:
        await agent.run(initial_message)
    except Exception:
        final_status = "failed"

    if job_id:
        async with httpx.AsyncClient() as http_client:
            resp = await http_client.post(
                f"{gateway_url}/internal/jobs/{job_id}/status",
                json={"status": final_status, "result": agent.last_response or None},
            )
            resp.raise_for_status()


class _SessionEventHandler:
    """
    Wraps AgentLogger for session mode.
    - On llm_query: check interrupt endpoint; steer agent if pending.
    - On input_request: call await-input; inject answer via follow_up.
    """

    def __init__(
        self,
        agent_logger: AgentLogger,
        session_id: str,
        gateway_url: str,
        http_client: httpx.AsyncClient,
    ):
        self._logger = agent_logger
        self._session_id = session_id
        self._gateway_url = gateway_url
        self._http = http_client
        self.agent: Agent | None = None  # Set after Agent is created

    async def __call__(self, event: AgentEvent) -> None:
        if event.event_type == "llm_query":
            await self._check_interrupt(event)
        elif event.event_type == "input_request":
            await self._handle_input_request(event)
            return  # input_request + input_received both logged inside
        await self._logger.handle_event(event)

    async def _check_interrupt(self, llm_query_event: AgentEvent) -> None:
        try:
            resp = await self._http.post(
                f"{self._gateway_url}/internal/sessions/{self._session_id}/interrupt-check",
                json={},
                timeout=5.0,
            )
            data = resp.json()
            interrupt_msg = data.get("interrupt")
            if interrupt_msg and self.agent is not None:
                self.agent.steer(interrupt_msg)
                await self._logger.handle_event(AgentEvent(
                    event_type="interrupted",
                    payload={"redirect_message": interrupt_msg},
                ))
        except Exception:
            pass  # Non-fatal

    async def _handle_input_request(self, event: AgentEvent) -> None:
        question = event.payload.get("question", "")
        # Log the input_request event
        await self._logger.handle_event(event)
        try:
            resp = await self._http.post(
                f"{self._gateway_url}/internal/sessions/{self._session_id}/await-input",
                json={"question": question},
                timeout=None,  # Blocks until user responds
            )
            answer = resp.json().get("content", "")
        except Exception:
            answer = ""
        # Inject into agent context
        if self.agent is not None:
            self.agent.follow_up(answer)
        # Log the input_received event
        await self._logger.handle_event(AgentEvent(
            event_type="input_received",
            payload={"response": answer},
        ))


async def run_session(session_id: str) -> None:
    project_id = os.getenv("PROJECT_ID", "0")
    goal = os.getenv("SESSION_GOAL", "")
    gateway_url = os.getenv("GATEWAY_URL", "http://pi-agent-gateway")
    endpoint = os.getenv("LLM_ENDPOINT", "https://api.openai.com/v1")
    api_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("LLM_MODEL", "gpt-4o")
    gas_limit_input = int(os.getenv("GAS_LIMIT_INPUT", "160000"))
    gas_limit_output = int(os.getenv("GAS_LIMIT_OUTPUT", "40000"))

    toolkit = get_toolkit(project_id=project_id)
    tools = toolkit.get_tools() + load_global_tools()

    system_prompt = (
        "You are an interactive agent helping with a software project. "
        "Use the available tools to assist the user. When you have completed "
        "a task or need more information, respond clearly and wait for the user's next instruction."
    )

    agent_logger = AgentLogger(
        job_id=session_id,
        gateway_url=gateway_url,
        model=model,
        tool_names=[t["name"] for t in tools],
    )

    async with httpx.AsyncClient() as http_client:
        handler = _SessionEventHandler(
            agent_logger=agent_logger,
            session_id=session_id,
            gateway_url=gateway_url,
            http_client=http_client,
        )

        agent = Agent(
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            event_handler=handler,
            gas_limit_input=gas_limit_input,
            gas_limit_output=gas_limit_output,
            interactive=True,
        )
        handler.agent = agent

        final_status = "complete"
        try:
            await agent.run(goal)
        except Exception:
            final_status = "failed"

        try:
            await http_client.post(
                f"{gateway_url}/internal/sessions/{session_id}/status",
                json={"status": final_status},
            )
        except Exception:
            pass
