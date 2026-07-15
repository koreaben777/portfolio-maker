from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.artifact_approval import write_sample_artifact_policy
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.discovery import discover_sources
from portfolio_maker.application.draft_portfolio import draft_portfolio
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DiscoverSourcesRequest,
    DraftPortfolioRequest,
    PublicPortfolioRequest,
    RenderHtmlRequest,
    ComposeProjectsRequest,
    PrepareProjectReviewRequest,
)
from portfolio_maker.application.project_composition import compose_projects, prepare_project_review
from portfolio_maker.application.public_portfolio import build_public_portfolio
from portfolio_maker.application.render_html import HtmlRenderError, render_html
import portfolio_maker.application.render_html as render_html_module
from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.github_connector import (
    GitHubActivityCandidate,
    GitHubDiscoveryResult,
    GitHubRepositoryCandidate,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.managed_files import write_managed_text
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


def _setup_multi_origin_render_workspace(
    tmp_path: Path,
    *,
    html_policy: dict[str, object] | None = None,
    path_like_label: bool = False,
    local_display_name: str | None = None,
) -> tuple[Path, WorkspacePaths, Path, Path, str, str, str]:
    workspace = tmp_path / "multi-origin"
    paths = WorkspacePaths.from_root(workspace)
    source_path = tmp_path / "local" / "notes.md"
    source_path.parent.mkdir()
    persisted_display_name = local_display_name or (
        "/synthetic/private/project" if path_like_label else "Local notes"
    )
    local_text = (
        f"{persisted_display_name}\nApproved local evidence.\n"
        if local_display_name is not None
        else "Local project\nApproved local evidence.\n"
    )
    source_path.write_text(local_text, encoding="utf-8")
    local_uri = source_path.resolve().as_uri()
    public_url = "https://github.com/octo/public/pull/1"
    private_url = "https://github.com/octo/private/pull/2"
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval.update(
        {
            "approved_source_uris": [local_uri],
            "allowed_repositories": ["octo/public", "octo/private"],
            "approved_github_activity_urls": [public_url],
            "private_sources_allowed": True,
            "approved_private_github_activity_urls": [private_url],
        }
    )
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    write_sample_artifact_policy(paths)
    if html_policy is not None:
        artifact_policy = json.loads(
            paths.artifact_approval_path.read_text(encoding="utf-8")
        )
        artifact_policy["artifacts"]["portfolio_html"] = html_policy
        paths.artifact_approval_path.write_text(
            json.dumps(artifact_policy), encoding="utf-8"
        )

    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    local_id = repository.upsert_source(
        Source(
            None,
            SourceType.LOCAL_FILE,
            local_uri,
            persisted_display_name,
            None,
            SourceStatus.INGESTED,
        )
    )
    extracted = extract_text(source_path)
    snapshot_path = paths.local_snapshots_dir / f"source-{local_id}-{extracted.content_hash}.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "source_id": local_id,
                "source_uri": local_uri,
                "display_name": source_path.name,
                "content_hash": extracted.content_hash,
                "extractor": extracted.extractor,
                "extracted_at": "2026-07-13T00:00:00Z",
                "text": local_text,
            }
        ),
        encoding="utf-8",
    )
    repository.insert_source_snapshot(
        local_id, snapshot_path, extracted.content_hash, extracted.extractor
    )
    public_source_id = repository.upsert_source(
        Source(
            None,
            SourceType.GITHUB_REPOSITORY,
            "https://github.com/octo/public",
            "octo/public",
            "octo",
            SourceStatus.DISCOVERED,
            "public_github",
            "public",
        )
    )
    private_source_id = repository.upsert_source(
        Source(
            None,
            SourceType.GITHUB_REPOSITORY,
            "https://github.com/octo/private",
            "octo/private",
            "octo",
            SourceStatus.DISCOVERED,
            "private_github",
            "private",
        )
    )
    repository.insert_github_activity(
        GitHubActivity(
            None,
            public_source_id,
            "octo/public",
            "pull_request",
            public_url,
            "Public activity",
            "OPEN",
            "octo",
            "2026-01-01T00:00:00Z",
            None,
        )
    )
    repository.insert_github_activity(
        GitHubActivity(
            None,
            private_source_id,
            "octo/private",
            "pull_request",
            private_url,
            "Private activity",
            "MERGED",
            "octo",
            "2026-01-02T00:00:00Z",
            None,
            True,
        )
    )
    generated = workspace / "web" / "portfolio" / "src" / "generated"
    generated.mkdir(parents=True)
    (generated / "portfolio-data.ts").write_text(
        'export const portfolioData = { version: 1, projects: [] } as const;\n',
        encoding="utf-8",
    )
    return (
        workspace,
        paths,
        source_path,
        snapshot_path,
        local_uri,
        public_url,
        private_url,
    )


