"""Immutable render assets, scoped to an app rather than a process-global cache.

Python code does not hot reload in `factory serve`. Templates and CSS therefore move
with an explicit console restart too. Request context propagates into Starlette's sync
workers and asyncio.to_thread, including the existing SSE render callbacks.
"""

from __future__ import annotations

import hashlib
import marshal
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from types import FunctionType, MappingProxyType

from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True)
class RenderBundle:
    templates: Mapping[str, str]
    style: str
    identity: str
    started_at: str

    @classmethod
    def load(cls, directory: Path, *, code_namespace: Mapping[str, object]) -> RenderBundle:
        templates = {
            path.name: path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.html"))
        }
        style = (directory / "console.css").read_text(encoding="utf-8")
        assets = hashlib.sha256()
        for asset_name, asset_text in [*templates.items(), ("console.css", style)]:
            assets.update(asset_name.encode() + b"\0" + asset_text.encode() + b"\0")
        code = hashlib.sha256()
        # Hash loaded function bytecode, never claim that an on-disk Git HEAD is the
        # revision of code an older Python process has already imported.
        module_name = code_namespace.get("__name__")
        for name, value in sorted(code_namespace.items()):
            if isinstance(value, FunctionType) and value.__module__ == module_name:
                code.update(name.encode() + marshal.dumps(value.__code__))
        identity = (
            f"Console code {code.hexdigest()[:12]} / assets {assets.hexdigest()[:12]}"
            " · source revision unverified"
        )
        return cls(
            MappingProxyType(templates),
            style,
            identity,
            datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        )

    def render(self, name: str, /, **fields: str) -> str:
        return Template(self.templates[name]).substitute(**fields)


_current: ContextVar[RenderBundle] = ContextVar("factory_console_render_bundle")


def current_bundle() -> RenderBundle:
    return _current.get()


class BundleMiddleware:
    """Keep the bundle bound until the full response (including an SSE stream) ends."""

    def __init__(self, app: ASGIApp, *, bundle: RenderBundle) -> None:
        self.app = app
        self.bundle = bundle

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = _current.set(self.bundle)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(token)
