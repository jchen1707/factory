"""Retain trusted runtime code and inputs in bounded argv, never staging paths."""

from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path
from typing import Any

from factory.machine import Blocked


def command(
    request: dict[str, Any],
    *,
    prompt: str | None = None,
    schema: str | None = None,
    usage: bool = False,
) -> list[str]:
    """Build a self-contained Linux command; no sandbox calls or file mutations."""
    if not usage and (prompt is None or schema is None):
        raise ValueError("ordinary worker requires retained prompt and schema")
    payload = {
        "worker": Path(__file__).with_name("app_server_worker.py").read_text(),
        "request": request,
        "prompt": prompt,
        "schema": schema,
        "usage_worker": Path(__file__).with_name("certification_usage_worker.py").read_text()
        if usage
        else None,
    }
    compressed = base64.b85encode(zlib.compress(json.dumps(payload).encode())).decode()
    bootstrap = (
        "import base64,fcntl,json,os,types,zlib\n"
        "p=json.loads(zlib.decompress(base64.b85decode(" + repr(compressed) + ")))\n"
        "m=types.ModuleType('certified_worker')\nexec(p['worker'],m.__dict__)\n"
        "if p['usage_worker'] is not None:\n"
        " u=types.ModuleType('certified_usage_worker')\n exec(p['usage_worker'],u.__dict__)\n"
        " u.helpers=lambda:m\n raise SystemExit(u.run(p['request']))\n"
        "for key in ('prompt','schema'):\n"
        " fd=os.memfd_create('factory-input',os.MFD_ALLOW_SEALING)\n"
        " with os.fdopen(os.dup(fd),'wb') as f: f.write(p[key].encode())\n"
        " fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)\n"
        " p['request'][key]='/proc/self/fd/'+str(fd)\n"
        "raise SystemExit(m.run(p['request']))\n"
    )
    if len(bootstrap.encode()) >= 120_000:
        raise Blocked("certification-request-too-large", "Frozen worker request exceeds argv limit")
    return ["/usr/bin/python3", "-I", "-S", "-c", bootstrap]
