import json
import os
import uuid

from kubernetes import client, config
from kubernetes.config import ConfigException

from shared.models import AgentConfig, TaskSpec, SessionRecord

# Shell script run by the git-clone init container.
# Reads PROVIDER, PROJECT_PATH, CLONE_BRANCH plus provider credential env vars,
# constructs an authenticated clone URL, and clones into /workspace.
_GIT_CLONE_SCRIPT = """\
#!/bin/sh
set -e
BRANCH="${CLONE_BRANCH:-main}"
case "${PROVIDER:-gitlab}" in
  gitlab)
    SCHEME="${GITLAB_URL%%://*}"
    HOST="${GITLAB_URL#*://}"
    HOST="${HOST%/}"
    CLONE_URL="${SCHEME}://oauth2:${GITLAB_TOKEN}@${HOST}/${PROJECT_PATH}.git"
    ;;
  github)
    CLONE_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${PROJECT_PATH}.git"
    ;;
  bitbucket)
    CLONE_URL="https://${BB_USERNAME}:${BB_APP_PASSWORD}@bitbucket.org/${PROJECT_PATH}.git"
    ;;
  gitea)
    SCHEME="${GITEA_URL%%://*}"
    HOST="${GITEA_URL#*://}"
    HOST="${HOST%/}"
    CLONE_URL="${SCHEME}://oauth2:${GITEA_TOKEN}@${HOST}/${PROJECT_PATH}.git"
    ;;
  *)
    echo "Unknown PROVIDER: ${PROVIDER}" >&2
    exit 1
    ;;
esac
git clone --depth=1 -b "${BRANCH}" "${CLONE_URL}" /workspace \
  || git clone --depth=1 "${CLONE_URL}" /workspace
"""


