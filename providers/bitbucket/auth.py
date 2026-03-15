from providers.auth_base import AuthProvider, OAuthProxyConfig, UserIdentity


class BitbucketAuthProvider(AuthProvider):
    """
    Bitbucket auth implementation via generic OIDC.
    oauth2-proxy does not have a native Bitbucket provider, so this uses
    '--provider=oidc' pointed at Atlassian's OIDC endpoint.
    """

    def oauth_proxy_config(self) -> OAuthProxyConfig:
        return OAuthProxyConfig(
            provider_flag="oidc",
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
