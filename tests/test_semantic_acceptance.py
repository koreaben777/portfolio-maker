from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from portfolio_maker.application.approval import load_approval, write_sample_approval
from portfolio_maker.application.artifact_approval import (
    load_artifact_policy,
    write_sample_artifact_policy,
)
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.evidence_selection import (
    EvidenceSelectionRequest,
    EvidenceSelectionService,
)
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import (
    ApplySemanticIndexRequest,
    BuildProfileRequest,
    ComposeProjectsV2Request,
    IngestSourcesRequest,
    PrepareProjectReviewRequest,
    PrepareSemanticIndexRequest,
)
from portfolio_maker.application.project_boundary import prepare_project_review_v2
from portfolio_maker.application.project_composition import compose_projects_v2
from portfolio_maker.application.semantic_index import (
    apply_semantic_index,
    prepare_semantic_index,
)
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.domain.semantic_models import boundary_fingerprint
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


@dataclass(frozen=True)
class SemanticAcceptanceResult:
    active_project_ids: tuple[str, ...]
    candidate_count: int
    restricted_evidence_count: int
    open_public_evidence_count: int
    open_public_local_evidence_count: int


@pytest.fixture
def semantic_runner(tmp_path: Path) -> Callable[[str, str], SemanticAcceptanceResult]:
    fixtures_root = Path(__file__).parent / "fixtures" / "semantic_workspaces"

    def run(name: str, mode: str = "review") -> SemanticAcceptanceResult:
        manifest = _read_fixture_manifest(fixtures_root, name)
        workspace = tmp_path / f"{name}-workspace"
        source_root = tmp_path / f"{name}-source"
        source_files = _write_fixture_source(source_root, manifest)
        paths = WorkspacePaths.from_root(workspace)
        _write_fixture_approval(paths, source_root, source_files)
        _write_fixture_artifact_policy(paths, manifest["artifact_scope"])

        repository = SQLiteRepository(paths.db_path)
        repository.initialize()
        for path in source_files:
            repository.upsert_source(
                Source(
                    id=None,
                    type=SourceType.LOCAL_FILE,
                    uri=path.resolve().as_uri(),
                    display_name=path.name,
                    owner=None,
                    status=SourceStatus.APPROVED,
                )
            )
        ingest_sources(IngestSourcesRequest(workspace=workspace))

        prepared = prepare_semantic_index(
            PrepareSemanticIndexRequest(workspace=workspace, root=source_root, chunk_size=1)
        )
        _write_deterministic_semantic_outputs(prepared.manifest_path, prepared.revision_id)
        apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))

        # The current v2 APIs create source evidence and read semantic-node evidence
        # links, but do not yet expose a writer for that relationship.
        build_profile(
            BuildProfileRequest(
                workspace=workspace,
                invalidate_portfolio_draft=False,
                write_artifacts=False,
            )
        )
        _link_file_nodes_to_source_evidence(repository, prepared.revision_id, source_root)

        review = prepare_project_review_v2(PrepareProjectReviewRequest(workspace=workspace))
        review_payload = json.loads(review.input_path.read_text(encoding="utf-8"))
        candidate_payload = _candidate_payload(manifest, review_payload)
        paths.project_candidates_path.write_text(
            _canonical_json(candidate_payload), encoding="utf-8"
        )
        composition = compose_projects_v2(
            ComposeProjectsV2Request(workspace=workspace, mode=mode)  # type: ignore[arg-type]
        )

        approval = load_approval(paths)
        policy = load_artifact_policy(paths)
        selector = EvidenceSelectionService()
        restricted = selector.select(
            repository,
            EvidenceSelectionRequest(
                artifact_kind="master_profile",
                policy=policy,
                current_approval=approval,
            ),
        )
        open_public = selector.select(
            repository,
            EvidenceSelectionRequest(
                artifact_kind="portfolio_public_manifest",
                policy=policy,
                current_approval=approval,
            ),
        )
        _assert_locator_free_outputs(paths, source_root)

        return SemanticAcceptanceResult(
            active_project_ids=tuple(
                project["id"] for project in repository.list_portfolio_projects()
            ),
            candidate_count=len(candidate_payload["candidates"]),
            restricted_evidence_count=len(restricted.included_evidence_ids),
            open_public_evidence_count=len(open_public.included_evidence_ids),
            open_public_local_evidence_count=sum(
                record.activity_id is None for record in open_public.records
            ),
        )

    return run


