from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import portfolio_maker.application.semantic_work as semantic_work_module
from portfolio_maker.adapters.cli import main
from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.models import (
    FinalizeSemanticIndexRequest,
    NextSemanticWorkRequest,
    PrepareSemanticIndexRequest,
    ValidateSemanticWorkRequest,
)
from portfolio_maker.application.semantic_index import prepare_semantic_index
from portfolio_maker.application.semantic_work import (
    MAX_WORK_UNIT_BYTES,
    SemanticWorkError,
    finalize_semantic_index,
    next_semantic_work,
    validate_semantic_work,
)
from portfolio_maker.infrastructure.managed_files import write_managed_text
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


FINAL_NODE_KEYS = {
    "result_kind",
    "node_id",
    "source_id",
    "parent_node_id",
    "kind",
    "display_name",
    "relative_hierarchy",
    "content_fingerprint",
    "semantic_summary",
    "semantic_roles",
    "topics",
    "semantic_facts",
    "semantic_disposition",
    "analysis_status",
    "child_node_ids",
    "skip_reason",
    "evidence_ids",
    "coverage",
}
SEMANTIC_FACT_KEYS = {
    "purpose_signals",
    "parent_context_signals",
    "independence_signals",
    "triviality_signals",
    "grounding_signal_ids",
}
FINAL_MANIFEST_KEYS = {
    "version",
    "protocol",
    "revision_id",
    "source_id",
    "policy_hash",
    "analyzer_version",
    "work_plan_sha256",
    "input_manifest_sha256",
    "directory_timeout_seconds",
    "timed_out_directory_count",
    "unreadable_directory_count",
    "node_assignment_sha256",
    "receipt_file_sha256s",
    "chunk_sha256s",
    "chunk_node_counts",
    "chunk_count",
    "node_count",
    "analysis_node_count",
    "structural_node_count",
    "node_ids_sha256",
    "manifest_sha256",
}


@dataclass(frozen=True)
class PreparedFinalization:
    workspace: Path
    paths: WorkspacePaths
    revision_id: str


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_text(payload: object) -> str:
    return _canonical_bytes(payload).decode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _approve_root(workspace: Path, root: Path) -> None:
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [root.resolve().as_uri()]
    paths.approval_path.write_text(_canonical_text(approval), encoding="utf-8")


@pytest.fixture
def prepared_finalization(
    workspace: Path,
    tmp_path: Path,
) -> PreparedFinalization:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text(
        "# Grounded project\n"
        "The application retrieves approved evidence and validates results.",
        encoding="utf-8",
    )
    (root / "empty.txt").write_text("", encoding="utf-8")
    _approve_root(workspace, root)
    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(
            workspace=workspace,
            root=root,
            protocol="codex-semantic-v2",
            chunk_size=2,
        )
    )
    return PreparedFinalization(
        workspace=workspace,
        paths=WorkspacePaths.from_root(workspace),
        revision_id=prepared.revision_id,
    )


def _output_path(prepared: PreparedFinalization, unit_id: str) -> Path:
    return prepared.paths.semantic_work_output_dir / f"{unit_id}.json"


def _semantic_results(work_input: dict[str, object]) -> list[object]:
    child_result_ids = [
        result.get("node_id", result.get("aggregate_id"))
        for child in work_input["child_results"]
        for result in child["results"]
    ]
    if work_input["stage"] == "reduce":
        return [
            {
                "result_kind": "aggregate",
                "aggregate_id": f"aggregate:{work_input['unit_id']}",
                "semantic_summary": (
                    "직접 하위 결과를 결합해 상위 분석에 필요한 근거를 제공한다"
                ),
                "semantic_roles": ["evidence-aggregate"],
                "topics": ["근거 집계"],
                "purpose_signals": ["직접 하위 결과를 상위 분석에 전달한다"],
                "parent_context_signals": [],
                "independence_signals": [],
                "triviality_signals": [],
                "grounding_signal_ids": child_result_ids,
                "member_node_ids_sha256": _sha256(
                    _canonical_bytes(child_result_ids)
                ),
                "member_count": len(child_result_ids),
            }
        ]

    results: list[object] = []
    for target in work_input["targets"]:
        own_signal_ids = [signal["signal_id"] for signal in target["signals"]]
        grounding_ids = own_signal_ids[:1] or child_result_ids[:1]
        results.append(
            {
                "result_kind": "node",
                "node_id": target["node_id"],
                "semantic_summary": (
                    "검증된 입력을 사용해 근거 기반 기능을 제공한다"
                ),
                "semantic_roles": ["application-component"],
                "topics": ["근거 기반 기능"],
                "purpose_signals": ["검증된 입력을 제품 기능에 연결한다"],
                "parent_context_signals": ["상위 구성요소의 목적을 지원한다"],
                "independence_signals": [],
                "triviality_signals": [],
                "grounding_signal_ids": grounding_ids,
                "semantic_disposition": "analyzed",
                "analysis_status": target["analysis_status"],
            }
        )
    return results


