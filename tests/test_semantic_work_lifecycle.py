from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import portfolio_maker.application.semantic_index as semantic_index_module
import portfolio_maker.application.semantic_work as semantic_work_module
from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.models import (
    NextSemanticWorkRequest,
    PrepareSemanticIndexRequest,
    SemanticIndexStatusRequest,
    ValidateSemanticWorkRequest,
)
from portfolio_maker.application.semantic_index import prepare_semantic_index
from portfolio_maker.application.semantic_work import (
    SemanticWorkError,
    build_semantic_work_plan,
    next_semantic_work,
    semantic_index_status,
    semantic_work_plan_payload,
    validate_semantic_work,
)
from portfolio_maker.infrastructure.managed_files import write_managed_text
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


@dataclass(frozen=True)
class PreparedWork:
    workspace: Path
    paths: WorkspacePaths
    revision_id: str


def _canonical_text(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _approve_root(workspace: Path, root: Path) -> None:
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [root.resolve().as_uri()]
    paths.approval_path.write_text(_canonical_text(approval), encoding="utf-8")


@pytest.fixture
def prepared_work(workspace: Path, tmp_path: Path) -> PreparedWork:
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text(
        "OPENAI_API_KEY=sk-synthetic-placeholder\n"
        f"SOURCE_PATH={root}/private.txt\n"
        "# Safe evidence\nProject overview",
        encoding="utf-8",
    )
    (root / "guide.md").write_text("# Guide\nSetup details", encoding="utf-8")
    _approve_root(workspace, root)
    prepared = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=root, chunk_size=2)
    )
    return PreparedWork(
        workspace=workspace,
        paths=WorkspacePaths.from_root(workspace),
        revision_id=prepared.revision_id,
    )


def _output_path(prepared: PreparedWork, unit_id: str) -> Path:
    return prepared.paths.semantic_work_output_dir / f"{unit_id}.json"


def _receipt_path(prepared: PreparedWork, unit_id: str) -> Path:
    return prepared.paths.semantic_work_receipts_dir / f"{unit_id}.json"


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
                "member_node_ids_sha256": _sha256_text(
                    _canonical_text(child_result_ids)
                ),
                "member_count": len(child_result_ids),
            }
        ]
    results: list[object] = []
    for target in work_input["targets"]:
        own_signal_ids = [
            signal["signal_id"] for signal in target["signals"]
        ]
        grounding_ids = own_signal_ids[:1] or child_result_ids[:1]
        results.append(
            {
                "result_kind": "node",
                "node_id": target["node_id"],
                "semantic_summary": (
                    "이 구성요소는 검증된 근거를 바탕으로 제품 기능을 제공한다"
                ),
                "semantic_roles": ["application-component"],
                "topics": ["근거 기반 기능"],
                "purpose_signals": ["검증된 근거를 제품 기능에 연결한다"],
                "parent_context_signals": [],
                "independence_signals": [],
                "triviality_signals": [],
                "grounding_signal_ids": grounding_ids,
                "semantic_disposition": "analyzed",
                "analysis_status": target["analysis_status"],
                "child_node_ids": target["child_node_ids"],
            }
        )
    return results


def _write_valid_output(
    prepared: PreparedWork,
    input_path: Path,
    *,
    results: list[object] | None = None,
    overrides: dict[str, object] | None = None,
) -> Path:
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
        "results": (
            results
            if results is not None
            else _semantic_results(work_input)
        ),
    }
    if overrides:
        payload.update(overrides)
    payload["output_sha256"] = _sha256_text(_canonical_text(payload))
    output_path = _output_path(prepared, str(work_input["unit_id"]))
    write_managed_text(output_path, _canonical_text(payload))
    return output_path


def _validate_current(prepared: PreparedWork) -> tuple[str, str]:
    current = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared.workspace)
    )
    assert current.unit_id is not None
    assert current.input_path is not None
    _write_valid_output(prepared, current.input_path)
    validated = validate_semantic_work(
        ValidateSemanticWorkRequest(
            workspace=prepared.workspace,
            unit_id=current.unit_id,
        )
    )
    return current.unit_id, validated.output_sha256


def _complete_all(prepared: PreparedWork) -> list[str]:
    completed: list[str] = []
    while True:
        current = next_semantic_work(
            NextSemanticWorkRequest(workspace=prepared.workspace)
        )
        if current.unit_id is None:
            assert current.remaining_count == 0
            return completed
        unit_id, _ = _validate_current(prepared)
        completed.append(unit_id)


