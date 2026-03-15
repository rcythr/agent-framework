## Extending the Integration

Adding new agent capabilities follows a consistent pattern:

1. **New provider action** → add a method to `RepositoryProvider` ABC + implement in all provider classes; add a corresponding tool definition to each `ProviderToolkit` subclass
2. **New event type** → add a new model to `providers/base.py`; implement `parse_webhook_event` in each provider; add a `case` to `event_mapper.py`
3. **New task behaviour** → add a `case` to `build_task_message()` in `agent_runner.py` — no provider-specific changes needed
4. **New log event type** → add to the `Literal` union in `LogEvent`, emit from `AgentLogger`, add a renderer in the dashboard log panel
5. **New dashboard view** → add a route and fetch against the existing gateway REST API

No changes to the Kubernetes manifests or job spawner are required for most extensions.

**Adding a new provider** (e.g. GitHub) follows a fixed, contained pattern:

For the `AuthProvider`:
1. Implement `AuthProvider` in `providers/{name}/auth.py` — `oauth_proxy_config()` with the correct `--provider` flag and restriction flags, and `extract_user()` mapping the IdP's header names to `UserIdentity`
2. Register it in `providers/auth_registry.py`
3. Add `AUTH_PROVIDER={name}` and credential env vars to the K8s Secret and gateway Deployment

For the `RepositoryProvider`:

1. Create `providers/github/` with `provider.py` (implement all `RepositoryProvider` abstract methods), `webhook.py` (implement `verify_webhook` and `parse_webhook_event`), and `toolkit.py` (implement `ProviderToolkit`)
2. Register it in `providers/registry.py`
3. Add `PROVIDER=github` and `GITHUB_TOKEN` to the K8s Secret and gateway Deployment
4. No changes to `event_mapper.py`, `agent_runner.py`, `config_loader.py`, `session_broker.py`, or any dashboard code

Additionally, the project configuration layer is independently extensible:

6. **New global skill or tool** → add a definition to `global-config/skills/` or `global-config/tools/` and update `global-config/agent-config.yml` — available to all projects immediately
7. **New `config.yaml` field** → add to `ProjectConfig`, handle in `config_loader.py`, pass through `AgentConfig` — backward compatible since unrecognised fields are ignored by existing projects
8. **New image build strategy** → implement alongside the Kaniko builder in `config_loader.py` — the rest of the stack only cares about the resolved image tag
9. **New session message type** → add to the `message_type` Literal in `SessionMessage`, handle in `session_broker.py`, add a renderer in the conversation thread UI
10. **New launcher context field** → add to `SessionContext`, surface in the session launcher form, pass through to the worker via env var — no changes to the broker or message protocol required
11. **Gas limit policy change** → adjust `DEFAULT_JOB_INPUT_GAS_LIMIT` / `DEFAULT_JOB_OUTPUT_GAS_LIMIT` / `DEFAULT_SESSION_INPUT_GAS_LIMIT` / `DEFAULT_SESSION_OUTPUT_GAS_LIMIT` env vars or project-level `gas_limit_input` / `gas_limit_output` in `.agents/config.yaml` — no code changes required
