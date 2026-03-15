"""Unit tests for providers/registry.py and providers/auth_registry.py"""
import os
import pytest
from unittest.mock import patch


# ── get_provider ──────────────────────────────────────────────────────────────

def test_get_provider_returns_gitlab_provider():
    with patch.dict(os.environ, {"PROVIDER": "gitlab", "GITLAB_TOKEN": "tok"}):
        from providers.registry import get_provider
        from providers.gitlab.provider import GitLabProvider
        provider = get_provider()
        assert isinstance(provider, GitLabProvider)


def test_get_provider_raises_for_unknown():
    with patch.dict(os.environ, {"PROVIDER": "unknown_xyz"}):
        from providers.registry import get_provider
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider()


# ── get_auth_provider ─────────────────────────────────────────────────────────

def test_get_auth_provider_returns_gitlab_auth_provider():
    with patch.dict(os.environ, {"AUTH_PROVIDER": "gitlab"}, clear=False):
        from providers.auth_registry import get_auth_provider
        from providers.gitlab.auth import GitLabAuthProvider
        provider = get_auth_provider()
        assert isinstance(provider, GitLabAuthProvider)


def test_get_auth_provider_falls_back_to_provider_env():
    env = {"PROVIDER": "gitlab"}
    # Remove AUTH_PROVIDER if set
    with patch.dict(os.environ, env):
        os.environ.pop("AUTH_PROVIDER", None)
        from providers.auth_registry import get_auth_provider
        from providers.gitlab.auth import GitLabAuthProvider
        provider = get_auth_provider()
        assert isinstance(provider, GitLabAuthProvider)


def test_get_auth_provider_raises_for_unknown():
    with patch.dict(os.environ, {"AUTH_PROVIDER": "unknownauth_xyz"}):
        from providers.auth_registry import get_auth_provider
        with pytest.raises(ValueError, match="Unknown auth provider"):
            get_auth_provider()
