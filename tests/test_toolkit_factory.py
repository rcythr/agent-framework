import os
import pytest
from unittest.mock import patch, MagicMock


def test_get_toolkit_returns_gitlab_toolkit_when_provider_gitlab():
    from providers.gitlab.toolkit import GitLabToolkit

    mock_provider = MagicMock()

    with patch.dict(os.environ, {"PROVIDER": "gitlab"}), \
         patch("worker.tools.toolkit_factory.get_provider", return_value=mock_provider):
        from worker.tools.toolkit_factory import get_toolkit
        toolkit = get_toolkit(project_id=1)

    assert isinstance(toolkit, GitLabToolkit)


def test_get_toolkit_raises_for_unknown_provider():
    mock_provider = MagicMock()

    with patch.dict(os.environ, {"PROVIDER": "unknown"}), \
         patch("worker.tools.toolkit_factory.get_provider", return_value=mock_provider):
        from worker.tools.toolkit_factory import get_toolkit
        with pytest.raises(ValueError, match="No toolkit for provider"):
            get_toolkit(project_id=1)
