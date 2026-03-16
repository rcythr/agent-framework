from github import Github
from github import GithubException

from providers.base import (
    RepositoryProvider,
    FileContent,
    CommitResult,
    MRResult,
    IssueResult,
    Issue,
    MergeRequest,
    Commit,
    PushEvent,
    MREvent,
    CommentEvent,
    WebhookRegistration,
)
from providers.github.webhook import verify_webhook, parse_webhook_event


class GitHubProvider(RepositoryProvider):
    """GitHub implementation of RepositoryProvider using PyGithub."""

    def __init__(self, token: str | None):
        self._gh = Github(token)
        self._token = token or ""

    def get_file(self, project_id: int | str, path: str, ref: str) -> FileContent | None:
        try:
            repo = self._gh.get_repo(str(project_id))
            f = repo.get_contents(path, ref=ref)
            return FileContent(
                path=path,
                content=f.decoded_content.decode("utf-8"),
                ref=ref,
            )
        except GithubException:
            return None

    def get_file_at_sha(self, project_id: int | str, path: str, sha: str) -> FileContent | None:
        return self.get_file(project_id, path, sha)

    def commit_file(
        self, project_id: int | str, branch: str,
        path: str, content: str, message: str
    ) -> CommitResult:
        repo = self._gh.get_repo(str(project_id))
        try:
            existing = repo.get_contents(path, ref=branch)
            result = repo.update_file(path, message, content, existing.sha, branch=branch)
        except GithubException:
            result = repo.create_file(path, message, content, branch=branch)
        sha = result["commit"].sha
        return CommitResult(sha=sha, branch=branch)

    def create_mr(
        self, project_id: int | str,
        source_branch: str, target_branch: str,
        title: str, description: str
    ) -> MRResult:
        repo = self._gh.get_repo(str(project_id))
        pr = repo.create_pull(
            title=title,
            body=description,
            head=source_branch,
            base=target_branch,
        )
        return MRResult(iid=pr.number, web_url=pr.html_url)

    def get_mr(self, project_id: int | str, mr_iid: int) -> MergeRequest | None:
        try:
            repo = self._gh.get_repo(str(project_id))
            pr = repo.get_pull(mr_iid)
            return MergeRequest(
                iid=pr.number,
                title=pr.title,
                description=pr.body or "",
                source_branch=pr.head.ref,
                target_branch=pr.base.ref,
                web_url=pr.html_url,
            )
        except GithubException:
            return None

    def post_mr_comment(
        self, project_id: int | str, mr_iid: int, body: str
    ) -> None:
        repo = self._gh.get_repo(str(project_id))
        pr = repo.get_pull(mr_iid)
        pr.create_issue_comment(body)

    def post_inline_comment(
        self, project_id: int | str, mr_iid: int,
        file_path: str, line: int, body: str
    ) -> None:
        repo = self._gh.get_repo(str(project_id))
        pr = repo.get_pull(mr_iid)
        commits = list(pr.get_commits())
        head_commit = commits[-1]
        pr.create_review_comment(
            body=body,
            commit=head_commit,
            path=file_path,
            line=line,
        )

    def get_mr_diff(self, project_id: int | str, mr_iid: int) -> str:
        repo = self._gh.get_repo(str(project_id))
        pr = repo.get_pull(mr_iid)
        parts = []
        for f in pr.get_files():
            patch = f.patch or ""
            parts.append(f"--- a/{f.filename}\n+++ b/{f.filename}\n{patch}")
        return "\n".join(parts)

    def update_pipeline_status(
        self, project_id: int | str, sha: str,
        state: str, description: str, context: str = "pi-agent"
    ) -> None:
        repo = self._gh.get_repo(str(project_id))
        repo.get_commit(sha).create_status(
            state=state,
            description=description,
            context=context,
        )

    def get_issue(self, project_id: int | str, issue_iid: int) -> Issue | None:
        try:
            repo = self._gh.get_repo(str(project_id))
            issue = repo.get_issue(issue_iid)
            return Issue(
                iid=issue.number,
                title=issue.title,
                body=issue.body or "",
                state=issue.state,
                web_url=issue.html_url,
                author=issue.user.login if issue.user else "",
            )
        except GithubException:
            return None

    def list_issues(self, project_id: int | str, state: str = "open") -> list[Issue]:
        repo = self._gh.get_repo(str(project_id))
        gh_state = state if state in ("open", "closed", "all") else "open"
        issues = repo.get_issues(state=gh_state)
        return [
            Issue(
                iid=i.number,
                title=i.title,
                body=i.body or "",
                state=i.state,
                web_url=i.html_url,
                author=i.user.login if i.user else "",
            )
            for i in issues
            if i.pull_request is None  # exclude PRs (GitHub treats them as issues)
        ]

    def create_issue(
        self, project_id: int | str, title: str, body: str
    ) -> IssueResult:
        repo = self._gh.get_repo(str(project_id))
        issue = repo.create_issue(title=title, body=body)
        return IssueResult(iid=issue.number, web_url=issue.html_url)

    def post_issue_comment(
        self, project_id: int | str, issue_iid: int, body: str
    ) -> None:
        repo = self._gh.get_repo(str(project_id))
        issue = repo.get_issue(issue_iid)
        issue.create_comment(body)

    def search_projects(self, query: str, user_token: str) -> list[dict]:
        gh = Github(user_token)
        results = gh.search_repositories(query)
        return [
            {
                "id": r.full_name,
                "name": r.name,
                "path_with_namespace": r.full_name,
                "web_url": r.html_url,
            }
            for r in list(results)[:20]
        ]

    def list_branches(self, project_id: int | str, user_token: str = "") -> list[str]:
        gh = Github(user_token) if user_token else self._gh
        repo = gh.get_repo(str(project_id))
        return [b.name for b in repo.get_branches()]

    def list_open_mrs(self, project_id: int | str, user_token: str = "") -> list[MergeRequest]:
        gh = Github(user_token) if user_token else self._gh
        repo = gh.get_repo(str(project_id))
        return [
            MergeRequest(
                iid=pr.number,
                title=pr.title,
                description=pr.body or "",
                source_branch=pr.head.ref,
                target_branch=pr.base.ref,
                web_url=pr.html_url,
            )
            for pr in repo.get_pulls(state="open")
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
        gh = Github(user_token)
        repo = gh.get_repo(str(project_id))
        hook = repo.create_hook(
            name="web",
            config={"url": webhook_url, "content_type": "json", "secret": secret},
            events=["push", "pull_request", "issue_comment", "pull_request_review_comment"],
            active=True,
        )
        return WebhookRegistration(webhook_id=str(hook.id), webhook_url=webhook_url)

    def delete_webhook(
        self, project_id: int | str, webhook_id: str, user_token: str
    ) -> None:
        gh = Github(user_token)
        repo = gh.get_repo(str(project_id))
        repo.get_hook(int(webhook_id)).delete()
