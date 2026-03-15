"""Unit tests for register_webhook / delete_webhook across all providers."""
import pytest
from unittest.mock import MagicMock, patch, call

from providers.base import WebhookRegistration


# ── GitLab ────────────────────────────────────────────────────────────────────

def test_gitlab_register_webhook():
    with patch("providers.gitlab.provider.gitlab") as mock_gitlab:
        mock_project = MagicMock()
        mock_hook = MagicMock()
        mock_hook.id = 42
        mock_project.hooks.create.return_value = mock_hook
        mock_gl = MagicMock()
        mock_gl.projects.get.return_value = mock_project
        mock_gitlab.Gitlab.return_value = mock_gl

        from providers.gitlab.provider import GitLabProvider
        provider = GitLabProvider.__new__(GitLabProvider)
        provider._gl = MagicMock()
        provider._gl.url = "https://gitlab.example.com"

        reg = provider.register_webhook("99", "https://phalanx.example.com/webhook", "secret123", "user-token")

        assert isinstance(reg, WebhookRegistration)
        assert reg.webhook_id == "42"
        assert reg.webhook_url == "https://phalanx.example.com/webhook"
        mock_project.hooks.create.assert_called_once()
        create_args = mock_project.hooks.create.call_args[0][0]
        assert create_args["url"] == "https://phalanx.example.com/webhook"
        assert create_args["token"] == "secret123"
        assert create_args["push_events"] is True
        assert create_args["merge_requests_events"] is True
        assert create_args["note_events"] is True


def test_gitlab_delete_webhook():
    with patch("providers.gitlab.provider.gitlab") as mock_gitlab:
        mock_project = MagicMock()
        mock_gl = MagicMock()
        mock_gl.projects.get.return_value = mock_project
        mock_gitlab.Gitlab.return_value = mock_gl

        from providers.gitlab.provider import GitLabProvider
        provider = GitLabProvider.__new__(GitLabProvider)
        provider._gl = MagicMock()
        provider._gl.url = "https://gitlab.example.com"

        provider.delete_webhook("99", "42", "user-token")

        mock_project.hooks.delete.assert_called_once_with(42)


# ── GitHub ────────────────────────────────────────────────────────────────────

def test_github_register_webhook():
    with patch("providers.github.provider.Github") as MockGithub:
        mock_repo = MagicMock()
        mock_hook = MagicMock()
        mock_hook.id = 77
        mock_repo.create_hook.return_value = mock_hook
        MockGithub.return_value.get_repo.return_value = mock_repo

        from providers.github.provider import GitHubProvider
        provider = GitHubProvider.__new__(GitHubProvider)
        provider._gh = MagicMock()

        reg = provider.register_webhook("alice/myrepo", "https://phalanx.example.com/webhook", "secret123", "user-token")

        assert isinstance(reg, WebhookRegistration)
        assert reg.webhook_id == "77"
        mock_repo.create_hook.assert_called_once()
        _, kwargs = mock_repo.create_hook.call_args
        assert kwargs["config"]["url"] == "https://phalanx.example.com/webhook"
        assert kwargs["config"]["secret"] == "secret123"
        assert "push" in kwargs["events"]
        assert "pull_request" in kwargs["events"]


def test_github_delete_webhook():
    with patch("providers.github.provider.Github") as MockGithub:
        mock_repo = MagicMock()
        mock_hook = MagicMock()
        mock_repo.get_hook.return_value = mock_hook
        MockGithub.return_value.get_repo.return_value = mock_repo

        from providers.github.provider import GitHubProvider
        provider = GitHubProvider.__new__(GitHubProvider)
        provider._gh = MagicMock()

        provider.delete_webhook("alice/myrepo", "77", "user-token")

        mock_repo.get_hook.assert_called_once_with(77)
        mock_hook.delete.assert_called_once()


# ── Bitbucket ─────────────────────────────────────────────────────────────────

def test_bitbucket_register_webhook():
    with patch("providers.bitbucket.provider.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"uuid": "{abc-123}"}
        mock_httpx.post.return_value = mock_resp

        from providers.bitbucket.provider import BitbucketProvider
        provider = BitbucketProvider.__new__(BitbucketProvider)
        provider._auth = ("svc", "pass")

        reg = provider.register_webhook(
            "alice/myrepo", "https://phalanx.example.com/webhook", "secret123", "alice:token"
        )

        assert isinstance(reg, WebhookRegistration)
        assert reg.webhook_id == "{abc-123}"
        mock_httpx.post.assert_called_once()
        _, kwargs = mock_httpx.post.call_args
        payload = kwargs["json"]
        assert payload["url"] == "https://phalanx.example.com/webhook"
        assert payload["secret"] == "secret123"
        assert "repo:push" in payload["events"]
        assert payload["active"] is True


def test_bitbucket_delete_webhook():
    with patch("providers.bitbucket.provider.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_httpx.delete.return_value = mock_resp

        from providers.bitbucket.provider import BitbucketProvider
        provider = BitbucketProvider.__new__(BitbucketProvider)
        provider._auth = ("svc", "pass")

        provider.delete_webhook("alice/myrepo", "{abc-123}", "alice:token")

        mock_httpx.delete.assert_called_once()
        url = mock_httpx.delete.call_args[0][0]
        assert "{abc-123}" in url
        assert "alice/myrepo" in url


# ── Gitea ─────────────────────────────────────────────────────────────────────

def test_gitea_register_webhook():
    with patch("providers.gitea.provider.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 55}
        mock_httpx.post.return_value = mock_resp

        from providers.gitea.provider import GiteaProvider
        provider = GiteaProvider.__new__(GiteaProvider)
        provider._base = "https://gitea.example.com/api/v1"
        provider._headers = {"Authorization": "token svc-token"}

        reg = provider.register_webhook(
            "alice/myrepo", "https://phalanx.example.com/webhook", "secret123", "user-token"
        )

        assert isinstance(reg, WebhookRegistration)
        assert reg.webhook_id == "55"
        mock_httpx.post.assert_called_once()
        _, kwargs = mock_httpx.post.call_args
        payload = kwargs["json"]
        assert payload["config"]["url"] == "https://phalanx.example.com/webhook"
        assert payload["config"]["secret"] == "secret123"
        assert "push" in payload["events"]
        assert payload["active"] is True


def test_gitea_delete_webhook():
    with patch("providers.gitea.provider.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_httpx.delete.return_value = mock_resp

        from providers.gitea.provider import GiteaProvider
        provider = GiteaProvider.__new__(GiteaProvider)
        provider._base = "https://gitea.example.com/api/v1"
        provider._headers = {"Authorization": "token svc-token"}

        provider.delete_webhook("alice/myrepo", "55", "user-token")

        mock_httpx.delete.assert_called_once()
        url = mock_httpx.delete.call_args[0][0]
        assert "alice/myrepo" in url
        assert "55" in url
