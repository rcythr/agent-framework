from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Any


class MergeRequest(BaseModel):
    iid: int
    title: str
    description: str
    source_branch: str
    target_branch: str
    web_url: str


class Commit(BaseModel):
    sha: str
    title: str
    author: str


class PushEvent(BaseModel):
    branch: str
    commits: list[Commit]
    project_id: int | str
    actor: str


class MREvent(BaseModel):
    mr: MergeRequest
    project_id: int | str
    action: str
    actor: str


class CommentEvent(BaseModel):
    body: str
    project_id: int | str
    mr_iid: int | None
    note_id: int | str
    actor: str


class FileContent(BaseModel):
    path: str
    content: str
    ref: str


class CommitResult(BaseModel):
    sha: str
    branch: str


class MRResult(BaseModel):
    iid: int
    web_url: str


class RepositoryProvider(ABC):
    """
    Abstract interface for all repository provider operations.
    Implementations must not expose provider SDK types in return values —
    all returns must be instances of the shared models above.
    """

    @abstractmethod
    def get_file(self, project_id: int | str, path: str, ref: str) -> FileContent | None:
        """Read a file at a given ref."""

    @abstractmethod
    def get_file_at_sha(self, project_id: int | str, path: str, sha: str) -> FileContent | None:
        """
        Fetch a file at a specific commit SHA.
        Returns None if the file does not exist at that ref.
        """

    @abstractmethod
    def commit_file(
        self, project_id: int | str, branch: str,
        path: str, content: str, message: str
    ) -> CommitResult:
        """Create or update a file on a branch."""

    @abstractmethod
    def create_mr(
        self, project_id: int | str,
        source_branch: str, target_branch: str,
        title: str, description: str
    ) -> MRResult:
        """Open a merge/pull request."""

    @abstractmethod
    def post_mr_comment(
        self, project_id: int | str, mr_iid: int, body: str
    ) -> None:
        """Post a top-level comment on a merge/pull request."""

    @abstractmethod
    def post_inline_comment(
        self, project_id: int | str, mr_iid: int,
        file_path: str, line: int, body: str
    ) -> None:
        """Post an inline review comment on a specific diff line."""

    @abstractmethod
    def get_mr_diff(self, project_id: int | str, mr_iid: int) -> str:
        """Return the diff for a merge/pull request."""

    @abstractmethod
    def update_pipeline_status(
        self, project_id: int | str, sha: str,
        state: str, description: str, context: str = "pi-agent"
    ) -> None:
        """Post a commit status / check run result."""

    @abstractmethod
    def search_projects(self, query: str, user_token: str) -> list[dict]:
        """Search for projects accessible to the user identified by user_token."""

    @abstractmethod
    def list_branches(self, project_id: int | str, user_token: str) -> list[str]:
        """List branches for a project."""

    @abstractmethod
    def list_open_mrs(self, project_id: int | str, user_token: str) -> list[MergeRequest]:
        """List open merge/pull requests for a project."""

    @abstractmethod
    def verify_webhook(self, headers: dict, body: bytes, secret: str) -> bool:
        """Return True if the webhook signature is valid."""

    @abstractmethod
    def parse_webhook_event(
        self, headers: dict, body: dict
    ) -> PushEvent | MREvent | CommentEvent | None:
        """
        Parse a raw webhook payload into a provider-agnostic event model.
        Returns None for event types the system does not handle.
        """