class KubeClient:
    def __init__(self):
        try:
            config.load_incluster_config()
        except ConfigException:
            config.load_kube_config()

        self._batch = client.BatchV1Api()
        self._namespace = os.getenv("PI_AGENT_NAMESPACE", "pi-agents")
        self._image = os.getenv("PI_AGENT_IMAGE", "localhost:5001/pi-agent-worker:latest")
        self._gitlab_url = os.getenv("GITLAB_URL", "http://gitlab-webservice-default.gitlab.svc.cluster.local:8080")
        self._llm_endpoint = os.getenv("LLM_ENDPOINT", "")
        self._gateway_url = os.getenv("GATEWAY_URL", "http://pi-agent-gateway")

    def _provider_credential_env_vars(self) -> list[client.V1EnvVar]:
        """Return env var definitions for all supported provider credentials."""
        return [
            # GitLab
            client.V1EnvVar(name="GITLAB_URL", value=self._gitlab_url),
            client.V1EnvVar(
                name="GITLAB_TOKEN",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="gitlab-creds", key="token", optional=True,
                    )
                ),
            ),
            # GitHub
            client.V1EnvVar(
                name="GITHUB_TOKEN",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="github-creds", key="token", optional=True,
                    )
                ),
            ),
            # Bitbucket
            client.V1EnvVar(
                name="BB_USERNAME",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="bitbucket-creds", key="username", optional=True,
                    )
                ),
            ),
            client.V1EnvVar(
                name="BB_APP_PASSWORD",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="bitbucket-creds", key="app-password", optional=True,
                    )
                ),
            ),
            # Gitea
            client.V1EnvVar(
                name="GITEA_URL",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="gitea-creds", key="url", optional=True,
                    )
                ),
            ),
            client.V1EnvVar(
                name="GITEA_TOKEN",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="gitea-creds", key="token", optional=True,
                    )
                ),
            ),
        ]

    def _workspace_init_container(
        self, project_path: str, clone_branch: str, provider: str
    ) -> client.V1Container:
        """Return an init container that clones the repo into /workspace."""
        env = self._provider_credential_env_vars() + [
            client.V1EnvVar(name="PROVIDER", value=provider),
            client.V1EnvVar(name="PROJECT_PATH", value=project_path),
            client.V1EnvVar(name="CLONE_BRANCH", value=clone_branch),
        ]
        return client.V1Container(
            name="git-clone",
            image="alpine/git:latest",
            command=["sh", "-c", _GIT_CLONE_SCRIPT],
            env=env,
            volume_mounts=[
                client.V1VolumeMount(name="workspace", mount_path="/workspace"),
            ],
        )

    def spawn_agent_job(self, task_spec: TaskSpec, agent_config: AgentConfig | None = None) -> str:
        job_name = f"pi-agent-{task_spec.task.replace('_', '-')}-{uuid.uuid4().hex[:8]}"

        image = agent_config.image if agent_config is not None else self._image
        provider = os.getenv("PROVIDER", "gitlab")
        project_path = task_spec.project_path
        clone_branch = task_spec.context.get("clone_branch", "main")

        env_vars = [
            client.V1EnvVar(name="TASK", value=task_spec.task),
            client.V1EnvVar(name="PROJECT_ID", value=str(task_spec.project_id)),
            client.V1EnvVar(name="PROJECT_PATH", value=project_path),
            client.V1EnvVar(name="TASK_CONTEXT", value=json.dumps(task_spec.context)),
            client.V1EnvVar(name="JOB_ID", value=job_name),
            client.V1EnvVar(name="GATEWAY_URL", value=self._gateway_url),
            client.V1EnvVar(name="PROVIDER", value=provider),
            client.V1EnvVar(name="LLM_ENDPOINT", value=self._llm_endpoint),
            client.V1EnvVar(name="WORKSPACE", value="/workspace"),
            client.V1EnvVar(
                name="OPENAI_API_KEY",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="llm-creds",
                        key="api-key",
                    )
                ),
            ),
        ] + self._provider_credential_env_vars()

        if agent_config is not None:
            env_vars += [
                client.V1EnvVar(name="SYSTEM_PROMPT", value=agent_config.system_prompt),
                client.V1EnvVar(name="GAS_LIMIT_INPUT", value=str(agent_config.gas_limit_input)),
                client.V1EnvVar(name="GAS_LIMIT_OUTPUT", value=str(agent_config.gas_limit_output)),
                client.V1EnvVar(
                    name="AGENT_SKILLS",
                    value=json.dumps([s.model_dump() for s in agent_config.skills]),
                ),
                client.V1EnvVar(
                    name="AGENT_TOOLS",
                    value=json.dumps([t.model_dump() for t in agent_config.tools]),
                ),
            ]

        volume_mounts = [
            client.V1VolumeMount(name="workspace", mount_path="/workspace"),
        ]

        container = client.V1Container(
            name="worker",
            image=image,
            env=env_vars,
            volume_mounts=volume_mounts,
        )

        init_containers = []
        if project_path:
            init_containers.append(
                self._workspace_init_container(project_path, clone_branch, provider)
            )

        pod_spec = client.V1PodSpec(
            init_containers=init_containers if init_containers else None,
            containers=[container],
            restart_policy="Never",
            service_account_name="pi-agent-worker",
            volumes=[
                client.V1Volume(
                    name="workspace",
                    empty_dir=client.V1EmptyDirVolumeSource(),
                )
            ],
        )

        pod_template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels={"app": "pi-agent-worker"}),
            spec=pod_spec,
        )

        job_spec = client.V1JobSpec(
            template=pod_template,
            ttl_seconds_after_finished=300,
        )

        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self._namespace,
                labels={"app": "pi-agent-worker", "task": task_spec.task},
            ),
            spec=job_spec,
        )

        self._batch.create_namespaced_job(namespace=self._namespace, body=job)
        return job_name

    def spawn_kaniko_job(
        self,
        cache_key: str,
        dockerfile_content: str,
        image_tag: str,
    ) -> str:
        """Create a Kaniko K8s Job to build and push a custom Docker image."""
        job_name = f"pi-kaniko-{cache_key[:32]}-{uuid.uuid4().hex[:8]}"

        env_vars = [
            client.V1EnvVar(name="DOCKERFILE_CONTENT", value=dockerfile_content),
        ]

        # Write Dockerfile to /workspace via an init container approach using env var
        container = client.V1Container(
            name="kaniko",
            image="gcr.io/kaniko-project/executor:latest",
            args=[
                "--dockerfile=/workspace/Dockerfile",
                "--context=dir:///workspace",
                f"--destination={image_tag}",
                "--insecure",
            ],
            env=env_vars,
            volume_mounts=[
                client.V1VolumeMount(name="workspace", mount_path="/workspace"),
            ],
        )

        init_container = client.V1Container(
            name="write-dockerfile",
            image="busybox",
            command=["sh", "-c", "echo \"$DOCKERFILE_CONTENT\" > /workspace/Dockerfile"],
            env=env_vars,
            volume_mounts=[
                client.V1VolumeMount(name="workspace", mount_path="/workspace"),
            ],
        )

        pod_spec = client.V1PodSpec(
            init_containers=[init_container],
            containers=[container],
            restart_policy="Never",
            service_account_name="pi-agent-worker",
            volumes=[
                client.V1Volume(
                    name="workspace",
                    empty_dir=client.V1EmptyDirVolumeSource(),
                )
            ],
        )

        pod_template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels={"app": "pi-kaniko"}),
            spec=pod_spec,
        )

        job_spec = client.V1JobSpec(
            template=pod_template,
            ttl_seconds_after_finished=300,
        )

        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self._namespace,
                labels={"app": "pi-kaniko", "cache-key": cache_key[:63]},
            ),
            spec=job_spec,
        )

        self._batch.create_namespaced_job(namespace=self._namespace, body=job)
        return job_name

    def delete_job(self, job_name: str) -> None:
        """Delete a K8s Job by name."""
        self._batch.delete_namespaced_job(
            name=job_name,
            namespace=self._namespace,
            body=client.V1DeleteOptions(propagation_policy="Foreground"),
        )

    def spawn_session_job(self, session: SessionRecord) -> str:
        """Spawn a K8s Job for an interactive session worker."""
        import json as _json
        job_name = f"pi-session-{session.id}"
        provider = os.getenv("PROVIDER", "gitlab")

        env_vars = [
            client.V1EnvVar(name="SESSION_ID", value=session.id),
            client.V1EnvVar(name="GATEWAY_URL", value=self._gateway_url),
            client.V1EnvVar(name="PROVIDER", value=provider),
            client.V1EnvVar(name="LLM_ENDPOINT", value=self._llm_endpoint),
            client.V1EnvVar(name="PROJECT_ID", value=str(session.project_id)),
            client.V1EnvVar(name="PROJECT_PATH", value=session.project_path),
            client.V1EnvVar(name="BRANCH", value=session.branch),
            client.V1EnvVar(name="SESSION_GOAL", value=session.context.goal),
            client.V1EnvVar(name="GAS_LIMIT_INPUT", value=str(session.gas_limit_input)),
            client.V1EnvVar(name="GAS_LIMIT_OUTPUT", value=str(session.gas_limit_output)),
            client.V1EnvVar(name="WORKSPACE", value="/workspace"),
            client.V1EnvVar(
                name="OPENAI_API_KEY",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="llm-creds",
                        key="api-key",
                    )
                ),
            ),
        ] + self._provider_credential_env_vars()

        init_containers = []
        if session.project_path and session.branch:
            init_containers.append(
                self._workspace_init_container(session.project_path, session.branch, provider)
            )

        container = client.V1Container(
            name="worker",
            image=self._image,
            env=env_vars,
            volume_mounts=[
                client.V1VolumeMount(name="workspace", mount_path="/workspace"),
            ],
        )

        pod_spec = client.V1PodSpec(
            init_containers=init_containers if init_containers else None,
            containers=[container],
            restart_policy="Never",
            service_account_name="pi-agent-worker",
            volumes=[
                client.V1Volume(
                    name="workspace",
                    empty_dir=client.V1EmptyDirVolumeSource(),
                )
            ],
        )

        pod_template = client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(labels={"app": "pi-agent-worker", "mode": "session"}),
            spec=pod_spec,
        )

        job_spec = client.V1JobSpec(
            template=pod_template,
            ttl_seconds_after_finished=300,
        )

        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self._namespace,
                labels={"app": "pi-agent-worker", "mode": "session"},
            ),
            spec=job_spec,
        )

        self._batch.create_namespaced_job(namespace=self._namespace, body=job)
        return job_name

    def get_job_status(self, job_name: str) -> str:
        """Return 'succeeded', 'failed', or 'running' for a K8s Job."""
        job = self._batch.read_namespaced_job(name=job_name, namespace=self._namespace)
        if job.status.succeeded:
            return "succeeded"
        if job.status.failed:
            return "failed"
        return "running"
