from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from portfolio_maker.application.project_composition import (
    ProjectCompositionError,
    build_review_input_payload,
    parse_candidate_payload,
    parse_project_approval,
    validate_project_approval,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.artifact_approval import write_sample_artifact_policy
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import (
    BuildProfileRequest,
    ComposeProjectsRequest,
    IngestSourcesRequest,
    PrepareProjectReviewRequest,
    PublicPortfolioRequest,
)
from portfolio_maker.application.project_composition import (
    compose_projects,
    prepare_project_review,
)
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.public_portfolio import build_public_portfolio
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.managed_files import write_managed_text
from portfolio_maker.workspace import WorkspacePaths


def _review_input() -> dict[str, object]:
    payload = {
        "version": 1,
        "artifact_kind": "master_profile",
        "delivery_scope": "restricted",
        "policy_hash": "a" * 64,
        "evidence": [
            {
                "evidence_id": 101,
                "stable_id": "source-snapshot:1:abc",
                "origin": "local",
                "source_label": "README.md",
                "excerpt": "A safe excerpt",
            },
            {
                "evidence_id": 205,
                "stable_id": "github-activity:2",
                "origin": "public_github",
                "source_label": "octo/demo",
                "activity_type": "pull_request",
                "title": "A safe PR",
                "created_at": "2026-07-01T00:00:00Z",
            },
        ],
    }
    return build_review_input_payload(payload)


def test_review_input_hash_excludes_raw_locators_and_is_self_bound() -> None:
    payload = _review_input()

    assert payload["input_sha256"]
    assert "file:///private/secret/project.md" not in json.dumps(payload)
    canonical = dict(payload)
    input_hash = canonical.pop("input_sha256")
    expected = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert input_hash == expected


def test_direct_manual_approval_validates_without_candidate_file() -> None:
    approval = {
        "version": 1,
        "review_input_sha256": _review_input()["input_sha256"],
        "projects": [
            {
                "id": "demo-project",
                "title": "Demo project",
                "overview": "A grounded overview",
                "evidence_ids": [101, 205],
                "status": "approved",
            }
        ],
        "rejected_candidate_ids": [],
        "unassigned_evidence_ids": [],
    }

    parsed = parse_project_approval(approval)
    validate_project_approval(parsed, _review_input())
    assert parsed.projects[0].id == "demo-project"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["candidates"].append(dict(payload["candidates"][0])),
        lambda payload: payload["candidates"][0].update({"confidence": "certain"}),
        lambda payload: payload["candidates"][0].update({"review_required": False}),
    ],
)
def test_candidate_validation_rejects_ambiguous_or_unreviewed_candidates(mutate) -> None:
    review_input = _review_input()
    payload = {
        "version": 1,
        "review_input_sha256": review_input["input_sha256"],
        "candidates": [
            {
                "id": "candidate-one",
                "status": "candidate",
                "title": "A reviewed candidate",
                "overview": "A grounded overview",
                "grouping_rationale": "The evidence supports one work unit",
                "evidence_ids": [101],
                "confidence": "medium",
                "review_required": True,
            }
        ],
    }
    mutate(payload)

    with pytest.raises(ProjectCompositionError):
        parse_candidate_payload(payload, review_input)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda approval: approval["projects"][0].update({"evidence_ids": [999]}),
        lambda approval: approval["projects"][0].update({"evidence_ids": [101, 101]}),
        lambda approval: approval["projects"][0].update({"title": "   "}),
        lambda approval: approval.update({"review_input_sha256": "stale"}),
    ],
)
def test_invalid_project_approval_is_controlled_and_does_not_validate(mutate) -> None:
    approval = {
        "version": 1,
        "review_input_sha256": _review_input()["input_sha256"],
        "projects": [
            {
                "id": "demo-project",
                "title": "Demo project",
                "overview": "A grounded overview",
                "evidence_ids": [101],
                "status": "approved",
            }
        ],
        "rejected_candidate_ids": [],
        "unassigned_evidence_ids": [],
    }
    mutate(approval)

    with pytest.raises(ProjectCompositionError):
        parsed = parse_project_approval(approval)
        validate_project_approval(parsed, _review_input())


def test_safe_review_input_replaces_a_local_locator_with_a_safe_label() -> None:
    payload = {
        "version": 1,
        "artifact_kind": "master_profile",
        "delivery_scope": "restricted",
        "policy_hash": "a" * 64,
        "evidence": [
            {
                "evidence_id": 1,
                "stable_id": "source-snapshot:1:abc",
                "origin": "local",
                "source_label": "/private/project",
                "excerpt": "safe",
            }
        ],
    }

    result = build_review_input_payload(payload)
    assert result["evidence"][0]["source_label"] == "Approved local evidence"

    payload["evidence"][0]["source_label"] = "file:///private/project/README.md"
    result = build_review_input_payload(payload)
    assert result["evidence"][0]["source_label"] == "Approved local evidence"


