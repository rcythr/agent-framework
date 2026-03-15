from providers.base import PushEvent, MREvent, CommentEvent


def verify_webhook(headers: dict, body: bytes, secret: str) -> bool:
    raise NotImplementedError("GitHub webhook verification is not yet implemented")


def parse_webhook_event(
    headers: dict, body: dict
) -> PushEvent | MREvent | CommentEvent | None:
    raise NotImplementedError("GitHub webhook parsing is not yet implemented")
