from __future__ import annotations

import json

import pytest

from portfolio_maker.application.approval import (
    ApprovalFormatError,
    load_approval,
    write_sample_approval,
)
from portfolio_maker.application.artifact_approval import (
    load_artifact_policy,
    write_sample_artifact_policy,
)
from portfolio_maker.application.evidence_selection import (
    EvidenceSelectionRequest,
    EvidenceSelectionService,
    EvidenceSelectionError,
)
from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


PUBLIC_URL = "https://github.com/octo/public/pull/1"
PRIVATE_URL = "https://github.com/octo/private/pull/2"
LOCAL_URI = "file:///approved/local.md"


def _policy_paths(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval.update(
        {
            "approved_source_uris": [LOCAL_URI],
            "private_sources_allowed": True,
            "allowed_repositories": ["octo/public", "octo/private"],
            "approved_github_activity_urls": [PUBLIC_URL],
            "approved_private_github_activity_urls": [PRIVATE_URL],
        }
    )
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    write_sample_artifact_policy(paths)
    return paths


def _fixture(workspace):
    paths = _policy_paths(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    local_id = repository.upsert_source(
        Source(None, SourceType.LOCAL_FILE, LOCAL_URI, "local.md", None, SourceStatus.INGESTED)
    )
    public_id = repository.upsert_source(
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
    private_id = repository.upsert_source(
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
    public_activity_id = repository.insert_github_activity(
        GitHubActivity(
            None,
            public_id,
            "octo/public",
            "pull_request",
            PUBLIC_URL,
            "Public activity",
            "OPEN",
            "octo",
            "2026-01-01T00:00:00Z",
            None,
            False,
        )
    )
    private_activity_id = repository.insert_github_activity(
        GitHubActivity(
            None,
            private_id,
            "octo/private",
            "pull_request",
            PRIVATE_URL,
            "Private activity",
            "OPEN",
            "octo",
            "2026-01-01T00:00:00Z",
            None,
            True,
        )
    )
    records = []
    for name, source_id, activity_id, locator in (
        ("local", local_id, None, LOCAL_URI),
        ("public", public_id, public_activity_id, PUBLIC_URL),
        ("private", private_id, private_activity_id, PRIVATE_URL),
    ):
        project_id = repository.upsert_project(f"{name}:project", public_safe=True)
        evidence_id = repository.upsert_evidence_item(
            source_id=source_id,
            snapshot_id=None,
            github_activity_id=activity_id,
            locator=locator,
            stable_id=f"selection:{name}",
            content_hash=None,
            public_safe=True,
        )
        claim_id = repository.upsert_career_claim(
            project_id, f"{name} claim", public_safe=True
        )
        repository.link_claim_evidence(claim_id, evidence_id, "direct")
        records.append((project_id, claim_id, evidence_id))
    return paths, repository, records


def _select(workspace, kind="portfolio_html"):
    paths, repository, records = _fixture(workspace)
    return EvidenceSelectionService().select(
        repository,
        EvidenceSelectionRequest(
            artifact_kind=kind,
            policy=load_artifact_policy(paths),
            current_approval=load_approval(paths),
        ),
    ), records


def test_restricted_selection_includes_approved_local_public_and_private_origins(tmp_path):
    result, records = _select(tmp_path)

    assert set(result.included_evidence_ids) == {record[2] for record in records}
    assert set(result.included_claim_ids) == {record[1] for record in records}
    assert result.delivery_scope == "restricted"
    assert result.policy_hash


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("excluded_directories", ["/synthetic/excluded"]),
        ("excluded_file_patterns", ["*.secret"]),
    ),
)
def test_policy_hash_changes_when_source_exclusion_policy_changes(tmp_path, field, value):
    paths, repository, _ = _fixture(tmp_path)
    service = EvidenceSelectionService()
    baseline = service.select(
        repository,
        EvidenceSelectionRequest(
            artifact_kind="portfolio_html",
            policy=load_artifact_policy(paths),
            current_approval=load_approval(paths),
        ),
    )
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval[field] = value
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    changed = service.select(
        repository,
        EvidenceSelectionRequest(
            artifact_kind="portfolio_html",
            policy=load_artifact_policy(paths),
            current_approval=load_approval(paths),
        ),
    )

    assert changed.policy_hash != baseline.policy_hash


def test_artifact_exclusion_removes_one_source_without_changing_pool(tmp_path):
    paths, repository, records = _fixture(tmp_path)
    payload = {
        "version": 1,
        "artifacts": {
            "portfolio_html": {
                "delivery_scope": "restricted",
                "excluded_source_uris": [LOCAL_URI],
            }
        },
    }
    paths.artifact_approval_path.write_text(json.dumps(payload), encoding="utf-8")

    result = EvidenceSelectionService().select(
        repository,
        EvidenceSelectionRequest(
            artifact_kind="portfolio_html",
            policy=load_artifact_policy(paths),
            current_approval=load_approval(paths),
        ),
    )

    assert records[0][2] not in result.included_evidence_ids
    assert records[1][2] in result.included_evidence_ids
    assert records[2][2] in result.included_evidence_ids
    assert any(item["reason"] == "excluded_source" for item in result.excluded_decisions)


def test_open_public_selection_includes_public_github_only(tmp_path):
    paths, repository, records = _fixture(tmp_path)
    paths.artifact_approval_path.write_text(
        json.dumps(
            {
                "version": 1,
                "artifacts": {
                    "portfolio_html": {
                        "delivery_scope": "open_public",
                        "include_local": False,
                        "include_private_github": False,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = EvidenceSelectionService().select(
        repository,
        EvidenceSelectionRequest(
            artifact_kind="portfolio_html",
            policy=load_artifact_policy(paths),
            current_approval=load_approval(paths),
        ),
    )

    assert result.included_evidence_ids == (records[1][2],)
    assert sum(
        item["reason"] == "open_public_origin" for item in result.excluded_decisions
    ) == 2


def test_open_public_policy_with_local_or_private_request_is_controlled(tmp_path):
    paths, repository, _ = _fixture(tmp_path)
    paths.artifact_approval_path.write_text(
        json.dumps(
            {
                "version": 1,
                "artifacts": {
                    "portfolio_html": {
                        "delivery_scope": "open_public",
                        "include_local": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ApprovalFormatError, match="open_public"):
        load_artifact_policy(paths)


@pytest.mark.parametrize("mutation", ("unknown_origin", "stale_source"))
def test_selector_excludes_unknown_or_stale_records(tmp_path, mutation):
    paths, repository, records = _fixture(tmp_path)
    if mutation == "unknown_origin":
        with repository._connection() as connection:
            connection.execute(
                "UPDATE evidence_items SET origin_visibility = 'unknown' WHERE id = ?",
                (records[0][2],),
            )
    else:
        repository.update_source_status(records[0][0], SourceStatus.STALE_SOURCE)

    result = EvidenceSelectionService().select(
        repository,
        EvidenceSelectionRequest(
            artifact_kind="portfolio_html",
            policy=load_artifact_policy(paths),
            current_approval=load_approval(paths),
        ),
    )

    assert records[0][2] not in result.included_evidence_ids
    assert any(
        item["evidence_id"] == records[0][2]
        for item in result.excluded_decisions
    )