def test_status_and_next_use_verified_lexical_ready_order_idempotently(
    prepared_work: PreparedWork,
) -> None:
    before_paths = tuple(sorted(prepared_work.paths.semantic_index_dir.rglob("*")))
    status = semantic_index_status(
        SemanticIndexStatusRequest(workspace=prepared_work.workspace)
    )
    after_paths = tuple(sorted(prepared_work.paths.semantic_index_dir.rglob("*")))

    first = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )
    assert first.input_path is not None
    first_bytes = first.input_path.read_bytes()
    repeated = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )

    assert status.revision_id == prepared_work.revision_id
    assert status.node_count == 3
    assert status.work_unit_count == 3
    assert status.ready_count == 2
    assert status.receipted_count == 0
    assert status.invalid_count == 0
    assert status.complete is False
    assert before_paths == after_paths
    assert first.unit_id == repeated.unit_id == "unit-000001"
    assert repeated.input_path == first.input_path
    assert repeated.input_path.read_bytes() == first_bytes
    assert first.remaining_count == repeated.remaining_count == 3

    work_input = json.loads(first_bytes)
    assert set(work_input) == {
        "version",
        "revision_id",
        "unit_id",
        "stage",
        "input_sha256",
        "dependencies",
        "coverage",
        "targets",
        "child_results",
    }
    input_sha256 = work_input.pop("input_sha256")
    assert input_sha256 == _sha256_text(_canonical_text(work_input))
    serialized = first_bytes.decode("utf-8")
    assert "sk-synthetic-placeholder" not in serialized
    assert str(prepared_work.workspace) not in serialized
    assert "private.txt" not in serialized
    assert "SOURCE_PATH=[REDACTED]" in serialized


def test_receipts_advance_dependencies_and_complete_the_plan(
    prepared_work: PreparedWork,
) -> None:
    completed = _complete_all(prepared_work)
    status = semantic_index_status(
        SemanticIndexStatusRequest(workspace=prepared_work.workspace)
    )
    no_work = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )

    assert completed == ["unit-000001", "unit-000002", "unit-000003"]
    assert status.receipted_count == status.work_unit_count == 3
    assert status.ready_count == 0
    assert status.invalid_count == 0
    assert status.complete is True
    assert no_work.unit_id is None
    assert no_work.input_path is None
    assert no_work.remaining_count == 0

    for unit_id in completed:
        receipt = json.loads(
            _receipt_path(prepared_work, unit_id).read_text(encoding="utf-8")
        )
        assert set(receipt) == {
            "version",
            "revision_id",
            "unit_id",
            "input_sha256",
            "output_sha256",
            "dependency_output_sha256s",
            "validated_at",
            "receipt_sha256",
        }
        receipt_sha256 = receipt.pop("receipt_sha256")
        assert receipt_sha256 == _sha256_text(_canonical_text(receipt))


@pytest.mark.parametrize(
    "directory_attribute",
    (
        "semantic_work_input_dir",
        "semantic_work_output_dir",
        "semantic_work_receipts_dir",
    ),
)
def test_undeclared_lifecycle_artifact_prevents_false_completion(
    prepared_work: PreparedWork,
    directory_attribute: str,
) -> None:
    _complete_all(prepared_work)
    directory = getattr(prepared_work.paths, directory_attribute)
    unexpected_path = directory / "unexpected-canary.json"
    write_managed_text(unexpected_path, "{}")

    status = semantic_index_status(
        SemanticIndexStatusRequest(workspace=prepared_work.workspace)
    )

    assert status.complete is False
    assert status.invalid_count == 1
    with pytest.raises(SemanticWorkError, match="invalid"):
        next_semantic_work(
            NextSemanticWorkRequest(workspace=prepared_work.workspace)
        )
    assert unexpected_path.read_text(encoding="utf-8") == "{}"


def test_changed_dependency_output_invalidates_receipt_and_descendant_transitively(
    prepared_work: PreparedWork,
) -> None:
    completed = _complete_all(prepared_work)
    assert completed == ["unit-000001", "unit-000002", "unit-000003"]
    first_input = prepared_work.paths.semantic_work_input_dir / "unit-000001.json"
    _write_valid_output(
        prepared_work,
        first_input,
        results=[{"synthetic": "changed dependency"}],
    )

    status = semantic_index_status(
        SemanticIndexStatusRequest(workspace=prepared_work.workspace)
    )
    resumed = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )

    assert status.complete is False
    assert status.receipted_count == 1
    assert status.invalid_count == 2
    assert resumed.unit_id == "unit-000001"


