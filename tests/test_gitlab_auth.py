"""Unit tests for providers/gitlab/auth.py"""
import pytest
from providers.gitlab.auth import GitLabAuthProvider
from providers.auth_base import UserIdentity, OAuthProxyConfig


def test_oauth_proxy_config_provider_flag():
    auth = GitLabAuthProvider()
    config = auth.oauth_proxy_config()
    assert isinstance(config, OAuthProxyConfig)
    assert config.provider_flag == "gitlab"


def test_oauth_proxy_config_extra_flags_contains_gitlab_group():
    auth = GitLabAuthProvider()
    config = auth.oauth_proxy_config()
    assert "--gitlab-group" in config.extra_flags


def test_extract_user_reads_forwarded_headers():
    auth = GitLabAuthProvider()
    headers = {
        "X-Forwarded-User": "alice",
        "X-Forwarded-Email": "alice@example.com",
        "X-Forwarded-Groups": "dev,admin",
    }
    user = auth.extract_user(headers)
    assert isinstance(user, UserIdentity)
    assert user.username == "alice"
    assert user.email == "alice@example.com"
    assert "dev" in user.groups
    assert "admin" in user.groups


def test_extract_user_empty_groups():
    auth = GitLabAuthProvider()
    headers = {
        "X-Forwarded-User": "bob",
        "X-Forwarded-Email": "bob@example.com",
        "X-Forwarded-Groups": "",
    }
    user = auth.extract_user(headers)
    assert user.groups == []


def test_extract_user_missing_headers():
    auth = GitLabAuthProvider()
    user = auth.extract_user({})
    assert user.username == ""
    assert user.email == ""
    assert user.groups == []