def _write_valid_output(
    prepared: PreparedFinalization,
    input_path: Path,
) -> None:
    work_input = json.loads(input_path.read_text(encoding="utf-8"))
    payload: dict[str, object] = {
        "version": 2,
        "revision_id": work_input["revision_id"],
        "unit_id": work_input["unit_id"],
        "stage": work_input["stage"],
        "input_sha256": work_input["input_sha256"],
        "dependency_output_sha256s": [
            dependency["output_sha256"]
            for dependency in work_input["dependencies"]
        ],
        "analysis_method": "codex-semantic-v2",
        "coverage": work_input["coverage"],
        "results": _semantic_results(work_input),
    }
    payload["output_sha256"] = _sha256(_canonical_bytes(payload))
    write_managed_text(
        _output_path(prepared, str(work_input["unit_id"])),
        _canonical_text(payload),
    )


def _complete_work(prepared: PreparedFinalization) -> None:
    while True:
        current = next_semantic_work(
            NextSemanticWorkRequest(workspace=prepared.workspace)
        )
        if current.unit_id is None:
            assert current.remaining_count == 0
            return
        assert current.input_path is not None
        _write_valid_output(prepared, current.input_path)
        validate_semantic_work(
            ValidateSemanticWorkRequest(
                workspace=prepared.workspace,
                unit_id=current.unit_id,
            )
        )


@pytest.fixture
def completed_finalization(
    prepared_finalization: PreparedFinalization,
) -> PreparedFinalization:
    _complete_work(prepared_finalization)
    return prepared_finalization


def _read_manifest(prepared: PreparedFinalization) -> dict[str, object]:
    return json.loads(
        (prepared.paths.semantic_final_dir / "manifest.json").read_text(
            encoding="utf-8"
        )
    )


def _read_final_nodes(prepared: PreparedFinalization) -> list[dict[str, object]]:
    manifest = _read_manifest(prepared)
    nodes: list[dict[str, object]] = []
    for index in range(1, manifest["chunk_count"] + 1):
        payload = json.loads(
            (
                prepared.paths.semantic_final_dir
                / semantic_work_module.final_chunk_name(index)
            ).read_text(encoding="utf-8")
        )
        assert set(payload) == {
            "version",
            "protocol",
            "revision_id",
            "chunk_index",
            "nodes",
        }
        nodes.extend(payload["nodes"])
    return nodes


def test_finalize_rejects_incomplete_and_foreign_lifecycle_artifacts(
    prepared_finalization: PreparedFinalization,
) -> None:
    with pytest.raises(SemanticWorkError, match="incomplete"):
        finalize_semantic_index(
            FinalizeSemanticIndexRequest(prepared_finalization.workspace)
        )

    foreign = prepared_finalization.paths.semantic_work_output_dir / "foreign.json"
    foreign.write_text("{}", encoding="utf-8")
    with pytest.raises(SemanticWorkError, match="invalid artifacts"):
        finalize_semantic_index(
            FinalizeSemanticIndexRequest(prepared_finalization.workspace)
        )

    assert not (prepared_finalization.paths.semantic_final_dir / "manifest.json").exists()
    assert foreign.exists()


