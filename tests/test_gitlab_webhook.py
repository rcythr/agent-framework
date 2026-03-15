"""Unit tests for providers/gitlab/webhook.py"""
import pytest
from providers.gitlab.webhook import verify_webhook, parse_webhook_event
from providers.base import PushEvent, MREvent, CommentEvent


# ── verify_webhook ────────────────────────────────────────────────────────────

def test_verify_webhook_valid():
    headers = {"X-Gitlab-Token": "mysecret"}
    assert verify_webhook(headers, b"body", "mysecret") is True


def test_verify_webhook_invalid():
    headers = {"X-Gitlab-Token": "wrongtoken"}
    assert verify_webhook(headers, b"body", "mysecret") is False


def test_verify_webhook_missing_header():
    assert verify_webhook({}, b"body", "mysecret") is False


# ── parse_webhook_event — PushEvent ───────────────────────────────────────────

PUSH_PAYLOAD = {
    "ref": "refs/heads/main",
    "project_id": 42,
    "user_username": "alice",
    "commits": [
        {
            "id": "abc123",
            "title": "Fix bug",
            "author": {"name": "Alice"},
        }
    ],
}


def test_parse_push_event():
    headers = {"X-Gitlab-Event": "Push Hook"}
    event = parse_webhook_event(headers, PUSH_PAYLOAD)
    assert isinstance(event, PushEvent)
    assert event.branch == "main"
    assert event.project_id == 42
    assert event.actor == "alice"
    assert len(event.commits) == 1
    assert event.commits[0].sha == "abc123"
    assert event.commits[0].title == "Fix bug"


def test_parse_push_event_actor_from_user_username():
    headers = {"X-Gitlab-Event": "Push Hook"}
    payload = {**PUSH_PAYLOAD, "user_username": "bob"}
    event = parse_webhook_event(headers, payload)
    assert event.actor == "bob"


# ── parse_webhook_event — MREvent ─────────────────────────────────────────────

MR_PAYLOAD = {
    "project": {"id": 7},
    "user": {"username": "bob"},
    "object_attributes": {
        "iid": 5,
        "title": "Add feature",
        "description": "Adds the feature",
        "source_branch": "feature-branch",
        "target_branch": "main",
        "url": "https://gitlab.example.com/mr/5",
        "action": "open",
    },
}


def test_parse_mr_event():
    headers = {"X-Gitlab-Event": "Merge Request Hook"}
    event = parse_webhook_event(headers, MR_PAYLOAD)
    assert isinstance(event, MREvent)
    assert event.project_id == 7
    assert event.actor == "bob"
    assert event.action == "open"
    assert event.mr.iid == 5
    assert event.mr.source_branch == "feature-branch"
    assert event.mr.target_branch == "main"


# ── parse_webhook_event — CommentEvent ────────────────────────────────────────

COMMENT_PAYLOAD = {
    "project_id": 10,
    "user": {"username": "carol"},
    "object_attributes": {
        "id": 99,
        "note": "LGTM!",
    },
    "merge_request": {"iid": 3},
}


def test_parse_comment_event():
    headers = {"X-Gitlab-Event": "Note Hook"}
    event = parse_webhook_event(headers, COMMENT_PAYLOAD)
    assert isinstance(event, CommentEvent)
    assert event.project_id == 10
    assert event.actor == "carol"
    assert event.body == "LGTM!"
    assert event.mr_iid == 3
    assert event.note_id == 99


def test_parse_comment_event_no_mr():
    headers = {"X-Gitlab-Event": "Note Hook"}
    payload = {
        "project_id": 10,
        "user": {"username": "carol"},
        "object_attributes": {"id": 99, "note": "Hello"},
    }
    event = parse_webhook_event(headers, payload)
    assert isinstance(event, CommentEvent)
    assert event.mr_iid is None


# ── parse_webhook_event — unknown types ───────────────────────────────────────

def test_parse_unknown_event_returns_none():
    headers = {"X-Gitlab-Event": "Pipeline Hook"}
    assert parse_webhook_event(headers, {}) is None


def test_parse_missing_event_header_returns_none():
    assert parse_webhook_event({}, {}) is None
