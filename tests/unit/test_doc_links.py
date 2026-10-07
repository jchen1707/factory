"""Every relative link under `docs/` resolves to an existing path. Anchors are not checked."""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"
_FENCE = re.compile(r"```.*?```", re.S)
_MARKDOWN = re.compile(r"\]\(<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\)")
_REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)", re.M)
_HTML = re.compile(r"\b(?:href|src)=[\"']([^\"']+)[\"']")
_EXTERNAL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*:|//|#")


def relative_links(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".md":
        text = _FENCE.sub("", text)
        targets = _MARKDOWN.findall(text) + _REFERENCE.findall(text) + _HTML.findall(text)
    else:
        targets = _HTML.findall(text)
    return [target for target in targets if not _EXTERNAL.match(target)]


def test_every_relative_docs_link_resolves() -> None:
    pages = sorted(p for p in DOCS.rglob("*") if p.suffix in {".md", ".html"})
    checked = 0
    broken: list[str] = []
    for page in pages:
        for target in relative_links(page):
            checked += 1
            if not (page.parent / target.split("#", 1)[0].split("?", 1)[0]).exists():
                broken.append(f"{page.relative_to(DOCS.parent)} -> {target}")
    assert checked > 300, f"link pattern matched only {checked} links"
    assert broken == []
