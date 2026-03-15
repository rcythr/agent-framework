import base64

import httpx

from providers.base import (
    RepositoryProvider,
    FileContent,
    CommitResult,
    MRResult,
    MergeRequest,
    Commit,
    PushEvent,
    MREvent,
    CommentEvent,
    WebhookRegistration,
)
from providers.bitbucket.webhook import verify_webhook, parse_webhook_event

_API_BASE = "https://api.bitbucket.org/2.0"


class BitbucketProvider(RepositoryProvider):
    """
    Bitbucket Cloud implementation of RepositoryProvider.
    project_id must be 'workspace/repo_slug' (e.g. 'acme/my-repo').
    Authenticates with an app password: username + app_password.
    """

    def __init__(self, username: str, app_password: str):
        self._auth = (username, app_password)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _get(self, path: str, **kwargs) -> httpx.Response:
        r = httpx.get(f"{_API_BASE}{path}", auth=self._auth, **kwargs)
        r.raise_for_status()
        return r

    def _post(self, path: str, **kwargs) -> httpx.Response:
        r = httpx.post(f"{_API_BASE}{path}", auth=self._auth, **kwargs)
        r.raise_for_status()
        return r

    @staticmethod
    def _split(project_id: int | str) -> tuple[str, str]:
        parts = str(project_id).split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Bitbucket project_id must be 'workspace/repo_slug', got {project_id!r}")
        return parts[0], parts[1]

    def _token_auth(self, user_token: str) -> tuple[str, str]:
        """Return (username, password) auth tuple from a colon-separated token or fall back to service account."""
        if ":" in user_token:
            username, password = user_token.split(":", 1)
            return (username, password)
        return self._auth

    # ── RepositoryProvider methods ────────────────────────────────────────────

    def get_file(self, project_id: int | str, path: str, ref: str) -> FileContent | None:
        workspace, slug = self._split(project_id)
        try:
            r = httpx.get(
                f"{_API_BASE}/repositories/{workspace}/{slug}/src/{ref}/{path}",
                auth=self._auth,
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return FileContent(path=path, content=r.text, ref=ref)
        except httpx.HTTPStatusError:
            return None

    def get_file_at_sha(self, project_id: int | str, path: str, sha: str) -> FileContent | None:
        return self.get_file(project_id, path, sha)

    def commit_file(
        self, project_id: int | str, branch: str,
        path: str, content: str, message: str
    ) -> CommitResult:
        workspace, slug = self._split(project_id)
        # Bitbucket accepts multipart form POSTs to /src for create/update
        r = httpx.post(
            f"{_API_BASE}/repositories/{workspace}/{slug}/src",
            auth=self._auth,
            data={"message": message, "branch": branch},
            files={path: content.encode()},
        )
        r.raise_for_status()
        # Fetch the new HEAD SHA on the branch
        branch_r = self._get(f"/repositories/{workspace}/{slug}/refs/branches/{branch}")
        sha = branch_r.json().get("target", {}).get("hash", "")
        return CommitResult(sha=sha, branch=branch)

    def create_mr(
        self, project_id: int | str,
        source_branch: str, target_branch: str,
        title: str, description: str
    ) -> MRResult:
        workspace, slug = self._split(project_id)
        payload = {
            "title": title,
            "description": description,
            "source": {"branch": {"name": source_branch}},
            "destination": {"branch": {"name": target_branch}},
        }
        data = self._post(f"/repositories/{workspace}/{slug}/pullrequests", json=payload).json()
        return MRResult(
            iid=data["id"],
            web_url=data.get("links", {}).get("html", {}).get("href", ""),
        )

    def post_mr_comment(
        self, project_id: int | str, mr_iid: int, body: str
    ) -> None:
        workspace, slug = self._split(project_id)
        self._post(
            f"/repositories/{workspace}/{slug}/pullrequests/{mr_iid}/comments",
            json={"content": {"raw": body}},
        )

    def post_inline_comment(
        self, project_id: int | str, mr_iid: int,
        file_path: str, line: int, body: str
    ) -> None:
        workspace, slug = self._split(project_id)
        self._post(
            f"/repositories/{workspace}/{slug}/pullrequests/{mr_iid}/comments",
            json={
                "content": {"raw": body},
                "inline": {"to": line, "path": file_path},
            },
        )

    def get_mr_diff(self, project_id: int | str, mr_iid: int) -> str:
        workspace, slug = self._split(project_id)
        r = httpx.get(
            f"{_API_BASE}/repositories/{workspace}/{slug}/pullrequests/{mr_iid}/diff",
            auth=self._auth,
        )
        r.raise_for_status()
        return r.text

    def update_pipeline_status(
        self, project_id: int | str, sha: str,
        state: str, description: str, context: str = "pi-agent"
    ) -> None:
        workspace, slug = self._split(project_id)
        # Bitbucket build status states: INPROGRESS, SUCCESSFUL, FAILED, STOPPED
        state_map = {
            "pending": "INPROGRESS",
            "running": "INPROGRESS",
            "success": "SUCCESSFUL",
            "failed": "FAILED",
            "error": "FAILED",
        }
        bb_state = state_map.get(state.lower(), state.upper())
        self._post(
            f"/repositories/{workspace}/{slug}/commit/{sha}/statuses/build",
            json={
                "state": bb_state,
                "key": context,
                "description": description,
                "url": f"https://api.bitbucket.org/2.0/repositories/{workspace}/{slug}",
            },
        )

    def search_projects(self, query: str, user_token: str) -> list[dict]:
        auth = self._token_auth(user_token)
        r = httpx.get(
            f"{_API_BASE}/repositories",
            auth=auth,
            params={"q": f'name~"{query}"', "pagelen": 20},
        )
        r.raise_for_status()
        return [
            {
                "id": repo["full_name"],
                "name": repo["name"],
                "path_with_namespace": repo["full_name"],
                "web_url": repo.get("links", {}).get("html", {}).get("href", ""),
            }
            for repo in r.json().get("values", [])
        ]

    def list_branches(self, project_id: int | str, user_token: str) -> list[str]:
        workspace, slug = self._split(project_id)
        auth = self._token_auth(user_token)
        r = httpx.get(
            f"{_API_BASE}/repositories/{workspace}/{slug}/refs/branches",
            auth=auth,
            params={"pagelen": 100},
        )
        r.raise_for_status()
        return [b["name"] for b in r.json().get("values", [])]

    def list_open_mrs(self, project_id: int | str, user_token: str) -> list[MergeRequest]:
        workspace, slug = self._split(project_id)
        auth = self._token_auth(user_token)
        r = httpx.get(
            f"{_API_BASE}/repositories/{workspace}/{slug}/pullrequests",
            auth=auth,
            params={"state": "OPEN", "pagelen": 50},
        )
        r.raise_for_status()
        return [
            MergeRequest(
                iid=pr["id"],
                title=pr.get("title", ""),
                description=pr.get("description") or "",
                source_branch=pr.get("source", {}).get("branch", {}).get("name", ""),
                target_branch=pr.get("destination", {}).get("branch", {}).get("name", ""),
                web_url=pr.get("links", {}).get("html", {}).get("href", ""),
            )
            for pr in r.json().get("values", [])
        ]

    def verify_webhook(self, headers: dict, body: bytes, secret: str) -> bool:
        return verify_webhook(headers, body, secret)

    def parse_webhook_event(
        self, headers: dict, body: dict
    ) -> PushEvent | MREvent | CommentEvent | None:
        return parse_webhook_event(headers, body)

    def register_webhook(
        self, project_id: int | str, webhook_url: str, secret: str, user_token: str
    ) -> WebhookRegistration:
        workspace, slug = self._split(project_id)
        auth = self._token_auth(user_token)
        r = httpx.post(
            f"{_API_BASE}/repositories/{workspace}/{slug}/hooks",
            auth=auth,
            json={
                "description": "Phalanx",
                "url": webhook_url,
                "secret": secret,
                "active": True,
                "events": [
                    "repo:push",
                    "pullrequest:created",
                    "pullrequest:updated",
                    "pullrequest:fulfilled",
                    "pullrequest:rejected",
                    "pullrequest:comment_created",
                ],
            },
        )
        r.raise_for_status()
        return WebhookRegistration(webhook_id=r.json()["uuid"], webhook_url=webhook_url)

    def delete_webhook(
        self, project_id: int | str, webhook_id: str, user_token: str
    ) -> None:
        workspace, slug = self._split(project_id)
        auth = self._token_auth(user_token)
        r = httpx.delete(
            f"{_API_BASE}/repositories/{workspace}/{slug}/hooks/{webhook_id}",
            auth=auth,
        )
        r.raise_for_status()
