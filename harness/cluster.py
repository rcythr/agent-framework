"""
ClusterManager — provisions and tears down KIND clusters on behalf of agents.

Each phase gets its own isolated KIND cluster named pi-agent-phase-{phase_id}.
The manager:
  1. Creates the cluster using a per-phase kind config
  2. Starts a local Docker registry and connects it to the cluster
  3. Installs the nginx ingress controller
  4. Optionally deploys GitLab CE via Helm and seeds it with test fixtures
  5. Returns a credentials dict the PromptComposer injects into the agent prompt

Agents never run docker, kind, kubectl, or helm themselves.  All cluster
operations happen here in the harness process; the agent receives only the
resulting credentials and a kubeconfig it can use via kubectl.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from phases import PhaseSpec
from state import HarnessState

log = logging.getLogger("harness.cluster")

NGINX_INGRESS_MANIFEST = (
    "https://raw.githubusercontent.com/kubernetes/ingress-nginx"
    "/main/deploy/static/provider/kind/deploy.yaml"
)
GITLAB_CHART = "gitlab/gitlab"
GITLAB_HELM_REPO = "https://charts.gitlab.io/"

# Port offsets per cluster so parallel clusters don't collide on the host.
# Phase ID is mapped to a deterministic HTTP port.
_PHASE_PORT_MAP: dict[str, int] = {
    "0": 0,    # no cluster
    "1": 8080,
    "2": 8180,
    "3": 8280,
    "4": 8380,
    "5": 8480,
    "6": 8580,
    "7a": 8680,
    "7b": 8780,
    "7c": 8880,
    "7d": 8980,
    "7e": 9080,
    "8": 9180,
}

_REGISTRY_PORT_MAP: dict[str, int] = {
    "1": 5001,
    "2": 5002,
    "3": 5003,
    "4": 5004,
    "5": 5005,
    "6": 5006,
    "7a": 5007,
    "7b": 5008,
    "7c": 5009,
    "7d": 5010,
    "7e": 5011,
    "8": 5012,
}


class ClusterManager:
    def __init__(self, state: HarnessState) -> None:
        self._state = state

    # ── Public API ────────────────────────────────────────────────────────────

    async def provision(self, phase_id: str, spec: PhaseSpec) -> dict:
        """
        Provision a full cluster for the given phase.
        Returns a credentials dict ready for prompt injection.
        """
        cluster_name = f"pi-agent-phase-{phase_id}"
        http_port = _PHASE_PORT_MAP.get(phase_id, 8080)
        registry_port = _REGISTRY_PORT_MAP.get(phase_id, 5001)
        registry_name = f"pi-agent-registry-{phase_id}"
        gitlab_password = "dev-local-only"
        webhook_secret = f"webhook-secret-phase-{phase_id}"

        log.info("[%s] Provisioning cluster '%s' (http_port=%d)", phase_id, cluster_name, http_port)

        # 1. Local registry
        await self._start_registry(registry_name, registry_port)

        # 2. KIND cluster
        kubeconfig_path = await self._create_cluster(cluster_name, http_port, registry_port)
        env = self._kubectl_env(kubeconfig_path)

        # 3. Connect registry to cluster network
        await self._run(["docker", "network", "connect", "kind", registry_name], check=False)

        # 4. Apply registry ConfigMap
        await self._apply_registry_configmap(env, registry_port)

        # 5. nginx ingress
        await self._install_ingress(env)

        credentials: dict = {
            "cluster_name": cluster_name,
            "kubeconfig_path": str(kubeconfig_path),
            "http_port": http_port,
            "registry_name": registry_name,
            "registry_port": registry_port,
            "registry_host": f"localhost:{registry_port}",
            "gateway_url": f"http://pi-agent.localhost:{http_port}",
        }

        # 6. GitLab CE (if needed)
        if spec.needs_gitlab:
            gitlab_creds = await self._deploy_gitlab(
                env=env,
                http_port=http_port,
                gitlab_password=gitlab_password,
                webhook_secret=webhook_secret,
            )
            credentials.update(gitlab_creds)

        # 7. Persist state
        self._state.save_cluster(
            phase_id=phase_id,
            cluster_name=cluster_name,
            kubeconfig_path=str(kubeconfig_path),
            credentials=credentials,
        )

        log.info("[%s] Cluster provisioned successfully", phase_id)
        return credentials

    async def teardown(self, phase_id: str) -> None:
        """Delete the KIND cluster and registry for a phase."""
        cluster_info = self._state.get_cluster(phase_id)
        if not cluster_info:
            log.warning("[%s] No cluster state found, nothing to tear down", phase_id)
            return

        cluster_name = cluster_info["cluster_name"]
        registry_name = f"pi-agent-registry-{phase_id}"

        log.info("[%s] Deleting KIND cluster '%s'...", phase_id, cluster_name)
        await self._run(["kind", "delete", "cluster", "--name", cluster_name], check=False)

        log.info("[%s] Removing registry container '%s'...", phase_id, registry_name)
        await self._run(["docker", "rm", "-f", registry_name], check=False)

        kubeconfig = Path(cluster_info["kubeconfig_path"])
        if kubeconfig.exists():
            kubeconfig.unlink()

        self._state.remove_cluster(phase_id)
        log.info("[%s] Teardown complete", phase_id)

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _start_registry(self, registry_name: str, registry_port: int) -> None:
        result = await self._run(
            ["docker", "ps", "--format", "{{.Names}}"], capture=True, check=False
        )
        if registry_name in (result.stdout or ""):
            log.info("Registry '%s' already running", registry_name)
            return
        log.info("Starting registry '%s' on port %d...", registry_name, registry_port)
        await self._run([
            "docker", "run", "-d", "--restart=always",
            "-p", f"127.0.0.1:{registry_port}:5000",
            "--name", registry_name,
            "registry:2",
        ])

    async def _create_cluster(
        self, cluster_name: str, http_port: int, registry_port: int
    ) -> Path:
        # Check if cluster already exists
        result = await self._run(
            ["kind", "get", "clusters"], capture=True, check=False
        )
        if cluster_name in (result.stdout or "").splitlines():
            log.info("Cluster '%s' already exists, skipping creation", cluster_name)
        else:
            log.info("Creating KIND cluster '%s'...", cluster_name)
            config_yaml = self._render_kind_config(cluster_name, http_port)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, prefix=f"kind-{cluster_name}-"
            ) as f:
                f.write(config_yaml)
                config_path = f.name
            try:
                await self._run([
                    "kind", "create", "cluster",
                    "--name", cluster_name,
                    "--config", config_path,
                ])
            finally:
                os.unlink(config_path)

        # Export kubeconfig to a dedicated file so parallel clusters don't clobber ~/.kube/config
        kubeconfig_dir = Path(tempfile.gettempdir()) / "harness-kubeconfigs"
        kubeconfig_dir.mkdir(exist_ok=True)
        kubeconfig_path = kubeconfig_dir / f"{cluster_name}.yaml"
        await self._run([
            "kind", "export", "kubeconfig",
            "--name", cluster_name,
            "--kubeconfig", str(kubeconfig_path),
        ])
        log.info("Kubeconfig written to %s", kubeconfig_path)
        return kubeconfig_path

    def _render_kind_config(self, cluster_name: str, http_port: int) -> str:
        https_port = http_port + 363  # e.g. 8080 → 8443
        return f"""kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: {cluster_name}
