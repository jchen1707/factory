"""List what deleting a set of root modules orphans, and every live reference into it.

    uv run python docs/audit/dead_modules.py 'factory.delegation*' factory.workflow_children

Roots are dotted module names; shell-style wildcards match module names. A module is live
when the `factory` console script reaches it through imports or through a string naming
its file (`"source_observer.py"`) or dotted path (`"factory.learning_worker"`), which is
how the in-sandbox workers are shipped. Tests are never entry points, so a test that keeps
a module alive does not count. It reads the repository in the current directory, so run it
from the root of the tree before the deletion (for example a checkout of the merge base);
on the tree after, the roots are gone and it reports no match.
"""

from __future__ import annotations

import ast
import fnmatch
import sys
import tomllib
from collections import deque
from pathlib import Path

ROOT = Path.cwd()
SOURCES = {"factory": ROOT / "src" / "factory", "tests": ROOT / "tests"}


def module_files() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for package, directory in SOURCES.items():
        for path in sorted(directory.rglob("*.py")):
            parts = [package, *path.relative_to(directory).with_suffix("").parts]
            if parts[-1] == "__init__":
                parts.pop()
            found[".".join(parts)] = path
    return found


def references(name: str, path: Path, modules: dict[str, Path]) -> dict[str, list[int]]:
    tree = ast.parse(path.read_text(), str(path))
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    hits: dict[str, list[int]] = {}

    def add(target: str, line: int) -> None:
        if target in modules and target != name:
            hits.setdefault(target, []).append(line)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*anchor, base] if base else anchor)
            add(base, node.lineno)
            for alias in node.names:
                add(f"{base}.{alias.name}", node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if text in modules:
                add(text, node.lineno)
            elif text.endswith(".py") and "\n" not in text:
                for other, other_path in modules.items():
                    if other_path.as_posix().endswith("/" + text.lstrip("/")):
                        add(other, node.lineno)
    for target in list(hits):
        parent = target.rpartition(".")[0]
        while parent:
            add(parent, hits[target][0])
            parent = parent.rpartition(".")[0]
    return hits


def reach(starts: set[str], graph: dict[str, dict[str, list[int]]], cut: set[str]) -> set[str]:
    seen = {start for start in starts if start not in cut}
    queue = deque(seen)
    while queue:
        for target in graph[queue.popleft()]:
            if target not in seen and target not in cut:
                seen.add(target)
                queue.append(target)
    return seen


def main(patterns: list[str]) -> int:
    modules = module_files()
    graph = {name: references(name, path, modules) for name, path in modules.items()}
    roots = {name for name in modules if any(fnmatch.fnmatch(name, p) for p in patterns)}
    unmatched = [p for p in patterns if not any(fnmatch.fnmatch(n, p) for n in modules)]
    if unmatched:
        print(f"no module matches: {', '.join(unmatched)}", file=sys.stderr)
        return 2
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    entries = {target.partition(":")[0] for target in scripts.values()}
    production = {name for name in modules if name.startswith("factory")}
    before = reach(entries, graph, set()) & production
    after = reach(entries, graph, roots) & production
    dead = (before - after) | roots
    preexisting = production - before

    def rel(name: str) -> str:
        return modules[name].relative_to(ROOT).as_posix()

    def lines(names: set[str]) -> int:
        return sum(len(modules[n].read_text().splitlines()) for n in names)

    print(f"## Production modules removed ({len(dead)}, {lines(dead)} lines)")
    for name in sorted(dead):
        tag = "root" if name in roots else "orphaned"
        print(f"{rel(name)}\t{tag}")

    print("\n## Live references into removed modules (migrate these callers first)")
    for name in sorted(after):
        for target, at in sorted(graph[name].items()):
            if target in dead:
                print(f"{rel(name)}:{','.join(map(str, sorted(set(at))))}\t-> {target}")

    tests = {name for name in modules if name.startswith("tests")}
    tainted = {name for name in tests if dead & graph[name].keys()}
    changed = True
    while changed:
        changed = False
        for name in tests - tainted:
            if tainted & graph[name].keys():
                tainted.add(name)
                changed = True
    print(f"\n## Tests that reference removed modules or tainted test helpers ({len(tainted)})")
    for name in sorted(tainted):
        targets = sorted(dead & graph[name].keys() | tainted & graph[name].keys())
        live = sorted(t for t in graph[name] if t in after and t != "factory")
        verdict = "only removed code" if not live else f"also uses {len(live)} live modules"
        print(f"{rel(name)}\t{verdict}\t-> {', '.join(targets)}")

    if preexisting:
        print(f"\n## Already unreachable from the console script, untouched ({len(preexisting)})")
        for name in sorted(preexisting):
            print(rel(name))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
