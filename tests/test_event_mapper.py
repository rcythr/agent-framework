import pytest
from providers.base import PushEvent, MREvent, CommentEvent, MergeRequest, Commit
from gateway.event_mapper import map_event_to_task


def _make_mr_event(**kwargs):
    defaults = dict(
        mr=MergeRequest(
            iid=42,
            title="Fix bug",
            description="Fixes #123",
            source_branch="fix/bug",
            target_branch="main",
            web_url="http://gitlab.localhost/group/repo/-/merge_requests/42",
        ),
        project_id=1,
        action="open",
        actor="alice",
    )
    defaults.update(kwargs)
    return MREvent(**defaults)


def _make_comment_event(**kwargs):
    defaults = dict(
        body="Please review this",
        project_id=1,
        mr_iid=42,
        note_id=99,
        actor="bob",
    )
    defaults.update(kwargs)
    return CommentEvent(**defaults)


def _make_push_event(**kwargs):
    defaults = dict(
        branch="main",
        commits=[Commit(sha="abc123", title="Initial commit", author="carol")],
        project_id=1,
        actor="carol",
    )
    defaults.update(kwargs)
    return PushEvent(**defaults)


def test_map_mr_event():
    event = _make_mr_event()
    task = map_event_to_task(event)
    assert task is not None
    assert task.task == "review_mr"
    assert task.project_id == 1
    assert task.context["mr_iid"] == 42
    assert task.context["action"] == "open"
    assert task.context["title"] == "Fix bug"
    assert task.context["source_branch"] == "fix/bug"
    assert task.context["target_branch"] == "main"
    assert task.context["actor"] == "alice"


def test_map_comment_event():
    event = _make_comment_event()
    task = map_event_to_task(event)
    assert task is not None
    assert task.task == "handle_comment"
    assert task.project_id == 1
    assert task.context["body"] == "Please review this"
    assert task.context["mr_iid"] == 42
    assert task.context["note_id"] == 99
    assert task.context["actor"] == "bob"


def test_map_push_event():
    event = _make_push_event()
    task = map_event_to_task(event)
    assert task is not None
    assert task.task == "analyze_push"
    assert task.project_id == 1
    assert task.context["branch"] == "main"
    assert len(task.context["commits"]) == 1
    assert task.context["commits"][0]["sha"] == "abc123"
    assert task.context["actor"] == "carol"


def test_map_none_returns_none():
    assert map_event_to_task(None) is None


def test_comment_event_no_mr_iid():
    event = _make_comment_event(mr_iid=None)
    task = map_event_to_task(event)
    assert task is not None
    assert task.task == "handle_comment"
    assert task.context["mr_iid"] is None
