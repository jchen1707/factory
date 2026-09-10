"""Audited carry-forward of one preserved Git candidate into one fresh run.

The external bundle is only accepted while its source run and untouched worktree can
still be compared. Retention binds the checked object to that source run. A fresh named
run may then copy the retained object only after the source run is terminal, and the
worktree step applies its exact base-to-candidate tree without executing repository code.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from factory import repo
from factory.harness import HarnessConfig
from factory.machine import Blocked, State
from factory.registry import Project
from factory.steps import Context
from factory.store import Run, Store

MAX_MANIFEST_BYTES = 64 * 1024
MAX_BUNDLE_BYTES = 128 * 1024 * 1024
MAX_PATCH_BYTES = 64 * 1024 * 1024
MAX_CHANGED_PATHS = 10_000
_SHA_LENGTH = 40
_EXTERNAL_KEYS = {
    "schema_version",
    "ticket",
    "source_run_id",
    "source_head",
    "base_commit",
    "candidate_commit",
    "candidate_tree",
    "bundle_file",
    "bundle_sha256",
    "diff_sha256",
}
_RETAINED_DIR = Path("handoffs") / "candidate"


@dataclass(frozen=True)
class Candidate:
    ticket: str
    source_run_id: str
    source_head: str
    base_commit: str
    candidate_commit: str
    candidate_tree: str
    bundle_path: Path
    bundle_sha256: str
    diff_sha256: str
    changed_paths: tuple[str, ...]

    def retained_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "ticket": self.ticket,
            "source_run_id": self.source_run_id,
            "source_head": self.source_head,
            "base_commit": self.base_commit,
            "candidate_commit": self.candidate_commit,
            "candidate_tree": self.candidate_tree,
            "bundle_file": "candidate.bundle",
            "bundle_sha256": self.bundle_sha256,
            "diff_sha256": self.diff_sha256,
            "changed_paths": list(self.changed_paths),
        }


def check_external(
    store: Store,
    project: Project,
    harness: HarnessConfig,
    ticket: str,
    manifest_path: Path,
) -> Candidate:
    """Validate a bundle against its still-preserved source run; write nothing."""
    if project.requires_clone:
        raise Blocked(
            "candidate-clone-project-unsupported",
            "candidate carry-forward currently supports host-worktree projects only",
        )
    payload = _read_json(manifest_path, expected_keys=_EXTERNAL_KEYS)
    bundle_name = _string(payload, "bundle_file")
    if Path(bundle_name).name != bundle_name:
        raise Blocked("candidate-manifest-invalid", "bundle_file must be one filename")
    bundle_path = manifest_path.parent / bundle_name
    candidate = _candidate_from_payload(payload, bundle_path)
    source = _source_run(store, candidate, ticket, project, terminal=False)
    if source.state not in (State.BLOCKED, State.SUSPENDED):
        raise Blocked(
            "candidate-source-not-preserved",
            f"source run {source.id} is {source.state}; expected blocked or suspended",
        )
    if not source.worktree:
        raise Blocked("candidate-source-not-preserved", "source run has no recorded worktree")
    source_path = Path(source.worktree)
    if not source_path.is_dir() or repo.head_sha(source_path) != candidate.source_head:
        raise Blocked(
            "candidate-source-mismatch",
            "source worktree HEAD does not match the manifest source_head",
        )
    return _validate_git(candidate, project, harness)


def retain(
    store: Store,
    project: Project,
    harness: HarnessConfig,
    ticket: str,
    manifest_path: Path,
    state_dir: Path,
) -> Candidate:
    """Retain an externally preserved candidate under its source run."""
    candidate = check_external(store, project, harness, ticket, manifest_path)
    destination = state_dir / _RETAINED_DIR
    created = _write_retained(destination, candidate)
    if created:
        store.record_check(
            candidate.source_run_id,
            _source_run(store, candidate, ticket, project, terminal=False).attempt,
            "candidate-handoff",
            "pass",
            detail=(
                f"retained {candidate.candidate_commit} from {candidate.source_head}; "
                f"{len(candidate.changed_paths)} changed paths"
            ),
            artifact=str((destination / "manifest.json").relative_to(state_dir)),
        )
    return candidate


def validate_retained_source(
    store: Store,
    project: Project,
    harness: HarnessConfig,
    ticket: str,
    source_run_id: str,
    source_state_dir: Path,
) -> Candidate:
    """Validate retained evidence for a new run; the source must now be cancelled."""
    if project.requires_clone:
        raise Blocked(
            "candidate-clone-project-unsupported",
            "candidate carry-forward currently supports host-worktree projects only",
        )
    candidate = _read_retained(source_state_dir / _RETAINED_DIR)
    if candidate.source_run_id != source_run_id:
        raise Blocked("candidate-source-mismatch", "retained source run id does not match")
    _source_run(store, candidate, ticket, project, terminal=True)
    return _validate_git(candidate, project, harness)


def copy_to_new_run(candidate: Candidate, new_state_dir: Path) -> None:
    """Copy already validated evidence into a not-yet-visible new run directory."""
    _write_retained(new_state_dir / _RETAINED_DIR, candidate)


def restore(ctx: Context, worktree: Path) -> Candidate | None:
    """Apply a retained candidate exactly once to a fresh worktree."""
    retained = ctx.state_dir / _RETAINED_DIR
    if not retained.is_dir():
        return None
    if ctx.project.requires_clone:
        raise Blocked(
            "candidate-clone-project-unsupported",
            "candidate carry-forward currently supports host-worktree projects only",
        )
    if not repo.is_clean(worktree):
        raise Blocked("candidate-worktree-not-clean", f"{worktree} is not clean before restore")
    candidate = _read_retained(retained)
    if ctx.harness is None:
        raise Blocked("candidate-harness-unavailable", "target harness is not loaded")
    candidate = _validate_git(candidate, ctx.project, ctx.harness)
    if repo.resolve_ref(worktree, "HEAD") != candidate.base_commit:
        raise Blocked(
            "candidate-base-mismatch",
            "fresh worktree HEAD no longer matches the retained candidate base",
        )
    with tempfile.TemporaryDirectory(prefix="factory-candidate-restore-") as raw:
        materialized = Path(raw) / "repo"
        repo.clone_bundle(candidate.bundle_path, materialized)
        patch = repo.binary_diff(materialized, candidate.base_commit, candidate.candidate_commit)
    if len(patch.encode()) > MAX_PATCH_BYTES:
        raise Blocked(
            "candidate-patch-too-large", f"candidate patch exceeds {MAX_PATCH_BYTES} bytes"
        )
    if hashlib.sha256(patch.encode()).hexdigest() != candidate.diff_sha256:
        raise Blocked("candidate-digest-mismatch", "retained candidate diff digest changed")
    repo.apply_indexed_patch(worktree, patch)
    actual_tree = repo.index_tree(worktree)
    if actual_tree != candidate.candidate_tree:
        raise Blocked(
            "candidate-restore-mismatch",
            f"restored index tree {actual_tree} != retained tree {candidate.candidate_tree}",
        )
    receipt = {
        "schema_version": 1,
        "source_run_id": candidate.source_run_id,
        "base_commit": candidate.base_commit,
        "candidate_commit": candidate.candidate_commit,
        "candidate_tree": candidate.candidate_tree,
        "bundle_sha256": candidate.bundle_sha256,
        "diff_sha256": candidate.diff_sha256,
        "changed_paths": list(candidate.changed_paths),
        "restored_index_tree": actual_tree,
    }
    _atomic_write_json(retained / "restored.json", receipt)
    ctx.log(
        "candidate.restored",
        source_run_id=candidate.source_run_id,
        candidate_commit=candidate.candidate_commit,
        candidate_tree=candidate.candidate_tree,
        paths=len(candidate.changed_paths),
    )
    return candidate


def prompt_section(ctx: Context) -> list[str]:
    """Render the verified carry-forward identity into the implementation prompt."""
    receipt_path = ctx.state_dir / _RETAINED_DIR / "restored.json"
    if not receipt_path.is_file():
        return []
    payload = _read_json(receipt_path)
    paths = [str(path) for path in payload.get("changed_paths", [])]
    return [
        "",
        "## Preserved candidate carried forward",
        "",
        f"Factory restored the audited candidate from run `{payload['source_run_id']}` onto",
        f"the fresh base `{payload['base_commit']}`. Its preserved commit is",
        f"`{payload['candidate_commit']}` and its verified index tree is",
        f"`{payload['restored_index_tree']}`.",
        "",
        "The restored changes are staged in this worktree. Inspect and continue them; do not",
        "discard them or restart from nothing merely because this is a fresh run.",
        "Changed paths: " + ", ".join(f"`{path}`" for path in paths),
    ]


def _candidate_from_payload(payload: dict[str, Any], bundle_path: Path) -> Candidate:
    if payload.get("schema_version") != 1:
        raise Blocked("candidate-manifest-invalid", "schema_version must be 1")
    return Candidate(
        ticket=_string(payload, "ticket").upper(),
        source_run_id=_string(payload, "source_run_id"),
        source_head=_sha(payload, "source_head"),
        base_commit=_sha(payload, "base_commit"),
        candidate_commit=_sha(payload, "candidate_commit"),
        candidate_tree=_sha(payload, "candidate_tree"),
        bundle_path=bundle_path,
        bundle_sha256=_digest(payload, "bundle_sha256"),
        diff_sha256=_digest(payload, "diff_sha256"),
        changed_paths=tuple(str(path) for path in payload.get("changed_paths", [])),
    )


def _validate_git(candidate: Candidate, project: Project, harness: HarnessConfig) -> Candidate:
    bundle = candidate.bundle_path
    try:
        stat = bundle.lstat()
    except OSError as exc:
        raise Blocked("candidate-bundle-unavailable", f"{bundle}: {exc}") from exc
    if bundle.is_symlink() or not bundle.is_file():
        raise Blocked("candidate-bundle-invalid", f"{bundle} must be a regular, non-symlink file")
    if stat.st_size > MAX_BUNDLE_BYTES:
        raise Blocked("candidate-bundle-too-large", f"bundle exceeds {MAX_BUNDLE_BYTES} bytes")
    if _sha256_file(bundle) != candidate.bundle_sha256:
        raise Blocked("candidate-digest-mismatch", "bundle SHA-256 does not match the manifest")
    current_base = repo.resolve_ref(project.path, project.base_ref)
    if current_base != candidate.base_commit:
        raise Blocked(
            "candidate-base-mismatch",
            f"current {project.base_ref} is {current_base}, expected {candidate.base_commit}",
        )
    with tempfile.TemporaryDirectory(prefix="factory-candidate-check-") as raw:
        materialized = Path(raw) / "repo"
        repo.clone_bundle(bundle, materialized)
        if repo.resolve_ref(materialized, "HEAD") != candidate.candidate_commit:
            raise Blocked("candidate-commit-mismatch", "bundle HEAD is not candidate_commit")
        if (
            repo.resolve_ref(materialized, f"{candidate.candidate_commit}^")
            != candidate.source_head
        ):
            raise Blocked(
                "candidate-source-mismatch",
                "candidate commit must have source_head as its first parent",
            )
        if not repo.is_ancestor(materialized, candidate.base_commit, candidate.source_head):
            raise Blocked(
                "candidate-base-mismatch", "base_commit is not an ancestor of source_head"
            )
        tree = repo.head_sha(materialized, f"{candidate.candidate_commit}^{{tree}}")
        if tree != candidate.candidate_tree:
            raise Blocked("candidate-tree-mismatch", "candidate tree does not match the manifest")
        changed = repo.changed_paths_between(
            materialized, candidate.base_commit, candidate.candidate_commit
        )
        if not changed:
            raise Blocked("candidate-empty", "candidate contains no changes from base")
        if len(changed) > MAX_CHANGED_PATHS:
            raise Blocked("candidate-too-many-paths", f"candidate changes {len(changed)} paths")
        _validate_paths(materialized, candidate.candidate_commit, changed, harness)
        patch = repo.binary_diff(materialized, candidate.base_commit, candidate.candidate_commit)
    if len(patch.encode()) > MAX_PATCH_BYTES:
        raise Blocked(
            "candidate-patch-too-large", f"candidate patch exceeds {MAX_PATCH_BYTES} bytes"
        )
    if hashlib.sha256(patch.encode()).hexdigest() != candidate.diff_sha256:
        raise Blocked("candidate-digest-mismatch", "diff SHA-256 does not match the manifest")
    return replace(candidate, changed_paths=tuple(changed))


def _validate_paths(
    repository: Path, candidate_commit: str, paths: list[str], harness: HarnessConfig
) -> None:
    for path in paths:
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise Blocked("candidate-path-invalid", f"unsafe candidate path: {path!r}")
        if pure.parts[0] in {".factory", ".git"}:
            raise Blocked("candidate-protected-path", f"candidate changes {path}")
        mode = repo.mode_at_ref(repository, candidate_commit, path)
        if mode in {"120000", "160000"}:
            raise Blocked("candidate-unsafe-entry", f"candidate entry {path} has mode {mode}")
    if hits := harness.protected_hits(paths):
        raise Blocked(
            "candidate-protected-path",
            "candidate changes protected paths: " + ", ".join(hits),
        )


def _source_run(
    store: Store,
    candidate: Candidate,
    ticket: str,
    project: Project,
    *,
    terminal: bool,
) -> Run:
    source = store.run_by_id(candidate.source_run_id)
    if source is None:
        raise Blocked("candidate-source-unavailable", candidate.source_run_id)
    if candidate.ticket != ticket.upper() or source.linear_id != ticket.upper():
        raise Blocked("candidate-ticket-mismatch", "candidate and source run must match the ticket")
    if source.project != project.name:
        raise Blocked(
            "candidate-project-mismatch", "candidate source run belongs to another project"
        )
    latest = store.run_by_ticket(ticket.upper())
    if latest is None or latest.id != source.id:
        raise Blocked(
            "candidate-source-not-current",
            f"source run {source.id} is not the latest run for {ticket.upper()}",
        )
    if source.base_ref and source.base_ref != project.base_ref:
        raise Blocked("candidate-base-mismatch", "source run used a different base ref")
    if terminal and source.state is not State.CANCELLED:
        raise Blocked(
            "candidate-source-not-cancelled",
            f"source run {source.id} is {source.state}; cancel it before a fresh carry-forward run",
        )
    return source


def _read_retained(directory: Path) -> Candidate:
    manifest = directory / "manifest.json"
    payload = _read_json(manifest)
    required = _EXTERNAL_KEYS | {"changed_paths"}
    if set(payload) != required:
        raise Blocked(
            "candidate-manifest-invalid",
            f"retained manifest keys differ: expected {sorted(required)}",
        )
    candidate = _candidate_from_payload(payload, directory / "candidate.bundle")
    if candidate.changed_paths != tuple(str(path) for path in payload["changed_paths"]):
        raise Blocked("candidate-manifest-invalid", "changed_paths is invalid")
    return candidate


def _write_retained(destination: Path, candidate: Candidate) -> bool:
    payload = candidate.retained_payload()
    manifest_bytes = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    existing_manifest = destination / "manifest.json"
    existing_bundle = destination / "candidate.bundle"
    if destination.exists():
        if (
            existing_manifest.is_file()
            and existing_bundle.is_file()
            and existing_manifest.read_bytes() == manifest_bytes
            and _sha256_file(existing_bundle) == candidate.bundle_sha256
        ):
            return False
        raise Blocked("candidate-retention-conflict", f"different evidence exists at {destination}")
    destination.mkdir(parents=True)
    bundle_tmp = destination / ".candidate.bundle.tmp"
    manifest_tmp = destination / ".manifest.json.tmp"
    try:
        shutil.copyfile(candidate.bundle_path, bundle_tmp)
        if _sha256_file(bundle_tmp) != candidate.bundle_sha256:
            raise Blocked("candidate-retention-mismatch", "bundle changed while being retained")
        manifest_tmp.write_bytes(manifest_bytes)
        os.replace(bundle_tmp, existing_bundle)
        os.replace(manifest_tmp, existing_manifest)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return True


def _read_json(path: Path, expected_keys: set[str] | None = None) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Blocked("candidate-manifest-unavailable", f"{path}: {exc}") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise Blocked(
            "candidate-manifest-too-large", f"manifest exceeds {MAX_MANIFEST_BYTES} bytes"
        )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Blocked("candidate-manifest-invalid", f"{path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise Blocked("candidate-manifest-invalid", "manifest must be a JSON object")
    if expected_keys is not None and set(payload) != expected_keys:
        raise Blocked(
            "candidate-manifest-invalid",
            f"manifest keys differ: expected {sorted(expected_keys)}",
        )
    return payload


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise Blocked("candidate-manifest-invalid", f"{key} must be a non-empty string")
    return value


def _sha(payload: dict[str, Any], key: str) -> str:
    value = _string(payload, key).lower()
    if len(value) != _SHA_LENGTH or any(ch not in "0123456789abcdef" for ch in value):
        raise Blocked("candidate-manifest-invalid", f"{key} must be a 40-character Git SHA")
    return value


def _digest(payload: dict[str, Any], key: str) -> str:
    value = _string(payload, key).lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise Blocked("candidate-manifest-invalid", f"{key} must be a SHA-256 digest")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, path)
