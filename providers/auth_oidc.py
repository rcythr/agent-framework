from providers.auth_base import AuthProvider, OAuthProxyConfig, UserIdentity


class OIDCAuthProvider(AuthProvider):
    """
    Generic OIDC implementation of AuthProvider.
    Works with any OIDC-compliant identity provider (Keycloak, Okta, Dex, etc.)
    via oauth2-proxy's '--provider=oidc' mode.

    oauth2-proxy sets X-Auth-Request-* headers (not X-Forwarded-*) when
    using the OIDC provider.
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
