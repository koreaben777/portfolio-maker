from __future__ import annotations

import json

import pytest

from portfolio_maker.infrastructure.static_site import (
    DeploymentArtifact,
    StaticSiteError,
    inline_static_output,
    prepare_private_deployment,
    prepare_public_deployment,
    validate_static_output,
    write_generated_data_module,
)


def test_deployment_gate_rejects_restricted_public_and_allows_private(tmp_path):
    artifact = DeploymentArtifact(tmp_path / "portfolio.html", "restricted")

    with pytest.raises(StaticSiteError, match="open_public"):
        prepare_public_deployment(artifact)
    assert prepare_private_deployment(artifact).delivery_scope == "restricted"


def test_static_validator_accepts_relative_assets_and_no_runtime_fetch(tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<link rel="stylesheet" href="./assets/main.css">'
        '<script type="module" src="./assets/main.js"></script>',
        encoding="utf-8",
    )
    (assets / "main.css").write_text("body { color: #111; }", encoding="utf-8")
    (assets / "main.js").write_text("document.body.dataset.ready = 'true';", encoding="utf-8")

    assert validate_static_output(dist) == dist / "index.html"


def test_inline_static_output_embeds_relative_css_and_javascript(tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<link rel="stylesheet" href="./assets/main.css">'
        '<script type="module" src="./assets/main.js"></script>',
        encoding="utf-8",
    )
    (assets / "main.css").write_text("body { color: #111; }", encoding="utf-8")
    (assets / "main.js").write_text("document.body.dataset.ready = 'true';", encoding="utf-8")

    html = inline_static_output(dist)

    assert "./assets/" not in html
    assert "<style>" in html
    assert '<script type="module">' in html
    assert "fetch(" not in html


def test_inline_static_output_replaces_vite_stylesheet_link_with_attributes(tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<link rel="stylesheet" crossorigin href="./assets/index.css">',
        encoding="utf-8",
    )
    (assets / "index.css").write_text("body { color: #111; }", encoding="utf-8")

    html = inline_static_output(dist)

    assert "<style>\nbody { color: #111; }\n</style>" in html
    assert "<link" not in html


@pytest.mark.parametrize(
    "html",
    (
        '<script>fetch("./data.json")</script>',
        '<script src="/assets/main.js"></script>',
        '<script>const path = "portfolio.db"</script>',
    ),
)
def test_static_validator_rejects_runtime_or_unsafe_output(tmp_path, html):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(html, encoding="utf-8")

    with pytest.raises(StaticSiteError):
        validate_static_output(dist)


def test_generated_data_module_is_build_time_json_only(tmp_path):
    path = tmp_path / "src" / "generated" / "portfolio-data.ts"
    manifest = {
        "version": 1,
        "projects": [],
        "profile": {"summary": "</script><script>synthetic"},
        "skills": [],
        "links": [],
    }

    write_generated_data_module(path, manifest)

    content = path.read_text(encoding="utf-8")
    assert "export const portfolioData" in content
    assert "\\u003c/script\\u003e" in content
    assert "fetch(" not in content
