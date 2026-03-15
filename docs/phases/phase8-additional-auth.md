# Phase 8 — Additional Auth and Repository Providers

## Goal
Extend the system to support additional repository providers (GitHub, Bitbucket, etc.) and additional identity providers (GitHub OAuth, Keycloak/OIDC, Okta), each slotting cleanly into the existing abstraction without any changes to the gateway core, worker, dashboard, or session broker.

## Status
**Deferred** — this phase is explicitly out of scope for the initial implementation. The provider abstraction layer built in Phase 0 is designed to make this work straightforward when the time comes.

---

## Prerequisites
- **All phases 0–7 complete**
- Decision on which provider(s) to add

---

## Scope Per New Repository Provider

For each new provider (e.g. GitHub), create `providers/{name}/` with:

### `providers/{name}/provider.py`
Implement all `RepositoryProvider` abstract methods from `providers/base.py`. Return only shared Pydantic models — no SDK types.

### `providers/{name}/webhook.py`
Implement:
- `verify_webhook(headers, body, secret)` — use provider's signature verification mechanism
- `parse_webhook_event(headers, body)` — map raw payload to `PushEvent`, `MREvent`, or `CommentEvent`

### `providers/{name}/toolkit.py`
Implement `ProviderToolkit` subclass with `get_tools()` wrapping the new `RepositoryProvider` methods.

### `providers/{name}/auth.py`
Implement `AuthProvider`:
- `oauth_proxy_config()` — return `OAuthProxyConfig` with the correct `--provider` flag and restriction flags for this IdP
- `extract_user(headers)` — map provider-specific forwarded headers to `UserIdentity`

### Registry updates
- `providers/registry.py` — add new `case` for the provider name
- `providers/auth_registry.py` — add new `case` for the auth provider name

### K8s manifest updates
- `k8s/secrets.yaml` — add provider-specific credential secrets (e.g. `GITHUB_TOKEN`)
- `k8s/gateway-deployment.yaml` — add provider credential env vars

### For OIDC IdPs (Keycloak, Okta, etc.)
Implement `providers/auth_oidc.py` — `OIDCAuthProvider` using `--provider=oidc` and `X-Auth-Request-*` headers rather than GitLab-specific forwarded headers.

---

## What Does NOT Change
The following files require **zero modifications** when adding a new provider:

- `gateway/event_mapper.py`
- `worker/agent_runner.py`
- `gateway/config_loader.py`
- `gateway/session_broker.py`
- `gateway/kube_client.py`
- `gateway/db.py`
- `dashboard/index.html`

---

## Tests Per New Provider

### Provider unit tests
- `{Name}Provider.parse_webhook_event` maps all three raw payloads to correct shared event models
- `{Name}Provider.verify_webhook` returns correct boolean for valid/invalid signatures
- Each `{Name}Provider` method — mock the provider SDK; assert return values are shared model instances
- `get_provider()` returns `{Name}Provider` when `PROVIDER={name}`

### Auth unit tests
- `{Name}AuthProvider.extract_user()` reads correct headers and returns `UserIdentity`
- `{Name}AuthProvider.oauth_proxy_config()` returns correct `provider_flag` and `extra_flags`
- `get_auth_provider()` returns `{Name}AuthProvider` when `AUTH_PROVIDER={name}`

### Integration tests
- Full webhook path: raw `{name}` webhook payload → `parse_webhook_event` → `map_event_to_task` → K8s Job created
- Session launcher shows projects from the new provider alongside GitLab projects

### E2E test
- Full session via the new provider: launch session, send goal, receive response, complete

---

## Definition of Done (per provider added)
A new provider is fully functional — webhooks received, agent runs against the provider's API, sessions can target projects on the new provider — with no changes to any shared infrastructure code.

## Dependencies
- **Blocked by:** Phases 0–7
- **Parallel across providers** — two providers can be added in parallel by separate engineers as long as Phase 0's abstraction layer is in place
