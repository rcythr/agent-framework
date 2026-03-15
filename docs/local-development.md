# Local Development Guide

This guide explains how to run the full Phalanx stack locally using KIND (Kubernetes IN Docker).

## Prerequisites

Install the following tools before continuing:

- [Docker](https://docs.docker.com/get-docker/) — required for KIND and building images
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/) v0.23+
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [helm](https://helm.sh/docs/intro/install/) v3+

### Resource requirements

GitLab CE is memory-hungry. Ensure Docker has at least **8 GB RAM** available and allow the KIND cluster at least 6 GB. On Docker Desktop, adjust this under *Settings → Resources*.

## First-run walkthrough

From the repository root:

```bash
# Optional: set a non-default root password
export GITLAB_ROOT_PASSWORD="my-secret-password"

# Set your LLM API key so E2E tests can call the LLM
export OPENAI_API_KEY="sk-..."

bash scripts/cluster-up.sh
```

`cluster-up.sh` performs the following steps automatically:

1. Starts a local Docker registry on `localhost:5001`
2. Creates a three-node KIND cluster (`pi-agent`)
3. Installs the nginx ingress controller
4. Deploys GitLab CE via Helm (takes 3–5 minutes on the first run)
5. Builds and pushes the gateway and worker Docker images to the local registry
6. Applies all Kubernetes manifests for the `pi-agents` namespace
7. Seeds GitLab with a test group, project, access token, and webhook

When the script finishes you will see:

```
✅ Environment ready
   GitLab:   http://gitlab.localhost:8080  (root / <password>)
   Gateway:  http://phalanx.localhost:8080
   Test credentials: .env.test
```

## Accessing services

| Service | URL | Credentials |
|---|---|---|
| GitLab web UI | `http://gitlab.localhost:8080` | `root` / value of `GITLAB_ROOT_PASSWORD` |
| Phalanx gateway | `http://phalanx.localhost:8080` | — |
| Local registry | `localhost:5001` | — |

Both `gitlab.localhost` and `phalanx.localhost` resolve to `127.0.0.1` on Linux and most macOS setups without any `/etc/hosts` edits. Windows users may need to add entries manually.

## Rebuilding images after code changes

Use `scripts/load-images.sh` to rebuild and redeploy the gateway and worker without recreating the cluster or reseeding GitLab:

```bash
bash scripts/load-images.sh
```

This rebuilds both images, pushes them to the local registry, and restarts the gateway deployment.

## Running tests

### Unit tests

```bash
pip install -r requirements.txt pytest pytest-asyncio
pytest tests/
```

### E2E tests

E2E tests require the cluster to be running and `.env.test` to be present (written by `seed-gitlab.sh`). Source it before running:

```bash
set -a; source .env.test; set +a
pytest tests/e2e/
```

`OPENAI_API_KEY` must also be set in the environment for tests that invoke the LLM.

## The `.env.test` file

After a successful `cluster-up.sh` or `reseed-gitlab.sh` run, a `.env.test` file is written to the repository root with the following variables:

```
GITLAB_URL=http://gitlab.localhost:8080
GITLAB_TOKEN=<project access token>
GITLAB_WEBHOOK_SECRET=<webhook secret>
GITLAB_PROJECT_ID=<project numeric id>
GITLAB_PROJECT_PATH=pi-agent-test/test-repo
```

This file is gitignored and should never be committed. It is sourced by E2E tests and local dev scripts.

## Re-seeding GitLab after a cluster wipe

If you recreated the cluster but want to re-register the test project and webhook without running the full `cluster-up.sh`:

```bash
bash scripts/reseed-gitlab.sh
```

## Tearing down

```bash
bash scripts/cluster-down.sh
```

This deletes the KIND cluster, removes the local registry container, and deletes `.env.test`.
