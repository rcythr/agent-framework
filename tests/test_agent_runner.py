import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------

def test_build_system_prompt_includes_task_type():
    from worker.agent_runner import build_system_prompt
    prompt = build_system_prompt("review_mr")
    assert "review_mr" in prompt


def test_build_system_prompt_includes_handle_comment():
    from worker.agent_runner import build_system_prompt
    prompt = build_system_prompt("handle_comment")
    assert "handle_comment" in prompt


# ---------------------------------------------------------------------------
# build_task_message
# ---------------------------------------------------------------------------

def test_build_task_message_review_mr():
    from worker.agent_runner import build_task_message
    context = {
        "mr_iid": 7,
        "source_branch": "feat/x",
        "target_branch": "main",
        "description": "adds feature x",
    }
    msg = build_task_message("review_mr", context)
    assert "7" in msg
    assert "feat/x" in msg
    assert "main" in msg
    assert "adds feature x" in msg


def test_build_task_message_handle_comment():
    from worker.agent_runner import build_task_message
    context = {
        "note_body": "please fix this",
        "mr_iid": 3,
        "note_id": 99,
    }
    msg = build_task_message("handle_comment", context)
    assert "please fix this" in msg
    assert "3" in msg
    assert "99" in msg


def test_build_task_message_analyze_push():
    from worker.agent_runner import build_task_message
    context = {
        "branch": "main",
        "commits": [{"sha": "abc", "title": "init", "author": "dev"}],
    }
    msg = build_task_message("analyze_push", context)
    assert "main" in msg
    assert "abc" in msg or "init" in msg


def test_build_task_message_unknown_task():
    from worker.agent_runner import build_task_message
    context = {"foo": "bar"}
    msg = build_task_message("unknown_task_type", context)
    assert isinstance(msg, str)
    assert len(msg) > 0


# ---------------------------------------------------------------------------
# run_agent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_agent_constructs_agent_with_correct_arguments():
    """run_agent builds Agent with correct endpoint, model, tools, system_prompt."""
    with patch("worker.agent_runner.Agent") as mock_agent_cls, \
         patch("worker.agent_runner.get_toolkit") as mock_get_toolkit, \
         patch("worker.agent_runner.httpx") as mock_httpx, \
         patch.dict(os.environ, {
             "LLM_ENDPOINT": "http://llm/v1",
             "OPENAI_API_KEY": "test-key",
             "LLM_MODEL": "gpt-test",
             "JOB_ID": "job-123",
             "GATEWAY_URL": "http://gateway",
         }):
        mock_toolkit = MagicMock()
        mock_toolkit.get_tools.return_value = [{"name": "echo"}]
        mock_get_toolkit.return_value = mock_toolkit

        mock_agent_instance = MagicMock()
        mock_agent_instance.run = AsyncMock()
        mock_agent_cls.return_value = mock_agent_instance

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)
        mock_http_client.post = AsyncMock(return_value=mock_resp)
        mock_httpx.AsyncClient.return_value = mock_http_client

        from worker.agent_runner import run_agent
        await run_agent(
            task="review_mr",
            project_id=1,
            context={"mr_iid": 1, "source_branch": "feat", "target_branch": "main", "description": "d"},
        )

        mock_agent_cls.assert_called_once()
        kwargs = mock_agent_cls.call_args.kwargs
        assert kwargs["endpoint"] == "http://llm/v1"
        assert kwargs["api_key"] == "test-key"
        assert kwargs["model"] == "gpt-test"
        assert kwargs["tools"] == [{"name": "echo"}]
        assert isinstance(kwargs["system_prompt"], str)
        mock_agent_instance.run.assert_called_once()


@pytest.mark.asyncio
async def test_run_agent_posts_completed_status_on_success():
    """run_agent POSTs completed status to gateway on success."""
    with patch("worker.agent_runner.Agent") as mock_agent_cls, \
         patch("worker.agent_runner.get_toolkit") as mock_get_toolkit, \
         patch("worker.agent_runner.httpx") as mock_httpx, \
         patch.dict(os.environ, {
             "JOB_ID": "job-abc",
             "GATEWAY_URL": "http://gateway",
         }):
        mock_toolkit = MagicMock()
        mock_toolkit.get_tools.return_value = []
        mock_get_toolkit.return_value = mock_toolkit

        mock_agent_instance = MagicMock()
        mock_agent_instance.run = AsyncMock()
        mock_agent_cls.return_value = mock_agent_instance

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)
        mock_http_client.post = AsyncMock(return_value=mock_resp)
        mock_httpx.AsyncClient.return_value = mock_http_client

        from worker.agent_runner import run_agent
        await run_agent(task="review_mr", project_id=1, context={"mr_iid": 1})

        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url") or call_args.args[0]
        assert "job-abc" in url
        assert "completed" in str(call_args)


@pytest.mark.asyncio
async def test_run_agent_posts_failed_status_on_exception():
    """run_agent POSTs failed status to gateway when Agent.run raises."""
    with patch("worker.agent_runner.Agent") as mock_agent_cls, \
         patch("worker.agent_runner.get_toolkit") as mock_get_toolkit, \
         patch("worker.agent_runner.httpx") as mock_httpx, \
         patch.dict(os.environ, {
             "JOB_ID": "job-xyz",
             "GATEWAY_URL": "http://gateway",
         }):
        mock_toolkit = MagicMock()
        mock_toolkit.get_tools.return_value = []
        mock_get_toolkit.return_value = mock_toolkit

        mock_agent_instance = MagicMock()
        mock_agent_instance.run = AsyncMock(side_effect=RuntimeError("boom"))
        mock_agent_cls.return_value = mock_agent_instance

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)
        mock_http_client.post = AsyncMock(return_value=mock_resp)
        mock_httpx.AsyncClient.return_value = mock_http_client

        from worker.agent_runner import run_agent
        # Should not raise despite Agent.run raising
        await run_agent(task="review_mr", project_id=1, context={"mr_iid": 1})

        mock_http_client.post.assert_called_once()
        assert "failed" in str(mock_http_client.post.call_args)


@pytest.mark.asyncio
async def test_run_agent_includes_result_in_status_post():
    """run_agent includes the agent's last_response as 'result' in the status POST body."""
    with patch("worker.agent_runner.Agent") as mock_agent_cls, \
         patch("worker.agent_runner.get_toolkit") as mock_get_toolkit, \
         patch("worker.agent_runner.httpx") as mock_httpx, \
         patch.dict(os.environ, {
             "JOB_ID": "job-result",
             "GATEWAY_URL": "http://gateway",
         }):
        mock_toolkit = MagicMock()
        mock_toolkit.get_tools.return_value = []
        mock_get_toolkit.return_value = mock_toolkit

        mock_agent_instance = MagicMock()
        mock_agent_instance.run = AsyncMock()
        mock_agent_instance.last_response = "Here is what I did."
        mock_agent_cls.return_value = mock_agent_instance

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_http_client = AsyncMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=None)
        mock_http_client.post = AsyncMock(return_value=mock_resp)
        mock_httpx.AsyncClient.return_value = mock_http_client

        from worker.agent_runner import run_agent
        await run_agent(task="review_mr", project_id=1, context={"mr_iid": 1})

        mock_http_client.post.assert_called_once()
        call_kwargs = mock_http_client.post.call_args.kwargs
        body = call_kwargs.get("json", {})
        assert body.get("result") == "Here is what I did."
        assert body.get("status") == "completed"
