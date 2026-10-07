from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path


def mattpocock(cache: Path, skills: Sequence[str] = ("tdd", "code-review", "grill-me")) -> Path:
    """A plugin cache laid out the way `claude plugin install` lays out mattpocock-skills
    1.2.3: `plugin.json` lists each skill's directory, grouped as the real one groups them."""
    root = cache / "claude-plugins-official" / "mattpocock-skills" / "1.2.3"
    listed = []
    for skill in skills:
        group = "productivity" if skill.startswith("grill") else "engineering"
        directory = root / "skills" / group / skill
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"---\nname: {skill}\n---\n\n{skill} body\n")
        listed.append(f"./skills/{group}/{skill}")
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "mattpocock-skills", "version": "1.2.3", "skills": listed})
    )
    return cache
