"""
Core tool: spawn_subagent — break down the current task and dispatch a sub-job.

The agent posts a new task to the gateway's /trigger endpoint.  The gateway
spawns a fresh K8s Job for the sub-task, which runs independently.  The
calling agent receives the new job's ID so it can reference it in follow-up
comments or logs.
"""
import json
import os

import httpx


def get_tool() -> dict:
    return {
        "name": "spawn_subagent",
        "description": (
            "Decompose the current task by spawning a new autonomous sub-agent job. "
            "The sub-agent receives its own task description and context and runs "
            "independently. Returns the new job ID. Use this to parallelise "
            "independent subtasks (e.g. 'fix bug in module A' and 'write tests for module B')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Short task identifier for the sub-agent "
                        "(e.g. 'fix_bug', 'write_tests', 'update_docs')."
                    ),
                },
                "goal": {
                    "type": "string",
                    "description": "Full description of what the sub-agent should accomplish.",
                },
                "context": {
                    "type": "object",
                    "description": "Additional key/value context to pass to the sub-agent.",
                },
            },
            "required": ["task", "goal"],
        },
        "execute": _execute,
    }


def _execute(task: str, goal: str, context: dict | None = None) -> str:
    gateway_url = os.getenv("GATEWAY_URL", "http://pi-agent-gateway")
    project_id = os.getenv("PROJECT_ID", "0")
    project_path = os.getenv("PROJECT_PATH", "")

    payload = {
        "task": task,
        "project_id": project_id,
        "project_path": project_path,
        "context": {
            **(context or {}),
            "goal": goal,
            "spawned_by": os.getenv("JOB_ID", os.getenv("SESSION_ID", "unknown")),
        },
    }

    try:
        r = httpx.post(
            f"{gateway_url}/trigger",
            json=payload,
            timeout=30.0,
        )
        r.raise_for_status()
        data = r.json()
        job_name = data.get("job_name", "unknown")
        return f"Sub-agent spawned: job_name={job_name}"
    except httpx.HTTPStatusError as exc:
        return f"Error spawning sub-agent (HTTP {exc.response.status_code}): {exc.response.text}"
    except Exception as exc:
        return f"Error spawning sub-agent: {exc}"
