# pi-agent Implementation Harness

Manages KIND clusters and composes agent prompts for each implementation phase. Agents receive a fully self-contained prompt with credentials — they never run `docker`, `kind`, or `helm` themselves.

## Prerequisites

Install on the **host machine** running the harness (not inside the cluster):

| Tool | Minimum version | Purpose |
|---|---|---|
| Python | 3.11+ | Harness runtime |
| `kind` | 0.23+ | Cluster creation |
| `kubectl` | 1.28+ | Cluster interaction (also used by agents) |
| `docker` | 24+ | Local registry + image builds |
| `helm` | 3.14+ | GitLab CE deployment |

**Memory:** GitLab CE requires ~3.5 GB per cluster. Each parallel phase gets its own cluster. Running phases 2, 3, 4, and 5 simultaneously requires ~16 GB RAM available to Docker.

## Directory structure

```
harness/
├── harness.py        # CLI entrypoint
├── phases.py         # Phase registry (IDs, titles, cluster requirements)
├── cluster.py        # KIND cluster provisioning and teardown
├── prompt.py         # Agent prompt composition
├── state.py          # JSON state persistence
├── requirements.txt  # No third-party deps needed
└── README.md

tasks/                # Phase task files (one directory up)
├── phase-0-provider-abstraction.md
├── phase-1-infrastructure.md
├── ...
└── DEPENDENCIES.md
```

## Usage

### Run a single phase

```bash
# Phase 0 — pure Python, no cluster needed
python harness.py run --phase 0 --repo-path /path/to/pi-agent

# Phase 1 — provisions a full KIND cluster with GitLab CE
python harness.py run --phase 1 --repo-path /path/to/pi-agent

# Phase 0 explicitly skipping cluster (same as default for phase 0)
python harness.py run --phase 0 --no-cluster --repo-path /path/to/pi-agent
```

### Run parallel phases

Phases 2, 3, 4, and 5 can all run simultaneously. Each gets its own isolated cluster:

```bash
python harness.py run --phase 2 --phase 3 --phase 4 --phase 5 \
  --repo-path /path/to/pi-agent
```

The harness provisions all four clusters concurrently, then emits four separate prompts.

### Output formats

```bash
# Print prompt to stdout (default)
python harness.py run --phase 1 --output print

# Write prompt to a file (prompt-phase-1.txt)
python harness.py run --phase 1 --output file

# Emit JSON (includes credentials dict alongside the prompt)
python harness.py run --phase 1 --output json
```

### Check cluster status

```bash
python harness.py status
```

### Tear down clusters

```bash
# Tear down a specific phase's cluster
python harness.py teardown --phase 2

# Tear down all managed clusters
python harness.py teardown --all
```

## How it works

### Cluster naming and port allocation

Each phase gets a deterministically named cluster and non-colliding host ports:

| Phase | Cluster name | HTTP port | Registry port |
|---|---|---|---|
| 1 | `pi-agent-phase-1` | 8080 | 5001 |
| 2 | `pi-agent-phase-2` | 8180 | 5002 |
| 3 | `pi-agent-phase-3` | 8280 | 5003 |
| 4 | `pi-agent-phase-4` | 8380 | 5004 |
| 5 | `pi-agent-phase-5` | 8480 | 5005 |
| 6 | `pi-agent-phase-6` | 8580 | 5006 |
| 7a | `pi-agent-phase-7a` | 8680 | 5007 |
| ... | ... | ... | ... |

### Cluster provisioning sequence

For each phase that needs a cluster:

1. Start a local Docker registry (`pi-agent-registry-{phase_id}`) on the assigned port
2. Create a 3-node KIND cluster (1 control-plane + 2 workers) with host port mappings
3. Connect the registry to the KIND Docker network
4. Install nginx ingress controller and wait for it to be ready
5. Deploy GitLab CE via Helm with minimal resource requests
6. Wait for GitLab webservice pod to be ready (~3–5 minutes)
7. Seed GitLab via API: create group, project, service token, webhook
8. Persist cluster name, kubeconfig path, and all credentials to `.harness-state.json`

### Kubeconfig isolation

Each cluster's kubeconfig is written to a separate file in `/tmp/harness-kubeconfigs/`. The agent is given the exact path and instructed to use it with `--kubeconfig` or `KUBECONFIG=`. This prevents parallel agents from clobbering each other's default kubeconfig.

### Prompt structure

Each agent prompt contains five sections:

1. **Role and constraints** — who the agent is; that it cannot run docker/kind/helm
2. **Environment** — kubeconfig path, registry host, GitLab URL/token/project ID, example kubectl commands
3. **Repository context** — the current directory tree of the repo
4. **Task specification** — full contents of the relevant phase task file (with sub-task extraction for Phase 7)
5. **Execution instructions** — how to run tests, how to deploy, the completion marker

### Phase 7 sub-task extraction

Phase 7 has a single task file covering sub-tasks 7a through 7e. The harness extracts only the relevant sub-task section (plus the global Goal and Definition of Done) so each agent isn't overwhelmed with context that doesn't apply to their specific sub-task.

### Completion detection

Agents signal completion by printing:
```
PHASE_<id>_COMPLETE
```

Or a blocker:
```
PHASE_<id>_BLOCKED: <description>
```

The harness looks for these markers in the agent's output stream to update state and potentially trigger dependent phases.

## State file

The harness writes `.harness-state.json` in its own directory. This file tracks all live clusters and their credentials. It is safe to inspect manually:

```json
{
  "clusters": {
    "1": {
      "phase_id": "1",
      "cluster_name": "pi-agent-phase-1",
      "kubeconfig_path": "/tmp/harness-kubeconfigs/pi-agent-phase-1.yaml",
      "status": "ready",
      "credentials": {
        "gitlab_url": "http://gitlab.localhost:8080",
        "gitlab_service_token": "glpat-...",
        ...
      }
    }
  }
}
```

**Security note:** The state file contains GitLab tokens. It is only used for local development clusters and should not be committed.

## Troubleshooting

**GitLab takes too long to start:** Helm waits up to 10 minutes. If it times out, the cluster is partially provisioned. Run `python harness.py teardown --phase <id>` and retry.

**Port already in use:** If a previous cluster wasn't cleaned up, the registry or ingress port may be taken. Run `python harness.py teardown --all` to clean everything up, then retry.

**Agent can't reach GitLab:** The agent should use the in-cluster DNS URL (`http://gitlab-webservice-default.gitlab.svc.cluster.local:8080`) for configuring the gateway, not the host-accessible URL. Both are provided in the prompt.

**Out of memory:** GitLab CE needs ~3.5 GB. If running multiple phases in parallel, increase Docker Desktop's memory allocation. Consider running phases sequentially if memory is constrained.
