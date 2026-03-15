# Phase 0 — Provider Abstraction Layer

## Goal
Establish the provider abstraction layer that all subsequent phases depend on. This is pure Python with no infrastructure dependencies and must be completed first.

## Deliverables

### `shared/models.py` (initial)
Define the following Pydantic models:
- `TaskSpec` — `task: str`, `project_id: int`, `context: dict[str, Any]`
- `JobRecord` — `id`, `task`, `project_id`, `project_name`, `status` (Literal), `context`, `started_at`, `finished_at`, `gas_limit`, `gas_used`, `gas_topups`
- `LogEvent` — `job_id`, `sequence`, `timestamp`, `event_type` (Literal of all event types), `payload`
- `SkillDef`, `ToolDef`, `ProjectConfig`, `AgentConfig`
- `SessionContext`, `SessionMessage`, `SessionRecord`

All models to be defined now even if not all fields are used until later phases. See `shared/models.py` in the design doc for full field specifications.

### `providers/base.py`
Define the `RepositoryProvider` abstract base class with all abstract methods:
- `get_file(project_id, path, ref)` → `FileContent | None`
- `get_file_at_sha(project_id, path, sha)` → `FileContent | None`
- `commit_file(project_id, branch, path, content, message)` → `CommitResult`
- `create_mr(project_id, source_branch, target_branch, title, description)` → `MRResult`
- `post_mr_comment(project_id, mr_iid, body)` → `None`
- `post_inline_comment(project_id, mr_iid, path, line, body)` → `None`
- `get_mr_diff(project_id, mr_iid)` → `str`
- `update_pipeline_status(project_id, sha, state, description)` → `None`
- `search_projects(query, user_token)` → `list[dict]`
- `list_branches(project_id, user_token)` → `list[str]`
- `list_open_mrs(project_id, user_token)` → `list[MergeRequest]`
- `verify_webhook(headers, body, secret)` → `bool`
- `parse_webhook_event(headers, body)` → `PushEvent | MREvent | CommentEvent | None`

Also define all shared data models: `FileContent`, `CommitResult`, `MRResult`, `MergeRequest`, `Commit`, `PushEvent`, `MREvent`, `CommentEvent`.

### `providers/auth_base.py`
Define:
- `UserIdentity` dataclass — `username: str`, `email: str`, `groups: list[str]`
- `OAuthProxyConfig` dataclass — `provider_flag: str`, `extra_flags: list[str]`
- `AuthProvider` ABC with abstract methods:
  - `oauth_proxy_config()` → `OAuthProxyConfig`
  - `extract_user(headers: dict)` → `UserIdentity`

### `providers/gitlab/provider.py`
Full GitLab implementation of `RepositoryProvider` using `python-gitlab`. All methods must return shared Pydantic models — no SDK types may leak out of this module.

Key implementation note for `commit_file`: attempt `update` first; fall back to `create` if `GitlabGetError` is raised.

### `providers/gitlab/webhook.py`
- `verify_webhook` — HMAC comparison against `X-Gitlab-Token` header
- `parse_webhook_event` — map `X-Gitlab-Event` + payload to `PushEvent`, `MREvent`, or `CommentEvent`; return `None` for unhandled types

### `providers/gitlab/auth.py`
`GitLabAuthProvider` implementing `AuthProvider`:
- `oauth_proxy_config()` — returns `provider_flag="gitlab"` and `--gitlab-group` in `extra_flags`
- `extract_user(headers)` — reads `X-Forwarded-User`, `X-Forwarded-Email`, `X-Forwarded-Groups` and returns `UserIdentity`

### `providers/registry.py`
`get_provider()` factory — reads `PROVIDER` env var (default `"gitlab"`); returns appropriate `RepositoryProvider` instance; raises `ValueError` for unknown provider.

### `providers/auth_registry.py`
`get_auth_provider()` factory — reads `AUTH_PROVIDER` env var, falls back to `PROVIDER` if unset; raises `ValueError` for unknown auth provider.

### `providers/github/` (placeholder)
Create stub files `provider.py`, `webhook.py`, `toolkit.py` with `NotImplementedError` in all methods. These placeholders ensure the directory structure is present for Phase 8.

### `worker/tools/toolkit_base.py`
`ProviderToolkit` ABC:
```python
class ProviderToolkit(ABC):
    @abstractmethod
    def get_tools(self) -> list[dict]: ...
```

### `requirements.txt`
```
fastapi>=0.111.0
uvicorn>=0.29.0
kubernetes>=29.0.0
python-gitlab>=4.6.0
httpx>=0.27.0
pydantic>=2.7.0
pydantic-settings>=2.2.0
openai>=1.30.0
aiosqlite>=0.20.0
sse-starlette>=2.1.0
pyyaml>=6.0.1
```

---

## Tests to Write First (TDD)

### Unit tests — `providers/gitlab/provider.py`
- `GitLabProvider.parse_webhook_event` maps all three raw GitLab payloads to correct shared event models
- `GitLabProvider.parse_webhook_event` returns `None` for unknown event types
- `GitLabProvider.verify_webhook` returns `True` for valid HMAC signature
- `GitLabProvider.verify_webhook` returns `False` for invalid signature
- `GitLabProvider.commit_file` falls back to `create` when `update` raises `GitlabGetError`
- Each `GitLabProvider` method — mock `python-gitlab`; assert correct API calls and return values are shared model instances, not SDK types

### Unit tests — `providers/registry.py`
- `get_provider()` returns `GitLabProvider` when `PROVIDER=gitlab`
- `get_provider()` raises `ValueError` for unknown provider name

### Unit tests — `providers/auth_registry.py`
- `get_auth_provider()` returns `GitLabAuthProvider` when `AUTH_PROVIDER=gitlab`
- `get_auth_provider()` defaults to `PROVIDER` value when `AUTH_PROVIDER` is unset
- `get_auth_provider()` raises `ValueError` for unknown auth provider names

### Unit tests — `providers/gitlab/auth.py`
- `GitLabAuthProvider.extract_user()` reads `X-Forwarded-User`, `X-Forwarded-Email`, `X-Forwarded-Groups` and returns correct `UserIdentity`
- `GitLabAuthProvider.oauth_proxy_config()` returns `provider_flag="gitlab"` and `--gitlab-group` in `extra_flags`

---

## Definition of Done
- All abstract methods defined with correct signatures
- GitLab implementation passes all unit tests with mocked SDK
- Registries return correct implementations or raise on unknown names
- No code outside `providers/` imports from concrete provider modules directly
- `shared/models.py` importable with all models valid

## Dependencies
**None** — this phase has no external dependencies and can begin immediately.
