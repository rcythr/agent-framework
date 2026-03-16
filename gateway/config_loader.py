"""
Per-project configuration loader for Phase 4.

Fetches .agents/config.yaml from the repo at the event commit SHA, merges it
with global defaults from global-config/agent-config.yml, and returns a fully
resolved AgentConfig with no optional fields.
"""

import asyncio
import hashlib
import logging
import os
from pathlib import Path

import httpx
import yaml
from pydantic import ValidationError

from shared.models import AgentConfig, ProjectConfig, SkillDef, ToolDef

logger = logging.getLogger(__name__)

_DEFAULT_AGENT_CONFIG_DIR = ".agents"
_DEFAULT_GLOBAL_CONFIG_DIR = "global-config"


def _load_global_config(global_config_dir: str) -> dict:
    """Load global agent-config.yml. Returns safe defaults if file not found."""
    path = Path(global_config_dir) / "agent-config.yml"
    if not path.exists():
        logger.warning("Global config not found at %s; using empty defaults", path)
        return {"base_prompt": "", "skills": [], "tools": []}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data


def _merge_skills(
    global_skills: list[SkillDef], project_skills: list[SkillDef]
) -> list[SkillDef]:
    """Global skills first, deduplicated by name — project definition wins."""
    merged: dict[str, SkillDef] = {s.name: s for s in global_skills}
    for s in project_skills:
        merged[s.name] = s
    return list(merged.values())


def _merge_tools(
    global_tools: list[ToolDef], project_tools: list[ToolDef]
) -> list[ToolDef]:
    """Global tools first, deduplicated by name — project definition wins."""
    merged: dict[str, ToolDef] = {t.name: t for t in global_tools}
    for t in project_tools:
        merged[t.name] = t
    return list(merged.values())


def _load_skill_prompts(skills: list[SkillDef], global_config_dir: str) -> str:
    """Load prompt snippets from global-config/skills/<name>.yml for each active skill."""
    skills_dir = Path(global_config_dir) / "skills"
    parts: list[str] = []
    for skill in skills:
        skill_file = skills_dir / f"{skill.name}.yml"
        if not skill_file.exists():
            continue
        try:
            with open(skill_file) as f:
                data = yaml.safe_load(f) or {}
            if prompt := data.get("prompt", "").strip():
                parts.append(f"## Skill: {skill.name}\n{prompt}")
        except Exception as e:
            logger.warning("Failed to load skill prompt for %s: %s", skill.name, e)
    return "\n\n".join(parts)