def _fake_build_with_generated_data(*args, **kwargs):
    temp_site = Path(kwargs["cwd"])
    dist = temp_site / "dist"
    (dist / "assets").mkdir(parents=True)
    generated = temp_site / "src" / "generated" / "portfolio-data.ts"
    (dist / "index.html").write_text(
        '<script type="module" src="./assets/main.js"></script>\n'
        + "<!-- generated data -->\n"
        + generated.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (dist / "assets" / "main.js").write_text(
        "document.body.dataset.ready = 'true';", encoding="utf-8"
    )
    return subprocess.CompletedProcess(args[0], 0)


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
    assert not hasattr(result, "dist_path")
    assert generated_path.read_text(encoding="utf-8") == template
    assert paths.portfolio_draft_path.read_text(encoding="utf-8") == "draft to preserve"
    assert "fetch(" not in result.html_path.read_text(encoding="utf-8")


def test_render_html_projects_use_one_approved_multi_origin_semantic_project(
    tmp_path,
    monkeypatch,
):
    workspace, paths, _, _, local_uri, public_url, private_url = _setup_multi_origin_render_workspace(tmp_path)
    review = prepare_project_review(PrepareProjectReviewRequest(workspace=workspace))
    review_payload = json.loads(review.input_path.read_text(encoding="utf-8"))
    evidence_ids = [item["evidence_id"] for item in review_payload["evidence"]]
    evidence_by_origin = {
        item["origin"]: item["evidence_id"] for item in review_payload["evidence"]
    }
    write_managed_text(
        paths.project_approval_path,
        json.dumps(
            {
                "version": 1,
                "review_input_sha256": review_payload["input_sha256"],
                "projects": [
                    {
                        "id": "multi-origin-project",
                        "title": "Multi-origin project",
                        "overview": "A reviewed project with local and GitHub evidence",
                        "evidence_ids": evidence_ids,
                        "status": "approved",
                    }
                ],
                "rejected_candidate_ids": [],
                "unassigned_evidence_ids": [],
            },
            indent=2,
        )
        + "\n",
    )
    compose_projects(ComposeProjectsRequest(workspace=workspace))
    artifact_policy = json.loads(paths.artifact_approval_path.read_text(encoding="utf-8"))
    artifact_policy["artifacts"]["master_profile"] = {
        "delivery_scope": "restricted",
        "include_local": False,
        "include_public_github": False,
        "include_private_github": False,
    }
    artifact_policy["artifacts"]["portfolio_html"] = {
        "delivery_scope": "open_public",
        "include_local": False,
        "include_public_github": True,
        "include_private_github": False,
    }
    paths.artifact_approval_path.write_text(json.dumps(artifact_policy), encoding="utf-8")
    monkeypatch.setattr(render_html_module.subprocess, "run", _fake_build_with_generated_data)

    render_html(RenderHtmlRequest(workspace=workspace))

    manifest_text = paths.portfolio_public_json_path.read_text(encoding="utf-8")
    html_text = paths.portfolio_html_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    profile = json.loads(paths.master_profile_json_path.read_text(encoding="utf-8"))
    assert profile["projects"] == []
    assert [project["id"] for project in manifest["projects"]] == ["multi-origin-project"]
    assert manifest["projects"][0]["title"] == "Multi-origin project"
    assert len(manifest["projects"][0]["timeline"]) == 3
    assert "Multi-origin project" in html_text
    assert "Public activity" in html_text
    assert "Private activity" not in html_text
    assert "Local notes" not in html_text
    with SQLiteRepository(paths.db_path)._read_connection() as connection:
        html_artifact = connection.execute(
            "SELECT input_manifest FROM artifacts WHERE kind = 'portfolio_html' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    html_manifest = json.loads(html_artifact["input_manifest"])
    assert html_manifest["included_evidence_ids"] == [evidence_by_origin["public_github"]]
    assert html_manifest["portfolio_project_ids"] == ["multi-origin-project"]
    assert html_manifest["project_approval_sha256"]
    assert html_manifest["project_review_input_sha256"] == review_payload["input_sha256"]
    assert len(html_manifest["manifest_sha256"]) == 64
    assert local_uri not in manifest_text + html_text
    assert private_url not in manifest_text + html_text
    assert public_url in manifest_text + html_text


def test_unapproved_private_activity_stays_out_of_all_review_and_artifacts(
    tmp_path,
    monkeypatch,
):
    workspace, paths, _, _, local_uri, public_url, private_url = (
        _setup_multi_origin_render_workspace(tmp_path)
    )
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_private_github_activity_urls"] = []
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    profile = build_profile(BuildProfileRequest(workspace=workspace))
    draft = draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    manifest = build_public_portfolio(PublicPortfolioRequest(workspace=workspace))
    review = prepare_project_review(PrepareProjectReviewRequest(workspace=workspace))
    monkeypatch.setattr(render_html_module.subprocess, "run", _fake_build_with_generated_data)
    render_html(RenderHtmlRequest(workspace=workspace))

    output_text = "\n".join(
        (
            profile.json_path.read_text(encoding="utf-8"),
            profile.markdown_path.read_text(encoding="utf-8"),
            draft.markdown_path.read_text(encoding="utf-8"),
            manifest.manifest_path.read_text(encoding="utf-8"),
            review.input_path.read_text(encoding="utf-8"),
            paths.portfolio_html_path.read_text(encoding="utf-8"),
        )
    )
    assert private_url not in output_text
    assert "Private activity" not in output_text
    assert local_uri not in output_text
    assert public_url in output_text


def test_stale_private_activity_stays_out_after_repository_reinitialization(
    tmp_path,
    monkeypatch,
):
    workspace, paths, _, _, local_uri, public_url, private_url = (
        _setup_multi_origin_render_workspace(tmp_path)
    )
    repository = SQLiteRepository(paths.db_path)
    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 3
    repository.invalidate_unobserved_github_activities(())
    repository.initialize()

    draft = draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    manifest = build_public_portfolio(PublicPortfolioRequest(workspace=workspace))
    review = prepare_project_review(PrepareProjectReviewRequest(workspace=workspace))
    monkeypatch.setattr(render_html_module.subprocess, "run", _fake_build_with_generated_data)
    render_html(RenderHtmlRequest(workspace=workspace))

    output_text = "\n".join(
        (
            paths.master_profile_json_path.read_text(encoding="utf-8"),
            draft.markdown_path.read_text(encoding="utf-8"),
            manifest.manifest_path.read_text(encoding="utf-8"),
            review.input_path.read_text(encoding="utf-8"),
            paths.portfolio_html_path.read_text(encoding="utf-8"),
        )
    )
    assert private_url not in output_text
    assert "Private activity" not in output_text
    assert local_uri not in output_text
    assert public_url not in output_text


def test_partial_private_rediscovery_revokes_completed_empty_endpoint_everywhere(
    tmp_path,
    monkeypatch,
):
    workspace, paths, _ = _setup_render_workspace(tmp_path)
    write_sample_artifact_policy(paths)
    activity = GitHubActivityCandidate(
        repo="octo/private",
        activity_type="pull_request",
        url="https://github.com/octo/private/pull/2",
        title="Private activity",
        state="MERGED",
        author="octo",
        created_at="2026-01-01T00:00:00Z",
        merged_at="2026-01-02T00:00:00Z",
    )
    private_repo = GitHubRepositoryCandidate(
        "octo/private", "https://github.com/octo/private", True
    )
    responses = [
        GitHubDiscoveryResult(
            [private_repo],
            [activity],
            [],
            (("octo/private", "pull_request"),),
        ),
        GitHubDiscoveryResult(
            [private_repo],
            [],
            ["GitHub workflow runs discovery failed for octo/private"],
            (("octo/private", "pull_request"),),
        ),
    ]
    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        lambda **kwargs: responses.pop(0),
    )
    request = DiscoverSourcesRequest(
        workspace=workspace,
        home=tmp_path,
        include_github=True,
    )
    discover_sources(request)

    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval.update(
        {
            "private_sources_allowed": True,
            "allowed_repositories": ["octo/private"],
            "approved_private_github_activity_urls": [activity.url],
        }
    )
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 1

    discover_sources(request)

    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 0
    draft = draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    manifest = build_public_portfolio(PublicPortfolioRequest(workspace=workspace))
    review = prepare_project_review(PrepareProjectReviewRequest(workspace=workspace))
    monkeypatch.setattr(render_html_module.subprocess, "run", _fake_build_with_generated_data)
    render_html(RenderHtmlRequest(workspace=workspace))
    output_text = "\n".join(
        (
            paths.master_profile_json_path.read_text(encoding="utf-8"),
            paths.master_profile_md_path.read_text(encoding="utf-8"),
            draft.markdown_path.read_text(encoding="utf-8"),
            manifest.manifest_path.read_text(encoding="utf-8"),
            review.input_path.read_text(encoding="utf-8"),
            paths.portfolio_html_path.read_text(encoding="utf-8"),
        )
    )
    assert activity.title not in output_text
    assert activity.url not in output_text


def test_restricted_approved_private_repository_name_is_display_text_only(
    tmp_path,
    monkeypatch,
):
    workspace, paths, _, _, _, _, private_url = _setup_multi_origin_render_workspace(tmp_path)
    review = prepare_project_review(PrepareProjectReviewRequest(workspace=workspace))
    review_payload = json.loads(review.input_path.read_text(encoding="utf-8"))
    write_managed_text(
        paths.project_approval_path,
        json.dumps(
            {
                "version": 1,
                "review_input_sha256": review_payload["input_sha256"],
                "projects": [
                    {
                        "id": "private-display-project",
                        "title": "octo/private-project",
                        "overview": "User-approved work for octo/private-project",
                        "evidence_ids": [
                            item["evidence_id"] for item in review_payload["evidence"]
                        ],
                        "status": "approved",
                    }
                ],
                "rejected_candidate_ids": [],
                "unassigned_evidence_ids": [],
            },
            indent=2,
        )
        + "\n",
    )
    compose_projects(ComposeProjectsRequest(workspace=workspace))
    profile = build_profile(BuildProfileRequest(workspace=workspace))
    draft = draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    manifest = build_public_portfolio(PublicPortfolioRequest(workspace=workspace))
    monkeypatch.setattr(render_html_module.subprocess, "run", _fake_build_with_generated_data)
    render_html(RenderHtmlRequest(workspace=workspace))

    output_text = "\n".join(
        (
            profile.json_path.read_text(encoding="utf-8"),
            profile.markdown_path.read_text(encoding="utf-8"),
            draft.markdown_path.read_text(encoding="utf-8"),
            manifest.manifest_path.read_text(encoding="utf-8"),
            paths.portfolio_html_path.read_text(encoding="utf-8"),
        )
    )
    assert "octo/private-project" in output_text
    assert private_url not in output_text


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


def test_render_html_missing_sites_invalidates_existing_canonical_html(tmp_path):
    workspace = tmp_path / "missing-sites"
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.portfolio_html_path.write_text("stale public HTML", encoding="utf-8")

    with pytest.raises(HtmlRenderError, match="Sites project missing"):
        render_html(RenderHtmlRequest(workspace=workspace))

    assert not paths.portfolio_html_path.exists()


def test_render_html_unsafe_canonical_target_is_controlled(tmp_path):
    workspace = tmp_path / "unsafe-output"
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.portfolio_html_path.mkdir()

    with pytest.raises(HtmlRenderError, match="could not be invalidated"):
        render_html(RenderHtmlRequest(workspace=workspace))

    assert paths.portfolio_html_path.is_dir()


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
    assert manifest["projects"] == []


def test_render_html_uses_its_own_policy_without_overwriting_manifest_policy(
    tmp_path,
    monkeypatch,
):
    workspace, paths, _, _, local_uri, public_url, private_url = (
        _setup_multi_origin_render_workspace(
            tmp_path,
            html_policy={
                "delivery_scope": "open_public",
                "include_local": False,
                "include_public_github": True,
                "include_private_github": False,
            },
        )
    )
    monkeypatch.setattr(
        render_html_module.subprocess, "run", _fake_build_with_generated_data
    )

    render_html(RenderHtmlRequest(workspace=workspace))

    public_manifest = json.loads(
        paths.portfolio_public_json_path.read_text(encoding="utf-8")
    )
    html_text = paths.portfolio_html_path.read_text(encoding="utf-8")
    with SQLiteRepository(paths.db_path)._read_connection() as connection:
        records = connection.execute(
            "SELECT kind, input_manifest FROM artifacts "
            "WHERE kind IN ('portfolio_public', 'portfolio_html') ORDER BY id"
        ).fetchall()
        evidence_rows = connection.execute(
            "SELECT evidence_items.id, github_activities.url "
            "FROM evidence_items JOIN github_activities "
            "ON github_activities.id = evidence_items.github_activity_id"
        ).fetchall()
    manifests = {row["kind"]: json.loads(row["input_manifest"]) for row in records}
    activity_evidence = {row["url"]: row["id"] for row in evidence_rows}

    assert public_manifest["delivery_scope"] == "restricted"
    assert manifests["portfolio_public"]["delivery_scope"] == "restricted"
    assert manifests["portfolio_html"]["artifact_kind"] == "portfolio_html"
    assert manifests["portfolio_html"]["delivery_scope"] == "open_public"
    assert len(manifests["portfolio_html"]["manifest_sha256"]) == 64
    assert manifests["portfolio_html"]["manifest_sha256"] != hashlib.sha256(
        paths.portfolio_public_json_path.read_bytes()
    ).hexdigest()
    assert manifests["portfolio_html"]["included_evidence_ids"] == []
    assert manifests["portfolio_html"]["policy_hash"]
    assert "Public activity" not in html_text
    assert "Private activity" not in html_text
    assert private_url not in html_text
    assert local_uri not in html_text


def test_artifact_policies_share_approved_pool_but_select_independently(
    tmp_path,
    monkeypatch,
):
    workspace, paths, _, _, local_uri, _, private_url = (
        _setup_multi_origin_render_workspace(tmp_path)
    )
    artifact_policy = json.loads(
        paths.artifact_approval_path.read_text(encoding="utf-8")
    )
    artifact_policy["artifacts"]["master_profile"] = {
        "delivery_scope": "restricted",
        "include_local": False,
        "include_public_github": False,
        "include_private_github": False,
    }
    paths.artifact_approval_path.write_text(
        json.dumps(artifact_policy), encoding="utf-8"
    )
    monkeypatch.setattr(
        render_html_module.subprocess, "run", _fake_build_with_generated_data
    )

    render_html(RenderHtmlRequest(workspace=workspace))

    profile = json.loads(paths.master_profile_json_path.read_text(encoding="utf-8"))
    public_manifest = json.loads(paths.portfolio_public_json_path.read_text(encoding="utf-8"))
    html_text = paths.portfolio_html_path.read_text(encoding="utf-8")
    with SQLiteRepository(paths.db_path)._read_connection() as connection:
        artifact_rows = connection.execute(
            "SELECT kind, input_manifest FROM artifacts "
            "WHERE kind IN ('portfolio_public', 'portfolio_html') ORDER BY id"
        ).fetchall()
        evidence_rows = connection.execute(
            "SELECT id FROM evidence_items ORDER BY id"
        ).fetchall()
    artifact_manifests = {
        row["kind"]: json.loads(row["input_manifest"]) for row in artifact_rows
    }
    expected_evidence_ids = {row["id"] for row in evidence_rows}

    assert profile["sources"] == []
    assert profile["claims"] == []
    assert len(expected_evidence_ids) == 3
    assert set(public_manifest["selection"]["included_evidence_ids"]) == set()
    assert set(artifact_manifests["portfolio_public"]["included_evidence_ids"]) == set()
    assert set(artifact_manifests["portfolio_html"]["included_evidence_ids"]) == set()
    assert local_uri not in html_text
    assert private_url not in html_text


def test_excluded_v2_project_is_absent_until_reincluded_and_html_uses_selected_evidence_only(
    tmp_path,
    monkeypatch,
):
    workspace, paths, _, _, _, public_url, _ = _setup_multi_origin_render_workspace(tmp_path)
    build_profile(BuildProfileRequest(workspace=workspace))
    repository = SQLiteRepository(paths.db_path)
    evidence_rows = repository.list_evidence_selection_records()
    evidence_ids = tuple(record.evidence_id for record in evidence_rows)
    public_evidence_id = next(
        record.evidence_id for record in evidence_rows if record.activity_url == public_url
    )
    repository.replace_portfolio_project_decisions(
        (
            {
                "id": "insurance-rag",
                "title": "Insurance RAG",
                "overview": "Grounded project evidence.",
                "evidence_ids": evidence_ids,
                "approval_sha256": "a" * 64,
                "review_input_sha256": "b" * 64,
                "decision_status": "auto_included_medium",
                "decision_origin": "automatic",
                "confidence": "medium",
                "boundary_fingerprint": "boundary-v2",
                "lineage_project_ids": (),
            },
        ),
        candidate_input_sha256="c" * 64,
        index_revision="revision-v2",
    )
    policy = json.loads(paths.artifact_approval_path.read_text(encoding="utf-8"))
    policy["artifacts"]["portfolio_html"] = {
        "delivery_scope": "open_public",
        "include_local": False,
        "include_public_github": True,
        "include_private_github": False,
    }
    paths.artifact_approval_path.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(render_html_module.subprocess, "run", _fake_build_with_generated_data)

    repository.set_project_decision_state("insurance-rag", "excluded")
    assert json.loads(build_profile(BuildProfileRequest(workspace=workspace)).json_path.read_text(encoding="utf-8"))["projects"] == []
    assert draft_portfolio(DraftPortfolioRequest(workspace=workspace)).project_count == 0
    assert build_public_portfolio(PublicPortfolioRequest(workspace=workspace)).project_count == 0
    render_html(RenderHtmlRequest(workspace=workspace))
    assert json.loads(paths.portfolio_public_json_path.read_text(encoding="utf-8"))["projects"] == []
    assert "Insurance RAG" not in paths.portfolio_html_path.read_text(encoding="utf-8")

    repository.set_project_decision_state("insurance-rag", "included")
    profile = json.loads(build_profile(BuildProfileRequest(workspace=workspace)).json_path.read_text(encoding="utf-8"))
    draft = draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    manifest = build_public_portfolio(PublicPortfolioRequest(workspace=workspace))
    render_html(RenderHtmlRequest(workspace=workspace))
    html_text = paths.portfolio_html_path.read_text(encoding="utf-8")
    with SQLiteRepository(paths.db_path)._read_connection() as connection:
        html_artifact = connection.execute(
            "SELECT input_manifest FROM artifacts "
            "WHERE kind = 'portfolio_html' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    html_manifest = json.loads(html_artifact["input_manifest"])

    assert [project["id"] for project in profile["projects"]] == ["insurance-rag"]
    assert draft.project_count == 1
    assert json.loads(manifest.manifest_path.read_text(encoding="utf-8"))["projects"][0]["id"] == "insurance-rag"
    assert public_evidence_id in html_manifest["included_evidence_ids"]
    assert len(html_manifest["included_evidence_ids"]) == 1
    assert "auto_included_medium" not in html_text
    assert '"confidence"' not in html_text
    assert '"decision_origin"' not in html_text


def test_draft_uses_its_own_selection_when_master_excludes_all_origins(
    tmp_path,
):
    workspace, paths, source_path, snapshot_path, local_uri, _, private_url = (
        _setup_multi_origin_render_workspace(tmp_path)
    )
    artifact_policy = json.loads(
        paths.artifact_approval_path.read_text(encoding="utf-8")
    )
    artifact_policy["artifacts"]["master_profile"] = {
        "delivery_scope": "restricted",
        "include_local": False,
        "include_public_github": False,
        "include_private_github": False,
    }
    paths.artifact_approval_path.write_text(
        json.dumps(artifact_policy), encoding="utf-8"
    )

    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    profile = json.loads(paths.master_profile_json_path.read_text(encoding="utf-8"))
    draft_text = paths.portfolio_draft_path.read_text(encoding="utf-8")
    with SQLiteRepository(paths.db_path)._read_connection() as connection:
        artifact = connection.execute(
            "SELECT input_manifest FROM artifacts "
            "WHERE kind = 'portfolio_draft' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        evidence_rows = connection.execute(
            "SELECT id, source_id FROM evidence_items ORDER BY id"
        ).fetchall()
        claim_rows = connection.execute(
            "SELECT id FROM career_claims ORDER BY id"
        ).fetchall()
    input_manifest = json.loads(artifact["input_manifest"])
    expected_source_ids = {row["source_id"] for row in evidence_rows}
    expected_evidence_ids = {row["id"] for row in evidence_rows}
    expected_claim_ids = {row["id"] for row in claim_rows}

    assert profile["sources"] == []
    assert profile["claims"] == []
    assert len(expected_source_ids) == 3
    assert len(expected_evidence_ids) == 3
    assert len(expected_claim_ids) == 3
    assert set(input_manifest["included_source_ids"]) == set()
    assert set(input_manifest["included_evidence_ids"]) == set()
    assert set(input_manifest["included_claim_ids"]) == set()
    assert "No approved portfolio projects" in draft_text
    assert local_uri not in draft_text
    assert str(source_path) not in draft_text
    assert str(snapshot_path) not in draft_text
    assert private_url not in draft_text


@pytest.mark.parametrize(
    "persisted_label",
    ("/synthetic/private/project", "sk-synthetic-local-token"),
)
def test_restricted_artifacts_hide_local_uri_snapshot_and_path_like_label(
    tmp_path,
    monkeypatch,
    persisted_label,
):
    workspace, paths, source_path, snapshot_path, local_uri, _, private_url = (
        _setup_multi_origin_render_workspace(
            tmp_path, local_display_name=persisted_label
        )
    )
    monkeypatch.setattr(
        render_html_module.subprocess, "run", _fake_build_with_generated_data
    )

    build_profile(BuildProfileRequest(workspace=workspace))
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    build_public_portfolio(PublicPortfolioRequest(workspace=workspace))
    render_html(RenderHtmlRequest(workspace=workspace))

    artifact_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            paths.master_profile_json_path,
            paths.master_profile_md_path,
            paths.portfolio_draft_path,
            paths.portfolio_public_json_path,
            paths.portfolio_html_path,
        )
    )
    assert local_uri not in artifact_text
    assert str(source_path) not in artifact_text
    assert str(snapshot_path) not in artifact_text
    assert persisted_label not in artifact_text
    assert private_url not in artifact_text
