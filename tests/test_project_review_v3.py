from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

import portfolio_maker.application.project_review_v3 as project_review_v3_module
from portfolio_maker.adapters import cli
from portfolio_maker.application.artifact_approval import (
    write_sample_artifact_policy,
)
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import (
    ApplySemanticIndexRequest,
    BuildProfileRequest,
    FinalizeSemanticIndexRequest,
    IngestSourcesRequest,
    PrepareProjectReviewRequest,
    PrepareSemanticIndexRequest,
)
from portfolio_maker.application.project_boundary import ProjectBoundaryError
from portfolio_maker.application.project_review_v3 import prepare_project_review_v3
from portfolio_maker.application.semantic_index import (
    apply_semantic_index,
    prepare_semantic_index,
)
from portfolio_maker.application.semantic_work import finalize_semantic_index
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.managed_files import write_managed_text
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths
from test_semantic_finalization import (
    PreparedFinalization,
    _approve_root,
    _complete_work,
)


MAX_REVIEW_CHUNK_BYTES = 262_144


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_text(value: object) -> str:
    return _canonical_bytes(value).decode("utf-8")


def _rehash_final_output(
    paths: WorkspacePaths,
    mutate,
) -> None:
    manifest_path = paths.semantic_final_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    chunk_hashes: list[str] = []
    for index in range(1, manifest["chunk_count"] + 1):
        chunk_path = paths.semantic_final_dir / f"chunk-{index:04}.json"
        chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
        mutate(chunk)
        chunk_text = _canonical_text(chunk)
        write_managed_text(chunk_path, chunk_text)
        chunk_hashes.append(hashlib.sha256(chunk_text.encode("utf-8")).hexdigest())
    manifest["chunk_sha256s"] = chunk_hashes
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256")
    manifest["manifest_sha256"] = hashlib.sha256(
        _canonical_bytes(unsigned)
    ).hexdigest()
    write_managed_text(manifest_path, _canonical_text(manifest))


def _apply_boundary_meaning(chunk: dict[str, object]) -> None:
    for node in chunk["nodes"]:
        hierarchy = node["relative_hierarchy"]
        if hierarchy == ".":
            node["semantic_summary"] = (
                "보험 약관 검색과 근거 기반 답변 평가를 하나의 제품으로 제공한다"
            )
            node["semantic_facts"]["purpose_signals"] = [
                "보험 약관 검색과 근거 기반 답변을 연결한다"
            ]
        elif hierarchy == "portfolio-maker":
            node["semantic_summary"] = (
                "Portfolio Maker는 근거 검증과 포트폴리오 생성을 연결한다"
            )
            node["semantic_facts"]["purpose_signals"] = [
                "근거 governance와 portfolio artifact를 연결한다"
            ]
        elif hierarchy == "playmcp":
            node["semantic_summary"] = "PlayMCP 공모전 제품을 독립적으로 제공한다"
            node["semantic_facts"]["independence_signals"] = [
                "별도 공모전 목적과 배포 lifecycle을 가진다"
            ]
        elif hierarchy == "generated-cache":
            node["semantic_summary"] = "생성 캐시와 설정만 보관한다"
            node["semantic_facts"]["triviality_signals"] = [
                "generated cache config only"
            ]


def _activate_v2_workspace(
    workspace: Path,
    tmp_path: Path,
    *,
    directory_count: int | None = None,
    add_unselected_evidence: bool = False,
) -> tuple[WorkspacePaths, int | None]:
    root = tmp_path / "approved-root"
    root.mkdir()
    source_path = root / "README.md"
    source_path.write_text(
        "Grounded evidence. Bearer private-token-value "
        "https://github.com/private/repository/actions/runs/1",
        encoding="utf-8",
    )
    if directory_count is None:
        directory_names = (
            "insurance-rag",
            "portfolio-maker",
            "playmcp",
            "generated-cache",
        )
        for name in directory_names:
            directory = root / name
            directory.mkdir()
            (directory / "README.md").write_text(
                f"Grounded description for {name}.",
                encoding="utf-8",
            )
    else:
        for index in range(directory_count):
            (root / f"component-{index:04}").mkdir()

    paths = WorkspacePaths.from_root(workspace)
    _approve_root(workspace, root)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [
        root.resolve().as_uri(),
        source_path.resolve().as_uri(),
    ]
    paths.approval_path.write_text(_canonical_text(approval), encoding="utf-8")
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
    build_profile(
        BuildProfileRequest(
            workspace=workspace,
            write_artifacts=False,
            invalidate_portfolio_draft=False,
        )
    )
    records = repository.list_evidence_selection_records()
    selected_evidence_id = records[0].evidence_id if records else None

    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(
            workspace=workspace,
            root=root,
            protocol="codex-semantic-v2",
            chunk_size=128,
        )
    )
    finalization = PreparedFinalization(
        workspace=workspace,
        paths=paths,
        revision_id=prepared.revision_id,
    )
    _complete_work(finalization)
    finalize_semantic_index(FinalizeSemanticIndexRequest(workspace=workspace))

    def mutate(chunk: dict[str, object]) -> None:
        _apply_boundary_meaning(chunk)
        if add_unselected_evidence:
            for node in chunk["nodes"]:
                if node["kind"] == "file" and node["relative_hierarchy"] == "README.md":
                    node["evidence_ids"].append(999_999)
                    node["evidence_ids"].sort()

    _rehash_final_output(paths, mutate)
    apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))
    return paths, selected_evidence_id


