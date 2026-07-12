from __future__ import annotations

import json

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.models import PublicPortfolioRequest
from portfolio_maker.application.public_portfolio import build_public_portfolio
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
    assert result.project_count == 1
    assert result.claim_count == 2
    assert result.evidence_count == 2
    assert manifest["version"] == 1
    assert len(manifest["projects"]) == 1
    project = manifest["projects"][0]
    assert project["public_safe"] is True
    assert project["repository"] == "octo/demo"
    assert all(claim["public_safe"] for claim in project["claims"])
    assert all(
        evidence["public_safe"]
        for claim in project["claims"]
        for evidence in claim["evidence"]
    )
    assert [item["created_at"] for item in project["timeline"]] == [
        "2026-02-01T00:00:00Z",
        "2026-01-01T00:00:00Z",
    ]
    assert {evidence["url"] for claim in project["claims"] for evidence in claim["evidence"]} == set(urls)
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
    assert len(input_manifest["claim_ids"]) == 2
    assert len(input_manifest["evidence_ids"]) == 2


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
    evidence = manifest["projects"][0]["claims"][0]["evidence"][0]
    assert evidence["state"] == "queued"


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
    evidence = [
        evidence
        for claim in manifest["projects"][0]["claims"]
        for evidence in claim["evidence"]
    ]
    assert [item["url"] for item in evidence] == [approved_url]
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
