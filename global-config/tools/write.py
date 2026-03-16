"""
Core tool: write — write content to a file on the local filesystem.
"""
import os


def get_tool() -> dict:
    return {
        "name": "write",
        "description": (
            "Write content to a file on the local filesystem, creating the file "
            "(and any missing parent directories) if it does not exist, or "
            "overwriting it if it does."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write (absolute or relative to working directory).",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
        "execute": _execute,
    }


def _execute(path: str, content: str) -> str:
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return f"Written {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error writing {path}: {exc}"
