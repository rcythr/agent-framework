from providers.auth_base import AuthProvider, OAuthProxyConfig, UserIdentity


class GitLabAuthProvider(AuthProvider):
    """GitLab implementation of AuthProvider."""

    def oauth_proxy_config(self) -> OAuthProxyConfig:
        return OAuthProxyConfig(
            provider_flag="gitlab",
            extra_flags=["--gitlab-group"],
        )

    def extract_user(self, headers: dict) -> UserIdentity:
        username = headers.get("X-Forwarded-User") or headers.get("x-forwarded-user", "")
        email = headers.get("X-Forwarded-Email") or headers.get("x-forwarded-email", "")
        groups_header = headers.get("X-Forwarded-Groups") or headers.get("x-forwarded-groups", "")
        groups = [g.strip() for g in groups_header.split(",") if g.strip()] if groups_header else []
        return UserIdentity(username=username, email=email, groups=groups)
