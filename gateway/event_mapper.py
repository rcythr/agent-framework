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
            project_id=int(event.project_id),
            context={
                "mr_iid": event.mr.iid,
                "action": event.action,
                "title": event.mr.title,
                "description": event.mr.description,
                "source_branch": event.mr.source_branch,
                "target_branch": event.mr.target_branch,
                "web_url": event.mr.web_url,
                "actor": event.actor,
            },
        )

    if isinstance(event, CommentEvent):
        return TaskSpec(
            task="handle_comment",
            project_id=int(event.project_id),
            context={
                "body": event.body,
                "note_id": event.note_id,
                "mr_iid": event.mr_iid,
                "actor": event.actor,
            },
        )

    if isinstance(event, PushEvent):
        return TaskSpec(
            task="analyze_push",
            project_id=int(event.project_id),
            context={
                "branch": event.branch,
                "commits": [
                    {"sha": c.sha, "title": c.title, "author": c.author}
                    for c in event.commits
                ],
                "actor": event.actor,
            },
        )

    return None
