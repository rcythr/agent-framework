from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class UserIdentity:
    username: str
    email: str
    groups: list[str]


@dataclass
class OAuthProxyConfig:
    provider_flag: str
    extra_flags: list[str] = field(default_factory=list)


class AuthProvider(ABC):
    """Abstract interface for authentication providers."""

    @abstractmethod
    def oauth_proxy_config(self) -> OAuthProxyConfig:
        """Return oauth2-proxy configuration flags for this provider."""

    @abstractmethod
    def extract_user(self, headers: dict) -> UserIdentity:
        """Extract user identity from request headers set by oauth2-proxy."""