def test_finalize_compiles_exact_nodes_and_excludes_reduce_aggregates(
    completed_finalization: PreparedFinalization,
) -> None:
    result = finalize_semantic_index(
        FinalizeSemanticIndexRequest(completed_finalization.workspace)
    )
    nodes = _read_final_nodes(completed_finalization)

    assert result.revision_id == completed_finalization.revision_id
    assert result.node_count == len(nodes)
    assert len({node["node_id"] for node in nodes}) == len(nodes)
    assert all(set(node) == FINAL_NODE_KEYS for node in nodes)
    assert all(node["result_kind"] == "node" for node in nodes)
    assert all(set(node["semantic_facts"]) == SEMANTIC_FACT_KEYS for node in nodes)

    analyzed = [
        node for node in nodes if node["semantic_disposition"] == "analyzed"
    ]
    structural = [
        node for node in nodes if node["semantic_disposition"] != "analyzed"
    ]
    assert analyzed
    assert structural
    assert analyzed[0]["semantic_summary"]
    assert analyzed[0]["semantic_facts"]["purpose_signals"]
    assert analyzed[0]["semantic_facts"]["grounding_signal_ids"]
    assert all(node["semantic_summary"] == "" for node in structural)
    assert all(node["semantic_roles"] == [] for node in structural)
    assert all(node["topics"] == [] for node in structural)
    assert all(
        all(not values for values in node["semantic_facts"].values())
        for node in structural
    )
    structural_records = {
        record["node_id"]: record
        for path in sorted(
            completed_finalization.paths.semantic_revision_structural_dir(
                completed_finalization.revision_id
            ).glob("chunk-*.json")
        )
        for record in json.loads(path.read_text(encoding="utf-8"))["nodes"]
    }
    for node in structural:
        private_record = structural_records[node["node_id"]]
        assert node["skip_reason"] == private_record["skip_reason"]
        assert node["coverage"] == private_record["coverage"]
        assert node["analysis_status"] == private_record["analysis_status"]

    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(completed_finalization.paths.semantic_final_dir.iterdir())
    )
    assert '"result_kind":"aggregate"' not in serialized
    for forbidden in (
        "locator",
        "device",
        "inode",
        "file://",
        str(completed_finalization.workspace),
        "The application retrieves approved evidence",
    ):
        assert forbidden not in serialized


