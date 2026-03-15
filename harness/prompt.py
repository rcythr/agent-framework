"""
PromptComposer — assembles the complete prompt delivered to an implementation agent.

The prompt has five sections:
  1. Role and constraints  — who the agent is and what it must not do
  2. Environment           — cluster credentials, URLs, kubeconfig path
  3. Repository context    — current repo layout so the agent can orient itself
  4. Task specification    — the full contents of the relevant phase task file,
                             with sub-task filtering for phases like 7a/7b/etc.
  5. Execution instructions — how to run tests, how to signal completion

Agents are explicitly told:
  - They cannot run `docker`, `kind`, or `helm` directly
  - They interact with the cluster via `kubectl` using the provided kubeconfig
  - All image builds must use `docker build` + `docker push` to the supplied registry
  - GitLab is already running at the supplied URL; they do not deploy it
"""

import os
from pathlib import Path

from phases import PhaseSpec

# Sub-task markers: for phase 7's single task file, we extract only the
# relevant sub-task section so the agent isn't given irrelevant context.
_SUBTASK_HEADERS = {
    "7a": "## Sub-task 7a",
    "7b": "## Sub-task 7b",
    "7c": "## Sub-task 7c",
    "7d": "## Sub-task 7d",
    "7e": "## Sub-task 7e",
}

_NEXT_SUBTASK = {
    "7a": "## Sub-task 7b",
    "7b": "## Sub-task 7c",
    "7c": "## Sub-task 7d",
    "7d": "## Definition of Done",
    "7e": "## Definition of Done",
}