@pytest.mark.parametrize(
    "target",
    ("manifest", "plan", "chunk", "unknown_chunk"),
)
def test_lifecycle_rejects_tampered_or_undeclared_managed_state(
    prepared_work: PreparedWork,
    target: str,
) -> None:
    manifest = json.loads(
        prepared_work.paths.semantic_index_manifest_path.read_text(encoding="utf-8")
    )
    if target == "manifest":
        manifest["work_plan_sha256"] = "0" * 64
        write_managed_text(
            prepared_work.paths.semantic_index_manifest_path,
            _canonical_text(manifest),
        )
    elif target == "plan":
        plan = json.loads(
            prepared_work.paths.semantic_work_plan_path.read_text(encoding="utf-8")
        )
        plan["revision_id"] = "cross-revision"
        plan_text = _canonical_text(plan)
        manifest["work_plan_sha256"] = _sha256_text(plan_text)
        write_managed_text(prepared_work.paths.semantic_work_plan_path, plan_text)
        write_managed_text(
            prepared_work.paths.semantic_index_manifest_path,
            _canonical_text(manifest),
        )
    elif target == "chunk":
        chunk_path = prepared_work.paths.semantic_index_input_dir / "chunk-0001.json"
        write_managed_text(chunk_path, '{"version":2,"capsules":[]}')
    else:
        write_managed_text(
            prepared_work.paths.semantic_index_input_dir / "chunk-9999.json",
            "{}",
        )

    with pytest.raises(SemanticWorkError, match="semantic work"):
        semantic_index_status(
            SemanticIndexStatusRequest(workspace=prepared_work.workspace)
        )


@pytest.mark.parametrize("mutation", ("duplicate_unit", "unknown_dependency"))
def test_lifecycle_rejects_invalid_plan_dag(
    prepared_work: PreparedWork,
    mutation: str,
) -> None:
    manifest = json.loads(
        prepared_work.paths.semantic_index_manifest_path.read_text(encoding="utf-8")
    )
    plan = json.loads(
        prepared_work.paths.semantic_work_plan_path.read_text(encoding="utf-8")
    )
    if mutation == "duplicate_unit":
        plan["units"][1]["unit_id"] = plan["units"][0]["unit_id"]
    else:
        plan["units"][0]["dependency_unit_ids"] = ["unit-999999"]
    plan_text = _canonical_text(plan)
    manifest["work_plan_sha256"] = _sha256_text(plan_text)
    write_managed_text(prepared_work.paths.semantic_work_plan_path, plan_text)
    write_managed_text(
        prepared_work.paths.semantic_index_manifest_path,
        _canonical_text(manifest),
    )

    with pytest.raises(SemanticWorkError, match="semantic work"):
        next_semantic_work(
            NextSemanticWorkRequest(workspace=prepared_work.workspace)
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"version": 1}, "output"),
        ({"revision_id": "cross-revision"}, "output"),
        ({"unit_id": "unit-999999"}, "output"),
        ({"stage": "root"}, "output"),
        ({"input_sha256": "0" * 64}, "output"),
        ({"dependency_output_sha256s": ["0" * 64]}, "output"),
        ({"unexpected": "value"}, "output"),
    ),
)
def test_validation_rejects_wrong_output_binding_without_receipt(
    prepared_work: PreparedWork,
    overrides: dict[str, object],
    message: str,
) -> None:
    current = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )
    assert current.unit_id is not None
    assert current.input_path is not None
    _write_valid_output(prepared_work, current.input_path, overrides=overrides)

    with pytest.raises(SemanticWorkError, match=message):
        validate_semantic_work(
            ValidateSemanticWorkRequest(
                workspace=prepared_work.workspace,
                unit_id=current.unit_id,
            )
        )

    assert not _receipt_path(prepared_work, current.unit_id).exists()


def test_validation_rejects_placeholder_semantics_without_receipt(
    prepared_work: PreparedWork,
) -> None:
    current = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )
    assert current.unit_id is not None
    assert current.input_path is not None
    work_input = json.loads(current.input_path.read_text(encoding="utf-8"))
    results = _semantic_results(work_input)
    results[0]["semantic_summary"] = "Indexed content."
    _write_valid_output(
        prepared_work,
        current.input_path,
        results=results,
    )

    with pytest.raises(SemanticWorkError, match="quality"):
        validate_semantic_work(
            ValidateSemanticWorkRequest(
                workspace=prepared_work.workspace,
                unit_id=current.unit_id,
            )
        )

    assert not _receipt_path(prepared_work, current.unit_id).exists()


def test_validation_is_idempotent_for_identical_input_output_and_dependencies(
    prepared_work: PreparedWork,
) -> None:
    current = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )
    assert current.unit_id is not None
    assert current.input_path is not None
    _write_valid_output(prepared_work, current.input_path)
    first = validate_semantic_work(
        ValidateSemanticWorkRequest(
            workspace=prepared_work.workspace,
            unit_id=current.unit_id,
        )
    )
    receipt_bytes = first.receipt_path.read_bytes()

    repeated = validate_semantic_work(
        ValidateSemanticWorkRequest(
            workspace=prepared_work.workspace,
            unit_id=current.unit_id,
        )
    )

    assert repeated == first
    assert repeated.receipt_path.read_bytes() == receipt_bytes


