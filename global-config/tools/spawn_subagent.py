"""
Core tool: spawn_subagent — break down the current task and dispatch a sub-job.

The agent posts a new task to the gateway's /trigger endpoint.  The gateway
spawns a fresh K8s Job for the sub-task, which runs independently.  The
calling agent receives the new job's ID so it can reference it in follow-up
comments or logs.

When ``wait=True`` the tool blocks until the sub-job reaches a terminal status
and returns the sub-agent's final text response.  The parent agent should use
``wait=True`` whenever it needs the sub-agent's output before it can continue.
Use ``wait=False`` (the default) to fire-and-forget when the sub-tasks are
fully independent.
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
            "independent subtasks (e.g. 'fix bug in module A' and 'write tests for module B'). "
            "Set wait=true to block until the sub-agent finishes and receive its result; "
            "use wait=false (default) to fire-and-forget when the subtask is fully independent."
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
                "wait": {
                    "type": "boolean",
                    "description": (
                        "If true, block until the sub-agent completes and return its result. "
                        "If false (default), return immediately after spawning."
                    ),
                },
                "timeout": {
                    "type": "number",
                    "description": (
                        "Maximum seconds to wait when wait=true (default 300). "
                        "Ignored when wait=false."
                    ),
                },
            },
            "required": ["task", "goal"],
        },
        "execute": _execute,
    }


def _execute(
    task: str,
    goal: str,
    context: dict | None = None,
    wait: bool = False,
    timeout: float = 300.0,
) -> str:
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
    except httpx.HTTPStatusError as exc:
        return f"Error spawning sub-agent (HTTP {exc.response.status_code}): {exc.response.text}"
    except Exception as exc:
        return f"Error spawning sub-agent: {exc}"

    if not wait:
        return f"Sub-agent spawned: job_name={job_name}"

    # Block until the sub-job reaches a terminal status
    try:
        r = httpx.get(
            f"{gateway_url}/internal/jobs/{job_name}/await-result",
            params={"timeout": timeout},
            timeout=timeout + 10.0,  # slightly longer than the server-side timeout
        )
        r.raise_for_status()
        data = r.json()
        status = data.get("status", "unknown")
        result = data.get("result") or ""
        if result:
            return f"Sub-agent {job_name} finished ({status}):\n{result}"
        return f"Sub-agent {job_name} finished with status={status} (no result text)."
    except httpx.HTTPStatusError as exc:
        return (
            f"Sub-agent {job_name} spawned but await-result failed "
            f"(HTTP {exc.response.status_code}): {exc.response.text}"
        )
    except Exception as exc:
        return f"Sub-agent {job_name} spawned but await-result failed: {exc}"
