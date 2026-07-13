from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.models import BuildProfileRequest, RenderHtmlRequest
from portfolio_maker.application.render_html import HtmlRenderError, render_html
import portfolio_maker.application.render_html as render_html_module
from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def _setup_render_workspace(tmp_path: Path) -> tuple[Path, WorkspacePaths, Path]:
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    SQLiteRepository(paths.db_path).initialize()
    generated = workspace / "web" / "portfolio" / "src" / "generated"
    generated.mkdir(parents=True)
    generated_path = generated / "portfolio-data.ts"
    generated_path.write_text(
        'export const portfolioData = { version: 1, projects: [] } as const;\n',
        encoding="utf-8",
    )
    return workspace, paths, generated_path


def _setup_legacy_github_render_workspace(tmp_path: Path) -> tuple[Path, WorkspacePaths]:
    workspace = tmp_path / "legacy-workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    activity_url = "https://github.com/octo/demo/pull/9"
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity_url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    paths.ensure()
    with sqlite3.connect(paths.db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL,
                visibility TEXT NOT NULL,
                primary_source_id INTEGER
            );
            CREATE TABLE career_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_type TEXT NOT NULL,
                text TEXT NOT NULL,
                confidence TEXT NOT NULL,
                public_safe INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE evidence_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                snapshot_id INTEGER,
                kind TEXT NOT NULL,
                locator TEXT NOT NULL,
                quote_hash TEXT,
                summary TEXT NOT NULL,
                confidence TEXT NOT NULL
            );
            CREATE TABLE artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                path TEXT NOT NULL,
                source_profile_version TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            None,
            SourceType.GITHUB_REPOSITORY,
            "https://github.com/octo/demo",
            "octo/demo",
            "octo",
            SourceStatus.DISCOVERED,
        )
    )
    repository.insert_github_activity(
        GitHubActivity(
            None,
            source_id,
            "octo/demo",
            "pull_request",
            activity_url,
            "Legacy approved activity",
            "MERGED",
            "octo",
            "2026-05-01T00:00:00Z",
            None,
        )
    )
    generated = workspace / "web" / "portfolio" / "src" / "generated"
    generated.mkdir(parents=True)
    (generated / "portfolio-data.ts").write_text(
        'export const portfolioData = { version: 1, projects: [] } as const;\n',
        encoding="utf-8",
    )
    return workspace, paths


def test_render_html_success_publishes_without_mutating_template_or_draft(
    tmp_path,
    monkeypatch,
):
    workspace, paths, generated_path = _setup_render_workspace(tmp_path)
    template = generated_path.read_text(encoding="utf-8")
    paths.portfolio_draft_path.write_text("draft to preserve", encoding="utf-8")

    def fake_build(*args, **kwargs):
        dist = Path(kwargs["cwd"]) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            '<link rel="stylesheet" href="./assets/main.css">'
            '<script type="module" src="./assets/main.js"></script>',
            encoding="utf-8",
        )
        (dist / "assets" / "main.css").write_text("body{}", encoding="utf-8")
        (dist / "assets" / "main.js").write_text(
            "document.body.dataset.ready = 'true';",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(render_html_module.subprocess, "run", fake_build)

    result = render_html(RenderHtmlRequest(workspace=workspace))

    assert result.html_path.is_file()
    assert generated_path.read_text(encoding="utf-8") == template
    assert paths.portfolio_draft_path.read_text(encoding="utf-8") == "draft to preserve"
    assert "fetch(" not in result.html_path.read_text(encoding="utf-8")


def test_render_html_failure_removes_stale_html_but_preserves_draft_and_template(
    tmp_path,
    monkeypatch,
):
    workspace, paths, generated_path = _setup_render_workspace(tmp_path)
    template = generated_path.read_text(encoding="utf-8")
    paths.portfolio_draft_path.write_text("draft to preserve", encoding="utf-8")
    paths.portfolio_html_path.write_text(
        "<html>revoked public evidence</html>",
        encoding="utf-8",
    )

    def fail_build(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="synthetic build failure")

    monkeypatch.setattr(render_html_module.subprocess, "run", fail_build)

    with pytest.raises(HtmlRenderError, match="Sites build failed"):
        render_html(RenderHtmlRequest(workspace=workspace))

    assert not paths.portfolio_html_path.exists()
    assert paths.portfolio_draft_path.read_text(encoding="utf-8") == "draft to preserve"
    assert generated_path.read_text(encoding="utf-8") == template


def test_legacy_schema_approved_github_activity_reaches_profile_and_html(
    tmp_path,
    monkeypatch,
):
    workspace, paths = _setup_legacy_github_render_workspace(tmp_path)

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    def fake_build(*args, **kwargs):
        dist = Path(kwargs["cwd"]) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            '<script type="module" src="./assets/main.js"></script>',
            encoding="utf-8",
        )
        (dist / "assets" / "main.js").write_text(
            "document.body.textContent = 'legacy build';",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(render_html_module.subprocess, "run", fake_build)
    render_result = render_html(RenderHtmlRequest(workspace=workspace))

    assert profile_result.claim_count == 1
    assert render_result.html_path.is_file()
    manifest = json.loads(paths.portfolio_public_json_path.read_text(encoding="utf-8"))
    assert manifest["projects"][0]["timeline"][0]["title"] == "Legacy approved activity"
