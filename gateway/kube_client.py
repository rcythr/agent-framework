import json
import os
import uuid

from kubernetes import client, config
from kubernetes.config import ConfigException

from shared.models import AgentConfig, TaskSpec


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

    def spawn_agent_job(self, task_spec: TaskSpec, agent_config: AgentConfig | None = None) -> str:
        job_name = f"pi-agent-{task_spec.task.replace('_', '-')}-{uuid.uuid4().hex[:8]}"

        image = agent_config.image if agent_config is not None else self._image

        env_vars = [
            client.V1EnvVar(name="TASK", value=task_spec.task),
            client.V1EnvVar(name="PROJECT_ID", value=str(task_spec.project_id)),
            client.V1EnvVar(name="TASK_CONTEXT", value=json.dumps(task_spec.context)),
            client.V1EnvVar(name="JOB_ID", value=job_name),
            client.V1EnvVar(name="GATEWAY_URL", value=self._gateway_url),
            client.V1EnvVar(name="GITLAB_URL", value=self._gitlab_url),
            client.V1EnvVar(name="LLM_ENDPOINT", value=self._llm_endpoint),
            client.V1EnvVar(
                name="GITLAB_TOKEN",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="gitlab-creds",
                        key="token",
                    )
                ),
            ),
            client.V1EnvVar(
                name="OPENAI_API_KEY",
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name="llm-creds",
                        key="api-key",
                    )
                ),
            ),
        ]

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

        container = client.V1Container(
            name="worker",
            image=image,
            env=env_vars,
        )

        pod_spec = client.V1PodSpec(
            containers=[container],
            restart_policy="Never",
            service_account_name="pi-agent-worker",
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

    def get_job_status(self, job_name: str) -> str:
        """Return 'succeeded', 'failed', or 'running' for a K8s Job."""
        job = self._batch.read_namespaced_job(name=job_name, namespace=self._namespace)
        if job.status.succeeded:
            return "succeeded"
        if job.status.failed:
            return "failed"
        return "running"
