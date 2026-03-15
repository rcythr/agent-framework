import json
import os

import httpx

from worker.agent import Agent, AgentEvent
from worker.agent_logger import AgentLogger
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


async def run_agent(task: str, project_id: int, context: dict) -> None:
    toolkit = get_toolkit(project_id=project_id)
    tools = toolkit.get_tools()

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
                json={"status": final_status},
            )
            resp.raise_for_status()
