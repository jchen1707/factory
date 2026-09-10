"""Each console instance serves one coherent asset revision for its whole lifetime."""

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from factory.console import app as console_app
from factory.steps import Context


def test_two_apps_keep_their_own_template_and_css_bundle(
    ctx: Context, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    templates = tmp_path / "console-assets"
    shutil.copytree(console_app.TEMPLATES, templates)
    css = templates / "console.css"
    page = templates / "page.html"
    css.write_text(css.read_text() + "\n/* bundle-one-css */")
    page.write_text(page.read_text().replace("</body>", "<!-- bundle-one-page --></body>"))
    monkeypatch.setattr(console_app, "TEMPLATES", templates)

    def client() -> TestClient:
        return TestClient(
            console_app.create_app(
                ctx.home,
                registry=ctx.registry,
                routing=ctx.routing,
                store=ctx.store,
                linear=ctx.linear,
            )
        )

    first = client()
    before = first.get("/").text
    assert "bundle-one-css" in before
    assert "bundle-one-page" in before
    css.write_text(css.read_text().replace("bundle-one-css", "bundle-two-css"))
    page.write_text(page.read_text().replace("bundle-one-page", "bundle-two-page"))
    second = client()
    # Alternate requests: loading B must neither change A nor inherit A's global cache.
    for current, own, other in [(second, "two", "one"), (first, "one", "two")]:
        body = current.get("/").text
        assert f"bundle-{own}-css" in body
        assert f"bundle-{own}-page" in body
        assert f"bundle-{other}-css" not in body
        assert f"bundle-{other}-page" not in body
        assert "Console code" in body
        assert "source revision unverified" in body