@pytest.mark.parametrize("target", ("input", "output", "receipt", "receipt_self_hash"))
def test_status_rejects_tampered_lifecycle_artifacts(
    prepared_work: PreparedWork,
    target: str,
) -> None:
    current = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )
    assert current.unit_id is not None
    assert current.input_path is not None
    output_path = _write_valid_output(prepared_work, current.input_path)
    validated = validate_semantic_work(
        ValidateSemanticWorkRequest(
            workspace=prepared_work.workspace,
            unit_id=current.unit_id,
        )
    )
    if target == "input":
        payload = json.loads(current.input_path.read_text(encoding="utf-8"))
        payload["coverage"]["node_count"] += 1
        write_managed_text(current.input_path, _canonical_text(payload))
    elif target == "output":
        write_managed_text(
            output_path,
            output_path.read_text(encoding="utf-8") + " ",
        )
    else:
        receipt = json.loads(validated.receipt_path.read_text(encoding="utf-8"))
        if target == "receipt":
            receipt["input_sha256"] = "0" * 64
        else:
            receipt["receipt_sha256"] = "0" * 64
        write_managed_text(validated.receipt_path, _canonical_text(receipt))

    status = semantic_index_status(
        SemanticIndexStatusRequest(workspace=prepared_work.workspace)
    )

    assert status.receipted_count == 0
    assert status.invalid_count == 1
    assert status.complete is False


def test_validation_rejects_duplicate_key_output_without_receipt(
    prepared_work: PreparedWork,
) -> None:
    current = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )
    assert current.unit_id is not None
    assert current.input_path is not None
    output_path = _write_valid_output(prepared_work, current.input_path)
    content = output_path.read_text(encoding="utf-8")
    write_managed_text(
        output_path,
        content[:-1] + ',"version":2}',
    )

    with pytest.raises(SemanticWorkError, match="output"):
        validate_semantic_work(
            ValidateSemanticWorkRequest(
                workspace=prepared_work.workspace,
                unit_id=current.unit_id,
            )
        )

    assert not _receipt_path(prepared_work, current.unit_id).exists()


def test_validation_rechecks_output_before_publishing_receipt(
    prepared_work: PreparedWork,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )
    assert current.unit_id is not None
    assert current.input_path is not None
    output_path = _write_valid_output(prepared_work, current.input_path)
    original_read = semantic_work_module.read_managed_bytes
    output_reads = 0

    def replace_before_final_read(path: Path) -> bytes:
        nonlocal output_reads
        if path == output_path:
            output_reads += 1
            if output_reads == 4:
                work_input = json.loads(
                    current.input_path.read_text(encoding="utf-8")
                )
                replacement_results = _semantic_results(work_input)
                replacement_results[0]["semantic_summary"] = (
                    "이 구성요소는 재검증 중 교체된 별도 제품 기능을 제공한다"
                )
                _write_valid_output(
                    prepared_work,
                    current.input_path,
                    results=replacement_results,
                )
        return original_read(path)

    monkeypatch.setattr(
        semantic_work_module,
        "read_managed_bytes",
        replace_before_final_read,
    )

    with pytest.raises(SemanticWorkError, match="state changed"):
        validate_semantic_work(
            ValidateSemanticWorkRequest(
                workspace=prepared_work.workspace,
                unit_id=current.unit_id,
            )
        )

    assert output_reads >= 4
    assert not _receipt_path(prepared_work, current.unit_id).exists()


@pytest.mark.parametrize("mutation", ("status", "policy_hash"))
def test_lifecycle_revalidates_staging_database_binding(
    prepared_work: PreparedWork,
    mutation: str,
) -> None:
    repository = SQLiteRepository(prepared_work.paths.db_path)
    with repository._connection() as connection:
        if mutation == "status":
            connection.execute(
                """
                UPDATE semantic_index_revisions
                SET status = 'failed'
                WHERE id = ?
                """,
                (prepared_work.revision_id,),
            )
        else:
            connection.execute(
                """
                UPDATE semantic_index_revisions
                SET policy_hash = ?
                WHERE id = ?
                """,
                ("0" * 64, prepared_work.revision_id),
            )

    with pytest.raises(SemanticWorkError, match="revision"):
        semantic_index_status(
            SemanticIndexStatusRequest(workspace=prepared_work.workspace)
        )


def test_validation_rejects_missing_output_without_receipt(
    prepared_work: PreparedWork,
) -> None:
    current = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )
    assert current.unit_id is not None

    with pytest.raises(SemanticWorkError, match="output"):
        validate_semantic_work(
            ValidateSemanticWorkRequest(
                workspace=prepared_work.workspace,
                unit_id=current.unit_id,
            )
        )

    assert not _receipt_path(prepared_work, current.unit_id).exists()


def test_oversize_dependency_context_is_rejected_before_materialization(
    prepared_work: PreparedWork,
) -> None:
    first = next_semantic_work(
        NextSemanticWorkRequest(workspace=prepared_work.workspace)
    )
    assert first.input_path is not None
    work_input = json.loads(first.input_path.read_text(encoding="utf-8"))
    oversized_results = _semantic_results(work_input)
    oversized_results[0]["purpose_signals"] = [
        "이 구성요소는 상위 근거 전달을 위해 " + "x" * 270_000
    ]
    _write_valid_output(
        prepared_work,
        first.input_path,
        results=oversized_results,
    )
    validate_semantic_work(
        ValidateSemanticWorkRequest(prepared_work.workspace, first.unit_id or "")
    )
    _validate_current(prepared_work)

    with pytest.raises(SemanticWorkError, match="byte budget"):
        next_semantic_work(
            NextSemanticWorkRequest(workspace=prepared_work.workspace)
        )


