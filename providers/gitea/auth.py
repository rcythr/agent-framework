from providers.auth_base import AuthProvider, OAuthProxyConfig, UserIdentity


class GiteaAuthProvider(AuthProvider):
    """
    Gitea auth implementation via oauth2-proxy using the Gitea provider.
    oauth2-proxy supports Gitea as a named provider (--provider=gitea).
    """

    def oauth_proxy_config(self) -> OAuthProxyConfig:
        return OAuthProxyConfig(
            provider_flag="gitea",
            extra_flags=[],
        )

    def extract_user(self, headers: dict) -> UserIdentity:
        username = headers.get("X-Auth-Request-User") or headers.get("x-auth-request-user", "")
        email = headers.get("X-Auth-Request-Email") or headers.get("x-auth-request-email", "")
        groups_header = (
            headers.get("X-Auth-Request-Groups") or headers.get("x-auth-request-groups", "")
        )
        groups = [g.strip() for g in groups_header.split(",") if g.strip()] if groups_header else []
        return UserIdentity(username=username, email=email, groups=groups)
