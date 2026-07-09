import json

from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.draft_portfolio import draft_portfolio
from portfolio_maker.application.models import BuildProfileRequest, DraftPortfolioRequest
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def test_build_profile_and_draft_portfolio_from_ingested_source(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_DIRECTORY,
            uri="/private/project/path",
            display_name="Portfolio Maker",
            owner=None,
            status=SourceStatus.INGESTED,
        )
    )

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))

    assert profile_result.json_path == paths.master_profile_json_path
    assert profile_result.markdown_path == paths.master_profile_md_path
    assert profile_result.claim_count == 1
    profile = json.loads(paths.master_profile_json_path.read_text(encoding="utf-8"))
    assert profile["version"] == 1
    assert profile["sources"][0]["display_name"] == "Portfolio Maker"
    assert profile["claims"] == [
        {
            "text": "Worked on Portfolio Maker.",
            "confidence": "low",
            "public_safe": False,
            "evidence_uri": "/private/project/path",
        }
    ]
    assert "Portfolio Maker" in paths.master_profile_md_path.read_text(encoding="utf-8")

    portfolio_result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    assert portfolio_result.markdown_path == paths.portfolio_draft_path
    assert portfolio_result.project_count == 1
    draft = paths.portfolio_draft_path.read_text(encoding="utf-8")
    assert "Portfolio Maker" in draft
    assert "Evidence: Portfolio Maker" in draft
    assert "/private/project/path" not in draft