def _synthetic_capsule(index: int) -> dict[str, object]:
    node_id = f"node-{index:05}"
    return {
        "node_id": node_id,
        "parent_node_id": None,
        "kind": "file",
        "display_name": node_id,
        "relative_hierarchy": f"{node_id}.md",
        "content_fingerprint": None,
        "analysis_status": "complete",
        "semantic_disposition": "analysis_required",
        "roles": [],
        "child_node_ids": [],
        "signals": [],
        "skip_reason": None,
        "evidence_ids": [],
        "coverage": {
            "node_count": 1,
            "content_node_count": 0,
            "structural_only_count": 0,
            "failed_count": 0,
            "node_ids_sha256": hashlib.sha256(node_id.encode("utf-8")).hexdigest(),
        },
    }


def _synthetic_capsule_with_disposition(
    index: int,
    disposition: str,
) -> dict[str, object]:
    capsule = _synthetic_capsule(index)
    capsule["semantic_disposition"] = disposition
    if disposition != "analysis_required":
        capsule["skip_reason"] = (
            "no_text_signal" if disposition == "structural_only" else None
        )
        capsule["coverage"]["structural_only_count"] = 1
        capsule["coverage"]["failed_count"] = int(
            disposition in {"unsupported", "unreadable"}
        )
    if disposition == "unsupported":
        capsule["analysis_status"] = "unsupported"
    elif disposition == "unreadable":
        capsule["analysis_status"] = "unreadable"
    return capsule


def _write_synthetic_v2_binding_state(
    paths: WorkspacePaths,
    capsules: list[dict[str, object]],
    *,
    chunk_size: int = 1_000,
) -> tuple[dict[str, object], object]:
    revision_id = "synthetic-binding-revision"
    policy_hash = "a" * 64
    plan = build_semantic_work_plan(
        tuple(capsules),
        revision_id=revision_id,
        policy_hash=policy_hash,
    )
    capsule_texts: list[str] = []
    for chunk_index, offset in enumerate(
        range(0, len(capsules), chunk_size),
        start=1,
    ):
        text = _canonical_text(
            {
                "version": 2,
                "revision_id": revision_id,
                "capsules": capsules[offset : offset + chunk_size],
            }
        )
        capsule_texts.append(text)
        write_managed_text(
            paths.semantic_index_input_dir / f"chunk-{chunk_index:04}.json",
            text,
        )
    plan_text = _canonical_text(semantic_work_plan_payload(plan))
    write_managed_text(paths.semantic_work_plan_path, plan_text)
    input_manifest = {
        "version": 2,
        "protocol": "codex-semantic-v2",
        "revision_id": revision_id,
        "source_id": "synthetic-source",
        "policy_hash": policy_hash,
        "analyzer_version": "semantic-input-v2",
        "capsule_chunk_sha256s": [
            _sha256_text(text) for text in capsule_texts
        ],
        "work_plan_sha256": _sha256_text(plan_text),
        "node_count": len(capsules),
        "work_unit_count": len(plan.units),
        "managed_input_bytes": len(plan_text.encode("utf-8"))
        + sum(len(text.encode("utf-8")) for text in capsule_texts),
        "analysis_target_count": plan.analysis_target_count,
        "structural_only_count": plan.structural_owner_count,
    }
    write_managed_text(
        paths.semantic_index_manifest_path,
        _canonical_text(input_manifest),
    )
    receipt_text = "{}"
    for unit in plan.units:
        write_managed_text(
            paths.semantic_work_receipts_dir / f"{unit.unit_id}.json",
            receipt_text,
        )
    final_manifest = {
        "revision_id": revision_id,
        "source_id": "synthetic-source",
        "policy_hash": policy_hash,
        "analyzer_version": "semantic-input-v2",
        "work_plan_sha256": _sha256_text(plan_text),
        "node_assignment_sha256": plan.node_assignment_sha256,
        "node_count": len(capsules),
        "analysis_node_count": plan.analysis_target_count,
        "structural_node_count": plan.structural_owner_count,
        "receipt_file_sha256s": [
            _sha256_text(receipt_text) for _ in plan.units
        ],
    }
    return final_manifest, plan