def test_high_fanout_finalization_restores_exact_private_child_topology(
    tmp_path: Path,
) -> None:
    """Catches allowing a bounded LLM projection to replace full topology."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text(
        "# Grounded project\n"
        "The application validates approved evidence and publishes results.",
        encoding="utf-8",
    )
    for index in range(3_584):
        (root / f"empty-{index:05}.txt").write_text("", encoding="utf-8")
    _approve_root(workspace, root)

    prepared_result = prepare_semantic_index(
        PrepareSemanticIndexRequest(
            workspace=workspace,
            root=root,
            protocol="codex-semantic-v2",
            chunk_size=1_000,
        )
    )
    prepared = PreparedFinalization(
        workspace=workspace,
        paths=WorkspacePaths.from_root(workspace),
        revision_id=prepared_result.revision_id,
    )
    _complete_work(prepared)

    structural_records = {
        record["node_id"]: record
        for path in sorted(
            prepared.paths.semantic_revision_structural_dir(
                prepared.revision_id
            ).glob("chunk-*.json")
        )
        for record in json.loads(path.read_text(encoding="utf-8"))["nodes"]
    }
    source_record = next(
        record
        for record in structural_records.values()
        if record["kind"] == "source"
    )
    source_input = next(
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(prepared.paths.semantic_work_input_dir.glob("unit-*.json"))
        if json.loads(path.read_text(encoding="utf-8"))["stage"] == "root"
    )
    topology_projection = source_input["targets"][0]["direct_child_topology"]

    assert len(source_record["child_node_ids"]) == 3_585
    assert topology_projection["direct_child_count"] == 3_585
    assert topology_projection["direct_child_ids_sha256"] == _sha256(
        _canonical_bytes(sorted(source_record["child_node_ids"]))
    )
    assert topology_projection["sample_child_node_ids"] == sorted(
        source_record["child_node_ids"]
    )[:64]
    assert len(topology_projection["sample_child_node_ids"]) == 64
    assert topology_projection["sample_complete"] is False
    assert len(_canonical_bytes(source_input)) <= MAX_WORK_UNIT_BYTES

    finalize_semantic_index(FinalizeSemanticIndexRequest(workspace))
    final_source = next(
        node for node in _read_final_nodes(prepared) if node["kind"] == "source"
    )

    assert final_source["child_node_ids"] == source_record["child_node_ids"]


@pytest.mark.parametrize("stale_kind", ("policy", "receipt", "revision"))
def test_finalize_revalidates_policy_receipts_and_staging_revision(
    completed_finalization: PreparedFinalization,
    stale_kind: str,
) -> None:
    if stale_kind == "policy":
        approval = json.loads(
            completed_finalization.paths.approval_path.read_text(encoding="utf-8")
        )
        approval["excluded_file_patterns"] = ["*.synthetic"]
        write_managed_text(
            completed_finalization.paths.approval_path,
            _canonical_text(approval),
        )
        expected = "policy"
    elif stale_kind == "receipt":
        receipt_path = next(
            iter(
                sorted(
                    completed_finalization.paths.semantic_work_receipts_dir.glob(
                        "unit-*.json"
                    )
                )
            )
        )
        write_managed_text(
            receipt_path,
            receipt_path.read_text(encoding="utf-8") + "\n",
        )
        expected = "lifecycle"
    else:
        SQLiteRepository(
            completed_finalization.paths.db_path
        ).fail_semantic_revision(completed_finalization.revision_id)
        expected = "staging"

    with pytest.raises(SemanticWorkError, match=expected):
        finalize_semantic_index(
            FinalizeSemanticIndexRequest(completed_finalization.workspace)
        )
    assert not (completed_finalization.paths.semantic_final_dir / "manifest.json").exists()


def test_finalize_manifest_is_content_free_and_exactly_hash_bound(
    completed_finalization: PreparedFinalization,
) -> None:
    result = finalize_semantic_index(
        FinalizeSemanticIndexRequest(completed_finalization.workspace)
    )
    manifest_path = result.manifest_path
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    unsigned = dict(manifest)
    manifest_sha256 = unsigned.pop("manifest_sha256")

    assert set(manifest) == FINAL_MANIFEST_KEYS
    assert manifest_sha256 == _sha256(_canonical_bytes(unsigned))
    assert manifest_bytes == _canonical_bytes(manifest)
    assert manifest["chunk_count"] == len(manifest["chunk_sha256s"])
    assert manifest["chunk_count"] == len(manifest["chunk_node_counts"])
    assert manifest["node_count"] == sum(manifest["chunk_node_counts"])
    assert (
        manifest["analysis_node_count"] + manifest["structural_node_count"]
        == manifest["node_count"]
    )
    input_manifest = json.loads(
        completed_finalization.paths.semantic_index_manifest_path.read_text(
            encoding="utf-8"
        )
    )
    assert manifest["input_manifest_sha256"] == input_manifest["manifest_sha256"]
    assert manifest["directory_timeout_seconds"] == 30.0
    assert manifest["timed_out_directory_count"] == 0
    assert manifest["unreadable_directory_count"] == 0

    chunk_paths = [
        completed_finalization.paths.semantic_final_dir
        / semantic_work_module.final_chunk_name(index)
        for index in range(1, manifest["chunk_count"] + 1)
    ]
    assert manifest["chunk_sha256s"] == [
        _sha256(path.read_bytes()) for path in chunk_paths
    ]
    receipt_paths = sorted(
        completed_finalization.paths.semantic_work_receipts_dir.glob("unit-*.json")
    )
    assert manifest["receipt_file_sha256s"] == [
        _sha256(path.read_bytes()) for path in receipt_paths
    ]
    ordered_node_ids = [
        node["node_id"] for node in _read_final_nodes(completed_finalization)
    ]
    assert manifest["node_ids_sha256"] == _sha256(
        _canonical_bytes(ordered_node_ids)
    )

    manifest_text = manifest_bytes.decode("utf-8")
    for content_key in (
        "semantic_summary",
        "semantic_facts",
        "display_name",
        "relative_hierarchy",
        "content_fingerprint",
        "evidence_ids",
        "locator",
        "signal",
    ):
        assert content_key not in manifest_text


@pytest.mark.parametrize(
    "mutation",
    ("noncanonical", "schema", "revision", "source", "children"),
)
def test_finalize_rejects_private_structural_drift(
    completed_finalization: PreparedFinalization,
    mutation: str,
) -> None:
    structural_path = next(
        iter(
            sorted(
                completed_finalization.paths.semantic_revision_structural_dir(
                    completed_finalization.revision_id
                ).glob("chunk-*.json")
            )
        )
    )
    payload = json.loads(structural_path.read_text(encoding="utf-8"))
    if mutation == "noncanonical":
        structural_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif mutation == "schema":
        payload["unexpected"] = True
        write_managed_text(structural_path, _canonical_text(payload))
    elif mutation == "revision":
        payload["revision_id"] = "different-revision"
        write_managed_text(structural_path, _canonical_text(payload))
    elif mutation == "source":
        payload["nodes"][0]["source_id"] = "different-source"
        write_managed_text(structural_path, _canonical_text(payload))
    else:
        record = next(
            node for node in payload["nodes"] if node["child_node_ids"]
        )
        record["child_node_ids"] = []
        write_managed_text(structural_path, _canonical_text(payload))

    with pytest.raises(SemanticWorkError, match="structural"):
        finalize_semantic_index(
            FinalizeSemanticIndexRequest(completed_finalization.workspace)
        )
    assert not (completed_finalization.paths.semantic_final_dir / "manifest.json").exists()


def test_finalize_never_reads_private_locator_directory(
    completed_finalization: PreparedFinalization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = semantic_work_module.read_managed_bytes
    locator_dir = completed_finalization.paths.semantic_revision_locator_dir(
        completed_finalization.revision_id
    )

    def guarded_read(path: Path) -> bytes:
        if path.parent == locator_dir:
            raise AssertionError("private locator directory was read")
        return original(path)

    monkeypatch.setattr(semantic_work_module, "read_managed_bytes", guarded_read)

    finalize_semantic_index(
        FinalizeSemanticIndexRequest(completed_finalization.workspace)
    )


def test_finalize_rejects_coherently_rebound_structural_locator_text(
    completed_finalization: PreparedFinalization,
) -> None:
    manifest_path = completed_finalization.paths.semantic_index_manifest_path
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    structural_node_id: str | None = None
    changed_chunk_index: int | None = None
    for index, chunk_path in enumerate(
        sorted(completed_finalization.paths.semantic_index_input_dir.glob("chunk-*.json"))
    ):
        payload = json.loads(chunk_path.read_text(encoding="utf-8"))
        for capsule in payload["capsules"]:
            if capsule["semantic_disposition"] != "analysis_required":
                structural_node_id = capsule["node_id"]
                capsule["display_name"] = "/tmp/x.py"
                content = _canonical_bytes(payload)
                write_managed_text(chunk_path, content.decode("utf-8"))
                manifest["capsule_chunk_sha256s"][index] = _sha256(content)
                changed_chunk_index = index
                break
        if structural_node_id is not None:
            break
    assert structural_node_id is not None
    assert changed_chunk_index is not None
    unsigned_manifest = dict(manifest)
    unsigned_manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = _sha256(
        _canonical_bytes(unsigned_manifest)
    )
    write_managed_text(manifest_path, _canonical_text(manifest))

    structural_path = next(
        iter(
            sorted(
                completed_finalization.paths.semantic_revision_structural_dir(
                    completed_finalization.revision_id
                ).glob("chunk-*.json")
            )
        )
    )
    structural = json.loads(structural_path.read_text(encoding="utf-8"))
    record = next(
        item for item in structural["nodes"] if item["node_id"] == structural_node_id
    )
    record["display_name"] = "/tmp/x.py"
    write_managed_text(structural_path, _canonical_text(structural))

    with pytest.raises(SemanticWorkError, match="structural"):
        finalize_semantic_index(
            FinalizeSemanticIndexRequest(completed_finalization.workspace)
        )
    assert not (completed_finalization.paths.semantic_final_dir / "manifest.json").exists()


def test_final_chunk_helpers_keep_one_thousand_boundary_and_no_global_cap() -> None:
    chunks = list(
        semantic_work_module._chunk_final_nodes(
            ({"node_id": str(index)} for index in range(1_001))
        )
    )

    assert [len(chunk) for chunk in chunks] == [1_000, 1]
    assert semantic_work_module.final_chunk_name(1) == "chunk-0001.json"
    assert semantic_work_module.final_chunk_name(10_000) == "chunk-10000.json"


def test_finalize_is_byte_idempotent(
    completed_finalization: PreparedFinalization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = finalize_semantic_index(
        FinalizeSemanticIndexRequest(completed_finalization.workspace)
    )
    before = {
        path.name: path.read_bytes()
        for path in completed_finalization.paths.semantic_final_dir.iterdir()
    }

    def unexpected_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("idempotent finalization rewrote managed output")

    monkeypatch.setattr(semantic_work_module, "write_managed_text", unexpected_write)
    monkeypatch.setattr(semantic_work_module, "remove_managed_file", unexpected_write)
    second = finalize_semantic_index(
        FinalizeSemanticIndexRequest(completed_finalization.workspace)
    )

    assert second == first
    assert {
        path.name: path.read_bytes()
        for path in completed_finalization.paths.semantic_final_dir.iterdir()
    } == before


def test_finalize_recovers_recognized_interrupted_set_with_manifest_last(
    completed_finalization: PreparedFinalization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = finalize_semantic_index(
        FinalizeSemanticIndexRequest(completed_finalization.workspace)
    )
    manifest = _read_manifest(completed_finalization)
    first_chunk = (
        completed_finalization.paths.semantic_final_dir
        / semantic_work_module.final_chunk_name(1)
    )
    write_managed_text(first_chunk, "{}")
    stale_chunk = completed_finalization.paths.semantic_final_dir / "chunk-9999.json"
    write_managed_text(stale_chunk, "{}")

    original_write = semantic_work_module.write_managed_text

    def interrupt_chunk(path: Path, content: str, *, overwrite: bool = True) -> Path:
        if path == first_chunk:
            raise OSError("synthetic write interruption")
        return original_write(path, content, overwrite=overwrite)

    monkeypatch.setattr(semantic_work_module, "write_managed_text", interrupt_chunk)
    with pytest.raises(SemanticWorkError, match="publication"):
        finalize_semantic_index(
            FinalizeSemanticIndexRequest(completed_finalization.workspace)
        )

    assert not first.manifest_path.exists()
    assert stale_chunk.exists()
    repository = SQLiteRepository(completed_finalization.paths.db_path)
    with repository._read_connection() as connection:
        status = connection.execute(
            "SELECT status FROM semantic_index_revisions WHERE id = ?",
            (completed_finalization.revision_id,),
        ).fetchone()["status"]
    assert status == "staging"

    monkeypatch.setattr(semantic_work_module, "write_managed_text", original_write)
    recovered = finalize_semantic_index(
        FinalizeSemanticIndexRequest(completed_finalization.workspace)
    )
    assert recovered.manifest_path.exists()
    assert not stale_chunk.exists()
    assert _read_manifest(completed_finalization) == manifest


@pytest.mark.parametrize("unsafe_kind", ("undeclared", "symlink"))
def test_finalize_rejects_unsafe_final_artifacts_without_deleting_them(
    completed_finalization: PreparedFinalization,
    unsafe_kind: str,
    tmp_path: Path,
) -> None:
    if unsafe_kind == "undeclared":
        unsafe_path = completed_finalization.paths.semantic_final_dir / "notes.json"
        unsafe_path.write_text("preserve", encoding="utf-8")
    else:
        external = tmp_path / "external.json"
        external.write_text("preserve", encoding="utf-8")
        unsafe_path = (
            completed_finalization.paths.semantic_final_dir / "chunk-0001.json"
        )
        unsafe_path.symlink_to(external)

    with pytest.raises(SemanticWorkError, match="final directory"):
        finalize_semantic_index(
            FinalizeSemanticIndexRequest(completed_finalization.workspace)
        )

    assert unsafe_path.exists()
    assert not (completed_finalization.paths.semantic_final_dir / "manifest.json").exists()


def test_finalize_cli_prints_counts_only(
    completed_finalization: PreparedFinalization,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "finalize-semantic-index",
            "--workspace",
            str(completed_finalization.workspace),
        ]
    )
    captured = capsys.readouterr()

    manifest = _read_manifest(completed_finalization)
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out.splitlines() == [
        f"Semantic revision: {completed_finalization.revision_id}",
        f"Final chunks: {manifest['chunk_count']}",
        f"Nodes: {manifest['node_count']}",
    ]
    for forbidden in (
        str(completed_finalization.workspace),
        "manifest",
        "receipt",
        "locator",
        "signal",
        "sha256",
    ):
        assert forbidden not in captured.out.casefold()


def test_finalize_cli_failure_is_controlled_and_private(
    prepared_finalization: PreparedFinalization,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "finalize-semantic-index",
            "--workspace",
            str(prepared_finalization.workspace),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "semantic work command failed safely\n"
    assert str(prepared_finalization.workspace) not in captured.err
