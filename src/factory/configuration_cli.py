"""Reviewable migration and explicit operator settings. No tracker writes."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from factory import authority, operator_controls
from factory.machine import Blocked
from factory.store import Store


def migrate(args: argparse.Namespace) -> int:
    from factory.store import SCHEMA_VERSION, migration_statements

    if args.database.is_file():
        connection = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
    elif args.apply:
        raise Blocked("migration-database-missing", str(args.database))
    else:
        version = SCHEMA_VERSION - 1
    statements = migration_statements(version)
    if not args.apply:
        print(f"Schema {version} -> {SCHEMA_VERSION}; existing run and effects rows are preserved.")
        print("\n".join(statement + ";" for statement in statements))
        print(
            "Review before applying with --apply. Stop factory writers and back up the database first."
        )
        return 0
    store = Store(args.database, migrate=True)
    try:
        print(store.integrity_ok()[1])
    finally:
        store.close()
    return 0


def configure(args: argparse.Namespace) -> int:
    from factory.cli import factory_home
    from factory.harness import load_harness_config
    from factory.isolation import project_for_run
    from factory.registry import load_registry

    home = factory_home()
    store = Store(home / "state" / "factory.db")
    try:
        run = store.run_by_ticket(args.ticket.upper()) if args.ticket else None
        if args.ticket and run is None:
            raise Blocked("run-not-found", args.ticket)
        owner = run.id if run else args.project
        if args.approve:
            if run is None:
                raise Blocked("approval-needs-run", "Pass --ticket")
            store.runtime.approve(run.id, args.approve)
        if args.replace_policy:
            refusal = operator_controls.policy_replacement_refusal(run)
            if refusal or run is None:
                raise Blocked("policy-replacement-needs-paused-run", refusal or "Run not found")
            if not store.acquire_lease(run.id, ttl_seconds=300):
                raise Blocked("run-leased", "The run is owned by another process")
            try:
                registry = load_registry(home / "config/projects.toml")
                project = project_for_run(registry.resolve(run.linear_id), run, store)
                load_harness_config(project.path)
                ctx = authority.SnapshotContext(home, project, run, store)
                print(
                    json.dumps(
                        authority.snapshot(ctx, profile=args.replace_policy, replace=True), indent=2
                    )
                )
            finally:
                store.release_lease(run.id)
        changes = {
            key: getattr(args, key)
            for key in (
                "mode",
                "model_preset",
                "delivery_profile",
                "workflow",
                "isolation",
                "isolation_measurement",
                "agent_adapter",
                "app_server_compatibility",
                "certification_mode",
                "certification_config",
                "delegation_mode",
            )
            if getattr(args, key, None) is not None
        }
        if changes.get("delivery_profile") == "inherit":
            changes["delivery_profile"] = None
        if args.concurrency is not None:
            changes["concurrency"] = (
                None if args.concurrency == "inherit" else int(args.concurrency)
            )
        if changes.get("delegation_mode") == "inherit":
            changes["delegation_mode"] = None
        for key in ("max_active_agents", "max_children_per_parent", "max_delegation_depth"):
            value = getattr(args, key, None)
            if value is not None:
                changes[key] = None if value == "inherit" else int(value)
        if changes:
            operator_controls.configure(
                store,
                "run" if run else "project",
                owner,
                changes,
                registry=load_registry(home / "config/projects.toml"),
            )
        print(
            json.dumps(
                {
                    "settings": store.runtime.settings("run" if run else "project", owner),
                    "effective": store.runtime.effective(run.project, run.id)
                    if run
                    else store.runtime.settings("project", owner),
                    "runtime": operator_controls.status(
                        store, run.project if run else owner, run.id if run else None
                    ),
                },
                indent=2,
            )
        )
    finally:
        store.close()
    return 0


def retry_certification(args: argparse.Namespace) -> int:
    """Queue one explicitly named retry after fresh observation; do not resume or approve."""
    from dataclasses import asdict
    from uuid import uuid4

    from factory import cli, workflow_certification
    from factory.intake.linear import LinearClient
    from factory.registry import load_registry
    from factory.routing import load_routing

    home = cli.factory_home()
    store = Store(home / "state/factory.db")
    try:
        run = store.run_by_ticket(args.ticket.upper())
        if run is None:
            raise Blocked("run-not-found", args.ticket)
        lease_owner = "certification-retry:" + uuid4().hex
        if not store.acquire_lease(run.id, ttl_seconds=300, owner=lease_owner):
            raise Blocked("run-leased", run.id)
        try:
            ctx = cli._context_for(
                home,
                load_registry(home / "config/projects.toml"),
                load_routing(home / "config/models.toml"),
                store,
                LinearClient(),
                run,
            )
            runner, _ = workflow_certification.service(
                ctx,
                review=args.role == "review",
                prepare_runtime=False,
            )
            # Reconcile retained execution before accepting a fresh attempt. Unknown
            # or active launch reservations still block retry at the transaction seam.
            previous = runner.status(args.job)
            if previous["run_id"] != run.id:
                raise Blocked("certification-retry-owner", args.job)
            runner.collect(args.job)
            current = runner.observe()
            runner.driver.preflight(current)
            with store.runtime.transaction():
                if not store.holds_lease(run.id, owner=lease_owner):
                    raise Blocked("run-lease-lost", run.id)
                result = runner.certifications.jobs.retry_certification(
                    run.id,
                    args.job,
                    asdict(current),
                    reason=args.reason,
                )
            print(
                json.dumps(
                    {
                        "job_id": result["id"],
                        "status": result["status"],
                        "retry_of": result["retry_of"],
                    }
                )
            )
        finally:
            with store.runtime.transaction():
                if store.holds_lease(run.id, owner=lease_owner):
                    store.release_lease(run.id)
    finally:
        store.close()
    return 0


def metrics(args: argparse.Namespace) -> int:
    from factory.evaluation import summarize

    store = Store(args.database)
    try:
        print(json.dumps(summarize(store, args.project), indent=2))
    finally:
        store.close()
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    retry = sub.add_parser(
        "retry-certification", help="request one explicit failed-certification retry; no launch"
    )
    retry.add_argument("--ticket", required=True)
    retry.add_argument("--job", required=True, help="exact failed certification ID")
    retry.add_argument("--role", choices=("build", "review"), required=True)
    retry.add_argument("--reason", required=True)
    retry.set_defaults(func=retry_certification)
    evaluation = sub.add_parser(
        "metrics", help="summarize retained workflow outcomes and estimated cost"
    )
    evaluation.add_argument("--database", type=Path, required=True)
    evaluation.add_argument("--project")
    evaluation.set_defaults(func=metrics)
    migration = sub.add_parser(
        "migrate", help="review or explicitly apply the runtime schema migration"
    )
    migration.add_argument("--database", type=Path, required=True)
    migration.add_argument("--apply", action="store_true")
    migration.set_defaults(func=migrate)
    settings = sub.add_parser("configure", help="configure a project or a run")
    scope = settings.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project")
    scope.add_argument("--ticket")
    settings.add_argument("--mode", choices=("automatic", "approval"))
    settings.add_argument("--model-preset", choices=("existing", "volume", "high-confidence"))
    settings.add_argument(
        "--delivery-profile", choices=("inherit", "prototype", "core", "hardening")
    )
    settings.add_argument("--replace-policy", choices=("prototype", "core", "hardening"))
    settings.add_argument("--workflow", choices=("existing", "diagnosis"))
    settings.add_argument("--agent-adapter", choices=("codex-exec", "app-server"))
    settings.add_argument(
        "--app-server-compatibility", help="directory of sandbox compatibility manifests"
    )
    settings.add_argument(
        "--delegation-mode", choices=("inherit", "disabled", "read-only", "isolated-write")
    )
    for option in ("--max-active-agents", "--max-children-per-parent", "--max-delegation-depth"):
        settings.add_argument(
            option, help="positive count or inherit; run values cannot exceed project limits"
        )
    settings.add_argument("--certification-mode", choices=("manual", "automatic"))
    settings.add_argument(
        "--certification-config", help="host-owned certification configuration JSON"
    )
    settings.add_argument("--isolation", choices=("shared", "per-run"))
    settings.add_argument("--isolation-measurement", help="retained isolation manifest path")
    settings.add_argument("--concurrency", help="positive count or inherit")
    settings.add_argument(
        "--approve-attempt", dest="approve", help="one invocation key, such as 2:implement:1"
    )
    settings.set_defaults(func=configure)
