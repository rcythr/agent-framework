# Provider setup guides

Each guide covers credentials, OAuth application setup, webhook configuration, per-project `.agents/config.yaml`, and deployment for that specific provider.

| Provider | Guide | Notes |
|---|---|---|
| GitLab | [gitlab.md](gitlab.md) | GitLab.com and self-hosted CE/EE; default provider |
| GitHub | [github.md](github.md) | GitHub.com; PAT or fine-grained token |
| Bitbucket Cloud | [bitbucket.md](bitbucket.md) | App passwords; Atlassian OIDC for dashboard auth |
| Gitea | [gitea.md](gitea.md) | Self-hosted; custom CA often required |

## Choosing a provider

All four providers implement the same `RepositoryProvider` interface and are fully interchangeable — only the credentials and webhook format differ. Set the `PROVIDER` environment variable (or the `provider` Helm value) to switch between them.

Only one provider can be active per Phalanx deployment. To serve multiple Git platforms, run a separate Phalanx deployment for each.

## Quick comparison

| | GitLab | GitHub | Bitbucket | Gitea |
|---|---|---|---|---|
| Credential type | Personal token | Personal token | App password | API token |
| Webhook signature | Direct compare | HMAC-SHA256 `sha256=` | HMAC-SHA256 `sha256=` | HMAC-SHA256 plain hex |
| Dashboard auth | GitLab OAuth | GitHub OAuth | Atlassian OIDC | Gitea OAuth |
| Self-hosted | Yes | GitHub Enterprise | No (Cloud only) | Yes (primary use case) |
| Project ID | int or slug | `owner/repo` | `workspace/slug` | `owner/repo` |
