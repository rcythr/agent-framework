import hmac
import hashlib

from providers.base import (
    PushEvent,
    MREvent,
    CommentEvent,
    MergeRequest,
    Commit,
)


def verify_webhook(headers: dict, body: bytes, secret: str) -> bool:
    """
    Verify a Gitea webhook using HMAC-SHA256.
    Gitea sends the hex digest (no prefix) in X-Gitea-Signature.
    """
    sig_header = headers.get("X-Gitea-Signature") or headers.get("x-gitea-signature", "")
    if not sig_header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig_header, expected)


def parse_webhook_event(
    headers: dict, body: dict
) -> PushEvent | MREvent | CommentEvent | None:
    """
    Map a Gitea webhook payload to a provider-agnostic event model.
    Returns None for unhandled event types.
    """
    event_type = headers.get("X-Gitea-Event") or headers.get("x-gitea-event", "")

    if event_type == "push":
        return _parse_push_event(body)
    elif event_type == "pull_request":
        return _parse_pr_event(body)
    elif event_type == "issue_comment":
        return _parse_comment_event(body)
    else:
        return None


def _parse_push_event(body: dict) -> PushEvent:
    ref = body.get("ref", "")
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref

    commits = [
        Commit(
            sha=c.get("id", ""),
            title=c.get("message", "").split("\n")[0],
            author=c.get("author", {}).get("name", ""),
        )
        for c in body.get("commits", [])
    ]

    repo = body.get("repository", {})
    project_id = repo.get("full_name", str(repo.get("id", "")))
    actor = body.get("pusher", {}).get("login", "")

    return PushEvent(
        branch=branch,
        commits=commits,
        project_id=project_id,
        actor=actor,
    )


def _parse_pr_event(body: dict) -> MREvent:
    action = body.get("action", "")
    pr = body.get("pull_request", {})
    repo = body.get("repository", {})
    project_id = repo.get("full_name", str(repo.get("id", "")))

    mr = MergeRequest(
        iid=pr.get("number", 0),
        title=pr.get("title", ""),
        description=pr.get("body") or "",
        source_branch=pr.get("head", {}).get("label", ""),
        target_branch=pr.get("base", {}).get("label", ""),
        web_url=pr.get("html_url", ""),
    )

    actor = body.get("sender", {}).get("login", "")

    return MREvent(
        mr=mr,
        project_id=project_id,
        action=action,
        actor=actor,
    )


def _parse_comment_event(body: dict) -> CommentEvent | None:
    repo = body.get("repository", {})
    project_id = repo.get("full_name", str(repo.get("id", "")))
    actor = body.get("sender", {}).get("login", "")

    issue = body.get("issue", {})
    # Only handle comments on pull requests
    if not issue.get("pull_request"):
        return None

    comment = body.get("comment", {})

    return CommentEvent(
        body=comment.get("body", ""),
        project_id=project_id,
        mr_iid=issue.get("number"),
        note_id=comment.get("id", ""),
        actor=actor,
    )
