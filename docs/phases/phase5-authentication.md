# Phase 5 — Authentication

## Goal
The dashboard and API are accessible only to authenticated GitLab users who are members of the configured group. Webhook and internal endpoints remain unprotected. Operator identity is recorded on manual triggers.

## Prerequisites
- **Phase 1 complete** — gateway running, endpoints exist

---

## Deliverables

### `k8s/oauth2-proxy-deployment.yaml`
Deploy `oauth2-proxy` as a Kubernetes Deployment in the `pi-agents` namespace. The proxy sits in front of the gateway and handles GitLab OAuth2. Configuration is driven by the `GET /internal/oauth2-proxy-config` endpoint implemented in Phase 1 (which returns args from `GitLabAuthProvider.oauth_proxy_config()`).

Required oauth2-proxy configuration:
- `--provider=gitlab`
- `--upstream=http://pi-agent-gateway:80`
- `--gitlab-group=<your-group>` (from `GitLabAuthProvider`)
- `--cookie-secret=<from K8s secret>`
- `--client-id=<from K8s secret>`
- `--client-secret=<from K8s secret>`

### `k8s/ingress.yaml` — split routing
Update the ingress to implement bypass routing:
- `/webhook/*` — route directly to gateway Service (bypass oauth2-proxy), no auth required
- `/internal/*` — route directly to gateway Service (bypass oauth2-proxy), cluster-internal only
- All other paths — route through oauth2-proxy Deployment

This is typically implemented with separate Ingress resources or nginx annotations (`nginx.ingress.kubernetes.io/auth-url`, `nginx.ingress.kubernetes.io/auth-signin`).

### `k8s/secrets.yaml` — add oauth2-proxy credentials
Add a new secret `oauth2-proxy-creds`:
```yaml
data:
  cookie-secret: <base64>
  client-id: <base64>
  client-secret: <base64>
```

### `gateway/main.py` — attach user identity to records
On every authenticated endpoint (all except `/webhook/*`, `/internal/*`, `/healthz`):
1. Call `auth_provider.extract_user(headers)` to get `UserIdentity`
2. Attach `UserIdentity.username` as `triggered_by` on `JobRecord` when a job is created via `POST /trigger`
3. For direct cluster calls (no forwarded headers present): set `triggered_by = "system"`

**Constraint:** The gateway must never directly read `X-Forwarded-User`. It must always go through `auth_provider.extract_user()`. Enforce this in tests by asserting no string literal `'X-Forwarded-User'` appears outside the `providers/` directory.

### GitLab OAuth2 application setup (documentation)
Create a `docs/gitlab-oauth-setup.md` documenting the manual steps:
1. In GitLab: go to **User Settings → Applications → Add new application**
2. Name: `pi-agent-gateway`
3. Redirect URI: `https://<your-domain>/oauth2/callback`
4. Scopes: `read_user`, `openid`
5. Copy **Application ID** and **Secret** into `oauth2-proxy-creds` K8s secret

---

## Tests to Write First (TDD)

### Integration tests
- Requests to `/` without a valid session cookie are redirected to GitLab OAuth2 login
- Requests to `/webhook/gitlab` bypass oauth2-proxy and reach the gateway directly (no redirect)
- Requests to `/internal/log` bypass oauth2-proxy and reach the gateway directly (no redirect)
- `POST /trigger` with valid session calls `auth_provider.extract_user()` and sets `triggered_by` on `JobRecord` to the returned `username`
- `POST /trigger` without forwarded headers (direct cluster call) sets `triggered_by` to `"system"`
- `GET /internal/oauth2-proxy-config` returns correct CLI args for `GitLabAuthProvider`

### Unit tests
- Gateway uses `auth_provider.extract_user()` and never directly reads `X-Forwarded-User` header — enforced by asserting no string literal `'X-Forwarded-User'` appears outside `providers/`

### E2E test (KIND cluster)
- Log in via GitLab OAuth; assert dashboard is accessible
- Log out; assert redirect to GitLab login page

---

## Definition of Done
Dashboard requires GitLab login. Webhook and internal endpoints are unaffected. Operator identity is recorded on manual triggers.

## Dependencies
- **Blocked by:** Phase 1 (gateway endpoints must exist)
- **Does not require:** Phase 2, 3, or 4 to be complete
- **Parallel with:** Phases 2, 3, 4
