# Phalanx — End-to-End Walkthrough

This walkthrough takes you from a blank machine to a running Phalanx installation with a live GitLab project that automatically triggers AI agent jobs. Follow it in order the first time.

---

## Prerequisites

| Tool | Minimum version | Install |
|---|---|---|
| Docker | 24+ | https://docs.docker.com/get-docker/ |
| kind | 0.23+ | `brew install kind` or https://kind.sigs.k8s.io/docs/user/quick-start/ |
| kubectl | 1.28+ | `brew install kubectl` |
| helm | 3.14+ | `brew install helm` |

Docker must have at least **8 GB RAM** and **4 CPUs** allocated (GitLab CE is the hungry one). On macOS, adjust this in Docker Desktop → Settings → Resources.

You also need an **OpenAI API key** (or a compatible provider key, e.g. Anthropic via an OpenAI-compatible proxy).

---

## Part 1 — Local development cluster

### 1.1 Start everything

```bash
git clone https://github.com/rcythr/phalanx.git
cd phalanx

export OPENAI_API_KEY=sk-...   # your LLM API key

./scripts/cluster-up.sh
```

The script takes 5–8 minutes on first run. It:

1. Starts a local Docker registry on `localhost:5001`
2. Creates a 3-node KIND cluster with port 8080 forwarded from the host
3. Installs the nginx ingress controller
4. Deploys GitLab CE via Helm (this is the slow part — the script waits for it to become healthy)
5. Builds and pushes the gateway and worker images
6. Applies all Kubernetes manifests
7. Seeds GitLab with a test group, project, service token, and webhook

When it finishes you'll see something like:

```
✓ Cluster ready
  GitLab:   http://gitlab.localhost:8080   (root / changeme-local-only)
  Gateway:  http://phalanx.localhost:8080
```

### 1.2 Open the dashboard

Navigate to `http://phalanx.localhost:8080` in your browser. Because this is a local dev cluster with no OAuth2 proxy, you'll land directly on the dashboard.

You should see an empty job list — no jobs have run yet.

### 1.3 Create your first webhook-triggered job

1. Open `http://gitlab.localhost:8080` and log in as `root` / `changeme-local-only`.
2. You'll find a pre-seeded project called `test-project` inside the `pi-agents` group.
3. Navigate to the project, open any file (e.g. `README.md`), and edit it directly in the GitLab UI.
4. Commit the change to the `main` branch.

Watch the Phalanx dashboard — within a few seconds you should see a new job appear with status `running`. Click it to see the live execution trace: every LLM prompt, every tool call, and every response streams in real time.

> **Note:** Automatic dispatch only fires if the committing user is in `allowed_users` in `.agents/config.yaml`. The seeded project allows the `root` user. If you add your own GitLab account, add it to `allowed_users` first.

---

## Part 2 — Per-project configuration

The seeded test project already has a minimal `.agents/config.yaml`. To see the configuration system in action:

1. In the test project, create `.agents/config.yaml` with the following content:

```yaml
allowed_users:
  - root

gas_limit_input: 40000
gas_limit_output: 10000

prompt_mode: append
prompt: |
  This is a test project. Keep all responses brief — one paragraph maximum.
  Always begin your response with "Test agent says:".
```

2. Commit it to `main`.
3. Edit another file and commit it.

You'll see the new job use the smaller gas limits and the customised system prompt addition.

---

## Part 3 — Interactive sessions

Interactive sessions let you give an agent an ad hoc goal against any project, then steer it mid-run.

1. Open the dashboard at `http://phalanx.localhost:8080`.
2. Click **New Session** (top right).
3. Fill in:
   - **Project**: start typing `test-project` and select it from the dropdown
   - **Branch**: `main`
   - **Goal**: `Review the README.md file and suggest three specific improvements.`
4. Click **Launch**.

