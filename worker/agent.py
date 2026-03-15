import asyncio
import json
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Literal

from openai import AsyncOpenAI


@dataclass
class AgentEvent:
    event_type: Literal[
        "llm_query", "llm_response", "tool_call", "tool_result",
        "input_request", "input_received", "interrupted",
        "gas_updated", "out_of_gas", "complete", "error"
    ]
    payload: dict


class Agent:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model: str,
        tools: list[dict],
        system_prompt: str,
        event_handler: Callable[[AgentEvent], Awaitable[None]],
        gas_limit_input: int = 80_000,
        gas_limit_output: int = 20_000,
    ):
        self._endpoint = endpoint
        self._api_key = api_key
        self._model = model
        self._tools = tools
        self._system_prompt = system_prompt
        self._event_handler = event_handler
        self._gas_limit_input = gas_limit_input
        self._gas_limit_output = gas_limit_output

        self._gas_used_input = 0
        self._gas_used_output = 0
        self._gas_event = asyncio.Event()

        self._steer_queue: asyncio.Queue[str] = asyncio.Queue()
        self._follow_up_queue: asyncio.Queue[str] = asyncio.Queue()

    @property
    def gas_used_input(self) -> int:
        return self._gas_used_input

    @property
    def gas_used_output(self) -> int:
        return self._gas_used_output

    def steer(self, message: str) -> None:
        self._steer_queue.put_nowait(message)

    def follow_up(self, message: str) -> None:
        self._follow_up_queue.put_nowait(message)

    def add_gas(self, input_amount: int = 0, output_amount: int = 0) -> None:
        self._gas_limit_input += input_amount
        self._gas_limit_output += output_amount
        self._gas_event.set()
        self._gas_event.clear()

    def _format_tools_for_llm(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in self._tools
        ]

    def _find_tool(self, name: str) -> dict | None:
        for t in self._tools:
            if t["name"] == name:
                return t
        return None

    def _serialize_result(self, result) -> str:
        if result is None:
            return "null"
        if hasattr(result, "model_dump"):
            return json.dumps(result.model_dump())
        if isinstance(result, (dict, list)):
            return json.dumps(result)
        return str(result)

    async def run(self, initial_message: str) -> None:
        client = AsyncOpenAI(api_key=self._api_key, base_url=self._endpoint)
        llm_tools = self._format_tools_for_llm()

        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": initial_message},
        ]

        while True:
            # Gas check before each LLM call
            if (self._gas_used_input >= self._gas_limit_input
                    or self._gas_used_output >= self._gas_limit_output):
                await self._event_handler(AgentEvent(
                    event_type="out_of_gas",
                    payload={
                        "gas_used_input": self._gas_used_input,
                        "gas_used_output": self._gas_used_output,
                        "gas_limit_input": self._gas_limit_input,
                        "gas_limit_output": self._gas_limit_output,
                    },
                ))
                # Wait until gas is sufficient — handles both synchronous add_gas
                # (called inside the event handler above) and asynchronous add_gas.
                while (self._gas_used_input >= self._gas_limit_input
                       or self._gas_used_output >= self._gas_limit_output):
                    self._gas_event.clear()
                    await self._gas_event.wait()

            # LLM call
            await self._event_handler(AgentEvent(
                event_type="llm_query",
                payload={"messages": len(messages)},
            ))

            response = await client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=llm_tools,
            )

            await self._event_handler(AgentEvent(
                event_type="llm_response",
                payload={"content": response.choices[0].message.content},
            ))

            # Update gas usage
            self._gas_used_input += response.usage.prompt_tokens
            self._gas_used_output += response.usage.completion_tokens

            await self._event_handler(AgentEvent(
                event_type="gas_updated",
                payload={
                    "gas_used_input": self._gas_used_input,
                    "gas_used_output": self._gas_used_output,
                },
            ))

            msg = response.choices[0].message

            # Build assistant message for history
            assistant_msg: dict = {"role": "assistant"}
            if msg.content:
                assistant_msg["content"] = msg.content
            if msg.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(assistant_msg)

            if not msg.tool_calls:
                # Agent is idle — inject follow-up messages if any
                if not self._follow_up_queue.empty():
                    follow_up_msg = self._follow_up_queue.get_nowait()
                    messages.append({"role": "user", "content": follow_up_msg})
                    continue
                break

            # Process tool calls
            for tc in msg.tool_calls:
                await self._event_handler(AgentEvent(
                    event_type="tool_call",
                    payload={"name": tc.function.name, "arguments": tc.function.arguments},
                ))

                tool = self._find_tool(tc.function.name)
                if tool is None:
                    result_str = f"Error: unknown tool '{tc.function.name}'"
                else:
                    try:
                        args = json.loads(tc.function.arguments)
                        raw_result = tool["execute"](**args)
                        result_str = self._serialize_result(raw_result)
                    except Exception as exc:
                        result_str = f"Error: {exc}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

                await self._event_handler(AgentEvent(
                    event_type="tool_result",
                    payload={"tool_call_id": tc.id, "result": result_str},
                ))

                # Inject steer messages after this tool, before remaining tools
                while not self._steer_queue.empty():
                    steer_msg = self._steer_queue.get_nowait()
                    messages.append({"role": "user", "content": steer_msg})

        await self._event_handler(AgentEvent(
            event_type="complete",
            payload={
                "gas_used_input": self._gas_used_input,
                "gas_used_output": self._gas_used_output,
            },
        ))
