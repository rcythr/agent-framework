## Authentication

## Overview

Authentication is handled by **oauth2-proxy** sitting in front of the gateway, but the specific IdP configuration — which OAuth2 provider to use, how to restrict access, and how to interpret forwarded identity headers — is driven by an `AuthProvider` abstraction that mirrors the `RepositoryProvider` pattern.

This means:
- Switching repo providers does not force a change in IdP (a GitHub-backed deployment can still authenticate via GitLab or Keycloak)
- Each repo provider ships a default `AuthProvider` that uses its own OAuth2 flow, so the common case requires no extra configuration
- Organisations with a centralised IdP (Keycloak, Okta, Azure AD) can override the auth provider independently of the repo provider

## AuthProvider Abstraction — `providers/auth_base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class OAuthProxyConfig:
    provider_flag: str          # value for --provider (e.g. "gitlab", "github", "oidc")
    oidc_issuer_url: str | None # value for --oidc-issuer-url (OIDC providers only)
    extra_flags: list[str]      # provider-specific flags, e.g.:
                                # ["--gitlab-group=my-group"]
                                # ["--github-org=my-org", "--github-team=agents"]

@dataclass
class UserIdentity:
    username: str               # canonical stable username for session ownership
    email: str
    groups: list[str]           # group/org/team memberships for future authz use

class AuthProvider(ABC):

    @abstractmethod
    def oauth_proxy_config(self) -> OAuthProxyConfig:
        """Return the oauth2-proxy flags for this IdP."""

    @abstractmethod
    def extract_user(self, headers: dict[str, str]) -> UserIdentity:
        """
        Extract a normalised UserIdentity from the forwarded headers
        set by oauth2-proxy after successful authentication.
        Different IdPs use different header names and value formats.
        """
```

## Initial AuthProvider Implementation

**`providers/gitlab/auth.py` — `GitLabAuthProvider`** is the only implementation built initially, since GitLab is the initial repository provider.

It implements `oauth_proxy_config()` returning `--provider=gitlab` and `--gitlab-group=<group>` in `extra_flags`, and `extract_user()` reading `X-Forwarded-User` (username), `X-Forwarded-Email`, and `X-Forwarded-Groups` — the headers oauth2-proxy sets in GitLab mode.

Additional implementations and their header mappings are documented in `providers/auth_base.py` as comments alongside the ABC:

- **`GitHubAuthProvider`** — `--provider=github`, `--github-org`, reads `X-Forwarded-User` (login handle) and `X-Forwarded-Groups`
- **`OIDCAuthProvider`** — `--provider=oidc`, configurable issuer URL, reads `X-Auth-Request-User` / `X-Auth-Request-Email` / `X-Auth-Request-Groups` (different header names from the provider-specific modes)

## Auth Provider Registry — `providers/auth_registry.py`

```python
import os
from providers.auth_base import AuthProvider

def get_auth_provider() -> AuthProvider:
    # Defaults to PROVIDER value so operators only need one env var in the common case.
    # Override AUTH_PROVIDER independently when repo and IdP differ (future use).
    auth_name = os.getenv("AUTH_PROVIDER", os.getenv("PROVIDER", "gitlab"))
    match auth_name:
        case "gitlab":
            from providers.gitlab.auth import GitLabAuthProvider
            return GitLabAuthProvider(
                group=os.getenv("GITLAB_AUTH_GROUP"),
                url=os.getenv("GITLAB_URL", "https://gitlab.com"),
            )
        # "github" and "oidc" cases — see providers/github/auth.py and providers/auth_registry.py
        case _:
            raise ValueError(f"Unknown auth provider: {auth_name!r}")
```

`AUTH_PROVIDER` defaults to the value of `PROVIDER`. The registry is structured so additional cases are added without touching any other code when new providers are implemented.

## How the Gateway Uses AuthProvider

The gateway calls `get_auth_provider()` once at startup and holds the instance for the lifetime of the process. It is used in two places:

**Identity extraction** — every authenticated request calls `auth_provider.extract_user(request.headers)` to obtain a `UserIdentity`. The `username` field is stored as `owner` on `SessionRecord` and `triggered_by` on `JobRecord`. Because `extract_user` is provider-specific, the correct header is always read regardless of IdP — the gateway never references `X-Forwarded-User` directly.

**oauth2-proxy configuration** — the gateway exposes `GET /internal/oauth2-proxy-config` which renders `auth_provider.oauth_proxy_config()` as oauth2-proxy CLI args. A Helm chart or init container consumes this at install time to generate the correct Deployment manifest. Constant flags (skip-auth routes, cookie settings, upstream URL, redirect URL) are set statically; only IdP-specific flags come from `AuthProvider`.

## GitLab OAuth2 Application Setup (Default Case)

When `AUTH_PROVIDER=gitlab` (the default), create a GitLab OAuth2 application:

**Group-level:** GitLab Group → Settings → Applications → Add new application
**Instance-level (self-hosted):** Admin → Applications → New application

| Field | Value |
|---|---|
| Name | `Agent Control Plane` |
| Redirect URI | `https://pi-agent.your-domain.com/oauth2/callback` |
| Scopes | `api`, `read_user`, `openid` |
| Confidential | ✅ |

Store the Application ID and Secret in the `oauth2-proxy-creds` K8s Secret alongside `GITLAB_AUTH_GROUP`.

## Ingress Routing

Webhook and internal paths bypass oauth2-proxy and route directly to the gateway. All browser traffic goes through oauth2-proxy. The ingress configuration is IdP-agnostic — oauth2-proxy presents a consistent interface to the Ingress regardless of which backend IdP is in use.

## When to Override AUTH_PROVIDER

| Scenario | `PROVIDER` | `AUTH_PROVIDER` |
|---|---|---|
| GitLab repos, GitLab auth | `gitlab` | _(inherits)_ |
| GitHub repos, GitHub auth | `github` | _(inherits)_ |
| GitLab repos, Keycloak/Okta | `gitlab` | `oidc` |
| GitHub repos, Keycloak/Okta | `github` | `oidc` |
| Multiple providers, single IdP | _(per deployment)_ | `oidc` |

