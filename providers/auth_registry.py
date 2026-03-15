import os
from providers.auth_base import AuthProvider


def get_auth_provider() -> AuthProvider:
    """
    Return the configured auth provider instance.
    The AUTH_PROVIDER env var selects the implementation; falls back to PROVIDER if unset.
    """
    auth_provider_name = os.getenv("AUTH_PROVIDER") or os.getenv("PROVIDER", "gitlab")
    match auth_provider_name:
        case "gitlab":
            from providers.gitlab.auth import GitLabAuthProvider
            return GitLabAuthProvider()
        case _:
            raise ValueError(f"Unknown auth provider: {auth_provider_name!r}")
