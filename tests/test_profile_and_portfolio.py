import json

import portfolio_maker.application.draft_portfolio as draft_portfolio_module
from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.draft_portfolio import draft_portfolio
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DraftPortfolioRequest,
    IngestSourcesRequest,
)
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def _ingest_approved_source(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "private" / "notes.md"
    source_path.parent.mkdir()
    source_path.write_text("private evidence", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="notes.md",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 1
    return workspace, source_path, paths


def test_build_profile_treats_github_sources_as_discovery_only_in_mvp(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.GITHUB_REPOSITORY,
            uri="https://github.com/octo/demo",
            display_name="octo/demo",
            owner="octo",
            status=SourceStatus.APPROVED,
        )
    )

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    profile = json.loads(profile_result.json_path.read_text(encoding="utf-8"))
    assert profile_result.claim_count == 0
    assert profile == {"version": 1, "sources": [], "claims": []}


def test_build_profile_and_draft_portfolio_from_ingested_source(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    source_path = tmp_path / "project" / "README.md"
    source_path.parent.mkdir()
    source_path.write_text(
        "# Portfolio Maker\nBuilt an approval-gated evidence pipeline.",
        encoding="utf-8",
    )
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="Portfolio Maker",
            owner=None,
            status=SourceStatus.INGESTED,
        )
    )
    content_hash = extract_text(source_path).content_hash
    snapshot_path = paths.local_snapshots_dir / f"source-{source_id}-{content_hash}.json"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "source_uri": source_path.resolve().as_uri(),
                "display_name": "README.md",
                "content_hash": content_hash,
                "extractor": "text-v2",
                "extracted_at": "2026-07-09T00:00:00Z",
                "text": "# Portfolio Maker\nBuilt an approval-gated evidence pipeline.",
            }
        ),
        encoding="utf-8",
    )
    repository.insert_source_snapshot(source_id, snapshot_path, content_hash, "text-v2")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.json_path == paths.master_profile_json_path
    assert profile_result.markdown_path == paths.master_profile_md_path
    assert profile_result.claim_count == 1
    profile = json.loads(paths.master_profile_json_path.read_text(encoding="utf-8"))
    assert profile["version"] == 1
    assert profile["sources"][0] == {
        "id": source_id,
        "type": "local_file",
        "uri": source_path.resolve().as_uri(),
        "display_name": "Portfolio Maker",
        "owner": None,
        "status": "ingested",
    }
    assert profile["claims"] == [
        {
            "claim_type": "project_evidence",
            "text": "Portfolio Maker: Built an approval-gated evidence pipeline.",
            "confidence": "medium",
            "public_safe": False,
            "evidence_uri": source_path.resolve().as_uri(),
            "evidence_snapshot": str(snapshot_path),
        }
    ]
    profile_markdown = paths.master_profile_md_path.read_text(encoding="utf-8")
    assert "## Sources" in profile_markdown
    assert "## Claims" in profile_markdown
    assert "Portfolio Maker" in profile_markdown
    assert "Built an approval-gated evidence pipeline." in profile_markdown

    portfolio_result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert portfolio_result.markdown_path == paths.portfolio_draft_path
    assert portfolio_result.project_count == 1
    draft = paths.portfolio_draft_path.read_text(encoding="utf-8")
    assert "Portfolio Maker" in draft
    assert "- Role: Evidence review required" in draft
    assert "- Technical approach: Evidence review required" in draft
    assert "- Outcome: Evidence review required" in draft
    assert "Internal evidence reference: `Portfolio Maker`" in draft
    assert str(source_path) not in draft


def test_build_profile_excludes_ingested_source_after_approval_revoked(tmp_path):
    workspace, source_path, paths = _ingest_approved_source(tmp_path)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = []
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))
    portfolio_result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert json.loads(profile_result.json_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "sources": [],
        "claims": [],
    }
    assert source_path.name not in profile_result.markdown_path.read_text(encoding="utf-8")
    assert portfolio_result.project_count == 0
    assert source_path.name not in portfolio_result.markdown_path.read_text(encoding="utf-8")


def test_build_profile_excludes_ingested_source_under_new_forbidden_path(tmp_path):
    workspace, source_path, paths = _ingest_approved_source(tmp_path)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["forbidden_paths"] = [str(source_path.parent)]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))
    portfolio_result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert json.loads(profile_result.json_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "sources": [],
        "claims": [],
    }
    assert source_path.name not in profile_result.markdown_path.read_text(encoding="utf-8")
    assert portfolio_result.project_count == 0
    assert source_path.name not in portfolio_result.markdown_path.read_text(encoding="utf-8")


def test_draft_portfolio_rebuilds_profile_after_approval_revoked(tmp_path):
    workspace, source_path, paths = _ingest_approved_source(tmp_path)
    build_profile(BuildProfileRequest(workspace=workspace))
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = []
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    portfolio_result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert portfolio_result.project_count == 0
    assert source_path.name not in portfolio_result.markdown_path.read_text(encoding="utf-8")


def test_build_profile_marks_changed_source_stale_and_requires_reingestion(tmp_path):
    workspace, source_path, paths = _ingest_approved_source(tmp_path)
    source_path.write_text("changed evidence", encoding="utf-8")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    repository = SQLiteRepository(paths.db_path)
    assert profile_result.claim_count == 0
    assert repository.list_sources()[0].status == SourceStatus.STALE_SOURCE


def test_build_profile_marks_missing_snapshot_stale_without_fallback_claim(tmp_path):
    workspace, _, paths = _ingest_approved_source(tmp_path)
    repository = SQLiteRepository(paths.db_path)
    snapshot_path = repository.latest_snapshots_by_source_id()[repository.list_sources()[0].id]
    snapshot_path.unlink()

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert repository.list_sources()[0].status == SourceStatus.STALE_SOURCE


def test_relative_forbidden_path_is_anchored_to_workspace_for_profile(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source_path = workspace / "private" / "notes.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("private evidence", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="notes.md",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )
    ingest_sources(IngestSourcesRequest(workspace=workspace))
    approval["forbidden_paths"] = ["private"]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.claim_count == 0


def test_build_profile_rejects_legacy_or_tampered_snapshot_text(tmp_path):
    workspace, _, paths = _ingest_approved_source(tmp_path)
    repository = SQLiteRepository(paths.db_path)
    source = repository.list_sources()[0]
    snapshot_path = repository.latest_snapshots_by_source_id()[source.id]
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["extractor"] = "text-v1"
    payload["text"] = "fabricated synthetic evidence"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert repository.list_sources()[0].status == SourceStatus.STALE_SOURCE


def test_build_profile_excludes_empty_snapshot_from_claims(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "empty.txt"
    source_path.write_text("", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="empty.txt",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )
    ingest_sources(IngestSourcesRequest(workspace=workspace))

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.claim_count == 0
    assert json.loads(profile_result.json_path.read_text(encoding="utf-8"))["claims"] == []


def test_draft_portfolio_masks_secret_shaped_display_names(tmp_path, monkeypatch):
    paths = WorkspacePaths.from_root(tmp_path / "workspace")
    paths.ensure()
    paths.master_profile_json_path.write_text(
        json.dumps({"sources": [{"display_name": "sk-synthetic-file-token"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(draft_portfolio_module, "build_profile", lambda request: None)

    draft_portfolio_module.draft_portfolio(DraftPortfolioRequest(workspace=paths.workspace))

    draft = paths.portfolio_draft_path.read_text(encoding="utf-8")
    assert "sk-synthetic-file-token" not in draft
    assert "[REDACTED]" in draft
