"""Unit tests for providers/bitbucket/auth.py"""
import pytest
from providers.bitbucket.auth import BitbucketAuthProvider
from providers.auth_base import UserIdentity, OAuthProxyConfig


def test_oauth_proxy_config_provider_flag():
    auth = BitbucketAuthProvider()
    config = auth.oauth_proxy_config()
    assert isinstance(config, OAuthProxyConfig)
    assert config.provider_flag == "oidc"


def test_oauth_proxy_config_no_extra_flags():
    auth = BitbucketAuthProvider()
    config = auth.oauth_proxy_config()
    assert config.extra_flags == []


def test_extract_user_reads_auth_request_headers():
    auth = BitbucketAuthProvider()
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
    auth = BitbucketAuthProvider()
    headers = {
        "X-Auth-Request-User": "bob",
        "X-Auth-Request-Email": "bob@example.com",
        "X-Auth-Request-Groups": "",
    }
    user = auth.extract_user(headers)
    assert user.groups == []


def test_extract_user_missing_headers():
    auth = BitbucketAuthProvider()
    user = auth.extract_user({})
    assert user.username == ""
    assert user.email == ""
    assert user.groups == []
