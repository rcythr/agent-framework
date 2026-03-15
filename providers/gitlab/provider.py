import gitlab
from gitlab.exceptions import GitlabGetError

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
from providers.gitlab.webhook import verify_webhook, parse_webhook_event


class GitLabProvider(RepositoryProvider):
    """GitLab implementation of RepositoryProvider using python-gitlab."""

    def __init__(self, url: str, token: str | None):
        self._gl = gitlab.Gitlab(url=url, private_token=token)

    def get_file(self, project_id: int | str, path: str, ref: str) -> FileContent | None:
        try:
            project = self._gl.projects.get(project_id)
            f = project.files.get(file_path=path, ref=ref)
            return FileContent(
                path=path,
                content=f.decode().decode("utf-8"),
                ref=ref,
            )
        except GitlabGetError:
            return None

    def get_file_at_sha(self, project_id: int | str, path: str, sha: str) -> FileContent | None:
        return self.get_file(project_id, path, sha)

    def commit_file(
        self, project_id: int | str, branch: str,
        path: str, content: str, message: str
    ) -> CommitResult:
        project = self._gl.projects.get(project_id)
        data = {
            "branch": branch,
            "content": content,
            "commit_message": message,
        }
        try:
            project.files.update(file_path=path, new_data=data)
        except GitlabGetError:
            project.files.create({**data, "file_path": path})

        # Fetch the latest commit SHA on the branch
        branch_obj = project.branches.get(branch)
        sha = branch_obj.commit["id"]
        return CommitResult(sha=sha, branch=branch)

    def create_mr(
        self, project_id: int | str,
        source_branch: str, target_branch: str,
        title: str, description: str
    ) -> MRResult:
        project = self._gl.projects.get(project_id)
        mr = project.mergerequests.create({
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "description": description,
        })
        return MRResult(iid=mr.iid, web_url=mr.web_url)

    def post_mr_comment(
        self, project_id: int | str, mr_iid: int, body: str
    ) -> None:
        project = self._gl.projects.get(project_id)
        mr = project.mergerequests.get(mr_iid)
        mr.notes.create({"body": body})

    def post_inline_comment(
        self, project_id: int | str, mr_iid: int,
        file_path: str, line: int, body: str
    ) -> None:
        project = self._gl.projects.get(project_id)
        mr = project.mergerequests.get(mr_iid)
        mr.discussions.create({
            "body": body,
            "position": {
                "position_type": "text",
                "new_path": file_path,
                "new_line": line,
            },
        })

    def get_mr_diff(self, project_id: int | str, mr_iid: int) -> str:
        project = self._gl.projects.get(project_id)
        mr = project.mergerequests.get(mr_iid)
        diffs = mr.diffs.list()
        if not diffs:
            return ""
        diff_detail = mr.diffs.get(diffs[0].id)
        parts = []
        for d in diff_detail.diffs:
            parts.append(f"--- a/{d['old_path']}\n+++ b/{d['new_path']}\n{d['diff']}")
        return "\n".join(parts)

    def update_pipeline_status(
        self, project_id: int | str, sha: str,
        state: str, description: str, context: str = "pi-agent"
    ) -> None:
        project = self._gl.projects.get(project_id)
        project.commits.get(sha).statuses.create({
            "state": state,
            "description": description,
            "name": context,
        })

    def search_projects(self, query: str, user_token: str) -> list[dict]:
        gl = gitlab.Gitlab(url=self._gl.url, private_token=user_token)
        projects = gl.projects.list(search=query, membership=True)
        return [
            {
                "id": p.id,
                "name": p.name,
                "path_with_namespace": p.path_with_namespace,
                "web_url": p.web_url,
            }
            for p in projects
        ]

    def list_branches(self, project_id: int | str, user_token: str) -> list[str]:
        gl = gitlab.Gitlab(url=self._gl.url, private_token=user_token)
        project = gl.projects.get(project_id)
        return [b.name for b in project.branches.list(all=True)]

    def list_open_mrs(self, project_id: int | str, user_token: str) -> list[MergeRequest]:
        gl = gitlab.Gitlab(url=self._gl.url, private_token=user_token)
        project = gl.projects.get(project_id)
        mrs = project.mergerequests.list(state="opened", all=True)
        return [
            MergeRequest(
                iid=mr.iid,
                title=mr.title,
                description=mr.description or "",
                source_branch=mr.source_branch,
                target_branch=mr.target_branch,
                web_url=mr.web_url,
            )
            for mr in mrs
        ]

    def verify_webhook(self, headers: dict, body: bytes, secret: str) -> bool:
        return verify_webhook(headers, body, secret)

    def parse_webhook_event(
        self, headers: dict, body: dict
    ) -> PushEvent | MREvent | CommentEvent | None:
        return parse_webhook_event(headers, body)
