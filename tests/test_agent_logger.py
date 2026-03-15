import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from worker.agent import AgentEvent
from worker.agent_logger import AgentLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger(job_id="job-1", gateway_url="http://gateway", model="gpt-test", tool_names=None):
    return AgentLogger(
        job_id=job_id,
        gateway_url=gateway_url,
        model=model,
        tool_names=tool_names or ["echo", "search"],
    )


async def _handle_and_drain(logger: AgentLogger, event: AgentEvent) -> dict:
    """Handle an event and drain pending tasks, returning the POSTed payload."""
    posted = []

    async def fake_post(event_obj):
        posted.append(event_obj)

    with patch.object(logger, "_post_event", side_effect=fake_post):
        await logger.handle_event(event)
        await asyncio.sleep(0)

    assert len(posted) == 1
    return posted[0]


# ---------------------------------------------------------------------------
# llm_query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_query_payload_includes_model_and_tools():
    logger = _make_logger(model="gpt-4o", tool_names=["echo", "search"])
    event = AgentEvent(event_type="llm_query", payload={"messages": 3})
    log_event = await _handle_and_drain(logger, event)
    assert log_event.event_type == "llm_query"
    assert log_event.payload["model"] == "gpt-4o"
    assert log_event.payload["tools"] == ["echo", "search"]
    assert log_event.payload["messages"] == 3


# ---------------------------------------------------------------------------
# llm_response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_response_payload_has_content_and_token_fields():
    logger = _make_logger()
    event = AgentEvent(
        event_type="llm_response",
        payload={"content": "Here is the answer."},
    )
    log_event = await _handle_and_drain(logger, event)
    assert log_event.event_type == "llm_response"
    assert log_event.payload["content"] == "Here is the answer."
    assert "tool_calls" in log_event.payload
    assert "input_tokens" in log_event.payload
    assert "output_tokens" in log_event.payload


# ---------------------------------------------------------------------------
# tool_call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_call_payload_translates_name_to_tool_name():
    logger = _make_logger()
    event = AgentEvent(
        event_type="tool_call",
        payload={"name": "echo", "arguments": json.dumps({"text": "hello"})},
    )
    log_event = await _handle_and_drain(logger, event)
    assert log_event.event_type == "tool_call"
    assert log_event.payload["tool_name"] == "echo"
    assert log_event.payload["arguments"] == {"text": "hello"}


@pytest.mark.asyncio
async def test_tool_call_payload_with_tool_name_key():
    logger = _make_logger()
    event = AgentEvent(
        event_type="tool_call",
        payload={"tool_name": "search", "arguments": {"query": "foo"}},
    )
    log_event = await _handle_and_drain(logger, event)
    assert log_event.payload["tool_name"] == "search"
    assert log_event.payload["arguments"] == {"query": "foo"}


# ---------------------------------------------------------------------------
# tool_result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_result_payload_includes_tool_name_and_duration():
    logger = _make_logger()
    # First emit tool_call to set pending tool name
    call_event = AgentEvent(
        event_type="tool_call",
        payload={"name": "echo", "arguments": "{}"},
    )
    result_event = AgentEvent(
        event_type="tool_result",
        payload={"tool_call_id": "tc-1", "result": "echo:hello"},
    )

    posted = []

    async def fake_post(event_obj):
        posted.append(event_obj)

    with patch.object(logger, "_post_event", side_effect=fake_post):
        await logger.handle_event(call_event)
        await asyncio.sleep(0)
        await logger.handle_event(result_event)
        await asyncio.sleep(0)

    assert len(posted) == 2
    tool_result_log = posted[1]
    assert tool_result_log.event_type == "tool_result"
    assert tool_result_log.payload["tool_name"] == "echo"
    assert tool_result_log.payload["result"] == "echo:hello"
    assert "duration_ms" in tool_result_log.payload
    assert isinstance(tool_result_log.payload["duration_ms"], int)


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_payload_shape():
    logger = _make_logger()
    event = AgentEvent(event_type="complete", payload={"gas_used_input": 100, "gas_used_output": 50})
    log_event = await _handle_and_drain(logger, event)
    assert log_event.event_type == "complete"
    assert "summary" in log_event.payload
    assert "total_llm_calls" in log_event.payload
    assert "total_tool_calls" in log_event.payload


