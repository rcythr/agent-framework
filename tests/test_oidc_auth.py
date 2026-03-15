"""Unit tests for providers/auth_oidc.py"""
import pytest
from providers.auth_oidc import OIDCAuthProvider
from providers.auth_base import UserIdentity, OAuthProxyConfig


def test_oauth_proxy_config_provider_flag():
    auth = OIDCAuthProvider()
    config = auth.oauth_proxy_config()
    assert isinstance(config, OAuthProxyConfig)
    assert config.provider_flag == "oidc"


def test_oauth_proxy_config_no_extra_flags():
    auth = OIDCAuthProvider()
    config = auth.oauth_proxy_config()
    assert config.extra_flags == []


def test_extract_user_reads_auth_request_headers():
    auth = OIDCAuthProvider()
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


def test_extract_user_single_group():
    auth = OIDCAuthProvider()
    headers = {
        "X-Auth-Request-User": "bob",
        "X-Auth-Request-Email": "bob@example.com",
        "X-Auth-Request-Groups": "ops",
    }
    user = auth.extract_user(headers)
    assert user.groups == ["ops"]


def test_extract_user_empty_groups():
    auth = OIDCAuthProvider()
    headers = {
        "X-Auth-Request-User": "carol",
        "X-Auth-Request-Email": "carol@example.com",
        "X-Auth-Request-Groups": "",
    }
    user = auth.extract_user(headers)
    assert user.groups == []


def test_extract_user_missing_headers():
    auth = OIDCAuthProvider()
    user = auth.extract_user({})
    assert user.username == ""
    assert user.email == ""
    assert user.groups == []


def test_extract_user_lowercase_headers():
    auth = OIDCAuthProvider()
    headers = {
        "x-auth-request-user": "dave",
        "x-auth-request-email": "dave@example.com",
        "x-auth-request-groups": "platform",
    }
    user = auth.extract_user(headers)
    assert user.username == "dave"
    assert "platform" in user.groups
