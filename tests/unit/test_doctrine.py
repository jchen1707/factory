"""`config/doctrine.toml`: one declared skill list, built into the one plugin a launch loads."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from factory import doctrine
from factory.doctrine import Doctrine, DoctrineError, Source
from tests.support import plugin_cache

HOME = Path(__file__).resolve().parents[2]
MATT = Source("mattpocock-skills", "claude-plugins-official", "1.2.3", ("tdd", "code-review"))


def _cache(tmp_path: Path, *, skills: tuple[str, ...] = ("tdd", "code-review", "grill-me")) -> Path:
    return plugin_cache.mattpocock(tmp_path / "cache", skills)


def test_the_shipped_declaration_loads() -> None:
    declared = doctrine.load(HOME / "config" / "doctrine.toml")
    assert "tdd" in declared.skills
    assert "implement" not in declared.skills


def test_an_absent_file_declares_nothing(tmp_path: Path) -> None:
    declared = doctrine.load(tmp_path / "doctrine.toml")
    assert declared == Doctrine(())
    assert doctrine.prompt_sentence(declared) == ""
    assert doctrine.build(declared, tmp_path, tmp_path / "built") is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[sources.p]\nmarketplace = 'm'\nskills = ['a']\n", "needs 'version'"),
        (
            "[sources.p]\nmarketplace='m'\nversion='1'\nskills=['a']\n"
            "[sources.q]\nmarketplace='m'\nversion='1'\nskills=['a']\n",
            "skills declared twice: a",
        ),
        ("[sources.p\n", "doctrine.toml"),
    ],
    ids=["missing-key", "duplicate-skill", "not-toml"],
)
def test_a_bad_declaration_is_refused(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "doctrine.toml"
    path.write_text(text)
    with pytest.raises(DoctrineError, match=message):
        doctrine.load(path)


def test_build_copies_exactly_the_declared_skills_into_one_plugin(tmp_path: Path) -> None:
    plugin = doctrine.build(Doctrine((MATT,)), _cache(tmp_path), tmp_path / "built")

    assert plugin is not None
    assert plugin.name == "doctrine"
    manifest = json.loads((plugin.path / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "doctrine"
    assert sorted(p.name for p in (plugin.path / "skills").iterdir()) == ["code-review", "tdd"]
    assert (plugin.path / "skills" / "tdd" / "SKILL.md").read_text().endswith("tdd body\n")


def test_build_is_a_lookup_until_a_source_skill_changes(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    first = doctrine.build(Doctrine((MATT,)), cache, tmp_path / "built")
    again = doctrine.build(Doctrine((MATT,)), cache, tmp_path / "built")
    skill = cache / "claude-plugins-official/mattpocock-skills/1.2.3/skills/engineering/tdd"
    (skill / "SKILL.md").write_text("edited\n")
    edited = doctrine.build(Doctrine((MATT,)), cache, tmp_path / "built")

    assert first == again
    assert first is not None
    assert edited is not None
    assert edited.path != first.path
    assert (first.path / "skills" / "tdd" / "SKILL.md").read_text().endswith("tdd body\n")
    assert [p.name for p in (tmp_path / "built").iterdir() if p.name.startswith(".")] == []


def test_the_built_tree_is_read_only_and_keeps_links_as_hashed(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    skill = cache / "claude-plugins-official/mattpocock-skills/1.2.3/skills/engineering/tdd"
    (skill / "shared.md").symlink_to("SKILL.md")

    plugin = doctrine.build(Doctrine((MATT,)), cache, tmp_path / "built")

    assert plugin is not None
    built = plugin.path / "skills" / "tdd"
    assert (built / "shared.md").is_symlink()
    assert not os.access(built / "SKILL.md", os.W_OK)


def test_a_skill_listed_but_absent_from_the_cache_is_a_problem_not_a_crash(
    tmp_path: Path,
) -> None:
    cache = _cache(tmp_path)
    (
        cache / "claude-plugins-official/mattpocock-skills/1.2.3/skills/engineering/tdd/SKILL.md"
    ).unlink()
    (cache / "claude-plugins-official/mattpocock-skills/1.2.3/skills/engineering/tdd").rmdir()

    assert doctrine.problems(Doctrine((MATT,)), cache) == [
        "mattpocock-skills 1.2.3 has no skill tdd"
    ]


def test_a_plugin_manifest_without_a_skill_list_is_refused(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    manifest = cache / "claude-plugins-official/mattpocock-skills/1.2.3/.claude-plugin/plugin.json"
    manifest.write_text(json.dumps({"name": "mattpocock-skills", "skills": "./skills"}))

    with pytest.raises(DoctrineError, match="lists no skills"):
        doctrine.build(Doctrine((MATT,)), cache, tmp_path / "built")


@pytest.mark.parametrize(
    ("source", "problem"),
    [
        (MATT, "has no skill code-review"),
        (
            Source("mattpocock-skills", "claude-plugins-official", "9.9.9", ("tdd",)),
            "mattpocock-skills 9.9.9 is not installed",
        ),
    ],
    ids=["skill-missing", "version-missing"],
)
def test_problems_name_what_the_cache_lacks(tmp_path: Path, source: Source, problem: str) -> None:
    cache = _cache(tmp_path, skills=("tdd",))
    found = doctrine.problems(Doctrine((source,)), cache)
    assert len(found) == 1
    assert problem in found[0]
    with pytest.raises(DoctrineError, match=problem):
        doctrine.build(Doctrine((source,)), cache, tmp_path / "built")


def test_the_prompt_names_each_skill_as_the_plugin_exposes_it() -> None:
    sentence = doctrine.prompt_sentence(Doctrine((MATT,)))
    assert "`doctrine:tdd`, `doctrine:code-review`" in sentence
    assert "Skill tool" in sentence


@pytest.mark.parametrize(
    ("settings", "enabled"),
    [
        (None, ()),
        ("{not json", ()),
        ('{"enabledPlugins": {"b@m": true, "a@m": true, "off@m": false}}', ("a@m", "b@m")),
        (
            '{"hooks": {}, "enabledPlugins": {"pstack@pstack-claude": true}}',
            ("pstack@pstack-claude",),
        ),
    ],
    ids=["absent", "not-json", "mixed", "beside-hooks"],
)
def test_enabled_plugins_are_read_off_the_target_settings(
    settings: str | None, enabled: tuple[str, ...]
) -> None:
    assert doctrine.enabled_plugins(settings) == enabled
