"""
Core tool: bash — run a shell command on the local filesystem.
"""
import subprocess


def get_tool() -> dict:
    return {
        "name": "bash",
        "description": (
            "Run a shell command and return its output. "
            "stdout and stderr are both captured and returned together with the exit code. "
            "Use this to run tests, linters, build tools, or any other shell command."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (passed to /bin/sh -c).",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Maximum seconds to wait for the command (default: 120).",
                },
            },
            "required": ["command"],
        },
        "execute": _execute,
    }


def _execute(command: str, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr}")
        parts.append(f"[exit code: {result.returncode}]")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as exc:
        return f"Error running command: {exc}"
