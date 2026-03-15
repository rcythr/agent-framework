# Security Considerations

| Concern | Mitigation |
|---|---|
| Webhook authenticity | HMAC token comparison via `hmac.compare_digest` |
| GitLab API credentials | Injected via K8s Secret, never in env files or CI vars |
| LLM API key | Injected via K8s Secret |
| Worker cluster permissions | Dedicated `ServiceAccount` with no K8s API access |
| Gateway cluster permissions | `Role` scoped to `batch/jobs` in `pi-agents` namespace only |
| Branch protection | System prompt instructs agent never to commit to `main` directly |
| Internal log endpoints | `/internal/*` routes protected by shared secret, excluded from Ingress auth |
| Dashboard authentication | oauth2-proxy configured by `AuthProvider.oauth_proxy_config()`; IdP and group/org restriction are provider-specific |
| Identity header extraction | Gateway never hardcodes `X-Forwarded-User`; always calls `auth_provider.extract_user(headers)` so correct headers are read for any IdP |
| AUTH_PROVIDER / PROVIDER decoupling | The two env vars default to the same value but can be set independently, allowing any combination of repo provider and IdP |
| Webhook bypass | `/webhook/*` and `/internal/*` routed directly to gateway, bypassing oauth2-proxy |
| User attribution | `X-Forwarded-User` header used to record which operator triggered manual runs |
| Log data sensitivity | LLM prompts and tool results may contain code and secrets — group restriction limits exposure to GitLab group members only |
| Project Dockerfile trust | Project Dockerfiles run as a layer on the global base image — the base image is controlled by operators; projects cannot replace it or escalate privileges |
| Config fetch credentials | Gateway uses the provider's service token to fetch `{AGENT_CONFIG_DIR}/config.yaml` via `provider.get_file_at_sha()` — no additional credentials required |
| Invalid project config | Config loader validates `config.yaml` with Pydantic; malformed files fall back to global defaults with a logged warning rather than failing the agent run |
| Image build isolation | Kaniko builds run in a dedicated namespace with no access to the host Docker socket, and images are pushed directly to the registry without a local daemon |
| Session ownership | Sessions are scoped to the `X-Forwarded-User` identity; the gateway rejects requests to view or message sessions owned by a different user |
| Session worker access | Session workers can only access GitLab projects the launching user has access to — the agent is spawned with a scoped token, not the global service token |
| Project search proxy | `GET /projects/search` proxies to the provider via `provider.search_projects(user_token=...)`, ensuring users cannot enumerate projects they lack access to |
| Interrupt safety | Interrupts are delivered at iteration boundaries, never mid-tool-execution, preventing partial writes or inconsistent repo state |
| Gas top-up safety | `add_gas` is processed only between LLM calls — never mid-tool-execution; the agent always finishes the current tool before checking the gas limit |
| Gas limit enforcement | Gas limit is enforced inside the `Agent` class before each new LLM call; it cannot be bypassed by the worker or toolkit code |


