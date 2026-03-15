# Phase 4 — Per-Project Configuration

## Goal
Projects can place a `.agents/config.yaml` in their repo. The gateway fetches it at webhook time, merges it with global defaults, and the worker runs with custom skills, tools, prompt, and optionally a custom image. Projects without a config file run identically to Phase 2/3.

## Prerequisites
- **Phase 1 complete** — gateway running, K8s job spawner exists

---

## Deliverables

### `shared/models.py` — add config models
```python
class SkillDef(BaseModel):
    name: str
    description: str
    inline: bool = False

class ToolDef(BaseModel):
    name: str
    description: str
    inline: bool = False

class ProjectConfig(BaseModel):
    skills: list[SkillDef] = []
    tools: list[ToolDef] = []
    prompt_mode: Literal["append", "override"] = "append"
    prompt: str = ""
    dockerfile: str | None = None
    gas_limit_input: int | None = None   # overrides DEFAULT_JOB_INPUT_GAS_LIMIT if set
    gas_limit_output: int | None = None  # overrides DEFAULT_JOB_OUTPUT_GAS_LIMIT if set
    allowed_users: list[str] = []        # usernames permitted to trigger webhook dispatch;
                                         # empty list = no automatic dispatch (deny-by-default)

class AgentConfig(BaseModel):
    skills: list[SkillDef]
    tools: list[ToolDef]
    system_prompt: str
    image: str
    gas_limit_input: int
    gas_limit_output: int
    allowed_users: list[str]             # from ProjectConfig; passed to dispatch check
```

### `global-config/`
```
global-config/
├── agent-config.yml          # base prompt, default skill/tool names
├── skills/                   # globally available skill YAML definitions
└── tools/                    # globally available tool YAML definitions
```

`agent-config.yml` schema:
```yaml
base_prompt: |
  You are an autonomous software engineering agent...
skills:
  - name: code-review
    description: "..."
tools:
  - name: notify-slack
    description: "..."
```

This directory is mounted as a ConfigMap in the gateway pod.

### `gateway/config_loader.py`
Full config loader implementation.

**Responsibilities:**
1. Read `AGENT_CONFIG_DIR` env var (default `".agents"`); resolve config path as `{agent_config_dir}/config.yaml`
2. Call `provider.get_file_at_sha(project_id, config_path, sha)` — use **event commit SHA, not HEAD**
3. If file absent or YAML invalid: log a warning and fall back to global defaults entirely
4. If file present but fails Pydantic validation: log a warning and fall back to global defaults
5. Load global defaults from `global-config/agent-config.yml`
6. Merge skills: `global_skills + project_skills`, deduplicate by `name` (project definition wins)
7. Merge tools: same deduplication logic as skills
8. Resolve prompt: `"append"` → concatenate global base + `\n` + project prompt; `"override"` → project prompt only
9. Resolve `gas_limit_input`: use project value if set, else `DEFAULT_JOB_INPUT_GAS_LIMIT` env var (default `80000`)
   Resolve `gas_limit_output`: use project value if set, else `DEFAULT_JOB_OUTPUT_GAS_LIMIT` env var (default `20000`)
10. Resolve image: if `dockerfile` is set, run Kaniko build (see below); otherwise use `PI_AGENT_IMAGE` env var
11. Pass through `allowed_users` from `ProjectConfig` into `AgentConfig` unchanged (no merging with global defaults — access control is purely project-defined)
12. Return `AgentConfig` — no optional fields

**Kaniko image build flow:**
- Compute cache key: `f"{project_id}-{dockerfile_blob_sha}"` where `dockerfile_blob_sha` is the git blob SHA of the Dockerfile fetched via the provider API
- Image tag: `f"{REGISTRY}/pi-agent-project:{cache_key}"`
- If image already exists in registry for this cache key: return tag immediately (no build)
- If not: create a K8s Job running Kaniko; wait for completion (with configurable timeout); return new image tag
- Agent K8s Job is only spawned after the image build Job completes

