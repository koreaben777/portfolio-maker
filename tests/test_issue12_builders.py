from __future__ import annotations

import json

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.draft_portfolio import draft_portfolio
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DraftPortfolioRequest,
    IngestSourcesRequest,
    PublicPortfolioRequest,
)
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.public_portfolio import build_public_portfolio
from portfolio_maker.application.artifact_approval import write_sample_artifact_policy
from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


PRIVATE_URL = "https://github.com/octo/private/pull/2"


def _private_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval.update(
        {
            "private_sources_allowed": True,
            "allowed_repositories": ["octo/private"],
            "approved_private_github_activity_urls": [PRIVATE_URL],
        }
    )
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    write_sample_artifact_policy(paths)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
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
            source_id,
            "octo/private",
            "pull_request",
            PRIVATE_URL,
            "Approved private work",
            "MERGED",
            "octo",
            "2026-01-01T00:00:00Z",
            None,
            True,
        )
    )
    return workspace, paths


def test_restricted_private_activity_reaches_profile_draft_and_manifest_without_url(
    tmp_path,
):
    workspace, paths = _private_workspace(tmp_path)

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))
    profile_text = profile_result.json_path.read_text(encoding="utf-8")
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    draft_text = paths.portfolio_draft_path.read_text(encoding="utf-8")
    build_public_portfolio(PublicPortfolioRequest(workspace=workspace))
    manifest_text = paths.portfolio_public_json_path.read_text(encoding="utf-8")

    assert "Approved private work" in profile_text
    assert "No approved portfolio projects" in draft_text
    assert PRIVATE_URL not in profile_text + draft_text + manifest_text
    assert "private-github" in profile_text.casefold() + draft_text.casefold()
    manifest = json.loads(manifest_text)
    assert manifest["delivery_scope"] == "restricted"
    assert manifest["projects"] == []

    with SQLiteRepository(paths.db_path)._read_connection() as connection:
        records = connection.execute(
            "SELECT kind, input_manifest FROM artifacts WHERE kind IN ('master_profile', 'portfolio_draft', 'portfolio_public') ORDER BY id"
        ).fetchall()
    assert len(records) >= 3
    for record in records[-3:]:
        input_manifest = json.loads(record["input_manifest"])
        assert input_manifest["delivery_scope"] == "restricted"
        assert input_manifest["policy_hash"]
        if record["kind"] == "master_profile":
            assert input_manifest["included_evidence_ids"]
        else:
            assert input_manifest["included_evidence_ids"] == []


def test_restricted_local_evidence_uses_safe_label_not_raw_uri(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "local" / "README.md"
    source_path.parent.mkdir()
    source_path.write_text("Approved local work\n", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    write_sample_artifact_policy(paths)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            None,
            SourceType.LOCAL_FILE,
            source_path.resolve().as_uri(),
            "README.md",
            None,
            SourceStatus.APPROVED,
        )
    )

    ingest_sources(IngestSourcesRequest(workspace=workspace))
    build_public_portfolio(PublicPortfolioRequest(workspace=workspace))

    manifest_text = paths.portfolio_public_json_path.read_text(encoding="utf-8")
    assert json.loads(manifest_text)["projects"] == []
    assert source_path.resolve().as_uri() not in manifest_text
