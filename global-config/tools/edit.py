"""
Core tool: edit — replace an exact string in a file on the local filesystem.
"""


def get_tool() -> dict:
    return {
        "name": "edit",
        "description": (
            "Replace an exact substring in a file with new content. "
            "The old_string must appear exactly once in the file; the tool "
            "returns an error if it is absent or appears more than once. "
            "Use the read tool first to confirm the exact text to replace."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit.",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find and replace (must match the file verbatim).",
                },
                "new_string": {
                    "type": "string",
                    "description": "Text to substitute in place of old_string.",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
        "execute": _execute,
    }


def _execute(path: str, old_string: str, new_string: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except Exception as exc:
        return f"Error reading {path}: {exc}"

    count = content.count(old_string)
    if count == 0:
        return f"Error: old_string not found in {path}"
    if count > 1:
        return (
            f"Error: old_string appears {count} times in {path}; "
            "provide more surrounding context to make it unique"
        )

    new_content = content.replace(old_string, new_string, 1)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_content)
    except Exception as exc:
        return f"Error writing {path}: {exc}"

    return f"Replaced 1 occurrence in {path}"
