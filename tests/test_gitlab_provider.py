"""Unit tests for providers/gitlab/provider.py"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from gitlab.exceptions import GitlabGetError

from providers.gitlab.provider import GitLabProvider
from providers.base import (
    FileContent, CommitResult, MRResult, MergeRequest,
    PushEvent, MREvent, CommentEvent,
)


@pytest.fixture
def mock_gl():
    with patch("providers.gitlab.provider.gitlab.Gitlab") as mock_gitlab_cls:
        mock_gl_instance = MagicMock()
        mock_gitlab_cls.return_value = mock_gl_instance
        yield mock_gl_instance


@pytest.fixture
def provider(mock_gl):
    return GitLabProvider(url="https://gitlab.example.com", token="token123")


# ── get_file ──────────────────────────────────────────────────────────────────

def test_get_file_returns_file_content(provider, mock_gl):
    mock_project = MagicMock()
    mock_gl.projects.get.return_value = mock_project
    mock_file = MagicMock()
    mock_file.decode.return_value = b"print('hello')"
    mock_project.files.get.return_value = mock_file

    result = provider.get_file(1, "main.py", "main")

    assert isinstance(result, FileContent)
    assert result.path == "main.py"
    assert result.content == "print('hello')"
    assert result.ref == "main"
    mock_project.files.get.assert_called_once_with(file_path="main.py", ref="main")


def test_get_file_returns_none_on_not_found(provider, mock_gl):
    mock_project = MagicMock()
    mock_gl.projects.get.return_value = mock_project
    mock_project.files.get.side_effect = GitlabGetError("not found", 404)

    result = provider.get_file(1, "missing.py", "main")
    assert result is None


# ── commit_file ───────────────────────────────────────────────────────────────

def test_commit_file_update_success(provider, mock_gl):
    mock_project = MagicMock()
    mock_gl.projects.get.return_value = mock_project
    mock_branch = MagicMock()
    mock_branch.commit = {"id": "deadbeef"}
    mock_project.branches.get.return_value = mock_branch

    result = provider.commit_file(1, "main", "foo.py", "content", "msg")

    assert isinstance(result, CommitResult)
    assert result.sha == "deadbeef"
    assert result.branch == "main"
    mock_project.files.update.assert_called_once()


def test_commit_file_falls_back_to_create_on_get_error(provider, mock_gl):
    mock_project = MagicMock()
    mock_gl.projects.get.return_value = mock_project
    mock_project.files.update.side_effect = GitlabGetError("not found", 404)
    mock_branch = MagicMock()
    mock_branch.commit = {"id": "cafebabe"}
    mock_project.branches.get.return_value = mock_branch

    result = provider.commit_file(1, "main", "new.py", "content", "msg")

    assert isinstance(result, CommitResult)
    assert result.sha == "cafebabe"
    mock_project.files.create.assert_called_once()


# ── create_mr ─────────────────────────────────────────────────────────────────

def test_create_mr_returns_mr_result(provider, mock_gl):
    mock_project = MagicMock()
    mock_gl.projects.get.return_value = mock_project
    mock_mr = MagicMock()
    mock_mr.iid = 7
    mock_mr.web_url = "https://gitlab.example.com/mr/7"
    mock_project.mergerequests.create.return_value = mock_mr

    result = provider.create_mr(1, "feature", "main", "My MR", "Description")

    assert isinstance(result, MRResult)
    assert result.iid == 7
    assert result.web_url == "https://gitlab.example.com/mr/7"


# ── post_mr_comment ───────────────────────────────────────────────────────────

def test_post_mr_comment_calls_notes_create(provider, mock_gl):
    mock_project = MagicMock()
    mock_gl.projects.get.return_value = mock_project
    mock_mr = MagicMock()
    mock_project.mergerequests.get.return_value = mock_mr

    provider.post_mr_comment(1, 5, "LGTM")

    mock_mr.notes.create.assert_called_once_with({"body": "LGTM"})


# ── list_open_mrs ─────────────────────────────────────────────────────────────

def test_list_open_mrs_returns_merge_requests(provider, mock_gl):
    with patch("providers.gitlab.provider.gitlab.Gitlab") as mock_user_gl_cls:
        mock_user_gl = MagicMock()
        mock_user_gl_cls.return_value = mock_user_gl
        mock_project = MagicMock()
        mock_user_gl.projects.get.return_value = mock_project

        mock_mr = MagicMock()
        mock_mr.iid = 3
        mock_mr.title = "Feature"
        mock_mr.description = "Adds feature"
        mock_mr.source_branch = "feat"
        mock_mr.target_branch = "main"
        mock_mr.web_url = "https://gitlab.example.com/mr/3"
        mock_project.mergerequests.list.return_value = [mock_mr]

        result = provider.list_open_mrs(1, "user-token")

    assert len(result) == 1
    assert isinstance(result[0], MergeRequest)
    assert result[0].iid == 3


# ── search_projects ───────────────────────────────────────────────────────────

def test_search_projects_returns_dicts(provider, mock_gl):
    with patch("providers.gitlab.provider.gitlab.Gitlab") as mock_user_gl_cls:
        mock_user_gl = MagicMock()
        mock_user_gl_cls.return_value = mock_user_gl

        mock_project = MagicMock()
        mock_project.id = 1
        mock_project.name = "MyProject"
        mock_project.path_with_namespace = "group/my-project"
        mock_project.web_url = "https://gitlab.example.com/group/my-project"
        mock_user_gl.projects.list.return_value = [mock_project]

        result = provider.search_projects("my", "user-token")

    assert len(result) == 1
    assert result[0]["id"] == 1
    assert result[0]["name"] == "MyProject"
