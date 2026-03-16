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
    Verify a Bitbucket webhook using HMAC-SHA256.
    Bitbucket sends the signature as 'sha256=<hex>' in X-Hub-Signature.
    """
    sig_header = headers.get("X-Hub-Signature") or headers.get("x-hub-signature", "")
    if not sig_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig_header, expected)


def parse_webhook_event(
    headers: dict, body: dict
) -> PushEvent | MREvent | CommentEvent | None:
    """
    Map a Bitbucket webhook payload to a provider-agnostic event model.
    Returns None for unhandled event types.
    """
    event_key = headers.get("X-Event-Key") or headers.get("x-event-key", "")

    if event_key == "repo:push":
        return _parse_push_event(body)
    elif event_key.startswith("pullrequest:") and not event_key.endswith("comment_created"):
        return _parse_pr_event(body, event_key)
    elif event_key == "pullrequest:comment_created":
        return _parse_comment_event(body)
    else:
        return None


def _parse_push_event(body: dict) -> PushEvent | None:
    repo = body.get("repository", {})
    project_id = repo.get("full_name", "")
    project_path = repo.get("full_name", "")
    actor = body.get("actor", {}).get("nickname", "") or body.get("actor", {}).get("display_name", "")

    push = body.get("push", {})
    changes = push.get("changes", [])

    # Use the first branch change
    branch = ""
    commits = []
    for change in changes:
        new = change.get("new", {})
        if new.get("type") != "branch":
            continue
        branch = new.get("name", "")
        for c in change.get("commits", []):
            commits.append(
                Commit(
                    sha=c.get("hash", ""),
                    title=c.get("message", "").split("\n")[0],
                    author=c.get("author", {}).get("user", {}).get("nickname", "")
                    or c.get("author", {}).get("raw", ""),
                )
            )
        break

    if not branch:
        return None

    return PushEvent(
        branch=branch,
        commits=commits,
        project_id=project_id,
        project_path=project_path,
        actor=actor,
    )


def _parse_pr_event(body: dict, event_key: str) -> MREvent:
    # Map Bitbucket event keys to action verbs
    action_map = {
        "pullrequest:created": "open",
        "pullrequest:updated": "update",
        "pullrequest:fulfilled": "merge",
        "pullrequest:rejected": "close",
        "pullrequest:approved": "approved",
        "pullrequest:unapproved": "unapproved",
    }
    action = action_map.get(event_key, event_key.split(":")[-1])

    repo = body.get("repository", {})
    project_id = repo.get("full_name", "")
    project_path = repo.get("full_name", "")
    actor = body.get("actor", {}).get("nickname", "") or body.get("actor", {}).get("display_name", "")

    pr = body.get("pullrequest", {})
    mr = MergeRequest(
        iid=pr.get("id", 0),
        title=pr.get("title", ""),
        description=pr.get("description") or "",
        source_branch=pr.get("source", {}).get("branch", {}).get("name", ""),
        target_branch=pr.get("destination", {}).get("branch", {}).get("name", ""),
        web_url=pr.get("links", {}).get("html", {}).get("href", ""),
    )

    return MREvent(
        mr=mr,
        project_id=project_id,
        project_path=project_path,
        action=action,
        actor=actor,
    )


def _parse_comment_event(body: dict) -> CommentEvent:
    repo = body.get("repository", {})
    project_id = repo.get("full_name", "")
    project_path = repo.get("full_name", "")
    actor = body.get("actor", {}).get("nickname", "") or body.get("actor", {}).get("display_name", "")

    comment = body.get("comment", {})
    pr = body.get("pullrequest", {})
    source_branch = pr.get("source", {}).get("branch", {}).get("name")

    return CommentEvent(
        body=comment.get("content", {}).get("raw", ""),
        project_id=project_id,
        project_path=project_path,
        mr_iid=pr.get("id"),
        source_branch=source_branch,
        note_id=comment.get("id", ""),
        actor=actor,
    )
