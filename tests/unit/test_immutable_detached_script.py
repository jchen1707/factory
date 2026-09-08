"""Retained argv bodies do not execute candidate replacements at the marker path."""

import os
import shlex
import subprocess
from pathlib import Path

from factory.sandbox.base import detached_shell_script


def test_immutable_body_ignores_existing_candidate_script(tmp_path: Path) -> None:
    # The host is macOS; replace only Linux session creation. The real /bin/sh
    # executes the generated envelope and argv body, including quoting and $0.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    setsid = bin_dir / "setsid"
    setsid.write_text('#!/bin/sh\nshift\nexec "$@"\n')
    setsid.chmod(0o755)
    pgid = tmp_path / "pgid"
    marker = Path(str(pgid) + "-body.sh")
    attack = "printf compromised > " + shlex.quote(str(tmp_path / "compromised")) + "\n"
    marker.write_text(attack)
    answer = tmp_path / "answer"
    literal = "retained ' quote $() `not executed`"
    body = shlex.join(["printf", "%s", literal]) + " > " + shlex.quote(str(answer))
    body += '\nprintf %s "$0" > ' + shlex.quote(str(tmp_path / "shell-marker"))
    script = detached_shell_script(
        heartbeat_path=tmp_path / "heartbeat",
        exit_path=tmp_path / "exit",
        pgid_path=pgid,
        body=body,
        immutable=True,
    )
    subprocess.run(
        ["/bin/sh", "-c", script],
        check=True,
        env=os.environ | {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    assert answer.read_text() == literal
    assert (tmp_path / "shell-marker").read_text() == str(marker)
    assert marker.read_text() == attack
    assert not (tmp_path / "compromised").exists()
    assert (tmp_path / "exit").read_text() == "0"


def test_legacy_body_protocol_remains_opt_in(tmp_path: Path) -> None:
    script = detached_shell_script(
        heartbeat_path=tmp_path / "heartbeat",
        exit_path=tmp_path / "exit",
        pgid_path=tmp_path / "pgid",
        body="true",
    )
    assert "__FACTORY_BODY__" in script
    assert "setsid --wait /bin/sh -c" not in script
