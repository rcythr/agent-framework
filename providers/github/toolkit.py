from worker.tools.toolkit_base import ProviderToolkit


class GitHubToolkit(ProviderToolkit):
    """GitHub placeholder — not yet implemented."""

    def __init__(self, provider, project_id):
        raise NotImplementedError("GitHub toolkit is not yet implemented")

    def get_tools(self) -> list[dict]:
        raise NotImplementedError
