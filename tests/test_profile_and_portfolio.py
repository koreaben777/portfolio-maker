import json

from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.draft_portfolio import draft_portfolio
from portfolio_maker.application.models import BuildProfileRequest, DraftPortfolioRequest
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def test_build_profile_treats_github_sources_as_discovery_only_in_mvp(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
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
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="/private/project/path",
            display_name="Portfolio Maker",
            owner=None,
            status=SourceStatus.INGESTED,
        )
    )
    snapshot_path = paths.local_snapshots_dir / f"source-{source_id}.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "source_uri": "/private/project/path",
                "display_name": "Portfolio Maker",
                "content_hash": "abc123",
                "extractor": "text-v1",
                "extracted_at": "2026-07-09T00:00:00Z",
                "text": "# Portfolio Maker\nBuilt an approval-gated evidence pipeline.",
            }
        ),
        encoding="utf-8",
    )
    repository.insert_source_snapshot(source_id, snapshot_path, "abc123", "text-v1")

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.json_path == paths.master_profile_json_path
    assert profile_result.markdown_path == paths.master_profile_md_path
    assert profile_result.claim_count == 1
    profile = json.loads(paths.master_profile_json_path.read_text(encoding="utf-8"))
    assert profile["version"] == 1
    assert profile["sources"][0] == {
        "id": source_id,
        "type": "local_file",
        "uri": "/private/project/path",
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
            "evidence_uri": "/private/project/path",
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
    assert "/private/project/path" not in draft
