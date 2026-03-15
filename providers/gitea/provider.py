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
)
from providers.gitea.webhook import verify_webhook, parse_webhook_event


class GiteaProvider(RepositoryProvider):
    """
    Gitea implementation of RepositoryProvider using the Gitea REST API.
    project_id must be 'owner/repo' (e.g. 'alice/my-repo').
    """

    def __init__(self, url: str, token: str | None):
        self._base = url.rstrip("/") + "/api/v1"
        self._headers = {"Authorization": f"token {token}"} if token else {}

    # ── helpers ───────────────────────────────────────────────────────────────

    def _get(self, path: str, **kwargs) -> httpx.Response:
        r = httpx.get(f"{self._base}{path}", headers=self._headers, **kwargs)
        r.raise_for_status()
        return r

    def _post(self, path: str, **kwargs) -> httpx.Response:
        r = httpx.post(f"{self._base}{path}", headers=self._headers, **kwargs)
        r.raise_for_status()
        return r

    def _patch(self, path: str, **kwargs) -> httpx.Response:
        r = httpx.patch(f"{self._base}{path}", headers=self._headers, **kwargs)
        r.raise_for_status()
        return r

    def _put(self, path: str, **kwargs) -> httpx.Response:
        r = httpx.put(f"{self._base}{path}", headers=self._headers, **kwargs)
        r.raise_for_status()
        return r

    @staticmethod
    def _split(project_id: int | str) -> tuple[str, str]:
        parts = str(project_id).split("/", 1)
        if len(parts) != 2:
            raise ValueError(f"Gitea project_id must be 'owner/repo', got {project_id!r}")
        return parts[0], parts[1]

    def _user_headers(self, user_token: str) -> dict:
        return {"Authorization": f"token {user_token}"}

    # ── RepositoryProvider methods ────────────────────────────────────────────

    def get_file(self, project_id: int | str, path: str, ref: str) -> FileContent | None:
        owner, repo = self._split(project_id)
        try:
            r = httpx.get(
                f"{self._base}/repos/{owner}/{repo}/contents/{path}",
                headers=self._headers,
                params={"ref": ref},
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return FileContent(path=path, content=content, ref=ref)
        except httpx.HTTPStatusError:
            return None

    def get_file_at_sha(self, project_id: int | str, path: str, sha: str) -> FileContent | None:
        return self.get_file(project_id, path, sha)

    def commit_file(
        self, project_id: int | str, branch: str,
        path: str, content: str, message: str
    ) -> CommitResult:
        owner, repo = self._split(project_id)
        encoded = base64.b64encode(content.encode()).decode()

        # Check if the file already exists to get its SHA (required for update)
        r = httpx.get(
            f"{self._base}/repos/{owner}/{repo}/contents/{path}",
            headers=self._headers,
            params={"ref": branch},
        )

        if r.status_code == 200:
            file_sha = r.json().get("sha", "")
            result = self._put(
                f"/repos/{owner}/{repo}/contents/{path}",
                json={"message": message, "content": encoded, "sha": file_sha, "branch": branch},
            )
        else:
            result = self._post(
                f"/repos/{owner}/{repo}/contents/{path}",
                json={"message": message, "content": encoded, "branch": branch},
            )

        sha = result.json().get("commit", {}).get("sha", "")
        return CommitResult(sha=sha, branch=branch)

    def create_mr(
        self, project_id: int | str,
        source_branch: str, target_branch: str,
        title: str, description: str
    ) -> MRResult:
        owner, repo = self._split(project_id)
        data = self._post(
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "body": description,
                "head": source_branch,
                "base": target_branch,
            },
        ).json()
        return MRResult(iid=data["number"], web_url=data.get("html_url", ""))

    def post_mr_comment(
        self, project_id: int | str, mr_iid: int, body: str
    ) -> None:
        owner, repo = self._split(project_id)
        self._post(
            f"/repos/{owner}/{repo}/issues/{mr_iid}/comments",
            json={"body": body},
        )

    def post_inline_comment(
        self, project_id: int | str, mr_iid: int,
        file_path: str, line: int, body: str
    ) -> None:
        owner, repo = self._split(project_id)
        # Gitea inline PR review comments require the pull request review endpoint
        self._post(
            f"/repos/{owner}/{repo}/pulls/{mr_iid}/reviews",
            json={
                "body": body,
                "comments": [{"path": file_path, "new_position": line, "body": body}],
                "event": "COMMENT",
            },
        )

    def get_mr_diff(self, project_id: int | str, mr_iid: int) -> str:
        owner, repo = self._split(project_id)
        r = httpx.get(
            f"{self._base}/repos/{owner}/{repo}/pulls/{mr_iid}.diff",
            headers=self._headers,
        )
        r.raise_for_status()
        return r.text

    def update_pipeline_status(
        self, project_id: int | str, sha: str,
        state: str, description: str, context: str = "pi-agent"
    ) -> None:
        owner, repo = self._split(project_id)
        self._post(
            f"/repos/{owner}/{repo}/statuses/{sha}",
            json={
                "state": state,
                "description": description,
                "context": context,
            },
        )

    def search_projects(self, query: str, user_token: str) -> list[dict]:
        r = httpx.get(
            f"{self._base}/repos/search",
            headers=self._user_headers(user_token),
            params={"q": query, "limit": 20},
        )
        r.raise_for_status()
        return [
            {
                "id": repo["full_name"],
                "name": repo["name"],
                "path_with_namespace": repo["full_name"],
                "web_url": repo.get("html_url", ""),
            }
            for repo in r.json().get("data", [])
        ]

    def list_branches(self, project_id: int | str, user_token: str) -> list[str]:
        owner, repo = self._split(project_id)
        r = httpx.get(
            f"{self._base}/repos/{owner}/{repo}/branches",
            headers=self._user_headers(user_token),
            params={"limit": 50},
        )
        r.raise_for_status()
        return [b["name"] for b in r.json()]

    def list_open_mrs(self, project_id: int | str, user_token: str) -> list[MergeRequest]:
        owner, repo = self._split(project_id)
        r = httpx.get(
            f"{self._base}/repos/{owner}/{repo}/pulls",
            headers=self._user_headers(user_token),
            params={"state": "open", "limit": 50},
        )
        r.raise_for_status()
        return [
            MergeRequest(
                iid=pr["number"],
                title=pr.get("title", ""),
                description=pr.get("body") or "",
                source_branch=pr.get("head", {}).get("label", ""),
                target_branch=pr.get("base", {}).get("label", ""),
                web_url=pr.get("html_url", ""),
            )
            for pr in r.json()
        ]

    def verify_webhook(self, headers: dict, body: bytes, secret: str) -> bool:
        return verify_webhook(headers, body, secret)

    def parse_webhook_event(
        self, headers: dict, body: dict
    ) -> PushEvent | MREvent | CommentEvent | None:
        return parse_webhook_event(headers, body)