def _rewrite_synthetic_capsule_chunk(
    paths: WorkspacePaths,
    chunk_index: int,
    mutate,
) -> None:
    chunk_path = (
        paths.semantic_index_input_dir / f"chunk-{chunk_index:04}.json"
    )
    chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
    mutate(chunk["capsules"])
    chunk_text = _canonical_text(chunk)
    write_managed_text(chunk_path, chunk_text)

    manifest = json.loads(
        paths.semantic_index_manifest_path.read_text(encoding="utf-8")
    )
    manifest["capsule_chunk_sha256s"][chunk_index - 1] = _sha256_text(
        chunk_text
    )
    plan_bytes = paths.semantic_work_plan_path.read_bytes()
    manifest["managed_input_bytes"] = len(plan_bytes) + sum(
        len(
            (
                paths.semantic_index_input_dir / f"chunk-{index:04}.json"
            ).read_bytes()
        )
        for index in range(1, len(manifest["capsule_chunk_sha256s"]) + 1)
    )
    write_managed_text(
        paths.semantic_index_manifest_path,
        _canonical_text(manifest),
    )


def _accept_synthetic_receipts(
    monkeypatch: pytest.MonkeyPatch,
    unit_count: int,
) -> None:
    monkeypatch.setattr(
        semantic_work_module,
        "_evaluate_lifecycle",
        lambda state, receipt_store=None: SimpleNamespace(
            invalid_count=0,
            ready_unit_ids=(),
            valid_receipts={
                f"unit-{index:06}": object()
                for index in range(1, unit_count + 1)
            },
        ),
    )


def test_status_has_no_global_cap_for_10001_units(workspace: Path) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    revision_id = "synthetic-revision"
    policy_hash = "a" * 64
    capsules = tuple(_synthetic_capsule(index) for index in range(10_001))
    plan = build_semantic_work_plan(
        capsules,
        revision_id=revision_id,
        policy_hash=policy_hash,
    )
    chunk_text = _canonical_text(
        {
            "version": 2,
            "revision_id": revision_id,
            "capsules": list(capsules),
        }
    )
    plan_text = _canonical_text(semantic_work_plan_payload(plan))
    manifest_text = _canonical_text(
        {
            "version": 2,
            "protocol": "codex-semantic-v2",
            "revision_id": revision_id,
            "source_id": "synthetic-source",
            "policy_hash": policy_hash,
            "analyzer_version": "semantic-input-v2",
            "capsule_chunk_sha256s": [_sha256_text(chunk_text)],
            "work_plan_sha256": _sha256_text(plan_text),
            "node_count": len(capsules),
            "work_unit_count": len(plan.units),
            "managed_input_bytes": len(chunk_text.encode()) + len(plan_text.encode()),
            "analysis_target_count": len(capsules),
            "structural_only_count": 0,
        }
    )
    write_managed_text(paths.semantic_index_input_dir / "chunk-0001.json", chunk_text)
    write_managed_text(paths.semantic_work_plan_path, plan_text)
    write_managed_text(paths.semantic_index_manifest_path, manifest_text)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.create_semantic_revision(
        revision_id,
        "synthetic-source",
        policy_hash,
        "semantic-input-v2",
    )

    status = semantic_index_status(SemanticIndexStatusRequest(workspace=workspace))

    assert status.work_unit_count == 10_001
    assert status.ready_count == 10_001
    assert status.invalid_count == 0


