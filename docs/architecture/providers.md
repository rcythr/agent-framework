# Provider Abstraction

## Provider Abstraction — `providers/base.py`

All repository provider interaction — fetching files, posting comments, creating branches, reporting status — is defined as an abstract interface. The gateway, config loader, and worker all program against this interface. Provider implementations live under `providers/{name}/` and are registered at gateway startup via the `PROVIDER` environment variable.

**Provider-agnostic data models:**

These are plain Pydantic models used throughout the system. No provider SDK types ever cross the boundary into gateway or worker code.

```python
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
    actor: str             # username of the user who pushed

class MREvent(BaseModel):
    mr: MergeRequest
    project_id: int | str
    action: str            # "open", "update", "close", "merge"
    actor: str             # username of the user who opened/updated the MR

class CommentEvent(BaseModel):
    body: str
    project_id: int | str
    mr_iid: int | None
    note_id: int | str
    actor: str             # username of the user who posted the comment

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
```

**`RepositoryProvider` abstract base class:**

```python
from abc import ABC, abstractmethod

class RepositoryProvider(ABC):
    """
    Abstract interface for all repository provider operations.
    Implementations must not expose provider SDK types in return values —
    all returns must be instances of the shared models above.
    """

    # ── Repo content ──────────────────────────────────────────────────────

    @abstractmethod
    def get_file(self, project_id: int | str, path: str, ref: str) -> FileContent:
        """Read a file at a given ref."""

    @abstractmethod
    def commit_file(
        self, project_id: int | str, branch: str,
        path: str, content: str, message: str
    ) -> CommitResult:
        """Create or update a file on a branch."""

    @abstractmethod
    def get_mr_diff(self, project_id: int | str, mr_iid: int) -> list[dict]:
        """Return the diff hunks for a merge/pull request."""

    # ── Comments ──────────────────────────────────────────────────────────

    @abstractmethod
    def post_mr_comment(
        self, project_id: int | str, mr_iid: int, body: str
    ) -> dict:
        """Post a top-level comment on a merge/pull request."""

    @abstractmethod
    def post_inline_comment(
        self, project_id: int | str, mr_iid: int,
        file_path: str, line: int, body: str
    ) -> dict:
        """Post an inline review comment on a specific diff line."""

    # ── MR / PR management ────────────────────────────────────────────────

    @abstractmethod
    def create_mr(
        self, project_id: int | str,
        source_branch: str, target_branch: str,
        title: str, description: str
    ) -> MRResult:
        """Open a merge/pull request."""

    # ── CI / Pipeline status ──────────────────────────────────────────────

    @abstractmethod
    def update_pipeline_status(
        self, project_id: int | str, sha: str,
        state: str, description: str, context: str = "pi-agent"
    ) -> dict:
        """Post a commit status / check run result."""

    # ── Config and project metadata ───────────────────────────────────────

    @abstractmethod
    def get_file_at_sha(
        self, project_id: int | str, path: str, sha: str
    ) -> FileContent | None:
        """
        Fetch a file at a specific commit SHA.
        Returns None if the file does not exist at that ref.
        Used by the config loader to read .agents/config.yaml at event SHA.
        """

    @abstractmethod
    def search_projects(self, query: str, user_token: str) -> list[dict]:
        """Search for projects accessible to the user identified by user_token."""

    @abstractmethod
    def list_branches(self, project_id: int | str, user_token: str) -> list[str]:
        """List branches for a project."""

    @abstractmethod
    def list_open_mrs(self, project_id: int | str, user_token: str) -> list[MergeRequest]:
        """List open merge/pull requests for a project."""

    # ── Webhook verification ──────────────────────────────────────────────

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
```

**Provider registry — `providers/registry.py`:**

```python
import os
from providers.base import RepositoryProvider

def get_provider() -> RepositoryProvider:
    """
    Return the configured provider instance.
    The PROVIDER env var selects the implementation; credentials
    are read from provider-specific env vars by each implementation.
    """
    provider_name = os.getenv("PROVIDER", "gitlab")
    match provider_name:
        case "gitlab":
            from providers.gitlab.provider import GitLabProvider
            return GitLabProvider(
                url=os.getenv("GITLAB_URL", "https://gitlab.com"),
                token=os.getenv("GITLAB_TOKEN"),
            )
        case "github":
            from providers.github.provider import GitHubProvider
            return GitHubProvider(
                token=os.getenv("GITHUB_TOKEN"),
            )
        case _:
            raise ValueError(f"Unknown provider: {provider_name!r}")
```

The gateway and worker both call `get_provider()` once at startup and hold the instance for the lifetime of the process. No code outside the `providers/` directory ever imports from a concrete provider module directly.

---

## Provider Abstraction — `providers/gitlab/provider.py`

The GitLab implementation of `RepositoryProvider`. Uses `python-gitlab` internally but returns only the shared Pydantic models defined in `providers/base.py`. The methods translate between GitLab API shapes and the shared Pydantic models defined in `providers/base.py`, ensuring callers never see GitLab SDK types.

---

## Provider Abstraction — `providers/gitlab/webhook.py`

Implements `verify_webhook` (HMAC comparison against `X-Gitlab-Token`) and `parse_webhook_event` (maps `X-Gitlab-Event` header + payload to `PushEvent`, `MREvent`, or `CommentEvent`). The gateway's webhook endpoint calls this rather than containing any GitLab-specific parsing logic.

---

## Provider Abstraction — `providers/gitlab/toolkit.py` and `worker/tools/toolkit_base.py`

**`ProviderToolkit` ABC** (`worker/tools/toolkit_base.py`):

```python
from abc import ABC, abstractmethod

class ProviderToolkit(ABC):
    """
    Produces the list of tool definitions for a given provider.
    Each tool wraps a RepositoryProvider method with a name, description,
    and parameter schema suitable for LLM tool-calling.
    """

    @abstractmethod
    def get_tools(self) -> list[dict]:
        """
        Return tool definitions in the format expected by Agent.
        Tool execute functions must call self.provider methods only —
        no direct SDK calls.
        """
```

**`GitLabToolkit`** (now `providers/gitlab/toolkit.py`) subclasses `ProviderToolkit` and implements `get_tools()` by wrapping `RepositoryProvider` method calls. The tool names, descriptions, and parameter schemas remain identical — only the internal implementation references `self.provider` rather than the `python-gitlab` SDK directly.

The worker instantiates the toolkit via a factory function that reads the `PROVIDER` env var:

```python
# worker/tools/toolkit_factory.py
import os
from providers.registry import get_provider

def get_toolkit(project_id: int | str) -> ProviderToolkit:
    provider = get_provider()
    provider_name = os.getenv("PROVIDER", "gitlab")
    match provider_name:
        case "gitlab":
            from providers.gitlab.toolkit import GitLabToolkit
            return GitLabToolkit(provider=provider, project_id=project_id)
        case "github":
            from providers.github.toolkit import GitHubToolkit
            return GitHubToolkit(provider=provider, project_id=project_id)
        case _:
            raise ValueError(f"No toolkit for provider: {provider_name!r}")
```

---
