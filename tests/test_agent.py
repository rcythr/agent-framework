import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from dataclasses import dataclass

from worker.agent import Agent, AgentEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_response(content=None, tool_calls=None, input_tokens=10, output_tokens=5):
    """Build a minimal mock resembling openai ChatCompletion response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []

    choice = MagicMock()
    choice.message = msg

    usage = MagicMock()
    usage.prompt_tokens = input_tokens
    usage.completion_tokens = output_tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_tool_call(id_, name, args: dict):
    tc = MagicMock()
    tc.id = id_
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


def _make_tools():
    executed = []

    def execute_echo(text):
        executed.append(text)
        return f"echo:{text}"

    return [
        {
            "name": "echo",
            "description": "Echoes text",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            "execute": execute_echo,
        }
    ], executed


async def _noop_handler(event: AgentEvent) -> None:
    pass


def _make_agent(tools=None, event_handler=None, gas_input=80_000, gas_output=20_000):
    if tools is None:
        tools, _ = _make_tools()
    if event_handler is None:
        event_handler = _noop_handler
    return Agent(
        endpoint="http://fake-llm/v1",
        api_key="fake-key",
        model="gpt-test",
        tools=tools,
        system_prompt="You are a helpful assistant.",
        event_handler=event_handler,
        gas_limit_input=gas_input,
        gas_limit_output=gas_output,
    )


# ---------------------------------------------------------------------------
# Tests: basic LLM call and message history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_calls_llm_with_tool_schemas():
    """Agent passes tool schemas to LLM in openai format."""
    tools, _ = _make_tools()
    agent = _make_agent(tools=tools)

    no_tool_response = _make_llm_response(content="done")

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=no_tool_response)

        await agent.run("hello")

        create_call = mock_client.chat.completions.create.call_args
        passed_tools = create_call.kwargs.get("tools") or create_call.args[0] if create_call.args else None
        passed_tools = create_call.kwargs["tools"]

        assert len(passed_tools) == 1
        assert passed_tools[0]["type"] == "function"
        assert passed_tools[0]["function"]["name"] == "echo"


@pytest.mark.asyncio
async def test_agent_includes_system_prompt_in_messages():
    """Agent includes system prompt as first message."""
    agent = _make_agent()
    no_tool_response = _make_llm_response(content="done")

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=no_tool_response)

        await agent.run("hello")

        create_call = mock_client.chat.completions.create.call_args
        messages = create_call.kwargs["messages"]

        assert messages[0]["role"] == "system"
        assert "helpful assistant" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "hello"


# ---------------------------------------------------------------------------
# Tests: tool execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_executes_tool_calls_and_appends_results():
    """Agent calls tool execute() and appends tool result to conversation."""
    tools, executed = _make_tools()
    agent = _make_agent(tools=tools)

    tc = _make_tool_call("call-1", "echo", {"text": "hi"})
    tool_response = _make_llm_response(tool_calls=[tc])
    final_response = _make_llm_response(content="all done")

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[tool_response, final_response]
        )

        await agent.run("do something")

    assert "hi" in executed


@pytest.mark.asyncio
async def test_agent_loops_until_no_tool_calls():
    """Agent keeps looping until LLM returns response with no tool calls."""
    tools, executed = _make_tools()
    agent = _make_agent(tools=tools)

    tc1 = _make_tool_call("c1", "echo", {"text": "first"})
    tc2 = _make_tool_call("c2", "echo", {"text": "second"})
    responses = [
        _make_llm_response(tool_calls=[tc1]),
        _make_llm_response(tool_calls=[tc2]),
        _make_llm_response(content="finished"),
    ]

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(side_effect=responses)

        await agent.run("go")

    assert executed == ["first", "second"]
    assert mock_client.chat.completions.create.call_count == 3


# ---------------------------------------------------------------------------
# Tests: event order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_emits_events_in_correct_order():
    """Agent emits: llm_query → llm_response → gas_updated → tool_call → tool_result → complete."""
    tools, _ = _make_tools()
    agent = _make_agent(tools=tools)

    events = []

    async def capture(event: AgentEvent):
        events.append(event.event_type)

    agent._event_handler = capture

    tc = _make_tool_call("c1", "echo", {"text": "x"})
    responses = [
        _make_llm_response(tool_calls=[tc]),
        _make_llm_response(content="done"),
    ]

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(side_effect=responses)

        await agent.run("start")

    assert "llm_query" in events
    assert "llm_response" in events
    assert "gas_updated" in events
    assert "tool_call" in events
    assert "tool_result" in events
    assert "complete" in events

    # Order checks
    qi = events.index("llm_query")
    ri = events.index("llm_response")
    gi = events.index("gas_updated")
    tci = events.index("tool_call")
    tri = events.index("tool_result")
    ci = len(events) - 1 - events[::-1].index("complete")

    assert qi < ri < gi <= tci < tri < ci


# ---------------------------------------------------------------------------
# Tests: steer queue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_steer_message_injected_after_tool_before_remaining_tools():
    """Steer message appears in history after current tool, and LLM sees it before remaining tools."""
    tools, executed = _make_tools()
    agent = _make_agent(tools=tools)

    tc1 = _make_tool_call("c1", "echo", {"text": "a"})
    tc2 = _make_tool_call("c2", "echo", {"text": "b"})
    final = _make_llm_response(content="done")

    call_count = 0

    async def side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: inject steer after first tool (simulated via background task)
            return _make_llm_response(tool_calls=[tc1, tc2])
        else:
            # Second call: verify steer message is present in history
            messages = kwargs["messages"]
            roles = [m["role"] for m in messages]
            assert "user" in roles[-3:]  # steer message injected as user message
            return final

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        original_execute = tools[0]["execute"]

        def execute_with_steer(text):
            result = original_execute(text)
            if text == "a":
                agent.steer("steer signal")
            return result

        tools[0]["execute"] = execute_with_steer
        mock_client.chat.completions.create = AsyncMock(side_effect=side_effect)

        await agent.run("go")


# ---------------------------------------------------------------------------
# Tests: follow-up queue
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_follow_up_message_injected_after_agent_idle():
    """Follow-up message is injected only after agent has no pending tool calls."""
    tools, _ = _make_tools()
    agent = _make_agent(tools=tools)

    call_count = 0
    injected_follow_up = False

    async def side_effect(**kwargs):
        nonlocal call_count, injected_follow_up
        call_count += 1
        if call_count == 1:
            # Trigger follow-up after first idle
            agent.follow_up("follow this up")
            return _make_llm_response(content="idle")
        elif call_count == 2:
            # Verify the follow-up message is present
            messages = kwargs["messages"]
            user_msgs = [m for m in messages if m["role"] == "user"]
            assert any("follow this up" in m["content"] for m in user_msgs)
            injected_follow_up = True
            return _make_llm_response(content="done")

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(side_effect=side_effect)

        await agent.run("start")

    assert injected_follow_up
    assert call_count == 2


# ---------------------------------------------------------------------------
# Tests: gas
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_emits_out_of_gas_when_input_limit_reached():
    """Agent emits out_of_gas when gas_used_input >= gas_limit_input before next LLM call."""
    tools, executed = _make_tools()
    # Limit of 5 — first call uses 10 tokens, so second iteration should hit out_of_gas
    agent = _make_agent(tools=tools, gas_input=5, gas_output=20_000)

    events = []

    async def capture(event: AgentEvent):
        events.append(event.event_type)
        if event.event_type == "out_of_gas":
            # Unblock by adding gas
            agent.add_gas(input_amount=100_000)

    agent._event_handler = capture

    tc = _make_tool_call("c1", "echo", {"text": "ping"})
    # First response: tool call (causes a second iteration)
    # Second iteration: gas check fires (10 >= 5), out_of_gas emitted, add_gas unblocks
    # Third response: final answer
    responses = [
        _make_llm_response(tool_calls=[tc], input_tokens=10, output_tokens=2),
        _make_llm_response(content="done", input_tokens=1, output_tokens=1),
    ]

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(side_effect=responses)

        await agent.run("go")

    assert "out_of_gas" in events


@pytest.mark.asyncio
async def test_agent_emits_out_of_gas_when_output_limit_reached():
    """Agent emits out_of_gas when gas_used_output >= gas_limit_output before next LLM call."""
    tools, _ = _make_tools()
    agent = _make_agent(tools=tools, gas_input=80_000, gas_output=3)

    events = []

    async def capture(event: AgentEvent):
        events.append(event.event_type)
        if event.event_type == "out_of_gas":
            agent.add_gas(output_amount=100_000)

    agent._event_handler = capture

    tc = _make_tool_call("c1", "echo", {"text": "ping"})
    # First response uses 5 output tokens (> limit of 3), second iteration triggers out_of_gas
    responses = [
        _make_llm_response(tool_calls=[tc], input_tokens=1, output_tokens=5),
        _make_llm_response(content="done", input_tokens=1, output_tokens=1),
    ]

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(side_effect=responses)

        await agent.run("go")

    assert "out_of_gas" in events


@pytest.mark.asyncio
async def test_add_gas_input_increments_limit_and_resumes():
    """add_gas(input_amount=N) increments gas_limit_input and resumes suspended loop."""
    tools, _ = _make_tools()
    agent = _make_agent(tools=tools, gas_input=5, gas_output=20_000)

    async def capture(event: AgentEvent):
        if event.event_type == "out_of_gas":
            agent.add_gas(input_amount=100_000)

    agent._event_handler = capture

    tc = _make_tool_call("c1", "echo", {"text": "ping"})
    responses = [
        _make_llm_response(tool_calls=[tc], input_tokens=10, output_tokens=1),
        _make_llm_response(content="done", input_tokens=1, output_tokens=1),
    ]

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(side_effect=responses)

        await agent.run("go")

    assert agent.gas_used_input >= 10
    assert agent._gas_limit_input >= 100_005


@pytest.mark.asyncio
async def test_add_gas_output_increments_limit_and_resumes():
    """add_gas(output_amount=N) increments gas_limit_output and resumes suspended loop."""
    tools, _ = _make_tools()
    agent = _make_agent(tools=tools, gas_input=80_000, gas_output=3)

    async def capture(event: AgentEvent):
        if event.event_type == "out_of_gas":
            agent.add_gas(output_amount=100_000)

    agent._event_handler = capture

    tc = _make_tool_call("c1", "echo", {"text": "ping"})
    responses = [
        _make_llm_response(tool_calls=[tc], input_tokens=1, output_tokens=5),
        _make_llm_response(content="done", input_tokens=1, output_tokens=1),
    ]

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(side_effect=responses)

        await agent.run("go")

    assert agent._gas_limit_output >= 100_003


@pytest.mark.asyncio
async def test_gas_checked_before_llm_call_not_mid_tool():
    """Gas check happens before LLM call; tool execution is never interrupted."""
    tools, executed = _make_tools()
    agent = _make_agent(tools=tools, gas_input=80_000, gas_output=20_000)

    events = []

    async def capture(event: AgentEvent):
        events.append(event.event_type)

    agent._event_handler = capture

    tc = _make_tool_call("c1", "echo", {"text": "go"})
    responses = [
        _make_llm_response(tool_calls=[tc], input_tokens=1, output_tokens=1),
        _make_llm_response(content="done", input_tokens=1, output_tokens=1),
    ]

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(side_effect=responses)

        await agent.run("go")

    # Tool was fully executed, no out_of_gas during tool execution
    assert "tool_call" in events
    assert "tool_result" in events
    assert "out_of_gas" not in events


@pytest.mark.asyncio
async def test_gas_used_properties():
    """gas_used_input and gas_used_output are accumulated correctly."""
    tools, _ = _make_tools()
    agent = _make_agent(tools=tools)

    responses = [
        _make_llm_response(content="done", input_tokens=30, output_tokens=10),
    ]

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(side_effect=responses)

        await agent.run("go")

    assert agent.gas_used_input == 30
    assert agent.gas_used_output == 10


# ---------------------------------------------------------------------------
# Tests: last_response / result propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_last_response_captured_after_run():
    """Agent.last_response holds the final LLM text after run() completes."""
    agent = _make_agent()
    response = _make_llm_response(content="All done, here is the summary.")

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        await agent.run("start")

    assert agent.last_response == "All done, here is the summary."


@pytest.mark.asyncio
async def test_last_response_empty_before_run():
    """Agent.last_response is empty string before run() is called."""
    agent = _make_agent()
    assert agent.last_response == ""


@pytest.mark.asyncio
async def test_complete_event_includes_result():
    """complete event payload contains a 'result' key with the final text."""
    agent = _make_agent()
    complete_events = []

    async def capture(event: AgentEvent):
        if event.event_type == "complete":
            complete_events.append(event.payload)

    agent._event_handler = capture

    response = _make_llm_response(content="Final answer.")

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=response)

        await agent.run("go")

    assert len(complete_events) == 1
    assert complete_events[0]["result"] == "Final answer."


@pytest.mark.asyncio
async def test_last_response_is_last_llm_reply_after_tool_calls():
    """last_response is the final (non-tool) LLM message, not an intermediate one."""
    tools, _ = _make_tools()
    agent = _make_agent(tools=tools)

    tc = _make_tool_call("c1", "echo", {"text": "x"})
    responses = [
        _make_llm_response(tool_calls=[tc]),
        _make_llm_response(content="Finished after tool."),
    ]

    with patch("worker.agent.AsyncOpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(side_effect=responses)

        await agent.run("do it")

    assert agent.last_response == "Finished after tool."
