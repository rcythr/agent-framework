import json
import os
import uuid

from kubernetes import client, config
from kubernetes.config import ConfigException

from shared.models import TaskSpec


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

    def spawn_agent_job(self, task_spec: TaskSpec) -> str:
        job_name = f"pi-agent-{task_spec.task.replace('_', '-')}-{uuid.uuid4().hex[:8]}"

        env_vars = [
            client.V1EnvVar(name="TASK", value=task_spec.task),
            client.V1EnvVar(name="PROJECT_ID", value=str(task_spec.project_id)),
            client.V1EnvVar(name="TASK_CONTEXT", value=json.dumps(task_spec.context)),
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

        container = client.V1Container(
            name="worker",
            image=self._image,
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