class ConfigLoader:
    """Resolves per-project AgentConfig by fetching and merging project + global config."""

    def __init__(self, provider, kube_client=None, global_config_dir: str | None = None):
        self._provider = provider
        self._kube = kube_client
        self._global_config_dir = global_config_dir or os.getenv(
            "GLOBAL_CONFIG_DIR", _DEFAULT_GLOBAL_CONFIG_DIR
        )

    async def resolve(self, project_id: int | str, sha: str) -> AgentConfig:
        """
        Resolve the fully merged AgentConfig for a project at a specific commit SHA.

        Falls back to global defaults on any error (file absent, malformed YAML,
        Pydantic validation failure).
        """
        agent_config_dir = os.getenv("AGENT_CONFIG_DIR", _DEFAULT_AGENT_CONFIG_DIR)
        config_path = f"{agent_config_dir}/config.yaml"

        # Load global config
        global_data = _load_global_config(self._global_config_dir)
        global_base_prompt: str = global_data.get("base_prompt", "")
        global_skills = [
            SkillDef(**s) for s in (global_data.get("skills") or [])
        ]
        global_tools = [
            ToolDef(**t) for t in (global_data.get("tools") or [])
        ]

        # Fetch project config at the event SHA (not HEAD)
        project_config: ProjectConfig | None = None
        file_content = self._provider.get_file_at_sha(project_id, config_path, sha)

        if file_content is None:
            logger.warning(
                "No %s found for project %s at sha %s; using global defaults",
                config_path, project_id, sha,
            )
        else:
            try:
                raw = yaml.safe_load(file_content.content)
                if not isinstance(raw, dict):
                    raise ValueError(f"Expected a YAML mapping, got {type(raw).__name__}")
                project_config = ProjectConfig(**raw)
            except yaml.YAMLError as e:
                logger.warning(
                    "Malformed YAML in %s for project %s: %s; using global defaults",
                    config_path, project_id, e,
                )
            except ValidationError as e:
                logger.warning(
                    "Pydantic validation failed for %s project %s: %s; using global defaults",
                    config_path, project_id, e,
                )
            except Exception as e:
                logger.warning(
                    "Failed to parse %s for project %s: %s; using global defaults",
                    config_path, project_id, e,
                )

        if project_config is not None:
            skills = _merge_skills(global_skills, project_config.skills)
            tools = _merge_tools(global_tools, project_config.tools)

            if project_config.prompt_mode == "override":
                system_prompt = project_config.prompt
            else:  # append
                if project_config.prompt:
                    system_prompt = global_base_prompt + "\n" + project_config.prompt
                else:
                    system_prompt = global_base_prompt

            gas_limit_input = (
                project_config.gas_limit_input
                if project_config.gas_limit_input is not None
                else int(os.getenv("DEFAULT_JOB_INPUT_GAS_LIMIT", "80000"))
            )
            gas_limit_output = (
                project_config.gas_limit_output
                if project_config.gas_limit_output is not None
                else int(os.getenv("DEFAULT_JOB_OUTPUT_GAS_LIMIT", "20000"))
            )

            skill_prompts = _load_skill_prompts(skills, self._global_config_dir)
            if skill_prompts:
                system_prompt = system_prompt + "\n\n" + skill_prompts

            if project_config.dockerfile is not None:
                image = await self._build_or_get_image(
                    project_id, project_config.dockerfile, sha
                )
            else:
                image = os.getenv("PI_AGENT_IMAGE", "localhost:5001/pi-agent-worker:latest")

            allowed_users = project_config.allowed_users
        else:
            # Fall back to global defaults entirely
            skills = global_skills
            tools = global_tools
            system_prompt = global_base_prompt
            skill_prompts = _load_skill_prompts(skills, self._global_config_dir)
            if skill_prompts:
                system_prompt = system_prompt + "\n\n" + skill_prompts
            gas_limit_input = int(os.getenv("DEFAULT_JOB_INPUT_GAS_LIMIT", "80000"))
            gas_limit_output = int(os.getenv("DEFAULT_JOB_OUTPUT_GAS_LIMIT", "20000"))
            image = os.getenv("PI_AGENT_IMAGE", "localhost:5001/pi-agent-worker:latest")
            allowed_users = []

        return AgentConfig(
            skills=skills,
            tools=tools,
            system_prompt=system_prompt,
            image=image,
            gas_limit_input=gas_limit_input,
            gas_limit_output=gas_limit_output,
            allowed_users=allowed_users,
        )

    async def _build_or_get_image(
        self, project_id: int | str, dockerfile_path: str, sha: str
    ) -> str:
        """Return the image tag for a custom Dockerfile, building via Kaniko if needed."""
        file_content = self._provider.get_file_at_sha(project_id, dockerfile_path, sha)
        if file_content is None:
            logger.warning(
                "Dockerfile %s not found for project %s at sha %s; using default image",
                dockerfile_path, project_id, sha,
            )
            return os.getenv("PI_AGENT_IMAGE", "localhost:5001/pi-agent-worker:latest")

        # Cache key: "{project_id}-{sha256_of_dockerfile_content[:16]}"
        dockerfile_blob_sha = hashlib.sha256(file_content.content.encode()).hexdigest()[:16]
        cache_key = f"{project_id}-{dockerfile_blob_sha}"
        registry = os.getenv("REGISTRY", "localhost:5001")
        image_tag = f"{registry}/pi-agent-project:{cache_key}"
        image_name = "pi-agent-project"

        # Check registry for cached image
        try:
            async with httpx.AsyncClient() as http:  # type: ignore[attr-defined]
                resp = await http.head(
                    f"http://{registry}/v2/{image_name}/manifests/{cache_key}",
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    logger.info("Using cached image %s for project %s", image_tag, project_id)
                    return image_tag
        except Exception as e:
            logger.debug("Registry cache check failed (will build): %s", e)

        # Build via Kaniko
        if self._kube is None:
            logger.warning(
                "No kube_client available; cannot build Kaniko image; using default"
            )
            return os.getenv("PI_AGENT_IMAGE", "localhost:5001/pi-agent-worker:latest")

        logger.info("Building image %s via Kaniko for project %s", image_tag, project_id)
        job_name = self._kube.spawn_kaniko_job(
            cache_key=cache_key,
            dockerfile_content=file_content.content,
            image_tag=image_tag,
        )

        timeout = int(os.getenv("KANIKO_TIMEOUT", "300"))
        elapsed = 0
        poll_interval = 5
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            status = self._kube.get_job_status(job_name)
            if status == "succeeded":
                logger.info("Kaniko build succeeded: %s", image_tag)
                return image_tag
            elif status == "failed":
                logger.error(
                    "Kaniko build failed for %s; using default image", image_tag
                )
                return os.getenv("PI_AGENT_IMAGE", "localhost:5001/pi-agent-worker:latest")

        logger.error("Kaniko build timed out for %s; using default image", image_tag)
        return os.getenv("PI_AGENT_IMAGE", "localhost:5001/pi-agent-worker:latest")
