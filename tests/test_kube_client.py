import json
import pytest
from unittest.mock import MagicMock, patch
from kubernetes.config import ConfigException

from gateway.kube_client import KubeClient
from shared.models import TaskSpec


def _make_task_spec() -> TaskSpec:
    return TaskSpec(
        task="review_mr",
        project_id=1,
        context={"mr_iid": 42, "action": "open"},
    )


@pytest.fixture
def kube_client():
    with patch("gateway.kube_client.config") as mock_config, \
         patch("gateway.kube_client.client") as mock_client:
        mock_config.load_incluster_config.side_effect = ConfigException("not in cluster")
        mock_batch = MagicMock()
        mock_client.BatchV1Api.return_value = mock_batch
        mock_client.V1EnvVar = MagicMock(side_effect=lambda **kw: kw)
        mock_client.V1EnvVarSource = MagicMock(side_effect=lambda **kw: kw)
        mock_client.V1SecretKeySelector = MagicMock(side_effect=lambda **kw: kw)
        mock_client.V1Container = MagicMock(side_effect=lambda **kw: kw)
        mock_client.V1PodSpec = MagicMock(side_effect=lambda **kw: kw)
        mock_client.V1ObjectMeta = MagicMock(side_effect=lambda **kw: kw)
        mock_client.V1PodTemplateSpec = MagicMock(side_effect=lambda **kw: kw)
        mock_client.V1JobSpec = MagicMock(side_effect=lambda **kw: kw)
        mock_client.V1Job = MagicMock(side_effect=lambda **kw: kw)

        kc = KubeClient()
        kc._batch = mock_batch
        yield kc, mock_batch


def test_spawn_agent_job_returns_job_name(kube_client):
    kc, mock_batch = kube_client
    task_spec = _make_task_spec()
    job_name = kc.spawn_agent_job(task_spec)
    assert job_name.startswith("pi-agent-review-mr-")
    assert len(job_name) > len("pi-agent-review-mr-")


def test_spawn_agent_job_calls_create(kube_client):
    kc, mock_batch = kube_client
    task_spec = _make_task_spec()
    kc.spawn_agent_job(task_spec)
    mock_batch.create_namespaced_job.assert_called_once()


def test_spawn_agent_job_unique_names(kube_client):
    kc, mock_batch = kube_client
    task_spec = _make_task_spec()
    name1 = kc.spawn_agent_job(task_spec)
    name2 = kc.spawn_agent_job(task_spec)
    assert name1 != name2