def _read_fixture_manifest(fixtures_root: Path, name: str) -> dict[str, object]:
    path = fixtures_root / name / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("fixture") != name
        or payload.get("artifact_scope") not in {"restricted", "open_public"}
        or not isinstance(payload.get("files"), dict)
        or not isinstance(payload.get("candidate"), dict)
    ):
        raise AssertionError(f"invalid semantic fixture: {name}")
    return payload


def _write_fixture_source(source_root: Path, manifest: dict[str, object]) -> tuple[Path, ...]:
    files = manifest["files"]
    assert isinstance(files, dict)
    paths: list[Path] = []
    for relative_path, text in sorted(files.items()):
        assert isinstance(relative_path, str) and isinstance(text, str)
        relative = Path(relative_path)
        assert not relative.is_absolute() and ".." not in relative.parts
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return tuple(paths)


def _write_fixture_approval(
    paths: WorkspacePaths, source_root: Path, source_files: tuple[Path, ...]
) -> None:
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [
        source_root.resolve().as_uri(),
        *(path.resolve().as_uri() for path in source_files),
    ]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")


def _write_fixture_artifact_policy(paths: WorkspacePaths, artifact_scope: object) -> None:
    write_sample_artifact_policy(paths)
    policy = json.loads(paths.artifact_approval_path.read_text(encoding="utf-8"))
    if artifact_scope == "open_public":
        policy["artifacts"]["portfolio_public_manifest"].update(
            {
                "delivery_scope": "open_public",
                "include_local": False,
                "include_private_github": False,
            }
        )
    paths.artifact_approval_path.write_text(json.dumps(policy), encoding="utf-8")


def _write_deterministic_semantic_outputs(manifest_path: Path, revision_id: str) -> None:
    input_dir = manifest_path.parent / "input"
    output_dir = manifest_path.parent / "output"
    output_dir.mkdir()
    for input_path in sorted(input_dir.glob("chunk-*.json")):
        input_text = input_path.read_text(encoding="utf-8")
        input_payload = json.loads(input_text)
        output = {
            "version": 1,
            "revision_id": revision_id,
            "input_sha256": hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
            "nodes": [
                {
                    "node_id": node["node_id"],
                    "semantic_summary": f"Synthetic summary for {node['display_name']}",
                    "semantic_roles": node["roles"],
                    "topics": ["synthetic-project"],
                    "analysis_status": node["analysis_status"],
                    "child_node_ids": node["child_node_ids"],
                }
                for node in input_payload["nodes"]
            ],
        }
        output["output_sha256"] = hashlib.sha256(
            _canonical_json(output).encode("utf-8")
        ).hexdigest()
        (output_dir / input_path.name).write_text(_canonical_json(output), encoding="utf-8")


def _link_file_nodes_to_source_evidence(
    repository: SQLiteRepository, revision_id: str, source_root: Path
) -> None:
    records_by_uri = {
        record.source_uri: record.evidence_id
        for record in repository.list_evidence_selection_records()
        if record.activity_id is None and record.source_uri is not None
    }
    nodes = repository.list_semantic_nodes(revision_id)
    with sqlite3.connect(repository.db_path) as connection:
        for node in nodes:
            if node["node_kind"] != "file":
                continue
            relative_hierarchy = node["relative_hierarchy"]
            assert isinstance(relative_hierarchy, str)
            evidence_id = records_by_uri[(source_root / relative_hierarchy).resolve().as_uri()]
            connection.execute(
                "UPDATE semantic_nodes SET evidence_ids_json = ? WHERE revision_id = ? AND node_id = ?",
                (json.dumps([evidence_id]), revision_id, node["node_id"]),
            )