The session view opens with two panels:
- **Left** — conversation thread (your messages and the agent's replies)
- **Right** — live execution trace (every LLM call and tool invocation)

While the agent is running, you can type in the input box to redirect it. For example, after it posts its first suggestion, type: `Focus only on improving the installation instructions.`

The agent will finish its current tool call (safe interrupt), then act on your redirect.

---

## Part 4 — Gas limits and top-up

1. Launch a new session with a very low gas limit. In the **New Session** form, set **Input gas limit** to `2000` and **Output gas limit** to `500`.
2. Give it a complex goal: `Analyse every file in this repository and produce a comprehensive audit report.`
3. The agent will pause quickly once the budget runs out.

On the paused session page, you'll see the gas meters for input and output tokens. Both will be at 100%. Enter a new value in the **Add tokens** box (e.g. `10000` input, `5000` output) and click **Top up**.

The agent resumes from exactly where it left off — no repeated tool calls, no re-execution.

---

## Part 5 — Custom root certificates (enterprise environments)

If your GitLab instance or LLM API endpoint uses a certificate signed by a private CA, you need to inject that CA into the Phalanx containers.

### Local dev (KIND)

1. Base64-encode your CA certificate:
   ```bash
   base64 -w0 my-corporate-ca.pem > my-corporate-ca.b64
   ```

2. Create a ConfigMap in the `pi-agents` namespace:
   ```bash
   kubectl create configmap phalanx-ca-certs \
     --from-file=ca-bundle.crt=my-corporate-ca.pem \
     -n pi-agents
   ```

3. Patch the gateway deployment to mount it:
   ```bash
   kubectl patch deployment pi-agent-gateway -n pi-agents --type=json -p='[
     {"op":"add","path":"/spec/template/spec/volumes/-","value":{"name":"ca-certs","configMap":{"name":"phalanx-ca-certs"}}},
     {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts/-","value":{"name":"ca-certs","mountPath":"/etc/phalanx/certs","readOnly":true}},
     {"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{"name":"REQUESTS_CA_BUNDLE","value":"/etc/phalanx/certs/ca-bundle.crt"}},
     {"op":"add","path":"/spec/template/spec/containers/0/env/-","value":{"name":"SSL_CERT_FILE","value":"/etc/phalanx/certs/ca-bundle.crt"}}
   ]'
   ```

### Helm deployment

Supply the certificate content in `values.yaml`:

```yaml
customCACerts: |
  -----BEGIN CERTIFICATE-----
  MIIBxTCCAW...your cert here...
  -----END CERTIFICATE-----
```

The Helm chart automatically creates a `phalanx-ca-certs` ConfigMap, mounts it into both gateway and worker containers, and sets `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` on both.

---

## Part 6 — Production deployment with Helm

For production environments, use the provided Helm chart rather than the raw K8s manifests.

### 6.1 Prerequisites

- A running Kubernetes cluster (EKS, GKE, AKS, or self-managed)
- An nginx ingress controller installed
- A GitLab OAuth2 application configured (see `docs/gitlab-oauth-setup.md`)
- The Phalanx gateway and worker images pushed to your container registry

### 6.2 Install

```bash
helm install phalanx ./helm/phalanx \
  --namespace pi-agents --create-namespace \
  \
  --set provider=gitlab \
  --set gitlab.url=https://gitlab.example.com \
  --set gitlab.token=glpat-xxxxxxxxxxxx \
  --set gitlab.webhookSecret=$(openssl rand -hex 32) \
  \
  --set llm.apiKey=sk-... \
  --set llm.baseUrl=https://api.openai.com/v1 \
  --set llm.model=gpt-4o \
  \
  --set oauth2proxy.clientId=<oauth-app-id> \
  --set oauth2proxy.clientSecret=<oauth-app-secret> \
  --set oauth2proxy.cookieSecret=$(openssl rand -base64 32) \
  \
  --set ingress.host=phalanx.example.com \
  --set ingress.tls.enabled=true \
  --set ingress.tls.secretName=phalanx-tls \
  \
  --set gateway.image.repository=registry.example.com/phalanx-gateway \
  --set worker.image.repository=registry.example.com/phalanx-worker
```

### 6.3 Configure your GitLab project

In any GitLab project you want Phalanx to watch:

1. Go to **Settings → Webhooks**.
2. Add a webhook pointing to `https://phalanx.example.com/webhook/gitlab`.
3. Select: **Push events**, **Merge request events**, **Comments**.
4. Set the **Secret token** to the same value you used for `gitlab.webhookSecret`.
5. Click **Add webhook**.

Then add `.agents/config.yaml` to the project root (see Part 2 above) with at least one entry in `allowed_users`.

### 6.4 Verify

```bash
kubectl get pods -n pi-agents
# NAME                               READY   STATUS    RESTARTS
# pi-agent-gateway-xxxxxxxxx-xxxxx   1/1     Running   0
# pi-agent-gateway-xxxxxxxxx-xxxxx   1/1     Running   0
# oauth2-proxy-xxxxxxxxx-xxxxx       1/1     Running   0

kubectl logs -n pi-agents -l app=pi-agent-gateway -f
```

Open `https://phalanx.example.com` and log in with your GitLab credentials.

---

## Troubleshooting

**Dashboard shows no jobs after a push**
- Check that `allowed_users` in `.agents/config.yaml` includes the user who pushed.
- Check the gateway logs: `kubectl logs -n pi-agents -l app=pi-agent-gateway`.
- Verify the webhook is delivering: GitLab → Settings → Webhooks → view recent deliveries.

**Worker pod fails with TLS errors**
- Your LLM API or GitLab instance may use a certificate not trusted by default.
- Follow Part 5 to inject your CA certificate.

**Agent pauses immediately**
- Check the gas limits. The defaults are 80k input / 20k output for jobs, 160k input / 40k output for sessions.
- A very large codebase context can consume the budget before meaningful work starts. Increase `gas_limit_input` in `.agents/config.yaml`.

**`cluster-up.sh` fails waiting for GitLab**
- Ensure Docker has at least 8 GB RAM and 4 CPUs allocated.
- GitLab can take longer than expected on slower machines. Re-run the script — it is idempotent.

**Pod stuck in `ImagePullBackOff`**
- For local KIND dev, images must be pushed to `localhost:5001`. Run `./scripts/load-images.sh`.
- For production, verify the image tags in your Helm values match what was pushed to your registry.
