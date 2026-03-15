"""Tests for gateway/config_loader.py — Phase 4."""

import os
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config_loader import ConfigLoader, _merge_skills, _merge_tools
from providers.base import FileContent
from shared.models import AgentConfig, SkillDef, ToolDef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_file_content(content: str, path: str = ".agents/config.yaml") -> FileContent:
    return FileContent(path=path, content=content, ref="abc123")


def _make_provider(file_content=None):
    provider = MagicMock()
    provider.get_file_at_sha.return_value = file_content
    return provider


def _make_loader(file_content=None, global_config_dir="global-config"):
    provider = _make_provider(file_content)
    loader = ConfigLoader(provider=provider, global_config_dir=global_config_dir)
    return loader, provider


# ---------------------------------------------------------------------------
# Global config loading
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returns_global_defaults_when_no_config_file(tmp_path):
    """When provider returns None (file absent), fall back to global defaults."""
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text(
        "base_prompt: 'global prompt'\nskills: []\ntools: []\n"
    )

    loader, provider = _make_loader(file_content=None, global_config_dir=str(gcdir))
    config = await loader.resolve(project_id=1, sha="deadbeef")

    assert isinstance(config, AgentConfig)
    assert config.system_prompt == "global prompt"
    assert config.skills == []
    assert config.tools == []
    assert config.allowed_users == []
    provider.get_file_at_sha.assert_called_once_with(1, ".agents/config.yaml", "deadbeef")


@pytest.mark.asyncio
async def test_returns_global_defaults_on_malformed_yaml(tmp_path):
    """Malformed YAML in .agents/config.yaml → warning + global defaults."""
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: 'base'\nskills: []\ntools: []\n")

    bad_yaml = _make_file_content("key: [unterminated")
    loader, _ = _make_loader(file_content=bad_yaml, global_config_dir=str(gcdir))

    with patch("gateway.config_loader.logger") as mock_log:
        config = await loader.resolve(project_id=2, sha="sha1")

    assert config.system_prompt == "base"
    assert config.allowed_users == []
    assert mock_log.warning.called


@pytest.mark.asyncio
async def test_returns_global_defaults_on_pydantic_validation_failure(tmp_path):
    """Invalid Pydantic data in .agents/config.yaml → warning + global defaults."""
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: 'base'\nskills: []\ntools: []\n")

    # prompt_mode must be 'append' or 'override'; 'bad_mode' fails Pydantic
    bad_config = _make_file_content("prompt_mode: bad_mode\n")
    loader, _ = _make_loader(file_content=bad_config, global_config_dir=str(gcdir))

    with patch("gateway.config_loader.logger") as mock_log:
        config = await loader.resolve(project_id=3, sha="sha2")

    assert config.system_prompt == "base"
    assert config.allowed_users == []
    assert mock_log.warning.called


# ---------------------------------------------------------------------------
# Skill and tool merging
# ---------------------------------------------------------------------------

def test_merge_skills_project_appended_after_global():
    global_skills = [SkillDef(name="global-skill", description="global")]
    project_skills = [SkillDef(name="project-skill", description="project")]
    result = _merge_skills(global_skills, project_skills)
    names = [s.name for s in result]
    assert "global-skill" in names
    assert "project-skill" in names


def test_merge_skills_deduplication_project_wins():
    global_skills = [SkillDef(name="shared", description="global description")]
    project_skills = [SkillDef(name="shared", description="project description")]
    result = _merge_skills(global_skills, project_skills)
    assert len(result) == 1
    assert result[0].description == "project description"


def test_merge_tools_deduplication_project_wins():
    global_tools = [ToolDef(name="notify", description="global tool")]
    project_tools = [ToolDef(name="notify", description="project tool")]
    result = _merge_tools(global_tools, project_tools)
    assert len(result) == 1
    assert result[0].description == "project tool"


@pytest.mark.asyncio
async def test_skill_merging_integration(tmp_path):
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text(textwrap.dedent("""\
        base_prompt: base
        skills:
          - name: global-skill
            description: from global
        tools: []
    """))

    project_yaml = textwrap.dedent("""\
        skills:
          - name: project-skill
            description: from project
          - name: global-skill
            description: overridden by project
        allowed_users:
          - alice
    """)
    loader, _ = _make_loader(
        file_content=_make_file_content(project_yaml),
        global_config_dir=str(gcdir),
    )
    config = await loader.resolve(project_id=1, sha="abc")

    names = {s.name: s for s in config.skills}
    assert "global-skill" in names
    assert names["global-skill"].description == "overridden by project"
    assert "project-skill" in names