# ---------------------------------------------------------------------------
# error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_error_payload_shape():
    logger = _make_logger()
    event = AgentEvent(
        event_type="error",
        payload={"message": "Something went wrong", "traceback": "Traceback..."},
    )
    log_event = await _handle_and_drain(logger, event)
    assert log_event.event_type == "error"
    assert log_event.payload["message"] == "Something went wrong"
    assert log_event.payload["traceback"] == "Traceback..."


# ---------------------------------------------------------------------------
# gas_updated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gas_updated_payload_shape():
    logger = _make_logger()
    event = AgentEvent(
        event_type="gas_updated",
        payload={
            "gas_used_input": 500,
            "gas_used_output": 100,
            "gas_limit_input": 1000,
            "gas_limit_output": 500,
        },
    )
    log_event = await _handle_and_drain(logger, event)
    assert log_event.event_type == "gas_updated"
    p = log_event.payload
    assert p["gas_used_input"] == 500
    assert p["gas_used_output"] == 100
    assert p["gas_limit_input"] == 1000
    assert p["gas_limit_output"] == 500
    assert "input_tokens" in p
    assert "output_tokens" in p


# ---------------------------------------------------------------------------
# out_of_gas — exhausted field
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_out_of_gas_exhausted_input():
    logger = _make_logger()
    event = AgentEvent(
        event_type="out_of_gas",
        payload={
            "gas_used_input": 1000,
            "gas_limit_input": 1000,
            "gas_used_output": 10,
            "gas_limit_output": 500,
        },
    )
    log_event = await _handle_and_drain(logger, event)
    assert log_event.payload["exhausted"] == "input"


@pytest.mark.asyncio
async def test_out_of_gas_exhausted_output():
    logger = _make_logger()
    event = AgentEvent(
        event_type="out_of_gas",
        payload={
            "gas_used_input": 100,
            "gas_limit_input": 1000,
            "gas_used_output": 500,
            "gas_limit_output": 500,
        },
    )
    log_event = await _handle_and_drain(logger, event)
    assert log_event.payload["exhausted"] == "output"


# ---------------------------------------------------------------------------
# Fire-and-forget: slow gateway does not block
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fire_and_forget_slow_gateway_does_not_block():
    """handle_event returns promptly even if the gateway is slow."""
    logger = _make_logger()
    event = AgentEvent(event_type="complete", payload={})

    async def slow_post(event_obj):
        await asyncio.sleep(10)  # simulate slow gateway

    with patch.object(logger, "_post_event", side_effect=slow_post):
        # Should return quickly — the task is scheduled, not awaited
        await asyncio.wait_for(logger.handle_event(event), timeout=1.0)


@pytest.mark.asyncio
async def test_gateway_timeout_is_swallowed():
    """A timeout/error from the gateway does not raise in handle_event."""
    logger = _make_logger()
    event = AgentEvent(event_type="complete", payload={})

    async def failing_post(event_obj):
        raise Exception("gateway unreachable")

    with patch.object(logger, "_post_event", side_effect=failing_post):
        # Should not raise
        await logger.handle_event(event)
        await asyncio.sleep(0)  # let the background task run


# ---------------------------------------------------------------------------
# Sequence numbers: monotonically increasing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sequence_numbers_monotonically_increasing():
    logger = _make_logger()
    posted = []

    async def fake_post(event_obj):
        posted.append(event_obj)

    event_types = ["llm_query", "llm_response", "gas_updated", "tool_call", "tool_result", "complete"]
    events = [
        AgentEvent(event_type=et, payload={})
        for et in event_types
    ]

    with patch.object(logger, "_post_event", side_effect=fake_post):
        for e in events:
            await logger.handle_event(e)
        await asyncio.sleep(0)

    sequences = [p.sequence for p in posted]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)  # all unique


@pytest.mark.asyncio
async def test_sequence_numbers_concurrent():
    """Concurrent handle_event calls still produce unique, monotonically increasing sequences."""
    logger = _make_logger()
    posted = []
    lock = asyncio.Lock()

    async def fake_post(event_obj):
        async with lock:
            posted.append(event_obj)

    events = [AgentEvent(event_type="complete", payload={}) for _ in range(20)]

    with patch.object(logger, "_post_event", side_effect=fake_post):
        await asyncio.gather(*[logger.handle_event(e) for e in events])
        await asyncio.sleep(0)

    sequences = sorted(p.sequence for p in posted)
    assert sequences == list(range(20))
