"""The build sandbox spec carries the project's `root_size`."""

from __future__ import annotations

from dataclasses import replace

from factory.steps import Context
from factory.steps import sandbox as sandbox_step


def test_the_build_spec_carries_the_declared_root_size(ctx: Context) -> None:
    assert sandbox_step.build_spec(ctx).root_size_gib is None
    ctx.project = replace(ctx.project, root_size_gib=40)
    assert sandbox_step.build_spec(ctx).root_size_gib == 40