def _read_review(
    paths: WorkspacePaths,
) -> tuple[dict[str, object], list[dict[str, object]], bytes]:
    manifest_bytes = paths.project_review_v3_manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    chunks = [
        json.loads(
            (
                paths.project_review_v3_input_dir / f"chunk-{index:04}.json"
            ).read_text(encoding="utf-8")
        )
        for index in range(1, manifest["chunk_count"] + 1)
    ]
    return manifest, chunks, manifest_bytes


def test_project_review_v3_projects_safe_meaning_and_complete_coverage(
    workspace: Path,
    tmp_path: Path,
) -> None:
    paths, selected_evidence_id = _activate_v2_workspace(
        workspace,
        tmp_path,
        add_unselected_evidence=True,
    )

    result = prepare_project_review_v3(
        PrepareProjectReviewRequest(workspace=workspace)
    )

    manifest, chunks, _ = _read_review(paths)
    nodes = [node for chunk in chunks for node in chunk["nodes"]]
    serialized = _canonical_text({"manifest": manifest, "chunks": chunks})
    assert result.input_path == paths.project_review_v3_manifest_path
    assert result.chunk_count == manifest["chunk_count"]
    unsigned_manifest = dict(manifest)
    manifest_sha256 = unsigned_manifest.pop("manifest_sha256")
    assert manifest_sha256 == hashlib.sha256(
        _canonical_bytes(unsigned_manifest)
    ).hexdigest()
    assert manifest["final_manifest_sha256"] == hashlib.sha256(
        (paths.semantic_final_dir / "manifest.json").read_bytes()
    ).hexdigest()
    assert manifest["input_chunk_sha256s"] == [
        hashlib.sha256(
            (
                paths.project_review_v3_input_dir
                / f"chunk-{index:04}.json"
            ).read_bytes()
        ).hexdigest()
        for index in range(1, manifest["chunk_count"] + 1)
    ]
    assert sum(manifest["input_chunk_projected_node_counts"]) == manifest[
        "projected_node_count"
    ]
    assert len(manifest["source_policy_hash"]) == 64
    assert len(manifest["artifact_policy_hash"]) == 64
    assert all(node["kind"] in {"source", "directory"} for node in nodes)
    assert manifest["semantic_node_count"] > manifest["projected_node_count"]
    assert manifest["covered_node_count"] == manifest["semantic_node_count"]
    assert manifest["condensed_node_count"] == (
        manifest["semantic_node_count"] - manifest["projected_node_count"]
    )
    assert len(manifest["coverage_sha256"]) == 64
    assert "보험 약관 검색" in serialized
    assert "Portfolio Maker" in serialized
    assert "별도 공모전 목적" in serialized
    assert "generated cache config only" in serialized
    root_node = next(node for node in nodes if node["parent_node_id"] is None)
    assert root_node["direct_child_context"]["projected_child_count"] == 4
    assert len(root_node["direct_child_context"]["node_ids_sha256"]) == 64
    assert any(
        node["parent_node_id"] == root_node["node_id"]
        and node["semantic_facts"]["independence_signals"]
        for node in nodes
    )
    assert selected_evidence_id is not None
    assert selected_evidence_id in {
        evidence_id
        for node in nodes
        for evidence_id in node["selected_evidence_ids"]
    }
    assert "999999" not in serialized
    assert str(paths.workspace) not in serialized
    assert "file://" not in serialized
    assert "portfolio.db" not in serialized
    assert "private-token-value" not in serialized
    assert "github.com/private" not in serialized
    assert all(
        len(
            (
                paths.project_review_v3_input_dir / f"chunk-{index:04}.json"
            ).read_bytes()
        )
        <= MAX_REVIEW_CHUNK_BYTES
        for index in range(1, manifest["chunk_count"] + 1)
    )


