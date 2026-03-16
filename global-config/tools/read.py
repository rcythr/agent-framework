"""
Core tool: read — read a file from the local filesystem.
"""


def get_tool() -> dict:
    return {
        "name": "read",
        "description": (
            "Read the contents of a file from the local filesystem. "
            "Returns the file content as a string."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read (absolute or relative to working directory).",
                },
            },
            "required": ["path"],
        },
        "execute": _execute,
    }


def _execute(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except IsADirectoryError:
        return f"Error: {path} is a directory, not a file"
    except Exception as exc:
        return f"Error reading {path}: {exc}"
