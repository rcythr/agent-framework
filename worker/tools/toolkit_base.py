from abc import ABC, abstractmethod


class ProviderToolkit(ABC):
    """
    Produces the list of tool definitions for a given provider.
    Each tool wraps a RepositoryProvider method with a name, description,
    and parameter schema suitable for LLM tool-calling.
    """

    @abstractmethod
    def get_tools(self) -> list[dict]:
        """
        Return tool definitions in the format expected by Agent.
        Tool execute functions must call self.provider methods only —
        no direct SDK calls.
        """