def test_project_review_v3_chunks_large_projection_without_global_cap(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = _activate_v2_workspace(
        workspace,
        tmp_path,
        directory_count=600,
    )
    observations: list[tuple[int, int]] = []
    scratch_paths: list[tuple[Path, Path, int, int]] = []
    monkeypatch.setattr(
        project_review_v3_module,
        "_observe_project_review_stream",
        lambda current, stored: observations.append((current, stored)),
    )
    monkeypatch.setattr(
        project_review_v3_module,
        "_observe_project_review_scratch",
        lambda directory, database: scratch_paths.append(
            (
                directory,
                database,
                stat.S_IMODE(directory.stat().st_mode),
                stat.S_IMODE(database.stat().st_mode),
            )
        ),
    )

    prepare_project_review_v3(PrepareProjectReviewRequest(workspace=workspace))

    manifest, _, _ = _read_review(paths)
    assert manifest["projected_node_count"] == 601
    assert manifest["chunk_count"] > 1
    assert observations
    assert max(current for current, _ in observations) <= 1_000
    assert observations[-1][1] == manifest["semantic_node_count"]
    assert len(scratch_paths) == 1
    scratch_directory, scratch_database, directory_mode, database_mode = (
        scratch_paths[0]
    )
    assert directory_mode == 0o700
    assert database_mode == 0o600
    assert not scratch_directory.exists()
    assert not scratch_database.exists()
    for index in range(1, manifest["chunk_count"] + 1):
        chunk_path = (
            paths.project_review_v3_input_dir / f"chunk-{index:04}.json"
        )
        assert len(chunk_path.read_bytes()) <= MAX_REVIEW_CHUNK_BYTES


@pytest.mark.parametrize(
    "failure",
    ("chunk", "receipt", "policy", "unexpected"),
)
def test_project_review_v3_rejects_tampered_or_stale_active_state(
    workspace: Path,
    tmp_path: Path,
    failure: str,
) -> None:
    paths, _ = _activate_v2_workspace(workspace, tmp_path)
    if failure == "chunk":
        chunk_path = paths.semantic_final_dir / "chunk-0001.json"
        chunk_path.write_bytes(chunk_path.read_bytes() + b" ")
    elif failure == "receipt":
        receipt = json.loads(
            paths.semantic_activation_receipt_path.read_text(encoding="utf-8")
        )
        receipt["final_manifest_sha256"] = "0" * 64
        write_managed_text(
            paths.semantic_activation_receipt_path,
            _canonical_text(receipt),
        )
    elif failure == "policy":
        approval = json.loads(
            paths.approval_path.read_text(encoding="utf-8")
        )
        approval["excluded_file_patterns"].append("*.private")
        write_managed_text(
            paths.approval_path,
            _canonical_text(approval),
        )
    else:
        write_managed_text(paths.semantic_final_dir / "undeclared.json", "{}")

    with pytest.raises(ProjectBoundaryError):
        prepare_project_review_v3(
            PrepareProjectReviewRequest(workspace=workspace)
        )


@pytest.mark.parametrize("failure", ("chunk", "policy"))
def test_project_review_v3_failed_refresh_invalidates_existing_manifest(
    workspace: Path,
    tmp_path: Path,
    failure: str,
) -> None:
    paths, _ = _activate_v2_workspace(workspace, tmp_path)
    prepare_project_review_v3(PrepareProjectReviewRequest(workspace=workspace))
    assert paths.project_review_v3_manifest_path.is_file()

    if failure == "chunk":
        chunk_path = paths.semantic_final_dir / "chunk-0001.json"
        chunk_path.write_bytes(chunk_path.read_bytes() + b" ")
    else:
        approval = json.loads(
            paths.approval_path.read_text(encoding="utf-8")
        )
        approval["excluded_file_patterns"].append("*.private")
        write_managed_text(
            paths.approval_path,
            _canonical_text(approval),
        )

    with pytest.raises(ProjectBoundaryError):
        prepare_project_review_v3(
            PrepareProjectReviewRequest(workspace=workspace)
        )

    assert not paths.project_review_v3_manifest_path.exists()


def test_project_review_v3_manifest_invalidation_failure_is_controlled(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = _activate_v2_workspace(workspace, tmp_path)
    prepare_project_review_v3(PrepareProjectReviewRequest(workspace=workspace))
    chunk_path = paths.semantic_final_dir / "chunk-0001.json"
    chunk_path.write_bytes(chunk_path.read_bytes() + b" ")
    original_remove = project_review_v3_module.remove_managed_file

    def fail_manifest_removal(path: Path, **kwargs) -> None:
        if path == paths.project_review_v3_manifest_path:
            raise OSError("synthetic manifest removal failure")
        original_remove(path, **kwargs)

    monkeypatch.setattr(
        project_review_v3_module,
        "remove_managed_file",
        fail_manifest_removal,
    )

    with pytest.raises(
        ProjectBoundaryError,
        match="project review manifest invalidation failed safely",
    ) as caught:
        prepare_project_review_v3(
            PrepareProjectReviewRequest(workspace=workspace)
        )

    assert str(workspace) not in str(caught.value)


def test_project_review_v3_rejects_symlinked_managed_output(
    workspace: Path,
    tmp_path: Path,
) -> None:
    paths, _ = _activate_v2_workspace(workspace, tmp_path)
    prepare_project_review_v3(PrepareProjectReviewRequest(workspace=workspace))
    chunk_path = paths.project_review_v3_input_dir / "chunk-0001.json"
    external = tmp_path / "external.json"
    external.write_text("{}", encoding="utf-8")
    chunk_path.unlink()
    chunk_path.symlink_to(external)

    with pytest.raises(ProjectBoundaryError):
        prepare_project_review_v3(
            PrepareProjectReviewRequest(workspace=workspace)
        )


def test_project_review_v3_publication_is_manifest_last_and_idempotent(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = _activate_v2_workspace(
        workspace,
        tmp_path,
        directory_count=120,
    )
    monkeypatch.setattr(
        project_review_v3_module,
        "PROJECT_REVIEW_CHUNK_BUDGET",
        64_000,
    )
    prepare_project_review_v3(PrepareProjectReviewRequest(workspace=workspace))
    manifest, _, manifest_bytes = _read_review(paths)
    before = {
        path.name: path.read_bytes()
        for path in paths.project_review_v3_input_dir.iterdir()
    }
    prepare_project_review_v3(PrepareProjectReviewRequest(workspace=workspace))
    assert paths.project_review_v3_manifest_path.read_bytes() == manifest_bytes
    assert {
        path.name: path.read_bytes()
        for path in paths.project_review_v3_input_dir.iterdir()
    } == before
    assert manifest["chunk_count"] > 1

    original = project_review_v3_module.write_managed_text
    writes = 0

    def fail_second_chunk(path: Path, content: str, **kwargs):
        nonlocal writes
        if path.parent == paths.project_review_v3_input_dir:
            writes += 1
            if writes == 2:
                raise OSError("synthetic publication failure")
        return original(path, content, **kwargs)

    monkeypatch.setattr(
        project_review_v3_module,
        "write_managed_text",
        fail_second_chunk,
    )
    monkeypatch.setattr(
        project_review_v3_module,
        "PROJECT_REVIEW_CHUNK_BUDGET",
        60_000,
    )
    with pytest.raises(ProjectBoundaryError):
        prepare_project_review_v3(
            PrepareProjectReviewRequest(workspace=workspace)
        )
    assert not paths.project_review_v3_manifest_path.exists()


def test_cli_v3_uses_chunked_projection_and_preserves_v1_v2_choices(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths, _ = _activate_v2_workspace(workspace, tmp_path)

    def reject_v2(_request):
        raise AssertionError("v3 must not use the v2 single-bundle path")

    monkeypatch.setattr(cli, "prepare_project_review_v2", reject_v2)
    exit_code = cli.main(
        [
            "prepare-project-review",
            "--workspace",
            str(workspace),
            "--version",
            "v3",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Project review version: v3" in captured.out
    assert "Chunks:" in captured.out
    assert str(workspace) not in captured.out
    assert paths.project_review_v3_manifest_path.is_file()
    parser = cli.build_parser()
    version_action = next(
        action
        for action in next(
            action
            for action in parser._actions
            if getattr(action, "dest", None) == "command"
        ).choices["prepare-project-review"]._actions
        if action.dest == "version"
    )
    assert tuple(version_action.choices) == ("v1", "v2", "v3")


def test_cli_v3_failure_is_controlled_and_non_disclosing(
    workspace: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths, _ = _activate_v2_workspace(workspace, tmp_path)
    chunk_path = paths.semantic_final_dir / "chunk-0001.json"
    chunk_path.write_bytes(chunk_path.read_bytes() + b" ")

    exit_code = cli.main(
        [
            "prepare-project-review",
            "--workspace",
            str(workspace),
            "--version",
            "v3",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "semantic final output is invalid" in captured.err
    assert "Traceback" not in captured.err
    assert str(workspace) not in captured.err
    assert "file://" not in captured.err
    assert "portfolio.db" not in captured.err
