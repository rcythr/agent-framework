"""Unit tests for providers/github/auth.py"""
import pytest
from providers.github.auth import GitHubAuthProvider
from providers.auth_base import UserIdentity, OAuthProxyConfig


def test_oauth_proxy_config_provider_flag():
    auth = GitHubAuthProvider()
    config = auth.oauth_proxy_config()
    assert isinstance(config, OAuthProxyConfig)
    assert config.provider_flag == "github"


def test_oauth_proxy_config_extra_flags_contains_github_org():
    auth = GitHubAuthProvider()
    config = auth.oauth_proxy_config()
    assert "--github-org" in config.extra_flags


def test_extract_user_reads_auth_request_headers():
    auth = GitHubAuthProvider()
    headers = {
        "X-Auth-Request-User": "alice",
        "X-Auth-Request-Email": "alice@example.com",
        "X-Auth-Request-Groups": "dev,admin",
    }
    user = auth.extract_user(headers)
    assert isinstance(user, UserIdentity)
    assert user.username == "alice"
    assert user.email == "alice@example.com"
    assert "dev" in user.groups
    assert "admin" in user.groups


def test_extract_user_empty_groups():
    auth = GitHubAuthProvider()
    headers = {
        "X-Auth-Request-User": "bob",
        "X-Auth-Request-Email": "bob@example.com",
        "X-Auth-Request-Groups": "",
    }
    user = auth.extract_user(headers)
    assert user.groups == []


def test_extract_user_missing_headers():
    auth = GitHubAuthProvider()
    user = auth.extract_user({})
    assert user.username == ""
    assert user.email == ""
    assert user.groups == []


def test_extract_user_lowercase_headers():
    auth = GitHubAuthProvider()
    headers = {
        "x-auth-request-user": "dave",
        "x-auth-request-email": "dave@example.com",
        "x-auth-request-groups": "ops",
    }
    user = auth.extract_user(headers)
    assert user.username == "dave"
    assert user.email == "dave@example.com"
    assert "ops" in user.groups
