import json
import os
import pytest
from unittest.mock import AsyncMock, patch


def test_worker_main_reads_env_and_calls_run_agent():
    """worker/main.py reads TASK, PROJECT_ID, TASK_CONTEXT and calls run_agent."""
    context = {"mr_iid": 5, "source_branch": "feat", "target_branch": "main", "description": "x"}

    with patch.dict(os.environ, {
        "TASK": "review_mr",
        "PROJECT_ID": "42",
        "TASK_CONTEXT": json.dumps(context),
    }), patch("worker.agent_runner.run_agent", new_callable=AsyncMock) as mock_run:
        # Execute main module logic directly
        import asyncio
        import worker.main as wm
        asyncio.run(wm._main())

        mock_run.assert_called_once_with(
            task="review_mr",
            project_id=42,
            context=context,
        )
