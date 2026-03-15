import pytest
from unittest.mock import MagicMock

from providers.gitlab.toolkit import GitLabToolkit
from providers.base import (
    FileContent, CommitResult, MRResult, RepositoryProvider,
)


def _make_provider() -> MagicMock:
    provider = MagicMock(spec=RepositoryProvider)
    provider.get_file.return_value = FileContent(path="README.md", content="hi", ref="main")
    provider.commit_file.return_value = CommitResult(sha="abc123", branch="main")
    provider.create_mr.return_value = MRResult(iid=1, web_url="https://gitlab.com/mr/1")
    provider.post_mr_comment.return_value = None
    provider.post_inline_comment.return_value = None
    provider.get_mr_diff.return_value = "diff content"
    provider.update_pipeline_status.return_value = None
    return provider


def _get_tool(toolkit: GitLabToolkit, name: str) -> dict:
    tools = {t["name"]: t for t in toolkit.get_tools()}
    assert name in tools, f"Tool '{name}' not found in toolkit"
    return tools[name]


PROJECT_ID = 42


def test_get_file_calls_provider():
    provider = _make_provider()
    toolkit = GitLabToolkit(provider=provider, project_id=PROJECT_ID)
    tool = _get_tool(toolkit, "get_file")

    tool["execute"](path="src/main.py", ref="main")

    provider.get_file.assert_called_once_with(PROJECT_ID, "src/main.py", "main")


def test_commit_file_calls_provider():
    provider = _make_provider()
    toolkit = GitLabToolkit(provider=provider, project_id=PROJECT_ID)
    tool = _get_tool(toolkit, "commit_file")

    tool["execute"](branch="feat", path="a.py", content="x=1", message="add a")

    provider.commit_file.assert_called_once_with(PROJECT_ID, "feat", "a.py", "x=1", "add a")


def test_create_mr_calls_provider():
    provider = _make_provider()
    toolkit = GitLabToolkit(provider=provider, project_id=PROJECT_ID)
    tool = _get_tool(toolkit, "create_mr")

    tool["execute"](source_branch="feat", target_branch="main", title="T", description="D")

    provider.create_mr.assert_called_once_with(PROJECT_ID, "feat", "main", "T", "D")


def test_post_mr_comment_calls_provider():
    provider = _make_provider()
    toolkit = GitLabToolkit(provider=provider, project_id=PROJECT_ID)
    tool = _get_tool(toolkit, "post_mr_comment")

    tool["execute"](mr_iid=7, body="looks good")

    provider.post_mr_comment.assert_called_once_with(PROJECT_ID, 7, "looks good")


def test_post_inline_comment_calls_provider():
    provider = _make_provider()
    toolkit = GitLabToolkit(provider=provider, project_id=PROJECT_ID)
    tool = _get_tool(toolkit, "post_inline_comment")

    tool["execute"](mr_iid=7, path="src/main.py", line=10, body="nit")

    provider.post_inline_comment.assert_called_once_with(PROJECT_ID, 7, "src/main.py", 10, "nit")


def test_get_mr_diff_calls_provider():
    provider = _make_provider()
    toolkit = GitLabToolkit(provider=provider, project_id=PROJECT_ID)
    tool = _get_tool(toolkit, "get_mr_diff")

    tool["execute"](mr_iid=5)

    provider.get_mr_diff.assert_called_once_with(PROJECT_ID, 5)


def test_update_pipeline_status_calls_provider():
    provider = _make_provider()
    toolkit = GitLabToolkit(provider=provider, project_id=PROJECT_ID)
    tool = _get_tool(toolkit, "update_pipeline_status")

    tool["execute"](sha="deadbeef", state="success", description="All good")

    provider.update_pipeline_status.assert_called_once_with(
        PROJECT_ID, "deadbeef", "success", "All good"
    )


def test_all_required_tools_present():
    provider = _make_provider()
    toolkit = GitLabToolkit(provider=provider, project_id=PROJECT_ID)
    names = {t["name"] for t in toolkit.get_tools()}
    required = {
        "get_file", "commit_file", "create_mr", "post_mr_comment",
        "post_inline_comment", "get_mr_diff", "update_pipeline_status",
    }
    assert required == names
