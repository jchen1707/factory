"""The skills a sandboxed builder can invoke by name, declared once.

`config/doctrine.toml` names each source plugin, the version it is pinned to and the
skills taken from it. That one list has three consumers:

- `build` materialises the skills as one plugin, `doctrine`, under
  `<home>/state/doctrine/<digest>/`, which the build sandbox mounts read-only and every
  builder launch passes as `--plugin-dir`. Measured on the host (Claude Code 2.1.292): a
  read-only plugin directory loads as `doctrine@inline`, its skills appear in
  `init.skills` as `doctrine:<skill>` and the Skill tool launches them.
- `prompt_sentence` tells the builder what it can invoke.
- `problems` drives `factory doctor`.

Nothing else enters the sandbox. pstack stays out: `--setting-sources project` excludes the
operator's plugins, and `enabled_plugins` names what a target repository's own settings
switch on, so the launch can switch it off again.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factory.agent.claude import PluginRef

__all__ = [
    "NAME",
    "Doctrine",
    "DoctrineError",
    "Source",
    "build",
    "config_path",
    "enabled_plugins",
    "load",
    "problems",
    "prompt_sentence",
    "root",
]

NAME = "doctrine"


class DoctrineError(Exception):
    pass


@dataclass(frozen=True)
class Source:
    plugin: str
    marketplace: str
    version: str
    skills: tuple[str, ...]

    def root(self, cache: Path) -> Path:
        return cache / self.marketplace / self.plugin / self.version


@dataclass(frozen=True)
class Doctrine:
    sources: tuple[Source, ...]

    @property
    def skills(self) -> tuple[str, ...]:
        return tuple(skill for source in self.sources for skill in source.skills)


def config_path(home: Path) -> Path:
    return home / "config" / "doctrine.toml"


def root(home: Path) -> Path:
    """Where `build` materialises the plugin; the build sandbox mounts it read-only."""
    return home / "state" / "doctrine"


def load(path: Path) -> Doctrine:
    """An absent file declares nothing: no plugin, no prompt sentence."""
    if not path.exists():
        return Doctrine(())
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DoctrineError(f"{path}: {exc}") from exc
    sources: list[Source] = []
    for plugin, table in (raw.get("sources") or {}).items():
        try:
            sources.append(
                Source(
                    plugin=plugin,
                    marketplace=str(table["marketplace"]),
                    version=str(table["version"]),
                    skills=tuple(str(skill) for skill in table["skills"]),
                )
            )
        except (KeyError, TypeError) as exc:
            raise DoctrineError(f"{path}: [sources.{plugin}] needs {exc}") from exc
    doctrine = Doctrine(tuple(sources))
    duplicated = sorted({s for s in doctrine.skills if doctrine.skills.count(s) > 1})
    if duplicated:
        raise DoctrineError(f"{path}: skills declared twice: {', '.join(duplicated)}")
    return doctrine


def _skill_dirs(source: Source, cache: Path) -> dict[str, Path]:
    """The declared skills' directories, found through the source's own `plugin.json`."""
    root = source.root(cache)
    manifest = root / ".claude-plugin" / "plugin.json"
    try:
        listed = json.loads(manifest.read_text(encoding="utf-8")).get("skills")
    except (OSError, ValueError) as exc:
        raise DoctrineError(f"{source.plugin} {source.version} is not installed: {exc}") from exc
    if not isinstance(listed, list):
        raise DoctrineError(f"{manifest} lists no skills")
    by_name = {Path(str(entry)).name: (root / str(entry)).resolve() for entry in listed}
    missing = [
        skill for skill in source.skills if skill not in by_name or not by_name[skill].is_dir()
    ]
    if missing:
        raise DoctrineError(f"{source.plugin} {source.version} has no skill {', '.join(missing)}")
    return {skill: by_name[skill] for skill in source.skills}


def _files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*") if path.is_file())


def _digest(doctrine: Doctrine, dirs: dict[str, Path]) -> str:
    hasher = hashlib.sha256()
    for source in doctrine.sources:
        hasher.update(f"{source.marketplace}/{source.plugin}@{source.version}\n".encode())
    for skill, directory in dirs.items():
        for path in _files(directory):
            hasher.update(f"{skill}/{path.relative_to(directory)}\n".encode())
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def build(doctrine: Doctrine, cache: Path, into: Path) -> PluginRef | None:
    """The `doctrine` plugin for this declaration, materialised once under `into`.

    None when nothing is declared. The directory is named by a digest of the declaration
    and the source files, so a rebuild with the same inputs is a lookup, and an edited
    source skill gets a new directory rather than changing one a live run is reading.
    """
    if not doctrine.skills:
        return None
    dirs = {
        skill: path
        for source in doctrine.sources
        for skill, path in _skill_dirs(source, cache).items()
    }
    digest = _digest(doctrine, dirs)
    target = into / digest
    if not target.is_dir():
        staging = into / f".{digest}.{uuid.uuid4().hex}.tmp"
        try:
            (staging / ".claude-plugin").mkdir(parents=True)
            manifest = {"name": NAME, "version": f"0.0.0+{digest[:12]}"}
            (staging / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest) + "\n")
            for skill, directory in dirs.items():
                # Links stay links, so the copy is exactly the tree `_digest` hashed.
                shutil.copytree(directory, staging / "skills" / skill, symlinks=True)
            for path in staging.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    path.chmod(0o444)
            os.rename(staging, target)
        except FileExistsError:
            pass
        except OSError:
            if not target.is_dir():
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return PluginRef(NAME, target)


def prompt_sentence(doctrine: Doctrine) -> str:
    if not doctrine.skills:
        return ""
    names = ", ".join(f"`{NAME}:{skill}`" for skill in doctrine.skills)
    return f"These skills are loaded and you can invoke them by name with the Skill tool: {names}."


def problems(doctrine: Doctrine, cache: Path) -> list[str]:
    """Why `build` would refuse this declaration on this machine; empty when it would not."""
    found: list[str] = []
    for source in doctrine.sources:
        try:
            _skill_dirs(source, cache)
        except DoctrineError as exc:
            found.append(str(exc))
    return found


def enabled_plugins(settings_text: str | None) -> tuple[str, ...]:
    """The plugins a target's `.claude/settings.json` switches on, which `--setting-sources
    project` would load (measured: `enabledPlugins` loads them on the host)."""
    if settings_text is None:
        return ()
    try:
        settings: Any = json.loads(settings_text)
    except ValueError:
        return ()
    enabled = settings.get("enabledPlugins") if isinstance(settings, dict) else None
    if not isinstance(enabled, dict):
        return ()
    return tuple(sorted(name for name, on in enabled.items() if on))
