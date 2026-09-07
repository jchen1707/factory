"""Reviewable migration and explicit operator settings. No tracker writes."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from factory import authority, operator_controls
from factory.machine import Blocked, State
from factory.store import Store


def migrate(args: argparse.Namespace) -> int:
    from factory.runtime_state import SCHEMA

    if not args.apply:
        print("Schema 4 -> 5; existing run and effects rows are preserved.")
        print("\n".join(statement + ";" for statement in SCHEMA))
        print(
            "Review before applying with --apply. Stop factory writers and back up the database first."
        )
        return 0
    # The preview above authorizes only this migration, not older table rebuilds.
    if not args.database.is_file():
        raise Blocked("migration-database-missing", str(args.database))
    connection = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    if version not in {4, 5}:
        raise Blocked(
            "migration-review-required",
            f"Schema {version} needs its own reviewed upgrade before 4 -> 5",
        )
    store = Store(args.database, migrate=True)
    try:
        print(store.integrity_ok()[1])
    finally:
        store.close()
    return 0


def configure(args: argparse.Namespace) -> int:
    from factory.cli import _context_for, factory_home
    from factory.intake.linear import LinearClient
    from factory.registry import load_registry
    from factory.routing import load_routing

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
            if run is None or run.state not in {
                State.SUSPENDED,
                State.BLOCKED,
                State.AWAITING_HUMAN,
            }:
                raise Blocked(
                    "policy-replacement-needs-paused-run",
                    "Suspend the run before replacing its policy",
                )
            if not store.acquire_lease(run.id, ttl_seconds=300):
                raise Blocked("run-leased", "The run is owned by another process")
            try:
                ctx = _context_for(
                    home,
                    load_registry(home / "config/projects.toml"),
                    load_routing(home / "config/models.toml"),
                    store,
                    LinearClient(),
                    run,
                )
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
            )
            if getattr(args, key) is not None
        }
        if changes.get("delivery_profile") == "inherit":
            changes["delivery_profile"] = None
        if args.concurrency is not None:
            changes["concurrency"] = (
                None if args.concurrency == "inherit" else int(args.concurrency)
            )
        if changes:
            operator_controls.configure(
                store,
                "run" if run else "project",
                owner,
                changes,
                registry=load_registry(home / "config/projects.toml"),
            )
        print(json.dumps(store.runtime.settings("run" if run else "project", owner), indent=2))
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
    settings.add_argument("--isolation", choices=("shared", "per-run"))
    settings.add_argument("--isolation-measurement", help="retained isolation manifest path")
    settings.add_argument("--concurrency", help="positive count or inherit")
    settings.add_argument(
        "--approve-attempt", dest="approve", help="one invocation key, such as 2:implement:1"
    )
    settings.set_defaults(func=configure)
