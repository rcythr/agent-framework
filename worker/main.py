import os
import json
import asyncio

from worker.agent_runner import run_agent


async def _main() -> None:
    await run_agent(
        task=os.environ["TASK"],
        project_id=int(os.environ["PROJECT_ID"]),
        context=json.loads(os.environ["TASK_CONTEXT"]),
    )


if __name__ == "__main__":
    asyncio.run(_main())