class PromptComposer:
    def __init__(self, tasks_dir: Path, repo_path: Path) -> None:
        self._tasks_dir = tasks_dir
        self._repo_path = repo_path

    def compose(self, phase_id: str, spec: PhaseSpec, credentials: dict) -> str:
        task_content = self._load_task(spec, phase_id)
        repo_tree = self._repo_tree()
        env_section = self._env_section(phase_id, spec, credentials)

        parts = [
            self._role_section(phase_id, spec),
            env_section,
            self._repo_section(repo_tree),
            self._task_section(task_content),
            self._execution_section(phase_id, spec, credentials),
        ]
        return "\n\n".join(p.strip() for p in parts if p.strip())

    # ── Sections ──────────────────────────────────────────────────────────────

    def _role_section(self, phase_id: str, spec: PhaseSpec) -> str:
        return f"""# Role

You are an autonomous software engineering agent implementing **Phase {phase_id} — {spec.title}** of the pi-agent project, an autonomous coding agent system that integrates with GitLab and runs on Kubernetes.

You will write code, create files, run tests, and iterate until the phase's Definition of Done is met. You work directly in the repository on disk.

## Hard constraints — read carefully

- **You cannot run `docker`, `kind`, `helm`, or any container/cluster management command.** The cluster has already been provisioned for you. All cluster credentials are provided below.
- **You interact with Kubernetes exclusively via `kubectl`**, using the kubeconfig path specified in the Environment section. Always pass `--kubeconfig <path>` or set `KUBECONFIG=<path>` in your shell environment before running kubectl commands.
- **You can build and push Docker images** using `docker build` and `docker push` targeting the local registry specified below. This is the only docker operation permitted.
- **GitLab is already running** at the URL specified below. Do not attempt to deploy, restart, or reconfigure it.
- **Follow the TDD approach** specified in the task: write tests first, then implement until they pass.
- **Do not modify files outside the repository path** specified in the Repository section.
- **Commit your work** with descriptive commit messages as you complete logical units. Do not leave the repo in a broken state."""

    def _env_section(self, phase_id: str, spec: PhaseSpec, credentials: dict) -> str:
        if not credentials:
            return """# Environment

This phase has no cluster dependency. You are working in a pure Python environment.
Run tests with `pytest` from the repository root. No Kubernetes or GitLab access is needed."""

        kubeconfig = credentials.get("kubeconfig_path", "")
        registry = credentials.get("registry_host", "")
        gateway_url = credentials.get("gateway_url", "")
        http_port = credentials.get("http_port", "")

        lines = [
            "# Environment",
            "",
            "The following credentials and endpoints have been provisioned for you.",
            "**Do not attempt to recreate or modify the cluster infrastructure.**",
            "",
            "## Kubernetes",
            f"- **Kubeconfig path:** `{kubeconfig}`",
            f"- **Cluster name:** `{credentials.get('cluster_name', '')}`",
            f"- **Namespace for pi-agent:** `pi-agents`",
            "",
            "Set this before running any kubectl command:",
            "```bash",
            f"export KUBECONFIG={kubeconfig}",
            "```",
            "",
            "## Docker Registry",
            f"- **Registry host:** `{registry}`",
            f"- **Gateway image:** `{registry}/pi-agent-gateway:latest`",
            f"- **Worker image:** `{registry}/pi-agent-worker:latest`",
            "",
            "Build and push images like this (the cluster will pull from this registry):",
            "```bash",
            f"docker build -t {registry}/pi-agent-gateway:latest -f Dockerfile.gateway .",
            f"docker push {registry}/pi-agent-gateway:latest",
            f"kubectl rollout restart deployment/pi-agent-gateway -n pi-agents --kubeconfig {kubeconfig}",
            "```",
            "",
            "## Gateway",
            f"- **Gateway URL (from host):** `{gateway_url}`",
        ]

        if spec.needs_gitlab and "gitlab_url" in credentials:
            lines += [
                "",
                "## GitLab",
                f"- **GitLab URL (from host):** `{credentials['gitlab_url']}`",
                f"- **GitLab URL (in-cluster DNS):** `{credentials.get('gitlab_internal_url', '')}`",
                f"- **Root username:** `{credentials.get('gitlab_root_user', 'root')}`",
                f"- **Root password:** `{credentials.get('gitlab_root_password', '')}`",
                f"- **Service token (project-scoped API token):** `{credentials.get('gitlab_service_token', '')}`",
                f"- **Test project ID:** `{credentials.get('gitlab_project_id', '')}`",
                f"- **Test project path:** `{credentials.get('gitlab_project_path', '')}`",
                f"- **Webhook secret:** `{credentials.get('gitlab_webhook_secret', '')}`",
                "",
                "The test project (`pi-agent-test/test-repo`) already exists in GitLab with a webhook",
                "registered to the gateway's in-cluster service DNS. Opening an MR or pushing a commit",
                "to this project will trigger the gateway.",
                "",
                "**When configuring the gateway deployment**, set these env vars / K8s secrets:",
                "```bash",
                f"GITLAB_URL={credentials.get('gitlab_internal_url', '')}",
                f"GITLAB_TOKEN={credentials.get('gitlab_service_token', '')}",
                f"GITLAB_WEBHOOK_SECRET={credentials.get('gitlab_webhook_secret', '')}",
                "```",
                "",
                "Apply secrets with:",
                "```bash",
                f"kubectl create secret generic gitlab-creds \\",
                f"  --namespace pi-agents \\",
                f"  --from-literal=token={credentials.get('gitlab_service_token', '')} \\",
                f"  --from-literal=webhook-secret={credentials.get('gitlab_webhook_secret', '')} \\",
                f"  --dry-run=client -o yaml | kubectl apply -f - --kubeconfig {kubeconfig}",
                "```",
            ]

        lines += [
            "",
            "## Useful kubectl commands",
            "```bash",
            f"# Watch pods across all namespaces",
            f"kubectl get pods -A --kubeconfig {kubeconfig}",
            f"# Watch agent jobs",
            f"kubectl get jobs -n pi-agents --kubeconfig {kubeconfig}",
            f"# Tail gateway logs",
            f"kubectl logs -f deployment/pi-agent-gateway -n pi-agents --kubeconfig {kubeconfig}",
            f"# Tail a worker job's logs",
            f"kubectl logs -f job/<job-name> -n pi-agents --kubeconfig {kubeconfig}",
            "```",
        ]

        return "\n".join(lines)

    def _repo_section(self, tree: str) -> str:
        return f"""# Repository

**Repository path:** `{self._repo_path}`

Work exclusively within this directory. The current layout is:

```
{tree}
```

If a file listed in the task does not exist yet, create it. If it exists, read it before modifying."""

    def _task_section(self, task_content: str) -> str:
        return f"""# Task Specification

The following is the complete specification for this phase. Implement everything described. Follow the TDD approach: write the listed tests first, then write the implementation until they pass.

---

{task_content}"""

    def _execution_section(self, phase_id: str, spec: PhaseSpec, credentials: dict) -> str:
        kubeconfig = credentials.get("kubeconfig_path", "~/.kube/config")
        gateway_url = credentials.get("gateway_url", "http://pi-agent.localhost:8080")
        gitlab_url = credentials.get("gitlab_url", "")

        e2e_note = ""
        if spec.needs_cluster:
            e2e_note = f"""
## Running E2E tests

E2E tests require the cluster to be running and the gateway to be deployed. Before running E2E tests:

1. Build and push images to the local registry (see Environment section)
2. Apply all manifests: `kubectl apply -f k8s/ --kubeconfig {kubeconfig}`
3. Wait for the gateway: `kubectl rollout status deployment/pi-agent-gateway -n pi-agents --kubeconfig {kubeconfig}`
4. Source test credentials:
```bash
export GITLAB_URL={gitlab_url}
export GITLAB_TOKEN={credentials.get("gitlab_service_token", "")}
export GITLAB_PROJECT_ID={credentials.get("gitlab_project_id", "")}
export GATEWAY_URL={gateway_url}
```
5. Run E2E tests: `pytest tests/e2e/ -v`"""

        return f"""# Execution Instructions

## Running unit and integration tests

```bash
cd {self._repo_path}
pip install -r requirements.txt
pytest tests/unit/ -v
pytest tests/integration/ -v
```

Run tests after each logical change. All tests in the task's "Tests to Write First" section must pass before the phase is considered complete.
{e2e_note}

## Definition of Done checklist

Before finishing:
- [ ] All tests listed in the task file pass (`pytest` exits 0)
- [ ] No regressions in previously passing tests
- [ ] All files listed in the Deliverables section exist and are implemented
- [ ] Code is committed with descriptive messages

## Signalling completion

When the Definition of Done is met, output the following marker on its own line so the harness can detect completion:

```
PHASE_{phase_id}_COMPLETE
```

If you encounter a blocker that requires human intervention (e.g. a missing credential, an infrastructure issue outside your control), output:

```
PHASE_{phase_id}_BLOCKED: <brief description of the blocker>
```"""

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_task(self, spec: PhaseSpec, phase_id: str) -> str:
        task_path = self._tasks_dir / spec.task_file
        if not task_path.exists():
            raise FileNotFoundError(f"Task file not found: {task_path}")
        content = task_path.read_text()

        # For Phase 7 sub-tasks, extract only the relevant section
        if phase_id in _SUBTASK_HEADERS:
            content = self._extract_subtask(content, phase_id)

        return content

    def _extract_subtask(self, content: str, phase_id: str) -> str:
        """
        Extract the relevant sub-task section from phase 7's combined task file.
        Always includes the top-level Goal, Prerequisites, and Definition of Done.
        """
        lines = content.splitlines()

        # Keep preamble up to the first Sub-task header
        first_subtask_pattern = "## Sub-task 7a"
        preamble_end = next(
            (i for i, l in enumerate(lines) if l.startswith(first_subtask_pattern)),
            len(lines),
        )
        preamble = "\n".join(lines[:preamble_end])

        # Extract this sub-task's section
        start_header = _SUBTASK_HEADERS[phase_id]
        end_header = _NEXT_SUBTASK.get(phase_id, "## Definition of Done")

        start = next((i for i, l in enumerate(lines) if l.startswith(start_header)), None)
        end = next((i for i, l in enumerate(lines) if l.startswith(end_header)), len(lines))

        if start is None:
            return content  # fallback: return full file

        subtask_content = "\n".join(lines[start:end])

        # Extract Definition of Done
        dod_start = next(
            (i for i, l in enumerate(lines) if l.startswith("## Definition of Done")),
            None,
        )
        dod_content = "\n".join(lines[dod_start:]) if dod_start is not None else ""

        return f"{preamble}\n\n{subtask_content}\n\n{dod_content}"

    def _repo_tree(self) -> str:
        """
        Build a simple directory tree of the repo (2 levels deep).
        Falls back gracefully if the repo doesn't exist yet.
        """
        if not self._repo_path.exists():
            return "(repository not yet initialised)"

        lines = []
        root = self._repo_path
        lines.append(root.name + "/")

        try:
            for item in sorted(root.iterdir()):
                if item.name.startswith(".") or item.name in ("__pycache__", "node_modules", ".git"):
                    continue
                if item.is_dir():
                    lines.append(f"  {item.name}/")
                    try:
                        for sub in sorted(item.iterdir()):
                            if sub.name.startswith(".") or sub.name == "__pycache__":
                                continue
                            marker = "/" if sub.is_dir() else ""
                            lines.append(f"    {sub.name}{marker}")
                    except PermissionError:
                        pass
                else:
                    lines.append(f"  {item.name}")
        except PermissionError:
            lines.append("  (permission denied)")

        return "\n".join(lines)
