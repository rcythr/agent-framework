import os

from providers.registry import get_provider
from worker.tools.toolkit_base import ProviderToolkit


def get_toolkit(project_id: int | str) -> ProviderToolkit:
    provider = get_provider()
    provider_name = os.getenv("PROVIDER", "gitlab")
    match provider_name:
        case "gitlab":
            from providers.gitlab.toolkit import GitLabToolkit
            return GitLabToolkit(provider=provider, project_id=project_id)
        case _:
            raise ValueError(f"No toolkit for provider: {provider_name!r}")
