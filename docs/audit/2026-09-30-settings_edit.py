"""Add plugin declarations to a .claude/settings.json by text edit, and keep the stack's
main-branch transform blocks in step with the edit.

A JSON re-dump would reformat the whole file and break the transform's exact-text blocks, so
this edits text: it appends entries to the `enabledPlugins` object and inserts or extends an
`extraKnownMarketplaces` object after it. The same edit is applied to every transform block
for `.claude/settings.json` whose text holds a complete `enabledPlugins` object, so a v2 edit
and its generated-main counterpart cannot drift. Idempotent: a second run changes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SETTINGS = ".claude/settings.json"
TRANSFORM = ".agents/transform/transform.json"


def _object_span(text: str, key: str) -> tuple[int, int] | None:
    match = re.search(r'( *)"' + re.escape(key) + r'": \{', text)
    if not match:
        return None
    depth = 0
    for index in range(match.end() - 1, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return match.start(), index + 1
    return None


def _render(key: str, entries: dict, indent: str) -> str:
    if not entries:
        return f'{indent}"{key}": {{}}'
    body = json.dumps(entries, indent=2)
    lines = body.splitlines()
    shifted = [lines[0]] + [indent + line for line in lines[1:]]
    return f'{indent}"{key}": ' + "\n".join(shifted)


def edit(text: str, plugins: dict[str, bool], marketplaces: dict[str, dict]) -> str:
    span = _object_span(text, "enabledPlugins")
    if span is None:
        return text
    start, end = span
    indent = re.match(r" *", text[start:]).group(0)
    current = json.loads(text[start:end].split(":", 1)[1])
    merged = {**current, **{k: v for k, v in plugins.items() if k not in current}}
    text = text[:start] + _render("enabledPlugins", merged, indent) + text[end:]
    if not marketplaces:
        return text
    market_span = _object_span(text, "extraKnownMarketplaces")
    if market_span is not None:
        m_start, m_end = market_span
        existing = json.loads(text[m_start:m_end].split(":", 1)[1])
        combined = {**existing, **{k: v for k, v in marketplaces.items() if k not in existing}}
        return text[:m_start] + _render("extraKnownMarketplaces", combined, indent) + text[m_end:]
    _, end = _object_span(text, "enabledPlugins")
    rendered = _render("extraKnownMarketplaces", marketplaces, indent)
    return text[:end] + ",\n" + rendered + text[end:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument(
        "--spec", type=Path, required=True, help='JSON: {"plugins": {...}, "marketplaces": {...}}'
    )
    args = parser.parse_args(argv)
    spec = json.loads(args.spec.read_text())
    plugins, marketplaces = spec.get("plugins", {}), spec.get("marketplaces", {})

    settings = args.repo / SETTINGS
    before = settings.read_text()
    after = edit(before, plugins, marketplaces)
    json.loads(after)
    settings.write_text(after)

    transform = args.repo / TRANSFORM
    if transform.exists():
        raw = transform.read_text()
        for block in json.loads(raw).get("blocks", []):
            if block.get("file") != SETTINGS:
                continue
            for side in ("from", "to"):
                replacement = edit(block[side], plugins, marketplaces)
                if replacement == block[side]:
                    continue
                for ascii_only in (False, True):
                    encoded = json.dumps(block[side], ensure_ascii=ascii_only)
                    if raw.count(encoded) == 1:
                        raw = raw.replace(encoded, json.dumps(replacement, ensure_ascii=ascii_only))
                        break
                else:
                    raise SystemExit(
                        f"{transform}: cannot locate the {side} text of a settings block"
                    )
        json.loads(raw)
        transform.write_text(raw)
    print(f"{args.repo}: settings {'changed' if after != before else 'unchanged'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