# ---------------------------------------------------------------------------
# Prompt mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prompt_mode_append(tmp_path):
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: 'BASE'\nskills: []\ntools: []\n")

    project_yaml = "prompt_mode: append\nprompt: 'PROJECT'\nallowed_users: [alice]\n"
    loader, _ = _make_loader(
        file_content=_make_file_content(project_yaml),
        global_config_dir=str(gcdir),
    )
    config = await loader.resolve(project_id=1, sha="abc")

    assert config.system_prompt == "BASE\nPROJECT"


@pytest.mark.asyncio
async def test_prompt_mode_override(tmp_path):
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: 'BASE'\nskills: []\ntools: []\n")

    project_yaml = "prompt_mode: override\nprompt: 'ONLY PROJECT'\nallowed_users: [alice]\n"
    loader, _ = _make_loader(
        file_content=_make_file_content(project_yaml),
        global_config_dir=str(gcdir),
    )
    config = await loader.resolve(project_id=1, sha="abc")

    assert config.system_prompt == "ONLY PROJECT"


# ---------------------------------------------------------------------------
# AGENT_CONFIG_DIR env var
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_config_dir_env_var(tmp_path):
    """AGENT_CONFIG_DIR changes the path used to fetch config."""
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: ''\nskills: []\ntools: []\n")

    provider = _make_provider(file_content=None)
    loader = ConfigLoader(provider=provider, global_config_dir=str(gcdir))

    with patch.dict(os.environ, {"AGENT_CONFIG_DIR": "custom-dir"}):
        await loader.resolve(project_id=5, sha="sha99")

    provider.get_file_at_sha.assert_called_once_with(5, "custom-dir/config.yaml", "sha99")


# ---------------------------------------------------------------------------
# Config fetched at event SHA, not HEAD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_fetched_at_event_sha(tmp_path):
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: ''\nskills: []\ntools: []\n")

    provider = _make_provider(file_content=None)
    loader = ConfigLoader(provider=provider, global_config_dir=str(gcdir))

    event_sha = "cafebabe1234"
    await loader.resolve(project_id=7, sha=event_sha)

    provider.get_file_at_sha.assert_called_once_with(7, ".agents/config.yaml", event_sha)


# ---------------------------------------------------------------------------
# Gas limits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gas_limits_from_project_config(tmp_path):
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: ''\nskills: []\ntools: []\n")

    project_yaml = "gas_limit_input: 12345\ngas_limit_output: 6789\nallowed_users: [alice]\n"
    loader, _ = _make_loader(
        file_content=_make_file_content(project_yaml),
        global_config_dir=str(gcdir),
    )
    config = await loader.resolve(project_id=1, sha="abc")

    assert config.gas_limit_input == 12345
    assert config.gas_limit_output == 6789


@pytest.mark.asyncio
async def test_gas_limits_fallback_to_env_vars(tmp_path):
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: ''\nskills: []\ntools: []\n")

    # No gas limits in project config
    project_yaml = "allowed_users: [alice]\n"
    loader, _ = _make_loader(
        file_content=_make_file_content(project_yaml),
        global_config_dir=str(gcdir),
    )
    with patch.dict(os.environ, {"DEFAULT_JOB_INPUT_GAS_LIMIT": "55000", "DEFAULT_JOB_OUTPUT_GAS_LIMIT": "11000"}):
        config = await loader.resolve(project_id=1, sha="abc")

    assert config.gas_limit_input == 55000
    assert config.gas_limit_output == 11000


@pytest.mark.asyncio
async def test_gas_limits_fallback_when_no_project_config(tmp_path):
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: ''\nskills: []\ntools: []\n")

    loader, _ = _make_loader(file_content=None, global_config_dir=str(gcdir))
    with patch.dict(os.environ, {"DEFAULT_JOB_INPUT_GAS_LIMIT": "77000", "DEFAULT_JOB_OUTPUT_GAS_LIMIT": "13000"}):
        config = await loader.resolve(project_id=1, sha="abc")

    assert config.gas_limit_input == 77000
    assert config.gas_limit_output == 13000


