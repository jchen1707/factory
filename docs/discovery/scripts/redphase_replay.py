#!/usr/bin/env python3
"""P0-14 measurement harness — NOT factory code.

Replays §15.3's red phase against already-merged commits to measure the baseline
inconclusive rate that seeds `redphase.inconclusive_alarm_pct` (§24.12).

For each commit: check out its base in a scratch worktree, apply only the test-file
half of the diff, run the repo's `kind: test` gate, and classify the outcome.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def test_gate(repo: Path) -> list[str]:
    cfg = json.loads((repo / "harness.config.json").read_text())
    for g in cfg["gates"]:
        if g["kind"] == "test":
            return g["run"]
    raise SystemExit("no kind: test gate")


IMPORT_ERR = re.compile(
    r"Failed to resolve import|Cannot find module|ModuleNotFoundError|ImportError|"
    r"No test suite found|error during collection|ERROR collecting|SyntaxError|"
    r"Error: Transform failed|does not provide an export",
)
ASSERT_ERR = re.compile(
    r"AssertionError|\bassert\b|expected .* to |\bFAILED\b|Test Files .*failed|"
    r"AssertionFailed|to(Be|Equal|Have)",
)


def classify(rc: int, out: str, changed_tests: list[str]) -> tuple[str, str]:
    """§15.3's outcome table.

    The distinction that matters, and that a naive "does the failure name the test"
    check gets wrong: a test file that fails because the module it imports does not
    exist yet is an *import* error, not a red test. It names the test file either way.
    """
    if rc == 0:
        return "PASSES", "green without the implementation - always blocks"
    names = [Path(p).stem for p in changed_tests]
    named = any(n and n in out for n in names)
    if IMPORT_ERR.search(out) and not ASSERT_ERR.search(out):
        return "INCONCLUSIVE", "import/collection error - the seam does not separate"
    if named and ASSERT_ERR.search(out):
        return "RED-REAL", "assertion failure naming a test the diff touched"
    if ASSERT_ERR.search(out):
        return "INCONCLUSIVE", "assertion failure, but not in a test the diff touched"
    return "INCONCLUSIVE", "failed for some other reason"


def replay(repo: Path, commit: str, pathspec: list[str], link: list[str]) -> dict:
    base = git(repo, "rev-parse", f"{commit}^").strip()
    changed = [
        p for p in git(repo, "diff", "--name-only", base, commit, "--", *pathspec).splitlines() if p
    ]
    row: dict = {"commit": commit, "base": base[:7], "test_files": changed}
    if not changed:
        row |= {"outcome": "NO-TEST-FILES", "why": "diff adds no test files"}
        return row

    scratch = Path(tempfile.mkdtemp(prefix="p0-14-"))
    wt = scratch / "wt"
    try:
        git(repo, "worktree", "add", "--detach", str(wt), base)
        patch = scratch / "tests-only.patch"
        patch.write_text(git(repo, "diff", base, commit, "--", *pathspec))
        ap = subprocess.run(
            ["git", "-C", str(wt), "apply", str(patch)], capture_output=True, text=True
        )
        if ap.returncode != 0:
            row |= {"outcome": "PATCH-FAILED", "why": ap.stderr.strip()[:200]}
            return row
        for name in link:  # e.g. node_modules — the base ref's deps, not re-installed
            (wt / name).symlink_to(repo / name)
        gate = test_gate(repo)
        r = subprocess.run(gate, cwd=wt, capture_output=True, text=True, timeout=900)
        out = (r.stdout + r.stderr)[-6000:]
        outcome, why = classify(r.returncode, out, changed)
        row |= {"outcome": outcome, "why": why, "rc": r.returncode, "tail": out[-400:]}
        return row
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)])
        subprocess.run(["git", "-C", str(repo), "worktree", "prune"])
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--pathspec", nargs="+", required=True)
    ap.add_argument(
        "--link",
        nargs="*",
        default=[],
        help="dirs to symlink from the repo into the scratch worktree",
    )
    ap.add_argument("commits", nargs="+")
    a = ap.parse_args()
    rows = [replay(a.repo, c, a.pathspec, a.link) for c in a.commits]
    for r in rows:
        print(f"{r['commit']}  {r['outcome']:<14} {r['why']}")
        if r.get("tail"):
            print("    " + r["tail"].replace("\n", "\n    ")[-500:])
    print(json.dumps(rows, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
