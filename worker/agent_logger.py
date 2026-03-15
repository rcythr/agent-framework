import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from worker.agent import AgentEvent
from shared.models import LogEvent

logger = logging.getLogger(__name__)


class AgentLogger:
    def __init__(
        self,
        job_id: str,
        gateway_url: str,
        model: str = "",
        tool_names: list[str] | None = None,
    ):
        self._job_id = job_id
        self._gateway_url = gateway_url
        self._model = model
        self._tool_names = tool_names or []
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._pending_tool_name: str = ""
        self._pending_tool_start: float = 0.0

    async def _next_sequence(self) -> int:
        async with self._lock:
            seq = self._sequence
            self._sequence += 1
            return seq

    async def handle_event(self, event: AgentEvent) -> None:
        payload = self._translate_payload(event)
        sequence = await self._next_sequence()
        log_event = LogEvent(
            job_id=self._job_id,
            sequence=sequence,
            timestamp=datetime.now(timezone.utc),
            event_type=event.event_type,
            payload=payload,
        )
        asyncio.create_task(self._post_event(log_event))

    def _translate_payload(self, event: AgentEvent) -> dict:
        et = event.event_type
        p = event.payload

        if et == "llm_query":
            return {
                "messages": p.get("messages", []),
                "model": self._model,
                "tools": self._tool_names,
            }

        if et == "llm_response":
            return {
                "content": p.get("content") or "",
                "tool_calls": p.get("tool_calls", []),
                "input_tokens": p.get("input_tokens", 0),
                "output_tokens": p.get("output_tokens", 0),
            }

        if et == "tool_call":
            name = p.get("tool_name") or p.get("name", "")
            args = p.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            self._pending_tool_name = name
            self._pending_tool_start = time.monotonic()
            return {"tool_name": name, "arguments": args}

        if et == "tool_result":
            duration_ms = int((time.monotonic() - self._pending_tool_start) * 1000)
            return {
                "tool_name": self._pending_tool_name,
                "result": p.get("result"),
                "duration_ms": duration_ms,
            }

        if et == "gas_updated":
            return {
                "gas_used_input": p.get("gas_used_input", 0),
                "gas_limit_input": p.get("gas_limit_input", 0),
                "gas_used_output": p.get("gas_used_output", 0),
                "gas_limit_output": p.get("gas_limit_output", 0),
                "input_tokens": p.get("input_tokens", 0),
                "output_tokens": p.get("output_tokens", 0),
            }

        if et == "out_of_gas":
            gas_used_input = p.get("gas_used_input", 0)
            gas_limit_input = p.get("gas_limit_input", 0)
            gas_used_output = p.get("gas_used_output", 0)
            gas_limit_output = p.get("gas_limit_output", 0)
            exhausted = "input" if gas_used_input >= gas_limit_input else "output"
            return {
                "gas_used_input": gas_used_input,
                "gas_limit_input": gas_limit_input,
                "gas_used_output": gas_used_output,
                "gas_limit_output": gas_limit_output,
                "exhausted": exhausted,
            }

        if et == "complete":
            return {
                "summary": p.get("summary", ""),
                "total_llm_calls": p.get("total_llm_calls", 0),
                "total_tool_calls": p.get("total_tool_calls", 0),
            }

        if et == "error":
            return {
                "message": p.get("message", ""),
                "traceback": p.get("traceback", ""),
            }

        if et == "input_request":
            return {"question": p.get("question", "")}

        if et == "input_received":
            return {"response": p.get("response", "")}

        if et == "interrupted":
            return {"redirect_message": p.get("redirect_message", "")}

        return dict(p)

    async def _post_event(self, event: LogEvent) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{self._gateway_url}/internal/log",
                    json=event.model_dump(mode="json"),
                )
        except Exception as exc:
            logger.debug("AgentLogger: failed to post event %s: %s", event.event_type, exc)
