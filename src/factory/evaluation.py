"""Read-only workflow outcomes. Unknown historical costs remain explicitly incomplete."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from factory.machine import State
from factory.store import Store


def summarize(store: Store, project: str | None = None) -> dict[str, Any]:
    runs = [run for run in store.all_runs() if project is None or run.project == project]
    completed = [run for run in runs if run.state is State.COMPLETED]
    interventions = episodes = repeated = 0
    for run in runs:
        interventions += store.runtime.db.execute(
            "SELECT COUNT(*) FROM transitions WHERE run_id=? AND actor='human'", (run.id,)
        ).fetchone()[0]
        interventions += store.runtime.db.execute(
            "SELECT COUNT(*) FROM operator_events WHERE scope='run' AND owner=? "
            "AND action IN ('approve-attempt','policy-replaced')",
            (run.id,),
        ).fetchone()[0]
        failures: Counter[str] = Counter()
        for check in store.checks(run.id):
            if check["check_name"] == "failure-reproduction" and check["detail"]:
                provenance = json.loads(check["detail"])
                if provenance.get("episode"):
                    failures[provenance["episode"]] += 1
        repairs = store.runtime.db.execute(
            "SELECT fingerprint FROM failure_episodes WHERE run_id=?", (run.id,)
        ).fetchall()
        episodes += len(set(failures) | {row["fingerprint"] for row in repairs})
        repeated += sum(count > 1 for count in failures.values())
    total = sum(store.known_spend(run.id) for run in runs)
    incomplete = sum(
        not store.costs(run.id) or any(row["usd"] is None for row in store.costs(run.id))
        for run in runs
    )
    return {
        "project": project,
        "runs": len(runs),
        "accepted_changes": len(completed),
        "completion_rate": len(completed) / len(runs) if runs else None,
        "human_interventions": interventions,
        "failure_episodes": episodes,
        "repeated_failure_episodes": repeated,
        "api_equivalent_estimated_usd": total,
        "cost_incomplete_runs": incomplete,
        "cost_complete": incomplete == 0 and bool(runs),
        "estimated_usd_per_accepted_change": total / len(completed) if completed else None,
        "accepted_definition": "Runs explicitly recorded completed after merge",
        "intervention_definition": "Human transitions plus attempt approvals and policy replacements",
        "historical_limit": "Counts include only retained events; unrecorded interventions and usage are unknown",
    }
