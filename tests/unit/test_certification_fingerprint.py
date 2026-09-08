"""Fresh certification identity construction through the sandbox observation boundary."""

from dataclasses import replace
from pathlib import Path

import pytest

from factory.certification_fingerprint import FingerprintInputs, observe
from factory.machine import Blocked
from factory.sandbox.base import SandboxSpec, Workspace


class Observations:
    def __init__(self) -> None:
        self.generation = "generation-one"
        self.environment = "e" * 64
        self.launcher = "c" * 64
        self.code_host = "e" * 64

    def observe_certification(self, spec: SandboxSpec, **kwargs: object) -> dict:
        return {
            "generation": self.generation,
            "image_digest": "sha256:" + "a" * 64,
            "runtime_path": "/opt/codex",
            "runtime_version": "codex-cli 0.153.4",
            "runtime_sha256": "b" * 64,
            "actual": {
                "environment_sha256": self.environment,
                "mounts": [],
                "launcher_sha256": self.launcher,
                "code_host_sha256": self.code_host,
            },
        }


def inputs(tmp_path: Path) -> FingerprintInputs:
    authority = tmp_path / "trusted"
    authority.mkdir()
    (authority / "hooks.json").write_text('{"hooks":{}}')
    source = tmp_path / "source"
    source.mkdir()
    (source / "probes.json").write_text('{"revision":1}')
    return FingerprintInputs(
        spec=SandboxSpec(
            "synthetic", "build", "factory-build-fingerprint", (Workspace(tmp_path / "candidate"),)
        ),
        binary="/opt/codex",
        workdir=str(tmp_path / "candidate"),
        env={"TEST_ENV": "one"},
        authority_root=authority,
        probe_root=source,
        hook_files={str(tmp_path / "candidate/.codex/hooks.json"): "1" * 64},
        usage_scope="connection",
    )


def test_changed_actual_environment_requested_layout_or_source_never_reuses_identity(
    tmp_path: Path,
) -> None:
    request = inputs(tmp_path)
    adapter = Observations()
    original = observe(adapter, request)
    assert observe(adapter, request) == original
    adapter.environment = "f" * 64
    assert observe(adapter, request) != original
    adapter.environment = "e" * 64
    assert observe(adapter, replace(request, spec=replace(request.spec, clone=True))) != original
    (request.probe_root / "probes.json").write_text('{"revision":2}')
    assert observe(adapter, request) != original


def test_authority_or_probe_source_in_candidate_mount_cannot_be_trusted(tmp_path: Path) -> None:
    request = inputs(tmp_path)
    with pytest.raises(Blocked, match="certification-inputs-untrusted"):
        observe(
            Observations(),
            replace(request, spec=replace(request.spec, workspaces=(Workspace(tmp_path),))),
        )


@pytest.mark.parametrize("mode", ["", "unknown", None, 1])
def test_invalid_automatic_mode_cannot_be_configured(tmp_path: Path, mode: object) -> None:
    from factory.operator_controls import configure
    from factory.store import Store

    store = Store(tmp_path / "store.db")
    with pytest.raises(ValueError, match="certification_mode"):
        configure(store, "project", "synthetic", {"certification_mode": mode})
    store.close()


def test_automatic_certification_requires_explicit_configuration_for_new_runs(
    tmp_path: Path,
) -> None:
    from factory.operator_controls import configure
    from factory.store import Store

    store = Store(tmp_path / "store.db")
    config = tmp_path / "certification.json"
    config.write_text("{}")
    configure(
        store,
        "project",
        "synthetic",
        {
            "certification_mode": "automatic",
            "certification_config": str(config),
            "agent_adapter": "app-server",
        },
    )
    assert store.runtime.settings("project", "synthetic")["certification_mode"] == "automatic"
    store.close()


def test_changed_launcher_invalidates_fingerprint(tmp_path: Path) -> None:
    adapter = Observations()
    request = inputs(tmp_path)
    original = observe(adapter, request)
    assert original.launcher_sha256 == "c" * 64
    adapter.launcher = "d" * 64
    assert observe(adapter, request) != original


def test_code_mode_host_change_invalidates_the_full_identity(tmp_path: Path) -> None:
    observer = Observations()
    request = inputs(tmp_path)
    original = observe(observer, request)
    observer.code_host = "f" * 64
    changed = observe(observer, request)
    assert original.code_host_sha256 == "e" * 64
    assert changed.code_host_sha256 == "f" * 64
    assert changed != original