def test_v2_apply_plan_ownership_uses_scratch_for_10001_analysis_targets(
    workspace: Path,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    capsules = tuple(_synthetic_capsule(index) for index in range(10_001))
    plan = build_semantic_work_plan(
        capsules,
        revision_id="synthetic-owner-revision",
        policy_hash="a" * 64,
    )
    plan_payload = semantic_work_plan_payload(plan)
    manifest = {
        "revision_id": plan.revision_id,
        "policy_hash": plan.policy_hash,
        "node_count": len(capsules),
        "analysis_target_count": len(capsules),
        "structural_only_count": 0,
        "work_unit_count": len(plan.units),
    }

    with semantic_index_module._SemanticBindingScratch() as scratch:
        verified_units = semantic_work_module._validate_plan_structure(
            plan_payload,
            manifest,
            owner_sink=scratch.insert_plan_owner,
        )

        assert len(verified_units) == 10_001
        assert scratch.plan_owner_count() == 10_001
        assert scratch.plan_owner("node-00000") == "unit-000001"
        assert scratch.plan_owner("node-10000") == "unit-010001"


@pytest.mark.parametrize(
    ("mutation", "mutate"),
    (
        (
            "unknown-disposition",
            lambda capsule: capsule.__setitem__(
                "semantic_disposition",
                "not-a-valid-disposition",
            ),
        ),
        (
            "extra-field",
            lambda capsule: capsule.__setitem__("unexpected", True),
        ),
        (
            "missing-field",
            lambda capsule: capsule.pop("roles"),
        ),
    ),
)
def test_v2_apply_rejects_rehashed_inexact_capsule_contract(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    mutate,
) -> None:
    del mutation
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    capsules = [
        _synthetic_capsule_with_disposition(0, "analysis_required"),
        _synthetic_capsule_with_disposition(1, "structural_only"),
    ]
    final_manifest, plan = _write_synthetic_v2_binding_state(
        paths,
        capsules,
    )
    _rewrite_synthetic_capsule_chunk(
        paths,
        1,
        lambda raw_capsules: mutate(raw_capsules[1]),
    )
    _accept_synthetic_receipts(monkeypatch, len(plan.units))

    with pytest.raises(
        semantic_index_module.SemanticIndexError,
        match="capsule input",
    ):
        semantic_index_module._validate_v2_input_and_work_bindings(
            paths,
            final_manifest,
        )


def test_v2_apply_accepts_every_protocol_v2_capsule_disposition(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    capsules = [
        _synthetic_capsule_with_disposition(0, "analysis_required"),
        _synthetic_capsule_with_disposition(1, "structural_only"),
        _synthetic_capsule_with_disposition(2, "unsupported"),
        _synthetic_capsule_with_disposition(3, "unreadable"),
    ]
    final_manifest, plan = _write_synthetic_v2_binding_state(
        paths,
        capsules,
    )
    _accept_synthetic_receipts(monkeypatch, len(plan.units))

    semantic_index_module._validate_v2_input_and_work_bindings(
        paths,
        final_manifest,
    )


@pytest.mark.parametrize(
    "mutation",
    ("missing-analysis-owner", "structural-with-owner"),
)
def test_v2_apply_rejects_capsule_plan_ownership_mismatch(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    capsules = [
        _synthetic_capsule_with_disposition(0, "analysis_required"),
        _synthetic_capsule_with_disposition(1, "structural_only"),
    ]
    final_manifest, plan = _write_synthetic_v2_binding_state(
        paths,
        capsules,
    )

    def mutate(raw_capsules: list[dict[str, object]]) -> None:
        if mutation == "missing-analysis-owner":
            raw_capsules[1]["semantic_disposition"] = "analysis_required"
            raw_capsules[1]["skip_reason"] = None
        else:
            raw_capsules[0]["semantic_disposition"] = "structural_only"
            raw_capsules[0]["skip_reason"] = "no_text_signal"

    _rewrite_synthetic_capsule_chunk(paths, 1, mutate)
    _accept_synthetic_receipts(monkeypatch, len(plan.units))

    with pytest.raises(
        semantic_index_module.SemanticIndexError,
        match="ownership|work plan",
    ):
        semantic_index_module._validate_v2_input_and_work_bindings(
            paths,
            final_manifest,
        )


def test_v2_apply_rejects_duplicate_capsule_across_chunks(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    capsules = [
        _synthetic_capsule_with_disposition(0, "analysis_required"),
        _synthetic_capsule_with_disposition(1, "structural_only"),
        _synthetic_capsule_with_disposition(2, "unsupported"),
    ]
    final_manifest, plan = _write_synthetic_v2_binding_state(
        paths,
        capsules,
        chunk_size=2,
    )
    first_chunk = json.loads(
        (
            paths.semantic_index_input_dir / "chunk-0001.json"
        ).read_text(encoding="utf-8")
    )
    duplicate = first_chunk["capsules"][1]
    _rewrite_synthetic_capsule_chunk(
        paths,
        2,
        lambda raw_capsules: raw_capsules.__setitem__(0, duplicate),
    )
    _accept_synthetic_receipts(monkeypatch, len(plan.units))

    with pytest.raises(
        semantic_index_module.SemanticIndexError,
        match="capsule input",
    ):
        semantic_index_module._validate_v2_input_and_work_bindings(
            paths,
            final_manifest,
        )


def test_v2_apply_rejects_duplicate_plan_ownership(
    workspace: Path,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    capsules = tuple(_synthetic_capsule(index) for index in range(2))
    plan = semantic_work_plan_payload(
        build_semantic_work_plan(
            capsules,
            revision_id="synthetic-duplicate-owner",
            policy_hash="a" * 64,
        )
    )
    plan["units"][1]["target_node_ids"] = list(
        plan["units"][0]["target_node_ids"]
    )
    manifest = {
        "revision_id": plan["revision_id"],
        "policy_hash": plan["policy_hash"],
        "node_count": 2,
        "analysis_target_count": 2,
        "structural_only_count": 0,
        "work_unit_count": 2,
    }

    with semantic_index_module._SemanticBindingScratch() as scratch:
        with pytest.raises(
            semantic_index_module.SemanticIndexError,
            match="work plan",
        ):
            semantic_work_module._validate_plan_structure(
                plan,
                manifest,
                owner_sink=scratch.insert_plan_owner,
            )


def test_v2_apply_binding_validation_keeps_only_one_capsule_chunk_in_memory(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    revision_id = "synthetic-bounded-revision"
    policy_hash = "a" * 64
    capsules = [_synthetic_capsule(index) for index in range(10_001)]
    plan = build_semantic_work_plan(
        tuple(capsules),
        revision_id=revision_id,
        policy_hash=policy_hash,
    )
    assert len(plan.units) == 10_001

    capsule_texts: list[str] = []
    for chunk_index, offset in enumerate(range(0, len(capsules), 1_000), start=1):
        text = _canonical_text(
            {
                "version": 2,
                "revision_id": revision_id,
                "capsules": capsules[offset : offset + 1_000],
            }
        )
        capsule_texts.append(text)
        write_managed_text(
            paths.semantic_index_input_dir / f"chunk-{chunk_index:04}.json",
            text,
        )
    plan_text = _canonical_text(semantic_work_plan_payload(plan))
    write_managed_text(paths.semantic_work_plan_path, plan_text)
    input_manifest = {
        "version": 2,
        "protocol": "codex-semantic-v2",
        "revision_id": revision_id,
        "source_id": "synthetic-source",
        "policy_hash": policy_hash,
        "analyzer_version": "semantic-input-v2",
        "capsule_chunk_sha256s": [
            _sha256_text(text) for text in capsule_texts
        ],
        "work_plan_sha256": _sha256_text(plan_text),
        "node_count": len(capsules),
        "work_unit_count": len(plan.units),
        "managed_input_bytes": len(plan_text.encode("utf-8"))
        + sum(len(text.encode("utf-8")) for text in capsule_texts),
        "analysis_target_count": 10_001,
        "structural_only_count": 0,
    }
    write_managed_text(
        paths.semantic_index_manifest_path,
        _canonical_text(input_manifest),
    )
    receipt_text = "{}"
    final_manifest = {
        "revision_id": revision_id,
        "source_id": "synthetic-source",
        "policy_hash": policy_hash,
        "analyzer_version": "semantic-input-v2",
        "work_plan_sha256": _sha256_text(plan_text),
        "node_assignment_sha256": plan.node_assignment_sha256,
        "node_count": len(capsules),
        "analysis_node_count": 10_001,
        "structural_node_count": 0,
        "receipt_file_sha256s": [
            _sha256_text(receipt_text) for _ in plan.units
        ],
    }

    observed_chunks: list[tuple[int, int, int]] = []
    owner_inserts = 0
    scratch_paths: list[Path] = []
    original_insert_owner = (
        semantic_index_module._SemanticBindingScratch.insert_plan_owner
    )
    original_managed_names = (
        semantic_index_module._managed_regular_file_names
    )
    original_read_content = semantic_index_module._read_managed_content

    def capture_owner(
        scratch,
        node_id: str,
        owner_unit_id: str,
    ) -> None:
        nonlocal owner_inserts
        owner_inserts += 1
        original_insert_owner(scratch, node_id, owner_unit_id)

    def managed_names(directory: Path) -> set[str]:
        if directory == paths.semantic_work_receipts_dir:
            return {
                f"{unit.unit_id}.json" for unit in plan.units
            }
        return original_managed_names(directory)

    def read_content(path: Path, label: str) -> bytes:
        if path.parent == paths.semantic_work_receipts_dir:
            return receipt_text.encode("utf-8")
        return original_read_content(path, label)

    def observe_scratch(path: Path) -> None:
        scratch_paths.append(path)
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    monkeypatch.setattr(
        semantic_index_module._SemanticBindingScratch,
        "insert_plan_owner",
        capture_owner,
    )
    monkeypatch.setattr(
        semantic_work_module,
        "_evaluate_lifecycle",
        lambda state, receipt_store=None: SimpleNamespace(
            invalid_count=0,
            ready_unit_ids=(),
            valid_receipts=range(len(plan.units)),
        ),
    )
    monkeypatch.setattr(
        semantic_index_module,
        "_managed_regular_file_names",
        managed_names,
    )
    monkeypatch.setattr(
        semantic_index_module,
        "_read_managed_content",
        read_content,
    )
    monkeypatch.setattr(
        semantic_index_module,
        "_observe_v2_capsule_chunk",
        lambda count, byte_count, running_total: observed_chunks.append(
            (count, byte_count, running_total)
        ),
        raising=False,
    )
    monkeypatch.setattr(
        semantic_index_module,
        "_observe_v2_binding_scratch",
        observe_scratch,
    )

    semantic_index_module._validate_v2_input_and_work_bindings(
        paths,
        final_manifest,
    )

    assert owner_inserts == 10_001
    assert sum(count for count, _, _ in observed_chunks) == 10_001
    assert max(count for count, _, _ in observed_chunks) <= 1_000
    assert [byte_count for _, byte_count, _ in observed_chunks] == [
        len(text.encode("utf-8")) for text in capsule_texts
    ]
    assert [running_total for _, _, running_total in observed_chunks] == [
        len(plan_text.encode("utf-8"))
        + sum(len(text.encode("utf-8")) for text in capsule_texts[:index])
        for index in range(1, len(capsule_texts) + 1)
    ]
    assert len(scratch_paths) == 1
    assert not scratch_paths[0].parent.exists()
    assert workspace not in scratch_paths[0].parents
