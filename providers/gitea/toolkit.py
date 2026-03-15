from worker.tools.toolkit_base import ProviderToolkit
from providers.base import RepositoryProvider


class GiteaToolkit(ProviderToolkit):
    """Gitea implementation of ProviderToolkit."""

    def __init__(self, provider: RepositoryProvider, project_id: int | str):
        self.provider = provider
        self.project_id = project_id

    def get_tools(self) -> list[dict]:
        return [
            {
                "name": "get_file",
                "description": "Read a file from the repository at a given ref.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path in the repository"},
                        "ref": {"type": "string", "description": "Branch, tag, or commit SHA"},
                    },
                    "required": ["path", "ref"],
                },
                "execute": lambda path, ref: self.provider.get_file(self.project_id, path, ref),
            },
            {
                "name": "commit_file",
                "description": "Create or update a file on a branch.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "branch": {"type": "string", "description": "Target branch"},
                        "path": {"type": "string", "description": "File path in the repository"},
                        "content": {"type": "string", "description": "File content"},
                        "message": {"type": "string", "description": "Commit message"},
                    },
                    "required": ["branch", "path", "content", "message"],
                },
                "execute": lambda branch, path, content, message: self.provider.commit_file(
                    self.project_id, branch, path, content, message
                ),
            },
            {
                "name": "create_mr",
                "description": "Open a pull request.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_branch": {"type": "string"},
                        "target_branch": {"type": "string"},
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["source_branch", "target_branch", "title", "description"],
                },
                "execute": lambda source_branch, target_branch, title, description: self.provider.create_mr(
                    self.project_id, source_branch, target_branch, title, description
                ),
            },
            {
                "name": "post_mr_comment",
                "description": "Post a top-level comment on a pull request.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mr_iid": {"type": "integer", "description": "Pull request number"},
                        "body": {"type": "string"},
                    },
                    "required": ["mr_iid", "body"],
                },
                "execute": lambda mr_iid, body: self.provider.post_mr_comment(
                    self.project_id, mr_iid, body
                ),
            },
            {
                "name": "get_mr_diff",
                "description": "Get the diff for a pull request.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mr_iid": {"type": "integer", "description": "Pull request number"},
                    },
                    "required": ["mr_iid"],
                },
                "execute": lambda mr_iid: self.provider.get_mr_diff(self.project_id, mr_iid),
            },
            {
                "name": "post_inline_comment",
                "description": "Post an inline review comment on a specific diff line.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mr_iid": {"type": "integer", "description": "Pull request number"},
                        "path": {"type": "string", "description": "File path"},
                        "line": {"type": "integer", "description": "Line number"},
                        "body": {"type": "string", "description": "Comment body"},
                    },
                    "required": ["mr_iid", "path", "line", "body"],
                },
                "execute": lambda mr_iid, path, line, body: self.provider.post_inline_comment(
                    self.project_id, mr_iid, path, line, body
                ),
            },
            {
                "name": "update_pipeline_status",
                "description": "Post a commit status result.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sha": {"type": "string", "description": "Commit SHA"},
                        "state": {
                            "type": "string",
                            "description": "Status state (pending, success, failure, error)",
                        },
                        "description": {"type": "string", "description": "Status description"},
                    },
                    "required": ["sha", "state", "description"],
                },
                "execute": lambda sha, state, description: self.provider.update_pipeline_status(
                    self.project_id, sha, state, description
                ),
            },
        ]
