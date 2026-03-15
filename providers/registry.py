import os
from providers.base import RepositoryProvider


def get_provider() -> RepositoryProvider:
    """
    Return the configured provider instance.
    The PROVIDER env var selects the implementation; credentials
    are read from provider-specific env vars by each implementation.
    """
    provider_name = os.getenv("PROVIDER", "gitlab")
    match provider_name:
        case "gitlab":
            from providers.gitlab.provider import GitLabProvider
            return GitLabProvider(
                url=os.getenv("GITLAB_URL", "https://gitlab.com"),
                token=os.getenv("GITLAB_TOKEN"),
            )
        case "github":
            from providers.github.provider import GitHubProvider
            return GitHubProvider(
                token=os.getenv("GITHUB_TOKEN"),
            )
        case _:
            raise ValueError(f"Unknown provider: {provider_name!r}")
