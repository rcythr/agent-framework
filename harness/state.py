"""
HarnessState — lightweight JSON persistence for cluster lifecycle.

Tracks which KIND clusters are currently managed, their kubeconfigs,
and the credentials associated with each phase. This lets the harness
resume after a restart without reprovisioning.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger("harness.state")


class HarnessState:
    def __init__(self, state_file: Path) -> None:
        self._path = state_file
        self._data: dict = self._load()

    # ── Cluster CRUD ──────────────────────────────────────────────────────────

    def save_cluster(
        self,
        phase_id: str,
        cluster_name: str,
        kubeconfig_path: str,
        credentials: dict,
    ) -> None:
        self._data.setdefault("clusters", {})[phase_id] = {
            "phase_id": phase_id,
            "cluster_name": cluster_name,
            "kubeconfig_path": kubeconfig_path,
            "status": "ready",
            "credentials": credentials,
        }
        self._save()

    def get_cluster(self, phase_id: str) -> dict | None:
        return self._data.get("clusters", {}).get(phase_id)

    def remove_cluster(self, phase_id: str) -> None:
        self._data.get("clusters", {}).pop(phase_id, None)
        self._save()

    def list_clusters(self) -> list[dict]:
        return list(self._data.get("clusters", {}).values())

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                log.warning("Could not read state file %s: %s — starting fresh", self._path, e)
        return {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))
      