nodes:
  - role: control-plane
    kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true"
    extraPortMappings:
      - containerPort: 80
        hostPort: {http_port}
        protocol: TCP
      - containerPort: 443
        hostPort: {https_port}
        protocol: TCP
  - role: worker
  - role: worker
"""

    async def _apply_registry_configmap(self, env: dict, registry_port: int) -> None:
        configmap = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:{registry_port}"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
"""
        await self._kubectl_apply_stdin(configmap, env)

    async def _install_ingress(self, env: dict) -> None:
        log.info("Installing nginx ingress controller...")
        await self._run(
            ["kubectl", "apply", "-f", NGINX_INGRESS_MANIFEST],
            env=env,
        )
        log.info("Waiting for ingress controller to be ready...")
        await self._run([
            "kubectl", "wait",
            "--namespace", "ingress-nginx",
            "--for=condition=ready", "pod",
            "--selector=app.kubernetes.io/component=controller",
            "--timeout=120s",
        ], env=env)

    async def _deploy_gitlab(
        self,
        env: dict,
        http_port: int,
        gitlab_password: str,
        webhook_secret: str,
    ) -> dict:
        log.info("Deploying GitLab CE via Helm (this takes 3–5 minutes)...")

        # Add Helm repo
        await self._run(
            ["helm", "repo", "add", "gitlab", GITLAB_HELM_REPO],
            check=False, env=env,
        )
        await self._run(["helm", "repo", "update"], env=env)

        # Create namespace
        await self._run(
            ["kubectl", "create", "namespace", "gitlab"],
            env=env, check=False,
        )

        # Root password secret
        await self._run([
            "kubectl", "create", "secret", "generic", "gitlab-root-password",
            "--namespace", "gitlab",
            f"--from-literal=password={gitlab_password}",
            "--dry-run=client", "-o", "yaml",
        ], env=env, pipe_to_apply=True)

        # Helm install
        helm_values = self._render_gitlab_helm_values(http_port)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, prefix="gitlab-values-"
        ) as f:
            f.write(helm_values)
            values_path = f.name

        try:
            await self._run([
                "helm", "upgrade", "--install", "gitlab", GITLAB_CHART,
                "--namespace", "gitlab",
                "--values", values_path,
                "--timeout", "10m",
                "--wait",
            ], env=env, timeout=660)
        finally:
            os.unlink(values_path)

        # Apply GitLab ingress
        gitlab_ingress = self._render_gitlab_ingress()
        await self._kubectl_apply_stdin(gitlab_ingress, env)

        # Wait for webservice pod
        log.info("Waiting for GitLab webservice pod to be ready...")
        await self._run([
            "kubectl", "wait",
            "--namespace", "gitlab",
            "--for=condition=ready", "pod",
            "--selector=app=webservice",
            "--timeout=300s",
        ], env=env, timeout=320)

        gitlab_url = f"http://gitlab.localhost:{http_port}"
        log.info("GitLab ready at %s", gitlab_url)

        # Seed GitLab
        seed_result = await self._seed_gitlab(
            gitlab_url=gitlab_url,
            root_password=gitlab_password,
            webhook_secret=webhook_secret,
            env=env,
        )

        return {
            "gitlab_url": gitlab_url,
            "gitlab_root_password": gitlab_password,
            "gitlab_root_user": "root",
            "gitlab_service_token": seed_result["service_token"],
            "gitlab_project_id": seed_result["project_id"],
            "gitlab_project_path": "pi-agent-test/test-repo",
            "gitlab_webhook_secret": webhook_secret,
            "gitlab_internal_url": "http://gitlab-webservice-default.gitlab.svc.cluster.local:8080",
        }

    def _render_gitlab_helm_values(self, http_port: int) -> str:
        return f"""global:
  hosts:
    domain: localhost
    externalIP: 127.0.0.1
    https: false
    gitlab:
      name: gitlab.localhost
      https: false
  ingress:
    class: nginx
    annotations:
      nginx.ingress.kubernetes.io/proxy-body-size: "0"
    tls:
      enabled: false
  initialRootPassword:
    secret: gitlab-root-password
    key: password

gitlab-runner:
  install: false
registry:
  enabled: false
prometheus:
  install: false
grafana:
  enabled: false
certmanager:
  install: false
nginx-ingress:
  enabled: false
gitlab-zoekt:
  install: false

gitlab:
  webservice:
    minReplicas: 1
    maxReplicas: 1
    resources:
      requests:
        cpu: 300m
        memory: 1.5Gi
  sidekiq:
    resources:
      requests:
        cpu: 100m
        memory: 512Mi
  gitaly:
    resources:
      requests:
        cpu: 100m
        memory: 200Mi
  gitlab-shell:
    enabled: false

postgresql:
  resources:
    requests:
      cpu: 100m
      memory: 256Mi

redis:
  resources:
    requests:
      cpu: 50m
      memory: 64Mi

minio:
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
"""

    def _render_gitlab_ingress(self) -> str:
        return """apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: gitlab
  namespace: gitlab
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "0"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
spec:
  ingressClassName: nginx
  rules:
    - host: gitlab.localhost
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: gitlab-webservice-default
                port:
                  number: 8080
"""

    async def _seed_gitlab(
        self,
        gitlab_url: str,
        root_password: str,
        webhook_secret: str,
        env: dict,
    ) -> dict:
        """
        Use the GitLab API to create: group, project, access token, webhook.
        Returns service_token and project_id.
        """
        import urllib.request
        import urllib.parse
        import urllib.error

        log.info("Seeding GitLab at %s...", gitlab_url)

        # Wait for readiness
        for attempt in range(60):
            try:
                urllib.request.urlopen(f"{gitlab_url}/-/readiness", timeout=5)
                break
            except Exception:
                if attempt == 59:
                    raise RuntimeError("GitLab readiness check timed out")
                await asyncio.sleep(5)

        def api(method: str, path: str, data: dict | None = None, token: str | None = None) -> dict:
            url = f"{gitlab_url}/api/v4{path}"
            headers = {"Content-Type": "application/x-www-form-urlencoded"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            body = urllib.parse.urlencode(data).encode() if data else None
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())

        # Get root OAuth token
        token_resp = api("POST", "/oauth/token", {  # type: ignore[arg-type]
            "grant_type": "password",
            "username": "root",
            "password": root_password,
        })
        root_token = token_resp["access_token"]

        # Create group
        group = api("POST", "/groups", {
            "name": "pi-agent-test",
            "path": "pi-agent-test",
            "visibility": "private",
        }, token=root_token)
        group_id = group["id"]

        # Create project
        project = api("POST", "/projects", {
            "name": "test-repo",
            "namespace_id": group_id,
            "initialize_with_readme": "true",
            "visibility": "private",
        }, token=root_token)
        project_id = project["id"]

        # Create project access token
        token_resp = api(
            "POST",
            f"/projects/{project_id}/access_tokens",
            {
                "name": "pi-agent",
                "scopes[]": "api",
                "access_level": "40",
                "expires_at": "2099-01-01",
            },
            token=root_token,
        )
        service_token = token_resp["token"]

        # Register webhook using in-cluster DNS so delivery doesn't traverse the host
        webhook_url = "http://pi-agent-gateway.pi-agents.svc.cluster.local/webhook/gitlab"
        api(
            "POST",
            f"/projects/{project_id}/hooks",
            {
                "url": webhook_url,
                "token": webhook_secret,
                "push_events": "true",
                "merge_requests_events": "true",
                "note_events": "true",
            },
            token=root_token,
        )

        log.info("GitLab seeded: project_id=%s, webhook→%s", project_id, webhook_url)
        return {"service_token": service_token, "project_id": project_id}

    # ── Shell helpers ─────────────────────────────────────────────────────────

    def _kubectl_env(self, kubeconfig_path: Path) -> dict:
        env = os.environ.copy()
        env["KUBECONFIG"] = str(kubeconfig_path)
        return env

    async def _kubectl_apply_stdin(self, manifest: str, env: dict) -> None:
        proc = await asyncio.create_subprocess_exec(
            "kubectl", "apply", "-f", "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate(manifest.encode())
        if proc.returncode != 0:
            raise RuntimeError(f"kubectl apply failed: {stderr.decode()}")

    async def _run(
        self,
        cmd: list[str],
        env: dict | None = None,
        capture: bool = False,
        check: bool = True,
        timeout: int = 300,
        pipe_to_apply: bool = False,
    ):
        """Run a subprocess, optionally capturing output."""
        log.debug("$ %s", " ".join(cmd))
        _env = env or os.environ.copy()

        if pipe_to_apply:
            # pipe stdout of cmd into `kubectl apply -f -`
            p1 = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_env,
            )
            p2 = await asyncio.create_subprocess_exec(
                "kubectl", "apply", "-f", "-",
                stdin=p1.stdout,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_env,
            )
            await asyncio.wait_for(asyncio.gather(p1.wait(), p2.wait()), timeout=timeout)
            return

        stdout_mode = asyncio.subprocess.PIPE if capture else None
        stderr_mode = asyncio.subprocess.PIPE if capture else None

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=stdout_mode,
            stderr=stderr_mode,
            env=_env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(cmd)}")

        class Result:
            returncode = proc.returncode
            stdout = stdout_bytes.decode() if stdout_bytes else ""
            stderr = stderr_bytes.decode() if stderr_bytes else ""

        result = Result()

        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Command failed (exit {proc.returncode}): {' '.join(cmd)}\n"
                f"stderr: {result.stderr}"
            )

        return result