@pytest.mark.parametrize(
    "field, value",
    [
        ("stable_id", "/private/project/.portfolio-maker/raw/snapshot.txt"),
        ("stable_id", "https://github.com/private/repository/pull/1"),
    ],
)
def test_review_input_rejects_persisted_locators_in_safe_fields(field: str, value: str) -> None:
    payload = {
        "version": 1,
        "artifact_kind": "master_profile",
        "delivery_scope": "restricted",
        "policy_hash": "a" * 64,
        "evidence": [
            {
                "evidence_id": 1,
                "stable_id": "source-snapshot:1:abc",
                "origin": "local",
                "source_label": "README.md",
                "excerpt": "safe",
            }
        ],
    }
    payload["evidence"][0][field] = value

    with pytest.raises(ProjectCompositionError):
        build_review_input_payload(payload)


def test_semantic_project_storage_is_additive_and_replaces_links_atomically(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / ".portfolio-maker" / "portfolio.db")
    repository.initialize()
    with repository._connection() as connection:
        connection.execute(
            "INSERT INTO sources (type, uri, display_name, status) VALUES ('local_file', ?, 'README.md', 'ingested')",
            ("file:///tmp/README.md",),
        )
        source_id = connection.execute("SELECT id FROM sources").fetchone()[0]
    evidence_id = repository.upsert_evidence_item(
        source_id=source_id,
        snapshot_id=None,
        github_activity_id=None,
        locator="file:///tmp/README.md",
        stable_id="source-snapshot:1:abc",
        content_hash="abc",
        public_safe=False,
    )
    repository.replace_portfolio_projects(
        (
            {
                "id": "demo-project",
                "title": "Demo project",
                "overview": "Grounded overview",
                "evidence_ids": (evidence_id,),
            },
        ),
        "a" * 64,
        "b" * 64,
    )

    projects = repository.list_portfolio_projects()
    assert projects[0]["id"] == "demo-project"
    assert projects[0]["evidence"] == [{"evidence_id": evidence_id, "support_level": "direct"}]
    assert "projects" in repository.table_names()
    assert "portfolio_projects" in repository.table_names()


def test_approved_semantic_project_is_the_only_manifest_project(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "source" / "README.md"
    source_path.parent.mkdir()
    source_path.write_text("Local evidence\n", encoding="utf-8")
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

    review = prepare_project_review(PrepareProjectReviewRequest(workspace=workspace))
    review_payload = json.loads(review.input_path.read_text(encoding="utf-8"))
    evidence_id = review_payload["evidence"][0]["evidence_id"]
    approval_payload = {
        "version": 1,
        "review_input_sha256": review_payload["input_sha256"],
        "projects": [
            {
                "id": "local-project",
                "title": "Local project",
                "overview": "A reviewed local project",
                "evidence_ids": [evidence_id],
                "status": "approved",
            }
        ],
        "rejected_candidate_ids": [],
        "unassigned_evidence_ids": [],
    }
    write_managed_text(
        paths.project_approval_path,
        json.dumps(approval_payload, indent=2) + "\n",
    )
    result = compose_projects(ComposeProjectsRequest(workspace=workspace))
    assert result.project_count == 1

    profile = build_profile(BuildProfileRequest(workspace=workspace))
    manifest = build_public_portfolio(PublicPortfolioRequest(workspace=workspace))
    profile_payload = json.loads(profile.json_path.read_text(encoding="utf-8"))
    manifest_payload = json.loads(manifest.manifest_path.read_text(encoding="utf-8"))
    assert [project["id"] for project in profile_payload["projects"]] == ["local-project"]
    assert [project["id"] for project in manifest_payload["projects"]] == ["local-project"]
    assert manifest_payload["projects"][0]["title"] == "Local project"
    with SQLiteRepository(paths.db_path)._read_connection() as connection:
        artifact_rows = connection.execute(
            "SELECT kind, input_manifest FROM artifacts "
            "WHERE kind IN ('master_profile', 'portfolio_public') ORDER BY id"
        ).fetchall()
    manifests = {row["kind"]: json.loads(row["input_manifest"]) for row in artifact_rows}
    assert manifests["master_profile"]["project_approval_sha256"]
    assert manifests["master_profile"]["project_review_input_sha256"] == review_payload["input_sha256"]
    assert manifests["portfolio_public"]["project_approval_sha256"] == manifests["master_profile"]["project_approval_sha256"]
    assert manifests["portfolio_public"]["project_review_input_sha256"] == review_payload["input_sha256"]


def test_no_project_approval_keeps_evidence_inventory_but_has_zero_project_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    write_sample_artifact_policy(paths)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    result = build_public_portfolio(PublicPortfolioRequest(workspace=workspace))

    assert result.project_count == 0
    assert json.loads(result.manifest_path.read_text(encoding="utf-8"))["projects"] == []
