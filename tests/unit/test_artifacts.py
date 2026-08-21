"""§21.1 — the secret scanner, the manifest, and the attempt directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.artifacts import (
    AttemptDir,
    SecretFound,
    log_event,
    scan_directory,
    scan_for_secrets,
    write_manifest,
)


@pytest.mark.parametrize(
    "planted",
    [
        "token = ghp_0123456789abcdefghij",
        "GITHUB_PAT=github_pat_11ABCDEFG0123456789abcdefg",
        "key: lin_api_0123456789abcdefghij",
        "OPENAI=sk-proj-0123456789abcdefghij",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_every_pattern_raises(planted: str) -> None:
    with pytest.raises(SecretFound):
        scan_for_secrets(planted, "a fixture")


def test_a_bare_variable_name_is_not_a_secret() -> None:
    # Every config file in this system mentions LINEAR_API_KEY. Raising on the name
    # would quarantine the repository's own documentation.
    scan_for_secrets(
        "hooks.secretVars lists LINEAR_API_KEY and GH_TOKEN",
        "a config file",
        extra_names=["LINEAR_API_KEY", "GH_TOKEN"],
    )


def test_an_assignment_to_a_declared_secret_var_raises() -> None:
    with pytest.raises(SecretFound):
        scan_for_secrets(
            'LINEAR_API_KEY="aaaaaaaaaaaaaaaaaaaa"', "an env file", extra_names=["LINEAR_API_KEY"]
        )


def test_the_message_names_nothing_but_the_kind_and_the_place() -> None:
    with pytest.raises(SecretFound) as caught:
        scan_for_secrets("ghp_0123456789abcdefghij", "events.jsonl")
    assert "ghp_0123456789abcdefghij" not in str(caught.value)
    assert "rotate" in str(caught.value)


def test_a_planted_secret_quarantines_the_directory(tmp_path: Path) -> None:
    (tmp_path / "events.jsonl").write_text('{"text":"ghp_0123456789abcdefghij"}')
    with pytest.raises(SecretFound):
        scan_directory(tmp_path)


def test_manifest_hashes_every_file_but_itself(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("one")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("two")
    manifest = write_manifest(tmp_path, produced_by="implementing")
    files: dict[str, object] = manifest["files"]  # type: ignore[assignment]
    assert set(files) == {"a.txt", "sub/b.txt"}
    assert manifest["producedBy"] == "implementing"
    assert (tmp_path / "manifest.json").exists()


def test_a_log_line_carrying_a_secret_raises_rather_than_redacting(tmp_path: Path) -> None:
    with pytest.raises(SecretFound):
        log_event(tmp_path, level="info", event="x", detail={"token": "ghp_0123456789abcdefghij"})
    assert not list(tmp_path.glob("*.jsonl"))


def test_log_event_writes_one_object_per_line(tmp_path: Path) -> None:
    import json

    log_event(tmp_path, level="info", event="state.changed", run_id="r1", ticket="BAC-4")
    log_event(tmp_path, level="warning", event="implement.timeout", run_id="r1")
    written = next(iter(tmp_path.glob("*.jsonl"))).read_text().splitlines()
    assert len(written) == 2
    assert json.loads(written[0])["event"] == "state.changed"


def test_exit_code_is_none_until_the_file_lands(tmp_path: Path) -> None:
    attempt = AttemptDir.create(tmp_path, 1)
    assert attempt.exit_code() is None
    attempt.exit_file.write_text("0")
    assert attempt.exit_code() == 0


def test_stderr_is_a_separate_file_from_the_event_stream(tmp_path: Path) -> None:
    # P0-7: hook denials arrive on stderr and never in the JSON stream. Merging them
    # would put non-JSON into a JSONL parser and lose the evidence either way.
    attempt = AttemptDir.create(tmp_path, 1)
    assert attempt.stderr != attempt.events
