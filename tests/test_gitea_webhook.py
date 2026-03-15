"""Unit tests for providers/gitea/webhook.py"""
import hashlib
import hmac

import pytest

from providers.gitea.webhook import verify_webhook, parse_webhook_event
from providers.base import PushEvent, MREvent, CommentEvent


SECRET = "mysecret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── verify_webhook ────────────────────────────────────────────────────────────

def test_verify_webhook_valid():
    body = b'{"test": true}'
    headers = {"X-Gitea-Signature": _sign(body)}
    assert verify_webhook(headers, body, SECRET) is True


def test_verify_webhook_invalid_sig():
    body = b'{"test": true}'
    headers = {"X-Gitea-Signature": "deadbeef"}
    assert verify_webhook(headers, body, SECRET) is False


def test_verify_webhook_missing_header():
    assert verify_webhook({}, b"body", SECRET) is False


def test_verify_webhook_lowercase_header():
    body = b'{"test": true}'
    headers = {"x-gitea-signature": _sign(body)}
    assert verify_webhook(headers, body, SECRET) is True


# ── parse_webhook_event — PushEvent ───────────────────────────────────────────

PUSH_PAYLOAD = {
    "ref": "refs/heads/main",
    "repository": {"id": 1, "full_name": "alice/myrepo"},
    "pusher": {"login": "alice"},
    "commits": [
        {
            "id": "abc123",
            "message": "Fix bug\n\nDetails",
            "author": {"name": "Alice"},
        }
    ],
}


def test_parse_push_event():
    headers = {"X-Gitea-Event": "push"}
    event = parse_webhook_event(headers, PUSH_PAYLOAD)
    assert isinstance(event, PushEvent)
    assert event.branch == "main"
    assert event.project_id == "alice/myrepo"
    assert event.actor == "alice"
    assert len(event.commits) == 1
    assert event.commits[0].sha == "abc123"
    assert event.commits[0].title == "Fix bug"


# ── parse_webhook_event — MREvent ─────────────────────────────────────────────

PR_PAYLOAD = {
    "action": "opened",
    "pull_request": {
        "number": 3,
        "title": "Add feature",
        "body": "Implements the feature",
        "html_url": "https://gitea.example.com/alice/myrepo/pulls/3",
        "head": {"label": "feature-branch"},
        "base": {"label": "main"},
    },
    "repository": {"id": 1, "full_name": "alice/myrepo"},
    "sender": {"login": "alice"},
}


def test_parse_pr_event():
    headers = {"X-Gitea-Event": "pull_request"}
    event = parse_webhook_event(headers, PR_PAYLOAD)
    assert isinstance(event, MREvent)
    assert event.project_id == "alice/myrepo"
    assert event.actor == "alice"
    assert event.action == "opened"
    assert event.mr.iid == 3
    assert event.mr.source_branch == "feature-branch"
    assert event.mr.target_branch == "main"


# ── parse_webhook_event — CommentEvent ────────────────────────────────────────

COMMENT_PAYLOAD = {
    "action": "created",
    "issue": {
        "number": 3,
        "pull_request": {"merged": False},
    },
    "comment": {
        "id": 77,
        "body": "LGTM!",
    },
    "repository": {"id": 1, "full_name": "alice/myrepo"},
    "sender": {"login": "bob"},
}


def test_parse_issue_comment_on_pr():
    headers = {"X-Gitea-Event": "issue_comment"}
    event = parse_webhook_event(headers, COMMENT_PAYLOAD)
    assert isinstance(event, CommentEvent)
    assert event.project_id == "alice/myrepo"
    assert event.actor == "bob"
    assert event.body == "LGTM!"
    assert event.mr_iid == 3
    assert event.note_id == 77


def test_parse_issue_comment_on_plain_issue_returns_none():
    headers = {"X-Gitea-Event": "issue_comment"}
    payload = {
        **COMMENT_PAYLOAD,
        "issue": {"number": 1},  # no pull_request key
    }
    event = parse_webhook_event(headers, payload)
    assert event is None


# ── unknown events ────────────────────────────────────────────────────────────

def test_parse_unknown_event_returns_none():
    headers = {"X-Gitea-Event": "release"}
    assert parse_webhook_event(headers, {}) is None


def test_parse_missing_event_header_returns_none():
    assert parse_webhook_event({}, {}) is None
