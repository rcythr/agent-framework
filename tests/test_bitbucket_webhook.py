"""Unit tests for providers/bitbucket/webhook.py"""
import hashlib
import hmac

import pytest

from providers.bitbucket.webhook import verify_webhook, parse_webhook_event
from providers.base import PushEvent, MREvent, CommentEvent


SECRET = "mysecret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


# ── verify_webhook ────────────────────────────────────────────────────────────

def test_verify_webhook_valid():
    body = b'{"test": true}'
    headers = {"X-Hub-Signature": _sign(body)}
    assert verify_webhook(headers, body, SECRET) is True


def test_verify_webhook_invalid_sig():
    body = b'{"test": true}'
    headers = {"X-Hub-Signature": "sha256=deadbeef"}
    assert verify_webhook(headers, body, SECRET) is False


def test_verify_webhook_missing_header():
    assert verify_webhook({}, b"body", SECRET) is False


def test_verify_webhook_lowercase_header():
    body = b'{"test": true}'
    headers = {"x-hub-signature": _sign(body)}
    assert verify_webhook(headers, body, SECRET) is True


# ── parse_webhook_event — PushEvent ───────────────────────────────────────────

PUSH_PAYLOAD = {
    "actor": {"nickname": "alice", "display_name": "Alice"},
    "repository": {"full_name": "alice/myrepo"},
    "push": {
        "changes": [
            {
                "new": {"type": "branch", "name": "main"},
                "commits": [
                    {
                        "hash": "abc123",
                        "message": "Fix bug\n\nDetails",
                        "author": {"user": {"nickname": "alice"}, "raw": "Alice <alice@example.com>"},
                    }
                ],
            }
        ]
    },
}


def test_parse_push_event():
    headers = {"X-Event-Key": "repo:push"}
    event = parse_webhook_event(headers, PUSH_PAYLOAD)
    assert isinstance(event, PushEvent)
    assert event.branch == "main"
    assert event.project_id == "alice/myrepo"
    assert event.actor == "alice"
    assert len(event.commits) == 1
    assert event.commits[0].sha == "abc123"
    assert event.commits[0].title == "Fix bug"


def test_parse_push_event_no_branch_returns_none():
    headers = {"X-Event-Key": "repo:push"}
    payload = {
        **PUSH_PAYLOAD,
        "push": {"changes": [{"new": {"type": "tag", "name": "v1.0"}, "commits": []}]},
    }
    event = parse_webhook_event(headers, payload)
    assert event is None


# ── parse_webhook_event — MREvent ─────────────────────────────────────────────

PR_PAYLOAD = {
    "actor": {"nickname": "bob"},
    "repository": {"full_name": "alice/myrepo"},
    "pullrequest": {
        "id": 5,
        "title": "Add feature",
        "description": "Adds the feature",
        "source": {"branch": {"name": "feature-branch"}},
        "destination": {"branch": {"name": "main"}},
        "links": {"html": {"href": "https://bitbucket.org/alice/myrepo/pull-requests/5"}},
    },
}


def test_parse_pr_created():
    headers = {"X-Event-Key": "pullrequest:created"}
    event = parse_webhook_event(headers, PR_PAYLOAD)
    assert isinstance(event, MREvent)
    assert event.project_id == "alice/myrepo"
    assert event.actor == "bob"
    assert event.action == "open"
    assert event.mr.iid == 5
    assert event.mr.source_branch == "feature-branch"
    assert event.mr.target_branch == "main"


def test_parse_pr_fulfilled():
    headers = {"X-Event-Key": "pullrequest:fulfilled"}
    event = parse_webhook_event(headers, PR_PAYLOAD)
    assert isinstance(event, MREvent)
    assert event.action == "merge"


# ── parse_webhook_event — CommentEvent ────────────────────────────────────────

COMMENT_PAYLOAD = {
    "actor": {"nickname": "carol"},
    "repository": {"full_name": "alice/myrepo"},
    "pullrequest": {"id": 5},
    "comment": {
        "id": 99,
        "content": {"raw": "LGTM!"},
    },
}


def test_parse_comment_event():
    headers = {"X-Event-Key": "pullrequest:comment_created"}
    event = parse_webhook_event(headers, COMMENT_PAYLOAD)
    assert isinstance(event, CommentEvent)
    assert event.project_id == "alice/myrepo"
    assert event.actor == "carol"
    assert event.body == "LGTM!"
    assert event.mr_iid == 5
    assert event.note_id == 99


# ── unknown events ────────────────────────────────────────────────────────────

def test_parse_unknown_event_returns_none():
    headers = {"X-Event-Key": "repo:fork"}
    assert parse_webhook_event(headers, {}) is None


def test_parse_missing_event_header_returns_none():
    assert parse_webhook_event({}, {}) is None
