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
    Verify a GitLab webhook using the X-Gitlab-Token header.
    GitLab sends the secret token directly (not as an HMAC signature).
    """
    token = headers.get("X-Gitlab-Token") or headers.get("x-gitlab-token", "")
    return hmac.compare_digest(token, secret)


def parse_webhook_event(
    headers: dict, body: dict
) -> PushEvent | MREvent | CommentEvent | None:
    """
    Map a GitLab webhook payload to a provider-agnostic event model.
    Returns None for unhandled event types.
    """
    event_type = headers.get("X-Gitlab-Event") or headers.get("x-gitlab-event", "")

    if event_type == "Push Hook":
        return _parse_push_event(body)
    elif event_type in ("Merge Request Hook", "Merge Request Event"):
        return _parse_mr_event(body)
    elif event_type in ("Note Hook", "Confidential Note Hook"):
        return _parse_comment_event(body)
    else:
        return None


def _parse_push_event(body: dict) -> PushEvent:
    ref = body.get("ref", "")
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref

    commits = [
        Commit(
            sha=c["id"],
            title=c.get("title") or c.get("message", "").split("\n")[0],
            author=c.get("author", {}).get("name", ""),
        )
        for c in body.get("commits", [])
    ]

    actor = body.get("user_username") or body.get("user_name", "")
    project_path = body.get("project", {}).get("path_with_namespace", "")

    return PushEvent(
        branch=branch,
        commits=commits,
        project_id=body["project_id"],
        project_path=project_path,
        actor=actor,
    )


def _parse_mr_event(body: dict) -> MREvent:
    attrs = body.get("object_attributes", {})
    action = attrs.get("action", "")

    mr = MergeRequest(
        iid=attrs["iid"],
        title=attrs.get("title", ""),
        description=attrs.get("description") or "",
        source_branch=attrs.get("source_branch", ""),
        target_branch=attrs.get("target_branch", ""),
        web_url=attrs.get("url", ""),
    )

    actor = body.get("user", {}).get("username", "")
    project_path = body.get("project", {}).get("path_with_namespace", "")

    return MREvent(
        mr=mr,
        project_id=body["project"]["id"],
        project_path=project_path,
        action=action,
        actor=actor,
    )


def _parse_comment_event(body: dict) -> CommentEvent:
    attrs = body.get("object_attributes", {})

    mr_iid = None
    source_branch = None
    if "merge_request" in body:
        mr = body["merge_request"]
        mr_iid = mr.get("iid")
        source_branch = mr.get("source_branch")

    actor = body.get("user", {}).get("username", "")
    project_path = body.get("project", {}).get("path_with_namespace", "")

    return CommentEvent(
        body=attrs.get("note", ""),
        project_id=body["project_id"],
        project_path=project_path,
        mr_iid=mr_iid,
        source_branch=source_branch,
        note_id=attrs.get("id", ""),
        actor=actor,
    )