### `gateway/kube_client.py` — update for `AgentConfig`
Update `spawn_agent_job` to accept `AgentConfig` alongside `TaskSpec`:
- Use `agent_config.image` as the pod image (replacing the unconditional `PI_AGENT_IMAGE`)
- Pass `agent_config.system_prompt` as `SYSTEM_PROMPT` env var
- Pass `agent_config.gas_limit_input` as `GAS_LIMIT_INPUT` env var
- Pass `agent_config.gas_limit_output` as `GAS_LIMIT_OUTPUT` env var
- Pass serialised `agent_config.skills` and `agent_config.tools` as env vars or ConfigMap volume

### `gateway/main.py` — wire config loader and access control
Before calling `kube_client.spawn_agent_job` for webhook-triggered events:
1. Call `config_loader.resolve(project_id, sha)` to get `AgentConfig`
2. Check `event.actor in agent_config.allowed_users`; if not, log the rejection and return HTTP 200 without spawning a job
3. Pass `AgentConfig` to the spawner

For manually-triggered jobs via `POST /trigger`, skip the `allowed_users` check — access is controlled by the dashboard authentication layer instead.

### `k8s/gateway-deployment.yaml` — add env vars
```yaml
- name: AGENT_CONFIG_DIR
  value: .agents
- name: DEFAULT_JOB_INPUT_GAS_LIMIT
  value: "80000"
- name: DEFAULT_JOB_OUTPUT_GAS_LIMIT
  value: "20000"
- name: DEFAULT_SESSION_INPUT_GAS_LIMIT
  value: "160000"
- name: DEFAULT_SESSION_OUTPUT_GAS_LIMIT
  value: "40000"
```

---

## Tests to Write First (TDD)

### Unit tests — `gateway/config_loader.py`
- Returns global defaults when project has no `.agents/config.yaml` (file absent)
- Returns global defaults with warning log when `.agents/config.yaml` is malformed YAML
- Returns global defaults with warning log when `.agents/config.yaml` fails Pydantic validation
- Skill merging: project skills are appended after global; duplicates (by name) deduplicated, project definition wins
- Tool merging: same deduplication behaviour as skills
- `prompt_mode: append` — project prompt appended to global base with newline separator
- `prompt_mode: override` — project prompt completely replaces global base
- `AGENT_CONFIG_DIR` env var changes the path used to fetch config (default `".agents"`)
- Config is fetched at event commit SHA, not HEAD — assert `get_file_at_sha` is called with the event SHA
- Kaniko cache key is `f"{project_id}-{dockerfile_blob_sha}"` — same key returns cached image without spawning a build Job
- Kaniko Job is spawned when cache key is absent; returned image tag follows expected pattern
- Gas limits: project `gas_limit_input` / `gas_limit_output` from config are used when set
- Gas limits: fall back to `DEFAULT_JOB_INPUT_GAS_LIMIT` / `DEFAULT_JOB_OUTPUT_GAS_LIMIT` env vars when absent

### Unit tests — access control
- Webhook event with actor in `allowed_users` → job is spawned
- Webhook event with actor NOT in `allowed_users` → no job spawned, HTTP 200 returned, rejection logged
- `allowed_users: []` (empty list or missing config file) → no job spawned for any actor
- `POST /trigger` (manual) bypasses `allowed_users` check regardless of actor
- `allowed_users` list is passed through unchanged from `ProjectConfig` to `AgentConfig`

### Integration tests
- Spawn a job for a project with a valid `.agents/config.yaml`; assert `AgentConfig` passed to `kube_client` has merged skills/tools and composed prompt
- Spawn a job for a project with a custom Dockerfile; assert Kaniko Job is created; after mock completion, assert derived image tag is used in worker Job manifest
- Webhook from unlisted actor returns 200 but does not create a job record in DB

### E2E test (KIND cluster)
- Create a test project with `.agents/config.yaml` adding a custom skill and listing the test user in `allowed_users`
- Trigger an agent run as the listed user
- Verify the custom skill is present in the worker environment
- Push a commit as an unlisted user; verify no agent job is created

---

## Definition of Done
A project with `.agents/config.yaml` runs an agent with its custom configuration. A project without the file runs identically to Phase 2/3.

## Dependencies
- **Blocked by:** Phase 1 (gateway, K8s spawner)
- **Does not require:** Phase 2 or 3 to be complete (config resolution is entirely gateway-side)