# ---------------------------------------------------------------------------
# Kaniko image build
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kaniko_cached_image_returned_without_build(tmp_path):
    """Same cache key → cached image returned; no Kaniko job spawned."""
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: ''\nskills: []\ntools: []\n")

    dockerfile_content = "FROM python:3.11\n"
    project_yaml = "dockerfile: Dockerfile\nallowed_users: [alice]\n"

    provider = MagicMock()
    provider.get_file_at_sha.side_effect = [
        # First call: .agents/config.yaml
        _make_file_content(project_yaml),
        # Second call: Dockerfile
        _make_file_content(dockerfile_content, path="Dockerfile"),
    ]

    mock_kube = MagicMock()
    loader = ConfigLoader(provider=provider, kube_client=mock_kube, global_config_dir=str(gcdir))

    # Registry returns 200 → cache hit
    with patch("gateway.config_loader.httpx") as mock_httpx:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.head = AsyncMock(return_value=MagicMock(status_code=200))
        mock_httpx.AsyncClient.return_value = mock_http

        config = await loader.resolve(project_id=1, sha="abc")

    # No Kaniko job should be spawned
    mock_kube.spawn_kaniko_job.assert_not_called()
    assert "pi-agent-project" in config.image


@pytest.mark.asyncio
async def test_kaniko_job_spawned_on_cache_miss(tmp_path):
    """Cache miss → Kaniko job is spawned; image tag follows expected pattern."""
    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: ''\nskills: []\ntools: []\n")

    dockerfile_content = "FROM python:3.11\n"
    project_yaml = "dockerfile: Dockerfile\nallowed_users: [alice]\n"

    provider = MagicMock()
    provider.get_file_at_sha.side_effect = [
        _make_file_content(project_yaml),
        _make_file_content(dockerfile_content, path="Dockerfile"),
    ]

    mock_kube = MagicMock()
    mock_kube.spawn_kaniko_job.return_value = "pi-kaniko-test-job"
    mock_kube.get_job_status.return_value = "succeeded"

    loader = ConfigLoader(provider=provider, kube_client=mock_kube, global_config_dir=str(gcdir))

    # Registry returns 404 → cache miss
    with patch("gateway.config_loader.httpx") as mock_httpx, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.head = AsyncMock(return_value=MagicMock(status_code=404))
        mock_httpx.AsyncClient.return_value = mock_http

        config = await loader.resolve(project_id=1, sha="abc")

    mock_kube.spawn_kaniko_job.assert_called_once()
    assert "pi-agent-project" in config.image


@pytest.mark.asyncio
async def test_kaniko_cache_key_uses_project_id_and_content_hash(tmp_path):
    """Cache key is f'{project_id}-{hash_of_dockerfile_content}'."""
    import hashlib

    gcdir = tmp_path / "global-config"
    gcdir.mkdir()
    (gcdir / "agent-config.yml").write_text("base_prompt: ''\nskills: []\ntools: []\n")

    dockerfile_content = "FROM alpine:3.18\n"
    expected_hash = hashlib.sha256(dockerfile_content.encode()).hexdigest()[:16]
    expected_key = f"42-{expected_hash}"

    project_yaml = "dockerfile: Dockerfile\nallowed_users: [alice]\n"
    provider = MagicMock()
    provider.get_file_at_sha.side_effect = [
        _make_file_content(project_yaml),
        _make_file_content(dockerfile_content, path="Dockerfile"),
    ]

    mock_kube = MagicMock()
    mock_kube.spawn_kaniko_job.return_value = "pi-kaniko-test"
    mock_kube.get_job_status.return_value = "succeeded"

    loader = ConfigLoader(provider=provider, kube_client=mock_kube, global_config_dir=str(gcdir))

    with patch("gateway.config_loader.httpx") as mock_httpx, \
         patch("asyncio.sleep", new_callable=AsyncMock):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.head = AsyncMock(return_value=MagicMock(status_code=404))
        mock_httpx.AsyncClient.return_value = mock_http

        config = await loader.resolve(project_id=42, sha="abc")

    call_kwargs = mock_kube.spawn_kaniko_job.call_args
    assert call_kwargs.kwargs["cache_key"] == expected_key
    assert expected_key in config.image
