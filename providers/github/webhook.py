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
    Verify a GitHub webhook using HMAC-SHA256.
    GitHub sends the signature as 'sha256=<hex>' in X-Hub-Signature-256.
    """
    sig_header = headers.get("X-Hub-Signature-256") or headers.get("x-hub-signature-256", "")
    if not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig_header, expected)


def parse_webhook_event(
    headers: dict, body: dict
) -> PushEvent | MREvent | CommentEvent | None:
    """
    Map a GitHub webhook payload to a provider-agnostic event model.
    Returns None for unhandled event types.
    """
    event_type = headers.get("X-GitHub-Event") or headers.get("x-github-event", "")

    if event_type == "push":
        # Ignore branch delete events (empty commits list, zero sha)
        if body.get("after", "").replace("0", "") == "":
            return None
        return _parse_push_event(body)
    elif event_type == "pull_request":
        return _parse_pr_event(body)
    elif event_type in ("pull_request_review_comment", "issue_comment"):
        return _parse_comment_event(body, event_type)
    else:
        return None


def _parse_push_event(body: dict) -> PushEvent:
    ref = body.get("ref", "")
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref

    commits = [
        Commit(
            sha=c["id"],
            title=c.get("message", "").split("\n")[0],
            author=c.get("author", {}).get("name", ""),
        )
        for c in body.get("commits", [])
    ]

    actor = body.get("pusher", {}).get("name", "") or body.get("sender", {}).get("login", "")
    repo = body.get("repository", {})
    project_id = repo.get("full_name", str(repo.get("id", "")))
    project_path = repo.get("full_name", "")

    return PushEvent(
        branch=branch,
        commits=commits,
        project_id=project_id,
        project_path=project_path,
        actor=actor,
    )


def _parse_pr_event(body: dict) -> MREvent:
    action = body.get("action", "")
    pr = body.get("pull_request", {})
    repo = body.get("repository", {})
    project_id = repo.get("full_name", str(repo.get("id", "")))
    project_path = repo.get("full_name", "")

    mr = MergeRequest(
        iid=pr["number"],
        title=pr.get("title", ""),
        description=pr.get("body") or "",
        source_branch=pr.get("head", {}).get("ref", ""),
        target_branch=pr.get("base", {}).get("ref", ""),
        web_url=pr.get("html_url", ""),
    )

    actor = body.get("sender", {}).get("login", "")

    return MREvent(
        mr=mr,
        project_id=project_id,
        project_path=project_path,
        action=action,
        actor=actor,
    )


def _parse_comment_event(body: dict, event_type: str) -> CommentEvent | None:
    repo = body.get("repository", {})
    project_id = repo.get("full_name", str(repo.get("id", "")))
    project_path = repo.get("full_name", "")
    actor = body.get("sender", {}).get("login", "")

    if event_type == "pull_request_review_comment":
        comment = body.get("comment", {})
        pr = body.get("pull_request", {})
        mr_iid = pr.get("number")
        note_id = comment.get("id", "")
        comment_body = comment.get("body", "")
        source_branch = pr.get("head", {}).get("ref")
    else:
        # issue_comment — only handle comments on pull requests
        issue = body.get("issue", {})
        if "pull_request" not in issue:
            return None
        comment = body.get("comment", {})
        mr_iid = issue.get("number")
        note_id = comment.get("id", "")
        comment_body = comment.get("body", "")
        # issue_comment doesn't include branch info directly
        source_branch = None

    return CommentEvent(
        body=comment_body,
        project_id=project_id,
        project_path=project_path,
        mr_iid=mr_iid,
        source_branch=source_branch,
        note_id=note_id,
        actor=actor,
    )
