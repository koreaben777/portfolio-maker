from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.artifact_approval import write_sample_artifact_policy
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import (
    ApplySemanticIndexRequest,
    IngestSourcesRequest,
    PrepareProjectReviewRequest,
    PrepareSemanticIndexRequest,
)
from portfolio_maker.application.project_boundary import (
    ProjectBoundaryError,
    build_project_review_input_v2,
)
from portfolio_maker.application.project_composition import prepare_project_review
from portfolio_maker.application.semantic_index import (
    apply_semantic_index,
    prepare_semantic_index,
)
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def _write_active_semantic_revision(workspace: Path, tmp_path: Path) -> WorkspacePaths:
    root = tmp_path / "approved-root"
    nested = root / "docs"
    nested.mkdir(parents=True)
    source_path = root / "README.md"
    source_path.write_text("Project overview", encoding="utf-8")
    (nested / "setup.md").write_text("Setup guidance", encoding="utf-8")

    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [root.resolve().as_uri(), source_path.resolve().as_uri()]
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

    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=1)
    )
    input_dir = prepared.manifest_path.parent / "input"
    output_dir = prepared.manifest_path.parent / "output"
    output_dir.mkdir()
    for input_path in sorted(input_dir.glob("chunk-*.json")):
        input_text = input_path.read_text(encoding="utf-8")
        input_payload = json.loads(input_text)
        output = {
            "version": 1,
            "revision_id": prepared.revision_id,
            "input_sha256": hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
            "nodes": [
                {
                    "node_id": node["node_id"],
                    "semantic_summary": f"Summary for {node['display_name']}",
                    "semantic_roles": node["roles"],
                    "topics": ["project-boundary"],
                    "analysis_status": node["analysis_status"],
                    "child_node_ids": node["child_node_ids"],
                }
                for node in input_payload["nodes"]
            ],
        }
        output["output_sha256"] = hashlib.sha256(
            json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        (output_dir / input_path.name).write_text(
            json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
    apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))
    return paths


def test_v2_review_input_contains_hierarchy_without_locator(
    workspace: Path, tmp_path: Path
) -> None:
    paths = _write_active_semantic_revision(workspace, tmp_path)

    payload = build_project_review_input_v2(workspace)

    assert payload["version"] == 2
    assert payload["artifact_kind"] == "master_profile"
    assert payload["delivery_scope"] == "restricted"
    assert payload["index_revision"]
    assert payload["nodes"][0]["relative_hierarchy"]
    assert "locator" not in json.dumps(payload).casefold()
    assert str(paths.workspace) not in json.dumps(payload)
    canonical = dict(payload)
    input_sha256 = canonical.pop("input_sha256")
    assert input_sha256 == hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def test_unapproved_index_node_can_be_candidate_context_but_not_evidence(
    workspace: Path, tmp_path: Path
) -> None:
    _write_active_semantic_revision(workspace, tmp_path)

    payload = build_project_review_input_v2(workspace)

    node = next(item for item in payload["nodes"] if item["evidence_ids"] == [])
    assert node["semantic_summary"]
    assert payload["github_evidence"] == []


def test_v2_review_input_filters_node_evidence_to_current_artifact_selection(
    workspace: Path, tmp_path: Path
) -> None:
    paths = _write_active_semantic_revision(workspace, tmp_path)
    repository = SQLiteRepository(paths.db_path)
    initial_payload = build_project_review_input_v2(workspace)
    selected_evidence_id = repository.list_evidence_selection_records()[0].evidence_id
    file_node_id = next(
        node["node_id"]
        for node in repository.list_semantic_nodes(
            initial_payload["index_revision"]
        )
        if node["node_kind"] == "file"
    )
    with sqlite3.connect(paths.db_path) as connection:
        connection.execute(
            "UPDATE semantic_nodes SET evidence_ids_json = ? WHERE revision_id = ? AND node_id = ?",
            (json.dumps([selected_evidence_id, 999_999]), initial_payload["index_revision"], file_node_id),
        )

    payload = build_project_review_input_v2(workspace)

    node = next(item for item in payload["nodes"] if item["node_id"] == file_node_id)
    assert node["evidence_ids"] == [selected_evidence_id]


def test_v2_review_input_rejects_managed_path_hierarchy(
    workspace: Path, tmp_path: Path
) -> None:
    paths = _write_active_semantic_revision(workspace, tmp_path)
    payload = build_project_review_input_v2(workspace)
    node_id = payload["nodes"][0]["node_id"]
    with sqlite3.connect(paths.db_path) as connection:
        connection.execute(
            "UPDATE semantic_nodes SET relative_hierarchy = ? WHERE revision_id = ? AND node_id = ?",
            ("raw/snapshot.md", payload["index_revision"], node_id),
        )

    with pytest.raises(ProjectBoundaryError, match="unsafe locator"):
        build_project_review_input_v2(workspace)


def test_prepare_project_review_keeps_v1_without_active_semantic_revision(
    workspace: Path, tmp_path: Path
) -> None:
    source_path = tmp_path / "README.md"
    source_path.write_text("Local evidence", encoding="utf-8")
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

    payload = json.loads(review.input_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["evidence"]
