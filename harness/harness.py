#!/usr/bin/env python3
"""
pi-agent implementation harness.

Manages KIND clusters, composes agent prompts, and dispatches implementation
tasks to coding agents. Each agent gets its own isolated KIND cluster with
credentials injected — agents never run docker or kind themselves.

Usage:
    python harness.py run --phase 0
    python harness.py run --phase 1
    python harness.py run --phase 2 --phase 3   # parallel phases
    python harness.py status
    python harness.py teardown --phase 1
    python harness.py teardown --all
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from cluster import ClusterManager
from phases import PHASE_REGISTRY, PhaseSpec
from prompt import PromptComposer
from state import HarnessState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("harness")

TASKS_DIR = Path(__file__).parent.parent / "tasks"
STATE_FILE = Path(__file__).parent / ".harness-state.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="pi-agent implementation harness")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Provision cluster(s) and emit agent prompt(s)")
    run_p.add_argument(
        "--phase",
        type=str,
        action="append",
        dest="phases",
        required=True,
        metavar="PHASE",
        help="Phase to run (e.g. 0, 1, 2, 7a). Repeat for parallel phases.",
    )
    run_p.add_argument(
        "--repo-path",
        type=Path,
        default=Path.cwd(),
        help="Path to the pi-agent source repo the agent will work in.",
    )
    run_p.add_argument(
        "--output",
        choices=["print", "file", "json"],
        default="print",
        help="How to emit the composed prompt.",
    )
    run_p.add_argument(
        "--no-cluster",
        action="store_true",
        help="Skip cluster provisioning (for phases that need no cluster, e.g. phase 0).",
    )

    status_p = sub.add_parser("status", help="Show state of all managed clusters")

    teardown_p = sub.add_parser("teardown", help="Tear down cluster(s)")
    teardown_group = teardown_p.add_mutually_exclusive_group(required=True)
    teardown_group.add_argument("--phase", type=str, metavar="PHASE")
    teardown_group.add_argument("--all", action="store_true")

    return parser.parse_args()


async def cmd_run(args: argparse.Namespace, state: HarnessState) -> None:
    phases: list[str] = args.phases
    repo_path: Path = args.repo_path.resolve()

    # Validate all phases before doing any work
    for phase_id in phases:
        if phase_id not in PHASE_REGISTRY:
            log.error("Unknown phase %r. Known phases: %s", phase_id, sorted(PHASE_REGISTRY))
            sys.exit(1)

    cluster_mgr = ClusterManager(state=state)
    composer = PromptComposer(tasks_dir=TASKS_DIR, repo_path=repo_path)

    results = []

    if len(phases) == 1:
        result = await _run_single_phase(
            phase_id=phases[0],
            cluster_mgr=cluster_mgr,
            composer=composer,
            state=state,
            skip_cluster=args.no_cluster,
        )
        results.append(result)
    else:
        # Parallel phases — provision clusters concurrently then emit prompts
        log.info("Running %d phases in parallel: %s", len(phases), phases)
        tasks = [
            _run_single_phase(
                phase_id=p,
                cluster_mgr=cluster_mgr,
                composer=composer,
                state=state,
                skip_cluster=args.no_cluster,
            )
            for p in phases
        ]
        results = await asyncio.gather(*tasks)

    for result in results:
        if args.output == "print":
            print("\n" + "=" * 80)
            print(f"AGENT PROMPT — Phase {result['phase_id']}")
            print("=" * 80 + "\n")
            print(result["prompt"])
        elif args.output == "file":
            out_path = Path(f"prompt-phase-{result['phase_id']}.txt")
            out_path.write_text(result["prompt"])
            log.info("Prompt written to %s", out_path)
        elif args.output == "json":
            print(json.dumps(result, indent=2))


async def _run_single_phase(
    phase_id: str,
    cluster_mgr: ClusterManager,
    composer: PromptComposer,
    state: HarnessState,
    skip_cluster: bool,
) -> dict:
    spec: PhaseSpec = PHASE_REGISTRY[phase_id]
    log.info("Phase %s: %s", phase_id, spec.title)

    credentials = {}

    if spec.needs_cluster and not skip_cluster:
        log.info("Phase %s: provisioning KIND cluster...", phase_id)
        credentials = await cluster_mgr.provision(phase_id=phase_id, spec=spec)
        log.info("Phase %s: cluster ready", phase_id)
    else:
        log.info("Phase %s: no cluster needed", phase_id)

    prompt = composer.compose(phase_id=phase_id, spec=spec, credentials=credentials)

    return {
        "phase_id": phase_id,
        "prompt": prompt,
        "credentials": credentials,
    }


async def cmd_status(state: HarnessState) -> None:
    clusters = state.list_clusters()
    if not clusters:
        print("No clusters currently managed by harness.")
        return

    print(f"{'Phase':<10} {'Cluster name':<30} {'Status':<15} {'GitLab URL'}")
    print("-" * 80)
    for c in clusters:
        print(
            f"{c['phase_id']:<10} {c['cluster_name']:<30} "
            f"{c['status']:<15} {c.get('gitlab_url', 'n/a')}"
        )


async def cmd_teardown(args: argparse.Namespace, state: HarnessState) -> None:
    cluster_mgr = ClusterManager(state=state)
    if args.all:
        clusters = state.list_clusters()
        if not clusters:
            log.info("No clusters to tear down.")
            return
        for c in clusters:
            log.info("Tearing down cluster for phase %s...", c["phase_id"])
            await cluster_mgr.teardown(c["phase_id"])
        log.info("All clusters torn down.")
    else:
        log.info("Tearing down cluster for phase %s...", args.phase)
        await cluster_mgr.teardown(args.phase)
        log.info("Done.")


async def main() -> None:
    args = parse_args()
    state = HarnessState(STATE_FILE)

    if args.command == "run":
        await cmd_run(args, state)
    elif args.command == "status":
        await cmd_status(state)
    elif args.command == "teardown":
        await cmd_teardown(args, state)


if __name__ == "__main__":
    asyncio.run(main())
  
