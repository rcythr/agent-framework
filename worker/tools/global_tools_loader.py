"""
Load agent tool definitions from global-config/tools/*.py.

Each Python file in that directory must expose a get_tool() -> dict function
returning a tool dict with keys: name, description, parameters, execute.
"""
import importlib.util
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_GLOBAL_CONFIG_DIR = "global-config"


def load_global_tools(global_config_dir: str | None = None) -> list[dict]:
    """Return tool dicts loaded from global-config/tools/*.py."""
    config_dir = global_config_dir or os.getenv(
        "GLOBAL_CONFIG_DIR", _DEFAULT_GLOBAL_CONFIG_DIR
    )
    tools_dir = Path(config_dir) / "tools"
    if not tools_dir.exists():
        return []

    tools: list[dict] = []
    for py_file in sorted(tools_dir.glob("*.py")):
        try:
            spec = importlib.util.spec_from_file_location(
                f"_global_tool_{py_file.stem}", py_file
            )
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            if hasattr(mod, "get_tool"):
                tools.append(mod.get_tool())
            else:
                logger.warning(
                    "Tool file %s has no get_tool() function; skipping", py_file
                )
        except Exception as exc:
            logger.warning("Failed to load tool from %s: %s", py_file, exc)

    return tools
