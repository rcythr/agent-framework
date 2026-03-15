from providers.base import (
    RepositoryProvider,
    FileContent,
    CommitResult,
    MRResult,
    MergeRequest,
    PushEvent,
    MREvent,
    CommentEvent,
)


class GitHubProvider(RepositoryProvider):
    """GitHub placeholder — not yet implemented."""

    def __init__(self, token: str | None):
        raise NotImplementedError("GitHub provider is not yet implemented")

    def get_file(self, project_id, path, ref):
        raise NotImplementedError

    def get_file_at_sha(self, project_id, path, sha):
        raise NotImplementedError

    def commit_file(self, project_id, branch, path, content, message):
        raise NotImplementedError

    def create_mr(self, project_id, source_branch, target_branch, title, description):
        raise NotImplementedError

    def post_mr_comment(self, project_id, mr_iid, body):
        raise NotImplementedError

    def post_inline_comment(self, project_id, mr_iid, file_path, line, body):
        raise NotImplementedError

    def get_mr_diff(self, project_id, mr_iid):
        raise NotImplementedError

    def update_pipeline_status(self, project_id, sha, state, description, context="pi-agent"):
        raise NotImplementedError

    def search_projects(self, query, user_token):
        raise NotImplementedError

    def list_branches(self, project_id, user_token):
        raise NotImplementedError

    def list_open_mrs(self, project_id, user_token):
        raise NotImplementedError

    def verify_webhook(self, headers, body, secret):
        raise NotImplementedError

    def parse_webhook_event(self, headers, body):
        raise NotImplementedError