def _candidate_payload(manifest: dict[str, object], review_input: dict[str, object]) -> dict[str, object]:
    candidate = manifest["candidate"]
    assert isinstance(candidate, dict)
    boundary_hierarchies = candidate.get("boundary_hierarchies")
    assert isinstance(boundary_hierarchies, list) and all(
        isinstance(item, str) for item in boundary_hierarchies
    )
    nodes = review_input["nodes"]
    assert isinstance(nodes, list)
    nodes_by_hierarchy = {
        node["relative_hierarchy"]: node
        for node in nodes
        if isinstance(node, dict)
        and isinstance(node.get("relative_hierarchy"), str)
        and isinstance(node.get("node_id"), str)
    }
    boundary_nodes = [nodes_by_hierarchy[hierarchy] for hierarchy in boundary_hierarchies]
    boundary_node_ids = [node["node_id"] for node in boundary_nodes]
    evidence_ids = sorted(
        {
            evidence_id
            for node in nodes_by_hierarchy.values()
            if isinstance(node.get("evidence_ids"), list)
            and any(
                node["relative_hierarchy"] == hierarchy
                or node["relative_hierarchy"].startswith(f"{hierarchy}/")
                for hierarchy in boundary_hierarchies
            )
            for evidence_id in node["evidence_ids"]
            if isinstance(evidence_id, int)
        }
    )
    boundary_type = candidate["boundary_type"]
    assert isinstance(boundary_type, str)
    return {
        "version": 2,
        "review_input_sha256": review_input["input_sha256"],
        "candidates": [
            {
                "id": candidate["id"],
                "project_id": candidate["project_id"],
                "title": candidate["title"],
                "overview": candidate["overview"],
                "boundary_type": boundary_type,
                "boundary_node_ids": boundary_node_ids,
                "boundary_fingerprint": boundary_fingerprint(
                    boundary_type, tuple(boundary_node_ids)
                ),
                "evidence_ids": evidence_ids,
                "grouping_rationale": ["Synthetic project boundary is coherent."],
                "counter_signals": [],
                "review_reasons": [],
                "confidence": candidate["confidence"],
            }
        ],
    }


def _assert_locator_free_outputs(paths: WorkspacePaths, source_root: Path) -> None:
    output_paths = [
        paths.semantic_index_manifest_path,
        paths.project_review_input_v2_path,
        paths.project_candidates_path,
        *(paths.semantic_index_input_dir.glob("chunk-*.json")),
        *((paths.semantic_index_dir / "output").glob("chunk-*.json")),
    ]
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in output_paths)
    assert str(source_root) not in serialized
    assert "file://" not in serialized.casefold()
    assert "locator" not in serialized.casefold()
    assert ".portfolio-maker" not in serialized.casefold()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def test_coherent_parent_becomes_one_project(semantic_runner) -> None:
    result = semantic_runner("coherent_parent", mode="automatic")

    assert result.active_project_ids == ("insurance-rag-chatbot",)


def test_independent_contest_child_is_split(semantic_runner) -> None:
    result = semantic_runner("independent_child", mode="automatic")

    assert "playmcp-contest" in result.active_project_ids


def test_known_projects_do_not_regress_to_zero_candidates(semantic_runner) -> None:
    assert semantic_runner("coherent_parent").candidate_count > 0


def test_cross_directory_fixture_preserves_artifact_scope_boundary(semantic_runner) -> None:
    result = semantic_runner("cross_directory", mode="automatic")

    assert result.active_project_ids == ("cross-directory-project",)
    assert result.restricted_evidence_count > result.open_public_evidence_count
    assert result.open_public_local_evidence_count == 0
