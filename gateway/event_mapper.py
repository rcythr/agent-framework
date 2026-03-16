from providers.base import PushEvent, MREvent, CommentEvent
from shared.models import TaskSpec


def map_event_to_task(
    event: PushEvent | MREvent | CommentEvent | None,
) -> TaskSpec | None:
    if event is None:
        return None

    if isinstance(event, MREvent):
        return TaskSpec(
            task="review_mr",
            project_id=event.project_id,
            project_path=event.project_path,
            context={
                "mr_iid": event.mr.iid,
                "action": event.action,
                "title": event.mr.title,
                "description": event.mr.description,
                "source_branch": event.mr.source_branch,
                "target_branch": event.mr.target_branch,
                "web_url": event.mr.web_url,
                "actor": event.actor,
                # Clone the PR's source branch so the agent has the proposed changes
                "clone_branch": event.mr.source_branch,
            },
        )

    if isinstance(event, CommentEvent):
        return TaskSpec(
            task="handle_comment",
            project_id=event.project_id,
            project_path=event.project_path,
            context={
                "body": event.body,
                "note_id": event.note_id,
                "mr_iid": event.mr_iid,
                "actor": event.actor,
                # Use the MR's source branch when available, else default to main
                "clone_branch": event.source_branch or "main",
            },
        )

    if isinstance(event, PushEvent):
        commits = [
            {"sha": c.sha, "title": c.title, "author": c.author}
            for c in event.commits
        ]
        return TaskSpec(
            task="analyze_push",
            project_id=event.project_id,
            project_path=event.project_path,
            context={
                "branch": event.branch,
                "commits": commits,
                "actor": event.actor,
                # Clone the branch that was pushed to
                "clone_branch": event.branch,
            },
        )

    return None
