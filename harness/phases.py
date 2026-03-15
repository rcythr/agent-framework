"""
Phase registry.

Each PhaseSpec declares whether the phase needs a KIND cluster, whether it
needs GitLab seeded inside that cluster, and which prior phase's cluster
it should inherit (for phases that share an environment rather than getting
their own).
"""

from dataclasses import dataclass, field


@dataclass
class PhaseSpec:
    phase_id: str
    title: str
    task_file: str                      # filename inside tasks/
    needs_cluster: bool = False         # does this phase need a KIND cluster?
    needs_gitlab: bool = False          # does the cluster need GitLab CE seeded?
    inherit_cluster_from: str | None = None  # reuse another phase's cluster instead of creating one
    # Phases that must have their clusters ready before this one's cluster is created
    cluster_depends_on: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# Phase 0 is pure Python — no cluster, no GitLab.
#
# Phases 1–7 all need a cluster.  Phase 1 owns the canonical cluster
# (pi-agent-phase-1) which includes GitLab CE.  Subsequent phases that
# need the same running environment (2, 3, 4, 5) get their own isolated
# clusters so they can proceed in parallel without interfering.  Phases
# that are strictly sequential and share a well-known state can inherit
# instead.
#
# The harness names clusters: pi-agent-phase-{phase_id}
# ---------------------------------------------------------------------------

PHASE_REGISTRY: dict[str, PhaseSpec] = {
    # ── Wave 1 ───────────────────────────────────────────────────────────────
    "0": PhaseSpec(
        phase_id="0",
        title="Provider Abstraction Layer",
        task_file="phase-0-provider-abstraction.md",
        needs_cluster=False,
        needs_gitlab=False,
    ),

    # ── Wave 2 ───────────────────────────────────────────────────────────────
    # Phase 1 is the reference cluster; it sets up GitLab and the full infra.
    "1": PhaseSpec(
        phase_id="1",
        title="Infrastructure Foundation",
        task_file="phase-1-infrastructure.md",
        needs_cluster=True,
        needs_gitlab=True,
    ),
    # Phases 2–5 each get their own cluster so they can run fully in parallel.
    # They all include GitLab so E2E tests work without depending on phase 1's cluster.
    "2": PhaseSpec(
        phase_id="2",
        title="Core Agent Worker",
        task_file="phase-2-agent-worker.md",
        needs_cluster=True,
        needs_gitlab=True,
    ),
    "3": PhaseSpec(
        phase_id="3",
        title="Structured Logging and Observability",
        task_file="phase-3-structured-logging.md",
        needs_cluster=True,
        needs_gitlab=True,
    ),
    "4": PhaseSpec(
        phase_id="4",
        title="Per-Project Configuration",
        task_file="phase-4-project-configuration.md",
        needs_cluster=True,
        needs_gitlab=True,
    ),
    "5": PhaseSpec(
        phase_id="5",
        title="Authentication",
        task_file="phase-5-authentication.md",
        needs_cluster=True,
        needs_gitlab=True,
    ),

    # ── Wave 3 ───────────────────────────────────────────────────────────────
    "6": PhaseSpec(
        phase_id="6",
        title="Control Plane Dashboard",
        task_file="phase-6-dashboard.md",
        needs_cluster=True,
        needs_gitlab=True,
    ),

    # ── Wave 4 — Phase 7 sub-tasks ────────────────────────────────────────────
    # 7a–7c are sequential and share a cluster; 7d and 7e get their own
    # clusters so they can run in parallel once 7c merges.
    "7a": PhaseSpec(
        phase_id="7a",
        title="Interactive Sessions — Data Layer and Broker",
        task_file="phase-7-interactive-sessions.md",
        needs_cluster=True,
        needs_gitlab=True,
    ),
    "7b": PhaseSpec(
        phase_id="7b",
        title="Interactive Sessions — Worker Session Mode",
        task_file="phase-7-interactive-sessions.md",
        needs_cluster=True,
        needs_gitlab=True,
        cluster_depends_on=["7a"],
    ),
    "7c": PhaseSpec(
        phase_id="7c",
        title="Interactive Sessions — Session Messaging",
        task_file="phase-7-interactive-sessions.md",
        needs_cluster=True,
        needs_gitlab=True,
        cluster_depends_on=["7b"],
    ),
    "7d": PhaseSpec(
        phase_id="7d",
        title="Interactive Sessions — Launcher UI",
        task_file="phase-7-interactive-sessions.md",
        needs_cluster=True,
        needs_gitlab=True,
        cluster_depends_on=["7c"],
    ),
    "7e": PhaseSpec(
        phase_id="7e",
        title="Interactive Sessions — Workspace UI",
        task_file="phase-7-interactive-sessions.md",
        needs_cluster=True,
        needs_gitlab=True,
        cluster_depends_on=["7c"],
    ),

    # ── Wave 5 (deferred) ────────────────────────────────────────────────────
    "8": PhaseSpec(
        phase_id="8",
        title="Additional Auth and Repository Providers",
        task_file="phase-8-additional-providers.md",
        needs_cluster=True,
        needs_gitlab=True,
    ),
}
