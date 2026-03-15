"""Unit tests for providers/github/webhook.py"""
import hashlib
import hmac
import json

import pytest

from providers.github.webhook import verify_webhook, parse_webhook_event
from providers.base import PushEvent, MREvent, CommentEvent


SECRET = "mysecret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


# ── verify_webhook ────────────────────────────────────────────────────────────

def test_verify_webhook_valid():
    body = b'{"test": true}'
    headers = {"X-Hub-Signature-256": _sign(body)}
    assert verify_webhook(headers, body, SECRET) is True


def test_verify_webhook_invalid_sig():
    body = b'{"test": true}'
    headers = {"X-Hub-Signature-256": "sha256=deadbeef"}
    assert verify_webhook(headers, body, SECRET) is False


def test_verify_webhook_missing_header():
    assert verify_webhook({}, b"body", SECRET) is False


def test_verify_webhook_wrong_secret():
    body = b'{"test": true}'
    headers = {"X-Hub-Signature-256": _sign(body, "wrongsecret")}
    assert verify_webhook(headers, body, SECRET) is False


def test_verify_webhook_lowercase_header():
    body = b'{"test": true}'
    headers = {"x-hub-signature-256": _sign(body)}
    assert verify_webhook(headers, body, SECRET) is True


# ── parse_webhook_event — PushEvent ───────────────────────────────────────────

PUSH_PAYLOAD = {
    "ref": "refs/heads/main",
    "after": "abc123def456",
    "repository": {"id": 1, "full_name": "alice/myrepo"},
    "pusher": {"name": "alice"},
    "commits": [
        {
            "id": "abc123",
            "message": "Fix bug\n\nDetails here",
            "author": {"name": "Alice"},
        }
    ],
}


def test_parse_push_event():
    headers = {"X-GitHub-Event": "push"}
    event = parse_webhook_event(headers, PUSH_PAYLOAD)
    assert isinstance(event, PushEvent)
    assert event.branch == "main"
    assert event.project_id == "alice/myrepo"
    assert event.actor == "alice"
    assert len(event.commits) == 1
    assert event.commits[0].sha == "abc123"
    assert event.commits[0].title == "Fix bug"


def test_parse_push_event_delete_returns_none():
    headers = {"X-GitHub-Event": "push"}
    payload = {**PUSH_PAYLOAD, "after": "0000000000000000000000000000000000000000"}
    event = parse_webhook_event(headers, payload)
    assert event is None


def test_parse_push_event_actor_fallback_to_sender():
    headers = {"X-GitHub-Event": "push"}
    payload = {
        **PUSH_PAYLOAD,
        "pusher": {},
        "sender": {"login": "bob"},
    }
    event = parse_webhook_event(headers, payload)
    assert event.actor == "bob"


# ── parse_webhook_event — MREvent ─────────────────────────────────────────────

PR_PAYLOAD = {
    "action": "opened",
    "pull_request": {
        "number": 7,
        "title": "Add feature",
        "body": "Implements the feature",
        "html_url": "https://github.com/alice/myrepo/pull/7",
        "head": {"ref": "feature-branch"},
        "base": {"ref": "main"},
    },
    "repository": {"id": 1, "full_name": "alice/myrepo"},
    "sender": {"login": "alice"},
}


def test_parse_pr_event():
    headers = {"X-GitHub-Event": "pull_request"}
    event = parse_webhook_event(headers, PR_PAYLOAD)
    assert isinstance(event, MREvent)
    assert event.project_id == "alice/myrepo"
    assert event.actor == "alice"
    assert event.action == "opened"
    assert event.mr.iid == 7
    assert event.mr.source_branch == "feature-branch"
    assert event.mr.target_branch == "main"
    assert event.mr.web_url == "https://github.com/alice/myrepo/pull/7"


# ── parse_webhook_event — CommentEvent ────────────────────────────────────────

ISSUE_COMMENT_PAYLOAD = {
    "action": "created",
    "issue": {
        "number": 7,
        "pull_request": {"url": "https://github.com/alice/myrepo/pull/7"},
    },
    "comment": {
        "id": 42,
        "body": "LGTM!",
    },
    "repository": {"id": 1, "full_name": "alice/myrepo"},
    "sender": {"login": "bob"},
}


def test_parse_issue_comment_on_pr():
    headers = {"X-GitHub-Event": "issue_comment"}
    event = parse_webhook_event(headers, ISSUE_COMMENT_PAYLOAD)
    assert isinstance(event, CommentEvent)
    assert event.project_id == "alice/myrepo"
    assert event.actor == "bob"
    assert event.body == "LGTM!"
    assert event.mr_iid == 7
    assert event.note_id == 42


def test_parse_issue_comment_on_plain_issue_returns_none():
    headers = {"X-GitHub-Event": "issue_comment"}
    payload = {
        **ISSUE_COMMENT_PAYLOAD,
        "issue": {"number": 1},  # no pull_request key
    }
    event = parse_webhook_event(headers, payload)
    assert event is None


PR_REVIEW_COMMENT_PAYLOAD = {
    "action": "created",
    "pull_request": {"number": 7},
    "comment": {"id": 99, "body": "Nit: rename this"},
    "repository": {"id": 1, "full_name": "alice/myrepo"},
    "sender": {"login": "carol"},
}


def test_parse_pr_review_comment():
    headers = {"X-GitHub-Event": "pull_request_review_comment"}
    event = parse_webhook_event(headers, PR_REVIEW_COMMENT_PAYLOAD)
    assert isinstance(event, CommentEvent)
    assert event.mr_iid == 7
    assert event.body == "Nit: rename this"
    assert event.actor == "carol"


# ── unknown events ────────────────────────────────────────────────────────────

def test_parse_unknown_event_returns_none():
    headers = {"X-GitHub-Event": "check_run"}
    assert parse_webhook_event(headers, {}) is None


def test_parse_missing_event_header_returns_none():
    assert parse_webhook_event({}, {}) is None
