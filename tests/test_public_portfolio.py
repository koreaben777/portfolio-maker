from __future__ import annotations

import json
from pathlib import Path
import subprocess

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.models import PublicPortfolioRequest, RenderHtmlRequest
from portfolio_maker.application.public_portfolio import build_public_portfolio
from portfolio_maker.application.render_html import render_html
import portfolio_maker.application.render_html as render_html_module
from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def _setup_workspace(tmp_path, approved_urls):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = list(approved_urls)
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
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
    return workspace, paths, repository, source_id


def test_build_public_portfolio_writes_safe_manifest_and_timeline(tmp_path):
    urls = (
        "https://github.com/octo/demo/pull/1",
        "https://github.com/octo/demo/issues/2",
    )
    workspace, paths, repository, source_id = _setup_workspace(tmp_path, urls)
    repository.insert_github_activity(
        GitHubActivity(
            None,
            source_id,
            "octo/demo",
            "pull_request",
            urls[0],
            "Review the evidence trail",
            "MERGED",
            "octo",
            "2026-01-01T00:00:00Z",
            "2026-01-02T00:00:00Z",
        )
    )
    repository.insert_github_activity(
        GitHubActivity(
            None,
            source_id,
            "octo/demo",
            "issue",
            urls[1],
            "Document the public boundary",
            "OPEN",
            "octo",
            "2026-02-01T00:00:00Z",
            None,
        )
    )

    result = build_public_portfolio(PublicPortfolioRequest(workspace=workspace))

    manifest = json.loads(paths.portfolio_public_json_path.read_text(encoding="utf-8"))
    assert result.manifest_path == paths.portfolio_public_json_path
    assert result.project_count == 0
    assert result.claim_count == 0
    assert result.evidence_count == 0
    assert manifest["version"] == 1
    assert manifest["projects"] == []
    assert manifest["skills"] == []
    serialized = paths.portfolio_public_json_path.read_text(encoding="utf-8")
    assert "portfolio.db" not in serialized
    assert ".portfolio-maker" not in serialized
    assert "/private/" not in serialized

    with repository._read_connection() as conn:
        record = conn.execute(
            "SELECT input_manifest FROM artifacts WHERE kind = 'portfolio_public' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    input_manifest = json.loads(record["input_manifest"])
    assert input_manifest["claim_ids"] == []
    assert input_manifest["evidence_ids"] == []


def test_build_public_portfolio_preserves_workflow_state_provenance(tmp_path):
    url = "https://github.com/octo/demo/actions/runs/123"
    workspace, paths, repository, source_id = _setup_workspace(tmp_path, (url,))
    repository.insert_github_activity(
        GitHubActivity(
            None,
            source_id,
            "octo/demo",
            "workflow_run",
            url,
            "Release workflow",
            "queued",
            "octo",
            "2026-03-01T00:00:00Z",
            None,
            state_field="status",
        )
    )

    build_public_portfolio(PublicPortfolioRequest(workspace=workspace))

    manifest = json.loads(paths.portfolio_public_json_path.read_text(encoding="utf-8"))
    assert manifest["projects"] == []


def test_build_public_portfolio_excludes_private_and_unapproved_activity(tmp_path):
    approved_url = "https://github.com/octo/demo/pull/1"
    private_url = "https://github.com/octo/demo/issues/2"
    unapproved_url = "https://github.com/octo/demo/pull/3"
    workspace, paths, repository, source_id = _setup_workspace(tmp_path, (approved_url, private_url))
    for url, activity_type, title, is_private in (
        (approved_url, "pull_request", "Approved public work", False),
        (private_url, "issue", "Private work", True),
        (unapproved_url, "pull_request", "Unapproved work", False),
    ):
        repository.insert_github_activity(
            GitHubActivity(
                None,
                source_id,
                "octo/demo",
                activity_type,
                url,
                title,
                "OPEN",
                "octo",
                "2026-01-01T00:00:00Z",
                None,
                is_private=is_private,
            )
        )

    build_public_portfolio(PublicPortfolioRequest(workspace=workspace))

    manifest = json.loads(paths.portfolio_public_json_path.read_text(encoding="utf-8"))
    assert manifest["projects"] == []
    assert "Private work" not in paths.portfolio_public_json_path.read_text(encoding="utf-8")
    assert "Unapproved work" not in paths.portfolio_public_json_path.read_text(encoding="utf-8")


def test_build_public_portfolio_empty_manifest_is_explicit(tmp_path):
    workspace, paths, repository, _ = _setup_workspace(tmp_path, ())

    result = build_public_portfolio(PublicPortfolioRequest(workspace=workspace))

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert result.project_count == 0
    assert result.claim_count == 0
    assert result.evidence_count == 0
    assert manifest["projects"] == []
    assert manifest["skills"] == []
    assert manifest["profile"] == {}


def test_build_public_portfolio_excludes_legacy_public_safe_local_path_claim(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = ["file:///Users/june/private/project.md"]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            None,
            SourceType.LOCAL_FILE,
            "file:///Users/june/private/project.md",
            "project.md",
            None,
            SourceStatus.DISCOVERED,
        )
    )
    project_id = repository.upsert_project("local:legacy", public_safe=True)
    evidence_id = repository.upsert_evidence_item(
        source_id=source_id,
        snapshot_id=None,
        github_activity_id=None,
        locator="file:///Users/june/private/project.md",
        stable_id="legacy-local-path-evidence",
        content_hash=None,
        public_safe=True,
    )
    claim_id = repository.upsert_career_claim(
        project_id,
        "/Users/june/private/project.md: shipped an outcome",
        public_safe=True,
    )
    repository.link_claim_evidence(claim_id, evidence_id, "direct")

    result = build_public_portfolio(PublicPortfolioRequest(workspace=workspace))

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["projects"] == []
    assert "/Users/june/private/project.md" not in result.manifest_path.read_text(
        encoding="utf-8"
    )

    generated = workspace / "web" / "portfolio" / "src" / "generated"
    generated.mkdir(parents=True)
    (generated / "portfolio-data.ts").write_text(
        'export const portfolioData = { version: 1, projects: [] } as const;\n',
        encoding="utf-8",
    )

    def fake_build(*args, **kwargs):
        dist = Path(kwargs["cwd"]) / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text(
            '<script type="module" src="./assets/main.js"></script>',
            encoding="utf-8",
        )
        (dist / "assets" / "main.js").write_text(
            "document.body.textContent = 'empty';",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args[0], 0)

    monkeypatch.setattr(render_html_module.subprocess, "run", fake_build)
    html_result = render_html(RenderHtmlRequest(workspace=workspace))
    assert "/Users/june/private/project.md" not in html_result.html_path.read_text(
        encoding="utf-8"
    )
