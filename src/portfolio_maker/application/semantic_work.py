from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from portfolio_maker.application.models import (
    FinalizeSemanticIndexRequest,
    FinalizeSemanticIndexResult,
    NextSemanticWorkRequest,
    NextSemanticWorkResult,
    SemanticIndexStatusRequest,
    SemanticIndexStatusResult,
    SemanticIndexStatusV3Result,
    SemanticIndexStatusV3Request,
    ValidateSemanticWorkRequest,
    ValidateSemanticWorkResult,
)
from portfolio_maker.application.semantic_quality import (
    SemanticQualityError,
    ValidatedSemanticNodeResult,
    ValidatedSemanticWorkOutput,
    validate_semantic_work_output,
)
from portfolio_maker.domain.history_models import utc_now
from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.domain.semantic_work import (
    SemanticDisposition,
    SemanticWorkPlan,
    SemanticWorkStage,
    SemanticWorkUnit,
)
from portfolio_maker.infrastructure.managed_files import (
    ensure_managed_directory,
    read_managed_bytes,
    remove_managed_file,
    write_managed_text,
)
from portfolio_maker.infrastructure.policy import (
    contains_hidden_secret_shaped_public_value,
    mask_public_value,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.semantic_crawler import (
    MAX_DIRECTORY_TIMEOUT_SECONDS,
)
from portfolio_maker.workspace import WorkspacePaths


MAX_WORK_UNIT_BYTES = 262_144
REDUCE_FAN_IN = 64
SEMANTIC_CHILD_SAMPLE_LIMIT = 64
STRUCTURAL_CHUNK_SIZE = 1_000
_VALID_DISPOSITIONS = {disposition.value for disposition in SemanticDisposition}
_VALID_STAGES = {stage.value for stage in SemanticWorkStage}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9:._-]+")
_UNIT_ID = re.compile(r"unit-[0-9]{6,}")
_FINAL_CHUNK_NAME = re.compile(r"chunk-([0-9]{4,})\.json")
_MANIFEST_KEYS = {
    "version",
    "protocol",
    "revision_id",
    "source_id",
    "policy_hash",
    "analyzer_version",
    "capsule_chunk_sha256s",
    "work_plan_sha256",
    "node_count",
    "work_unit_count",
    "managed_input_bytes",
    "analysis_target_count",
    "structural_only_count",
    "directory_timeout_seconds",
    "timed_out_directory_count",
    "unreadable_directory_count",
    "manifest_sha256",
}
_PLAN_KEYS = {
    "version",
    "revision_id",
    "policy_hash",
    "protocol",
    "node_count",
    "analysis_target_count",
    "structural_owner_count",
    "work_unit_count",
    "max_work_unit_bytes",
    "reduce_fan_in",
    "node_assignment_sha256",
    "units",
}
_UNIT_KEYS = {
    "unit_id",
    "stage",
    "target_node_ids",
    "dependency_unit_ids",
    "template_sha256",
    "template_byte_count",
}
_CAPSULE_KEYS = {
    "node_id",
    "parent_node_id",
    "kind",
    "display_name",
    "relative_hierarchy",
    "content_fingerprint",
    "analysis_status",
    "semantic_disposition",
    "roles",
    "child_node_ids",
    "signals",
    "skip_reason",
    "evidence_ids",
    "coverage",
}
_SIGNAL_KEYS = {"signal_id", "kind", "text"}
_INPUT_KEYS = {
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
_OUTPUT_KEYS = {
    "version",
    "revision_id",
    "unit_id",
    "stage",
    "input_sha256",
    "dependency_output_sha256s",
    "analysis_method",
    "coverage",
    "results",
    "output_sha256",
}
_RECEIPT_KEYS = {
    "version",
    "revision_id",
    "unit_id",
    "input_sha256",
    "output_sha256",
    "dependency_output_sha256s",
    "validated_at",
    "receipt_sha256",
}
_STRUCTURAL_RECORD_KEYS = {
    "node_id",
    "source_id",
    "parent_node_id",
    "kind",
    "display_name",
    "relative_hierarchy",
    "content_fingerprint",
    "analysis_status",
    "semantic_disposition",
    "roles",
    "child_node_ids",
    "skip_reason",
    "evidence_ids",
    "coverage",
}
_COVERAGE_KEYS = {
    "node_count",
    "content_node_count",
    "structural_only_count",
    "failed_count",
    "node_ids_sha256",
}
_FINAL_MANIFEST_KEYS = {
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
}


class SemanticWorkError(ValueError):
    pass


@dataclass(frozen=True)
class _VerifiedUnit:
    unit_id: str
    stage: str
    target_node_ids: tuple[str, ...]
    dependency_unit_ids: tuple[str, ...]
    template_sha256: str
    template_byte_count: int


@dataclass(frozen=True)
class _VerifiedState:
    paths: WorkspacePaths
    revision_id: str
    source_id: str
    policy_hash: str
    analyzer_version: str
    work_plan_sha256: str
    input_manifest_sha256: str
    directory_timeout_seconds: float
    timed_out_directory_count: int
    unreadable_directory_count: int
    node_assignment_sha256: str
    node_count: int
    analysis_node_count: int
    structural_node_count: int
    max_work_unit_bytes: int
    capsules: Mapping[str, dict[str, object]]
    units: tuple[_VerifiedUnit, ...]
    unit_by_id: Mapping[str, _VerifiedUnit]


@dataclass(frozen=True)
class _OutputBinding:
    output_sha256: str
    payload: dict[str, object]
    content: bytes
    validated: ValidatedSemanticWorkOutput | None


@dataclass(frozen=True)
class _ReceiptBinding:
    input_sha256: str
    output: _OutputBinding
    dependency_output_sha256s: tuple[str, ...]
    content: bytes


@dataclass(frozen=True)
class _LifecycleEvaluation:
    valid_receipts: Mapping[str, _ReceiptBinding]
    ready_unit_ids: tuple[str, ...]
    invalid_count: int
    undeclared_artifact_count: int


@dataclass(frozen=True)
class _FinalizationMetadata:
    manifest: dict[str, object]
    manifest_content: bytes
    chunk_sha256s: tuple[str, ...]
    chunk_node_counts: tuple[int, ...]


def build_semantic_work_plan(
    capsules: tuple[dict[str, object], ...],
    *,
    revision_id: str,
    policy_hash: str,
    max_work_unit_bytes: int = MAX_WORK_UNIT_BYTES,
    reduce_fan_in: int = REDUCE_FAN_IN,
) -> SemanticWorkPlan:
    if (
        not capsules
        or max_work_unit_bytes <= 0
        or reduce_fan_in <= 0
    ):
        raise SemanticWorkError("semantic work ownership is invalid")
    ordered = tuple(
        sorted(
            capsules,
            key=lambda capsule: (
                str(capsule.get("relative_hierarchy", "")),
                str(capsule.get("node_id", "")),
            ),
        )
    )
    capsule_by_id: dict[str, dict[str, object]] = {}
    for capsule in ordered:
        node_id = capsule.get("node_id")
        disposition = capsule.get("semantic_disposition")
        if (
            not isinstance(node_id, str)
            or not node_id
            or node_id in capsule_by_id
            or disposition not in _VALID_DISPOSITIONS
        ):
            raise SemanticWorkError("semantic work ownership is invalid")
        capsule_by_id[node_id] = capsule

    children_by_parent: dict[str, list[str]] = {}
    for node_id, capsule in capsule_by_id.items():
        parent_id = capsule.get("parent_node_id")
        if isinstance(parent_id, str) and parent_id in capsule_by_id:
            children_by_parent.setdefault(parent_id, []).append(node_id)
    for child_ids in children_by_parent.values():
        child_ids.sort()

    units: list[SemanticWorkUnit] = []
    unit_weights: dict[str, int] = {}
    semantic_owner_by_node: dict[str, str] = {}

    def add_unit(
        stage: SemanticWorkStage,
        target_node_ids: tuple[str, ...],
        dependency_unit_ids: tuple[str, ...],
    ) -> SemanticWorkUnit:
        unit_id = f"unit-{len(units) + 1:06}"
        template = {
            "version": 2,
            "revision_id": revision_id,
            "unit_id": unit_id,
            "stage": stage.value,
            "target_node_ids": list(target_node_ids),
            "dependency_unit_ids": list(dependency_unit_ids),
            "capsules": [
                _semantic_target_projection(capsule_by_id[node_id])
                for node_id in target_node_ids
            ],
        }
        template_bytes = _canonical_json_bytes(template)
        if len(template_bytes) > max_work_unit_bytes:
            raise SemanticWorkError("semantic work template exceeds byte budget")
        unit = SemanticWorkUnit(
            unit_id=unit_id,
            stage=stage,
            target_node_ids=target_node_ids,
            dependency_unit_ids=dependency_unit_ids,
            template_sha256=hashlib.sha256(template_bytes).hexdigest(),
            template_byte_count=len(template_bytes),
        )
        units.append(unit)
        unit_weights[unit_id] = len(template_bytes)
        return unit

    processing_order = sorted(
        capsule_by_id.values(),
        key=lambda capsule: (
            -_hierarchy_depth(str(capsule.get("relative_hierarchy", ""))),
            str(capsule.get("relative_hierarchy", "")),
            str(capsule["node_id"]),
        ),
    )
    for capsule in processing_order:
        if (
            capsule["semantic_disposition"]
            != SemanticDisposition.ANALYSIS_REQUIRED.value
        ):
            continue
        node_id = str(capsule["node_id"])
        kind = str(capsule.get("kind", ""))
        dependencies = tuple(
            semantic_owner_by_node[child_id]
            for child_id in children_by_parent.get(node_id, [])
            if child_id in semantic_owner_by_node
        )
        dependencies = _reduce_dependencies(
            dependencies,
            add_unit,
            unit_weights,
            max_work_unit_bytes,
            reduce_fan_in,
        )
        if kind == "source":
            stage = SemanticWorkStage.ROOT
        elif kind == "directory":
            stage = SemanticWorkStage.DIRECTORY
        else:
            stage = SemanticWorkStage.LEAF
        unit = add_unit(stage, (node_id,), dependencies)
        semantic_owner_by_node[node_id] = unit.unit_id

    assignments: list[tuple[str, str, str]] = [
        (node_id, "semantic_work_unit", unit_id)
        for node_id, unit_id in semantic_owner_by_node.items()
    ]
    for index, capsule in enumerate(ordered):
        if (
            capsule["semantic_disposition"]
            == SemanticDisposition.ANALYSIS_REQUIRED.value
        ):
            continue
        assignments.append(
            (
                str(capsule["node_id"]),
                "private_structural_chunk",
                f"chunk-{index // STRUCTURAL_CHUNK_SIZE + 1:04}.json",
            )
        )
    if len(assignments) != len(capsule_by_id) or len(
        {assignment[0] for assignment in assignments}
    ) != len(capsule_by_id):
        raise SemanticWorkError("semantic work ownership is incomplete")

    return SemanticWorkPlan(
        revision_id=revision_id,
        policy_hash=policy_hash,
        protocol="codex-semantic-v2",
        node_count=len(capsule_by_id),
        analysis_target_count=len(semantic_owner_by_node),
        structural_owner_count=len(capsule_by_id) - len(semantic_owner_by_node),
        max_work_unit_bytes=max_work_unit_bytes,
        reduce_fan_in=reduce_fan_in,
        node_assignment_sha256=hashlib.sha256(
            _canonical_json_bytes(sorted(assignments))
        ).hexdigest(),
        units=tuple(units),
    )


def _semantic_target_projection(
    capsule: Mapping[str, object],
) -> dict[str, object]:
    child_node_ids = sorted(str(value) for value in capsule["child_node_ids"])
    sample = child_node_ids[:SEMANTIC_CHILD_SAMPLE_LIMIT]
    projected = {
        key: value
        for key, value in capsule.items()
        if key != "child_node_ids"
    }
    return {
        **projected,
        "target_projection_contract": "direct-child-attestation-v1",
        "direct_child_topology": {
            "direct_child_count": len(child_node_ids),
            "direct_child_ids_sha256": hashlib.sha256(
                _canonical_json_bytes(child_node_ids)
            ).hexdigest(),
            "sample_child_node_ids": sample,
            "sample_complete": len(child_node_ids) <= len(sample),
        },
    }


def _reduce_dependencies(
    dependency_ids: tuple[str, ...],
    add_unit: object,
    unit_weights: dict[str, int],
    max_work_unit_bytes: int,
    reduce_fan_in: int,
) -> tuple[str, ...]:
    current = dependency_ids
    while (
        len(current) > reduce_fan_in
        or sum(unit_weights[unit_id] for unit_id in current)
        > max_work_unit_bytes
    ):
        groups = _bounded_groups(
            current,
            unit_weights,
            max_work_unit_bytes,
            reduce_fan_in,
        )
        reduced: list[str] = []
        for group in groups:
            unit = add_unit(SemanticWorkStage.REDUCE, (), group)
            reduced.append(unit.unit_id)
        current = tuple(reduced)
    return current


def _bounded_groups(
    dependency_ids: tuple[str, ...],
    unit_weights: dict[str, int],
    max_work_unit_bytes: int,
    reduce_fan_in: int,
) -> tuple[tuple[str, ...], ...]:
    groups: list[tuple[str, ...]] = []
    current: list[str] = []
    current_bytes = 0
    for unit_id in dependency_ids:
        weight = unit_weights[unit_id]
        if current and (
            len(current) >= reduce_fan_in
            or current_bytes + weight > max_work_unit_bytes
        ):
            groups.append(tuple(current))
            current = []
            current_bytes = 0
        current.append(unit_id)
        current_bytes += weight
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def semantic_work_plan_payload(plan: SemanticWorkPlan) -> dict[str, object]:
    return {
        "version": 2,
        "revision_id": plan.revision_id,
        "policy_hash": plan.policy_hash,
        "protocol": plan.protocol,
        "node_count": plan.node_count,
        "analysis_target_count": plan.analysis_target_count,
        "structural_owner_count": plan.structural_owner_count,
        "work_unit_count": len(plan.units),
        "max_work_unit_bytes": plan.max_work_unit_bytes,
        "reduce_fan_in": plan.reduce_fan_in,
        "node_assignment_sha256": plan.node_assignment_sha256,
        "units": [
            {
                "unit_id": unit.unit_id,
                "stage": unit.stage.value,
                "target_node_ids": list(unit.target_node_ids),
                "dependency_unit_ids": list(unit.dependency_unit_ids),
                "template_sha256": unit.template_sha256,
                "template_byte_count": unit.template_byte_count,
            }
            for unit in plan.units
        ],
    }


def structural_chunk_name(index: int) -> str:
    return f"chunk-{index // STRUCTURAL_CHUNK_SIZE + 1:04}.json"


def semantic_index_status(
    request: SemanticIndexStatusRequest,
) -> SemanticIndexStatusResult | SemanticIndexStatusV3Result:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    if repository.has_semantic_v3_runs():
        from portfolio_maker.application.semantic_lifecycle_v3 import (
            semantic_index_status_v3,
        )

        return semantic_index_status_v3(
            SemanticIndexStatusV3Request(workspace=request.workspace)
        )
    try:
        with repository._repository_critical_section():
            state = _load_verified_state(paths, repository)
            evaluation = _evaluate_lifecycle(state)
    except SemanticWorkError:
        raise
    except Exception as error:
        raise SemanticWorkError("semantic work state is invalid") from error
    return SemanticIndexStatusResult(
        revision_id=state.revision_id,
        node_count=state.node_count,
        work_unit_count=len(state.units),
        ready_count=len(evaluation.ready_unit_ids),
        receipted_count=len(evaluation.valid_receipts),
        invalid_count=evaluation.invalid_count,
        complete=(
            len(evaluation.valid_receipts) == len(state.units)
            and evaluation.invalid_count == 0
        ),
    )


def next_semantic_work(
    request: NextSemanticWorkRequest,
) -> NextSemanticWorkResult:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    try:
        with repository._repository_critical_section():
            state = _load_verified_state(paths, repository)
            evaluation = _evaluate_lifecycle(state)
            remaining_count = len(state.units) - len(evaluation.valid_receipts)
            if evaluation.undeclared_artifact_count:
                raise SemanticWorkError(
                    "semantic work lifecycle contains invalid artifacts"
                )
            if not evaluation.ready_unit_ids:
                if remaining_count == 0 and evaluation.invalid_count == 0:
                    return NextSemanticWorkResult(
                        revision_id=state.revision_id,
                        unit_id=None,
                        input_path=None,
                        remaining_count=0,
                    )
                raise SemanticWorkError(
                    "semantic work has no dependency-ready unit"
                )
            unit_id = evaluation.ready_unit_ids[0]
            unit = state.unit_by_id[unit_id]
            payload = _materialized_input(
                state,
                unit,
                evaluation.valid_receipts,
            )
            content = _canonical_json_bytes(payload)
            if len(content) > state.max_work_unit_bytes:
                raise SemanticWorkError(
                    "semantic work input exceeds byte budget"
                )
            input_path = _unit_path(
                paths.semantic_work_input_dir,
                unit_id,
            )
            write_managed_text(input_path, content.decode("utf-8"))
    except SemanticWorkError:
        raise
    except Exception as error:
        raise SemanticWorkError("semantic work materialization failed safely") from error
    return NextSemanticWorkResult(
        revision_id=state.revision_id,
        unit_id=unit_id,
        input_path=input_path,
        remaining_count=remaining_count,
    )


def validate_semantic_work(
    request: ValidateSemanticWorkRequest,
) -> ValidateSemanticWorkResult:
    if not _is_unit_id(request.unit_id):
        raise SemanticWorkError("semantic work unit is invalid")
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    try:
        with repository._repository_critical_section():
            state = _load_verified_state(paths, repository)
            unit = state.unit_by_id.get(request.unit_id)
            if unit is None:
                raise SemanticWorkError("semantic work unit is invalid")
            evaluation = _evaluate_lifecycle(state)
            current_receipt = evaluation.valid_receipts.get(unit.unit_id)
            receipt_path = _unit_path(
                paths.semantic_work_receipts_dir,
                unit.unit_id,
            )
            if current_receipt is not None:
                return ValidateSemanticWorkResult(
                    unit_id=unit.unit_id,
                    receipt_path=receipt_path,
                    output_sha256=current_receipt.output.output_sha256,
                )
            if any(
                dependency_id not in evaluation.valid_receipts
                for dependency_id in unit.dependency_unit_ids
            ):
                raise SemanticWorkError(
                    "semantic work dependencies are not valid"
                )

            input_payload, input_content = _read_expected_input(
                state,
                unit,
                evaluation.valid_receipts,
            )
            output = _read_valid_output(
                state,
                unit,
                input_payload,
                evaluation.valid_receipts,
            )
            receipt = _receipt_payload(
                state,
                unit,
                input_payload,
                output,
                evaluation.valid_receipts,
            )

            current_state = _load_verified_state(paths, repository)
            current_evaluation = _evaluate_lifecycle(current_state)
            if current_state.revision_id != state.revision_id:
                raise SemanticWorkError("semantic work state changed")
            if any(
                dependency_id not in current_evaluation.valid_receipts
                for dependency_id in unit.dependency_unit_ids
            ):
                raise SemanticWorkError("semantic work state changed")
            current_input, current_input_content = _read_expected_input(
                current_state,
                current_state.unit_by_id[unit.unit_id],
                current_evaluation.valid_receipts,
            )
            current_output = _read_valid_output(
                current_state,
                current_state.unit_by_id[unit.unit_id],
                current_input,
                current_evaluation.valid_receipts,
            )
            if (
                current_input_content != input_content
                or current_output.content != output.content
                or tuple(
                    current_evaluation.valid_receipts[dependency_id]
                    .output.output_sha256
                    for dependency_id in unit.dependency_unit_ids
                )
                != tuple(receipt["dependency_output_sha256s"])
            ):
                raise SemanticWorkError("semantic work state changed")
            write_managed_text(receipt_path, _canonical_json(receipt))
    except SemanticWorkError:
        raise
    except Exception as error:
        raise SemanticWorkError("semantic work output is unsafe or unavailable") from error
    return ValidateSemanticWorkResult(
        unit_id=unit.unit_id,
        receipt_path=receipt_path,
        output_sha256=output.output_sha256,
    )


def finalize_semantic_index(
    request: FinalizeSemanticIndexRequest,
) -> FinalizeSemanticIndexResult:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    try:
        with repository._repository_critical_section():
            ensure_managed_directory(paths.semantic_final_dir)
            state = _load_verified_state(paths, repository)
            _validate_current_policy(paths, state.policy_hash)
            evaluation = _evaluate_lifecycle(state)
            _require_complete_lifecycle(state, evaluation)
            analyzed = _validated_analysis_results(state, evaluation)
            metadata = _compile_finalization(state, evaluation, analyzed)
            existing_names = _inspect_final_directory(paths.semantic_final_dir)
            if _final_set_matches(
                paths.semantic_final_dir,
                existing_names,
                metadata,
            ):
                return _finalization_result(paths, state, metadata)

            manifest_path = paths.semantic_final_dir / "manifest.json"
            if "manifest.json" in existing_names:
                remove_managed_file(manifest_path)

            expected_chunk_names: set[str] = set()
            for index, nodes in enumerate(
                _chunk_final_nodes(
                    _iter_final_nodes(state, analyzed),
                ),
                start=1,
            ):
                name = final_chunk_name(index)
                expected_chunk_names.add(name)
                write_managed_text(
                    paths.semantic_final_dir / name,
                    _canonical_json(
                        _final_chunk_payload(state.revision_id, index, nodes)
                    ),
                )

            for name in sorted(existing_names - expected_chunk_names):
                if name == "manifest.json":
                    continue
                remove_managed_file(paths.semantic_final_dir / name)

            current_state = _load_verified_state(paths, repository)
            _validate_current_policy(paths, current_state.policy_hash)
            current_evaluation = _evaluate_lifecycle(current_state)
            _require_complete_lifecycle(current_state, current_evaluation)
            current_analyzed = _validated_analysis_results(
                current_state,
                current_evaluation,
            )
            current_metadata = _compile_finalization(
                current_state,
                current_evaluation,
                current_analyzed,
            )
            if current_metadata != metadata or not _published_chunks_match(
                paths.semantic_final_dir,
                metadata,
            ):
                raise SemanticWorkError("semantic finalization input changed")

            write_managed_text(
                manifest_path,
                metadata.manifest_content.decode("utf-8"),
            )
            return _finalization_result(paths, state, metadata)
    except SemanticWorkError:
        raise
    except Exception as error:
        raise SemanticWorkError(
            "semantic final publication failed safely"
        ) from error


def final_chunk_name(index: int) -> str:
    if type(index) is not int or index <= 0:
        raise SemanticWorkError("semantic final chunk index is invalid")
    return f"chunk-{index:04}.json"


def _require_complete_lifecycle(
    state: _VerifiedState,
    evaluation: _LifecycleEvaluation,
) -> None:
    if evaluation.undeclared_artifact_count:
        raise SemanticWorkError(
            "semantic work lifecycle contains invalid artifacts"
        )
    if evaluation.invalid_count:
        raise SemanticWorkError("semantic work lifecycle is invalid")
    if len(evaluation.valid_receipts) != len(state.units):
        raise SemanticWorkError("semantic work lifecycle is incomplete")


def _validated_analysis_results(
    state: _VerifiedState,
    evaluation: _LifecycleEvaluation,
) -> dict[str, ValidatedSemanticNodeResult]:
    results: dict[str, ValidatedSemanticNodeResult] = {}
    for unit in state.units:
        receipt = evaluation.valid_receipts.get(unit.unit_id)
        if receipt is None:
            raise SemanticWorkError("semantic work lifecycle is incomplete")
        validated = receipt.output.validated
        if validated is None:
            raise SemanticWorkError("semantic work lifecycle is invalid")
        for result in validated.node_results:
            if result.node_id in results:
                raise SemanticWorkError("semantic final node coverage is invalid")
            results[result.node_id] = result
    expected = {
        node_id
        for node_id, capsule in state.capsules.items()
        if capsule.get("semantic_disposition")
        == SemanticDisposition.ANALYSIS_REQUIRED.value
    }
    if set(results) != expected or len(results) != state.analysis_node_count:
        raise SemanticWorkError("semantic final node coverage is invalid")
    return results


def _compile_finalization(
    state: _VerifiedState,
    evaluation: _LifecycleEvaluation,
    analyzed: dict[str, ValidatedSemanticNodeResult],
) -> _FinalizationMetadata:
    chunk_sha256s: list[str] = []
    chunk_node_counts: list[int] = []
    node_ids_digest = hashlib.sha256()
    node_ids_digest.update(b"[")
    first_node = True
    node_count = 0
    analysis_node_count = 0
    for index, nodes in enumerate(
        _chunk_final_nodes(_iter_final_nodes(state, analyzed)),
        start=1,
    ):
        content = _canonical_json_bytes(
            _final_chunk_payload(state.revision_id, index, nodes)
        )
        chunk_sha256s.append(hashlib.sha256(content).hexdigest())
        chunk_node_counts.append(len(nodes))
        for node in nodes:
            if not first_node:
                node_ids_digest.update(b",")
            node_ids_digest.update(_canonical_json_bytes(node["node_id"]))
            first_node = False
            node_count += 1
            analysis_node_count += int(
                node["semantic_disposition"] == "analyzed"
            )
    node_ids_digest.update(b"]")
    structural_node_count = node_count - analysis_node_count
    if (
        node_count != state.node_count
        or analysis_node_count != state.analysis_node_count
        or structural_node_count != state.structural_node_count
        or sum(chunk_node_counts) != node_count
    ):
        raise SemanticWorkError("semantic final node coverage is invalid")

    receipt_hashes = tuple(
        hashlib.sha256(
            evaluation.valid_receipts[unit.unit_id].content
        ).hexdigest()
        for unit in state.units
    )
    unsigned: dict[str, object] = {
        "version": 2,
        "protocol": "codex-semantic-v2",
        "revision_id": state.revision_id,
        "source_id": state.source_id,
        "policy_hash": state.policy_hash,
        "analyzer_version": state.analyzer_version,
        "work_plan_sha256": state.work_plan_sha256,
        "input_manifest_sha256": state.input_manifest_sha256,
        "directory_timeout_seconds": state.directory_timeout_seconds,
        "timed_out_directory_count": state.timed_out_directory_count,
        "unreadable_directory_count": state.unreadable_directory_count,
        "node_assignment_sha256": state.node_assignment_sha256,
        "receipt_file_sha256s": list(receipt_hashes),
        "chunk_sha256s": chunk_sha256s,
        "chunk_node_counts": chunk_node_counts,
        "chunk_count": len(chunk_sha256s),
        "node_count": node_count,
        "analysis_node_count": analysis_node_count,
        "structural_node_count": structural_node_count,
        "node_ids_sha256": node_ids_digest.hexdigest(),
    }
    manifest = {
        **unsigned,
        "manifest_sha256": hashlib.sha256(
            _canonical_json_bytes(unsigned)
        ).hexdigest(),
    }
    if set(unsigned) != _FINAL_MANIFEST_KEYS:
        raise SemanticWorkError("semantic final manifest is invalid")
    return _FinalizationMetadata(
        manifest=manifest,
        manifest_content=_canonical_json_bytes(manifest),
        chunk_sha256s=tuple(chunk_sha256s),
        chunk_node_counts=tuple(chunk_node_counts),
    )


def _chunk_final_nodes(
    nodes: Iterable[dict[str, object]],
) -> Iterator[list[dict[str, object]]]:
    chunk: list[dict[str, object]] = []
    for node in nodes:
        chunk.append(node)
        if len(chunk) == STRUCTURAL_CHUNK_SIZE:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _final_chunk_payload(
    revision_id: str,
    index: int,
    nodes: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "version": 2,
        "protocol": "codex-semantic-v2",
        "revision_id": revision_id,
        "chunk_index": index,
        "nodes": nodes,
    }


def _iter_final_nodes(
    state: _VerifiedState,
    analyzed: dict[str, ValidatedSemanticNodeResult],
) -> Iterator[dict[str, object]]:
    for record in _iter_verified_structural_records(state):
        result = analyzed.get(str(record["node_id"]))
        if result is None:
            semantic_summary = ""
            semantic_roles: list[str] = []
            topics: list[str] = []
            semantic_facts = {
                "purpose_signals": [],
                "parent_context_signals": [],
                "independence_signals": [],
                "triviality_signals": [],
                "grounding_signal_ids": [],
            }
            semantic_disposition = record["semantic_disposition"]
            analysis_status = record["analysis_status"]
        else:
            semantic_summary = result.semantic_summary
            semantic_roles = list(result.semantic_roles)
            topics = list(result.topics)
            semantic_facts = {
                "purpose_signals": list(result.purpose_signals),
                "parent_context_signals": list(
                    result.parent_context_signals
                ),
                "independence_signals": list(
                    result.independence_signals
                ),
                "triviality_signals": list(result.triviality_signals),
                "grounding_signal_ids": list(result.grounding_signal_ids),
            }
            semantic_disposition = result.semantic_disposition
            analysis_status = result.analysis_status
        child_node_ids = record["child_node_ids"]
        yield {
            "result_kind": "node",
            "node_id": record["node_id"],
            "source_id": record["source_id"],
            "parent_node_id": record["parent_node_id"],
            "kind": record["kind"],
            "display_name": record["display_name"],
            "relative_hierarchy": record["relative_hierarchy"],
            "content_fingerprint": record["content_fingerprint"],
            "semantic_summary": semantic_summary,
            "semantic_roles": semantic_roles,
            "topics": topics,
            "semantic_facts": semantic_facts,
            "semantic_disposition": semantic_disposition,
            "analysis_status": analysis_status,
            "child_node_ids": child_node_ids,
            "skip_reason": record["skip_reason"],
            "evidence_ids": record["evidence_ids"],
            "coverage": record["coverage"],
        }


def _iter_verified_structural_records(
    state: _VerifiedState,
) -> Iterator[dict[str, object]]:
    directory = state.paths.semantic_revision_structural_dir(
        state.revision_id
    )
    names = _directory_entry_names(directory)
    expected_names = {
        final_chunk_name(index)
        for index in range(
            1,
            (state.node_count + STRUCTURAL_CHUNK_SIZE - 1)
            // STRUCTURAL_CHUNK_SIZE
            + 1,
        )
    }
    if names != expected_names:
        raise SemanticWorkError("semantic private structural state is invalid")

    expected_capsules = iter(
        sorted(
            state.capsules.values(),
            key=lambda capsule: (
                str(capsule.get("relative_hierarchy", "")),
                str(capsule.get("node_id", "")),
            ),
        )
    )
    count = 0
    for index in range(1, len(expected_names) + 1):
        content = _read_required(directory / final_chunk_name(index))
        payload = _decode_object(content, "private structural state")
        if (
            set(payload) != {"version", "revision_id", "nodes"}
            or payload.get("version") != 2
            or payload.get("revision_id") != state.revision_id
            or type(payload.get("nodes")) is not list
            or not payload["nodes"]
            or len(payload["nodes"]) > STRUCTURAL_CHUNK_SIZE
            or content != _canonical_json_bytes(payload)
        ):
            raise SemanticWorkError(
                "semantic private structural state is invalid"
            )
        for record in payload["nodes"]:
            try:
                capsule = next(expected_capsules)
            except StopIteration as error:
                raise SemanticWorkError(
                    "semantic private structural state is invalid"
                ) from error
            _validate_structural_record(record, capsule, state.source_id)
            count += 1
            yield record
    try:
        next(expected_capsules)
    except StopIteration:
        pass
    else:
        raise SemanticWorkError("semantic private structural state is invalid")
    if count != state.node_count:
        raise SemanticWorkError("semantic private structural state is invalid")


def _validate_structural_record(
    record: object,
    capsule: dict[str, object],
    source_id: str,
) -> None:
    if type(record) is not dict or set(record) != _STRUCTURAL_RECORD_KEYS:
        raise SemanticWorkError("semantic private structural state is invalid")
    coverage = record.get("coverage")
    if (
        not _is_safe_identifier(source_id)
        or record.get("source_id") != source_id
        or not _is_safe_identifier(record.get("node_id"))
        or record.get("node_id") != capsule.get("node_id")
        or record.get("parent_node_id") != capsule.get("parent_node_id")
        or record.get("parent_node_id") is not None
        and not _is_safe_identifier(record.get("parent_node_id"))
        or record.get("kind") != capsule.get("kind")
        or record.get("kind")
        not in {kind.value for kind in SemanticNodeKind}
        or record.get("display_name") != capsule.get("display_name")
        or not _is_safe_presentation_text(record.get("display_name"))
        or record.get("relative_hierarchy")
        != capsule.get("relative_hierarchy")
        or not _is_safe_relative_hierarchy(
            record.get("relative_hierarchy")
        )
        or record.get("content_fingerprint")
        != capsule.get("content_fingerprint")
        or record.get("content_fingerprint") is not None
        and (
            type(record.get("content_fingerprint")) is not str
            or _FINGERPRINT.fullmatch(
                str(record.get("content_fingerprint"))
            )
            is None
        )
        or record.get("analysis_status") != capsule.get("analysis_status")
        or record.get("analysis_status")
        not in {status.value for status in AnalysisStatus}
        or record.get("semantic_disposition")
        != capsule.get("semantic_disposition")
        or record.get("semantic_disposition") not in _VALID_DISPOSITIONS
        or record.get("roles") != capsule.get("roles")
        or record.get("child_node_ids") != capsule.get("child_node_ids")
        or record.get("skip_reason") != capsule.get("skip_reason")
        or record.get("skip_reason") is not None
        and not _is_safe_identifier(record.get("skip_reason"))
        or record.get("evidence_ids") != capsule.get("evidence_ids")
        or coverage != capsule.get("coverage")
        or not _is_unique_text_list(record.get("roles"))
        or not _is_unique_identifier_list(record.get("child_node_ids"))
        or not _is_unique_int_list(record.get("evidence_ids"))
        or not _is_valid_coverage(coverage)
    ):
        raise SemanticWorkError("semantic private structural state is invalid")


def _is_unique_text_list(value: object) -> bool:
    return (
        type(value) is list
        and all(_is_safe_presentation_text(item) for item in value)
        and len(value) == len(set(value))
    )


def _is_unique_identifier_list(value: object) -> bool:
    return (
        type(value) is list
        and all(_is_safe_identifier(item) for item in value)
        and len(value) == len(set(value))
    )


def _is_unique_int_list(value: object) -> bool:
    return (
        type(value) is list
        and all(type(item) is int and item > 0 for item in value)
        and len(value) == len(set(value))
    )


def _is_safe_identifier(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and _SAFE_IDENTIFIER.fullmatch(value) is not None
    )


def _is_safe_presentation_text(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and value == value.strip()
        and not any(
            unicodedata.category(character).startswith("C")
            for character in value
        )
        and not contains_hidden_secret_shaped_public_value(value)
        and mask_public_value(value) == value
        and "/" not in value
        and "\\" not in value
        and "://" not in value
        and ".portfolio-maker" not in value.casefold()
        and "portfolio.db" not in value.casefold()
    )


def _is_safe_relative_hierarchy(value: object) -> bool:
    if type(value) is not str or not value or "\\" in value:
        return False
    if (
        any(
            unicodedata.category(character).startswith("C")
            for character in value
        )
        or contains_hidden_secret_shaped_public_value(value)
        or mask_public_value(value) != value
        or "://" in value
        or value.startswith(("/", "~/"))
        or ".portfolio-maker" in value.casefold()
        or "portfolio.db" in value.casefold()
    ):
        return False
    if value == ".":
        return True
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and str(path) == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _is_valid_coverage(value: object) -> bool:
    return (
        type(value) is dict
        and set(value) == _COVERAGE_KEYS
        and all(
            _is_nonnegative_int(value.get(key))
            for key in (
                "node_count",
                "content_node_count",
                "structural_only_count",
                "failed_count",
            )
        )
        and _is_sha256(value.get("node_ids_sha256"))
    )


def _validate_capsule_contract(
    value: object,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != _CAPSULE_KEYS:
        raise SemanticWorkError("semantic work capsule input is invalid")
    parent_node_id = value.get("parent_node_id")
    content_fingerprint = value.get("content_fingerprint")
    signals = value.get("signals")
    skip_reason = value.get("skip_reason")
    if (
        not _is_safe_identifier(value.get("node_id"))
        or parent_node_id is not None
        and not _is_safe_identifier(parent_node_id)
        or value.get("kind")
        not in {kind.value for kind in SemanticNodeKind}
        or not _is_safe_presentation_text(value.get("display_name"))
        or not _is_safe_relative_hierarchy(
            value.get("relative_hierarchy")
        )
        or content_fingerprint is not None
        and (
            type(content_fingerprint) is not str
            or _FINGERPRINT.fullmatch(content_fingerprint) is None
        )
        or value.get("analysis_status")
        not in {status.value for status in AnalysisStatus}
        or value.get("semantic_disposition") not in _VALID_DISPOSITIONS
        or not _is_unique_text_list(value.get("roles"))
        or not _is_unique_identifier_list(value.get("child_node_ids"))
        or type(signals) is not list
        or not _is_unique_int_list(value.get("evidence_ids"))
        or skip_reason is not None
        and not _is_safe_identifier(skip_reason)
        or not _is_valid_coverage(value.get("coverage"))
    ):
        raise SemanticWorkError("semantic work capsule input is invalid")
    signal_ids: set[str] = set()
    for signal in signals:
        if (
            type(signal) is not dict
            or set(signal) != _SIGNAL_KEYS
            or type(signal.get("signal_id")) is not str
            or _FINGERPRINT.fullmatch(str(signal.get("signal_id"))) is None
            or signal.get("signal_id") in signal_ids
            or signal.get("kind") != "masked_text"
            or type(signal.get("text")) is not str
            or not signal.get("text")
            or len(str(signal.get("text"))) > 8_192
            or contains_hidden_secret_shaped_public_value(
                str(signal.get("text"))
            )
            or mask_public_value(str(signal.get("text")))
            != signal.get("text")
        ):
            raise SemanticWorkError("semantic work capsule input is invalid")
        signal_ids.add(str(signal["signal_id"]))
    return value


def _inspect_final_directory(directory: Path) -> set[str]:
    try:
        entries = tuple(directory.iterdir())
    except OSError as error:
        raise SemanticWorkError("semantic final directory is invalid") from error
    names: set[str] = set()
    for entry in entries:
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise SemanticWorkError(
                "semantic final directory is invalid"
            ) from error
        if (
            entry.name in names
            or (
                entry.name != "manifest.json"
                and _FINAL_CHUNK_NAME.fullmatch(entry.name) is None
            )
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise SemanticWorkError("semantic final directory is invalid")
        names.add(entry.name)
    return names


def _final_set_matches(
    directory: Path,
    names: set[str],
    metadata: _FinalizationMetadata,
) -> bool:
    expected_names = {
        "manifest.json",
        *{
            final_chunk_name(index)
            for index in range(1, len(metadata.chunk_sha256s) + 1)
        },
    }
    if names != expected_names:
        return False
    try:
        if (
            read_managed_bytes(directory / "manifest.json")
            != metadata.manifest_content
        ):
            return False
        return _published_chunks_match(directory, metadata)
    except OSError as error:
        raise SemanticWorkError("semantic final directory is invalid") from error


def _published_chunks_match(
    directory: Path,
    metadata: _FinalizationMetadata,
) -> bool:
    names = _inspect_final_directory(directory)
    expected_names = {
        final_chunk_name(index)
        for index in range(1, len(metadata.chunk_sha256s) + 1)
    }
    if names - {"manifest.json"} != expected_names:
        return False
    for index, expected_hash in enumerate(
        metadata.chunk_sha256s,
        start=1,
    ):
        content = _read_required(directory / final_chunk_name(index))
        if hashlib.sha256(content).hexdigest() != expected_hash:
            return False
    return True


def _finalization_result(
    paths: WorkspacePaths,
    state: _VerifiedState,
    metadata: _FinalizationMetadata,
) -> FinalizeSemanticIndexResult:
    return FinalizeSemanticIndexResult(
        revision_id=state.revision_id,
        manifest_path=paths.semantic_final_dir / "manifest.json",
        chunk_count=len(metadata.chunk_sha256s),
        node_count=state.node_count,
    )


def _validate_current_policy(paths: WorkspacePaths, policy_hash: str) -> None:
    try:
        from portfolio_maker.application.semantic_index import (
            _current_policy_hash,
        )

        current_hash = _current_policy_hash(paths)
    except Exception as error:
        raise SemanticWorkError(
            "semantic work approval policy is unavailable"
        ) from error
    if current_hash != policy_hash:
        raise SemanticWorkError("semantic work approval policy is stale")


def _load_verified_state(
    paths: WorkspacePaths,
    repository: SQLiteRepository,
) -> _VerifiedState:
    manifest_content = _read_required(paths.semantic_index_manifest_path)
    manifest = _decode_object(manifest_content, "manifest")
    unsigned_manifest = dict(manifest)
    manifest_sha256 = unsigned_manifest.pop("manifest_sha256", None)
    if set(manifest) != _MANIFEST_KEYS:
        raise SemanticWorkError("semantic work manifest is invalid")
    if (
        manifest.get("version") != 2
        or manifest.get("protocol") != "codex-semantic-v2"
        or not _is_nonempty_text(manifest.get("revision_id"))
        or not _is_nonempty_text(manifest.get("source_id"))
        or not _is_sha256(manifest.get("policy_hash"))
        or manifest.get("analyzer_version") != "semantic-input-v2"
        or not _is_sha256(manifest.get("work_plan_sha256"))
        or not _is_nonnegative_int(manifest.get("node_count"))
        or not _is_nonnegative_int(manifest.get("work_unit_count"))
        or not _is_nonnegative_int(manifest.get("managed_input_bytes"))
        or not _is_nonnegative_int(manifest.get("analysis_target_count"))
        or not _is_nonnegative_int(manifest.get("structural_only_count"))
        or not _is_valid_directory_timeout(
            manifest.get("directory_timeout_seconds")
        )
        or not _is_nonnegative_int(
            manifest.get("timed_out_directory_count")
        )
        or not _is_nonnegative_int(
            manifest.get("unreadable_directory_count")
        )
        or manifest["timed_out_directory_count"]
        > manifest["unreadable_directory_count"]
        or manifest["unreadable_directory_count"] > manifest["node_count"]
        or not _is_sha256(manifest_sha256)
        or hashlib.sha256(_canonical_json_bytes(unsigned_manifest)).hexdigest()
        != manifest_sha256
    ):
        raise SemanticWorkError("semantic work manifest is invalid")
    chunk_sha256s = manifest.get("capsule_chunk_sha256s")
    if (
        type(chunk_sha256s) is not list
        or not chunk_sha256s
        or not all(_is_sha256(value) for value in chunk_sha256s)
    ):
        raise SemanticWorkError("semantic work manifest is invalid")

    expected_chunk_paths = tuple(
        paths.semantic_index_input_dir / f"chunk-{index:04}.json"
        for index in range(1, len(chunk_sha256s) + 1)
    )
    existing_chunk_names = _directory_entry_names(paths.semantic_index_input_dir)
    if existing_chunk_names != {path.name for path in expected_chunk_paths}:
        raise SemanticWorkError("semantic work capsule input is invalid")

    capsules: dict[str, dict[str, object]] = {}
    chunk_contents: list[bytes] = []
    for path, expected_hash in zip(
        expected_chunk_paths,
        chunk_sha256s,
        strict=True,
    ):
        content = _read_required(path)
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise SemanticWorkError("semantic work capsule input is invalid")
        chunk = _decode_object(content, "capsule input")
        if (
            set(chunk) != {"version", "revision_id", "capsules"}
            or chunk.get("version") != 2
            or chunk.get("revision_id") != manifest["revision_id"]
            or type(chunk.get("capsules")) is not list
        ):
            raise SemanticWorkError("semantic work capsule input is invalid")
        for raw_capsule in chunk["capsules"]:
            try:
                capsule = _validate_capsule_contract(raw_capsule)
            except SemanticWorkError as error:
                if (
                    type(raw_capsule) is dict
                    and raw_capsule.get("semantic_disposition")
                    in _VALID_DISPOSITIONS
                    - {SemanticDisposition.ANALYSIS_REQUIRED.value}
                ):
                    raise SemanticWorkError(
                        "semantic private structural state is invalid"
                    ) from error
                raise
            node_id = str(capsule["node_id"])
            if node_id in capsules:
                raise SemanticWorkError("semantic work capsule input is invalid")
            capsules[node_id] = capsule
        chunk_contents.append(content)

    plan_content = _read_required(paths.semantic_work_plan_path)
    if hashlib.sha256(plan_content).hexdigest() != manifest["work_plan_sha256"]:
        raise SemanticWorkError("semantic work plan is invalid")
    plan = _decode_object(plan_content, "work plan")
    units = _validate_plan(plan, manifest, capsules)
    if (
        len(capsules) != manifest["node_count"]
        or len(units) != manifest["work_unit_count"]
        or len(plan_content) + sum(map(len, chunk_contents))
        != manifest["managed_input_bytes"]
    ):
        raise SemanticWorkError("semantic work manifest counts are invalid")
    _validate_staging_revision(repository, manifest)
    return _VerifiedState(
        paths=paths,
        revision_id=str(manifest["revision_id"]),
        source_id=str(manifest["source_id"]),
        policy_hash=str(manifest["policy_hash"]),
        analyzer_version=str(manifest["analyzer_version"]),
        work_plan_sha256=str(manifest["work_plan_sha256"]),
        input_manifest_sha256=str(manifest_sha256),
        directory_timeout_seconds=float(
            manifest["directory_timeout_seconds"]
        ),
        timed_out_directory_count=int(
            manifest["timed_out_directory_count"]
        ),
        unreadable_directory_count=int(
            manifest["unreadable_directory_count"]
        ),
        node_assignment_sha256=str(plan["node_assignment_sha256"]),
        node_count=int(manifest["node_count"]),
        analysis_node_count=int(manifest["analysis_target_count"]),
        structural_node_count=int(manifest["structural_only_count"]),
        max_work_unit_bytes=int(plan["max_work_unit_bytes"]),
        capsules=capsules,
        units=units,
        unit_by_id={unit.unit_id: unit for unit in units},
    )


def _validate_plan(
    plan: dict[str, object],
    manifest: dict[str, object],
    capsules: Mapping[str, dict[str, object]],
) -> tuple[_VerifiedUnit, ...]:
    semantic_owners: dict[str, str] = {}

    def record_owner(node_id: str, unit_id: str) -> None:
        if node_id in semantic_owners:
            raise SemanticWorkError("semantic work plan is invalid")
        semantic_owners[node_id] = unit_id

    verified_units = _validate_plan_structure(
        plan,
        manifest,
        owner_sink=record_owner,
    )
    for unit in verified_units:
        for node_id in unit.target_node_ids:
            capsule = capsules.get(node_id)
            if capsule is None:
                raise SemanticWorkError("semantic work plan is invalid")
            _validate_unit_capsule_binding(unit, capsule, manifest)

    semantic_nodes = {
        node_id
        for node_id, capsule in capsules.items()
        if capsule.get("semantic_disposition")
        == SemanticDisposition.ANALYSIS_REQUIRED.value
    }
    if semantic_nodes != set(semantic_owners):
        raise SemanticWorkError("semantic work ownership is invalid")
    ordered_capsules = sorted(
        capsules.values(),
        key=lambda capsule: (
            str(capsule.get("relative_hierarchy", "")),
            str(capsule.get("node_id", "")),
        ),
    )
    assignments = [
        (node_id, "semantic_work_unit", owner_id)
        for node_id, owner_id in semantic_owners.items()
    ]
    for index, capsule in enumerate(ordered_capsules):
        if (
            capsule.get("semantic_disposition")
            == SemanticDisposition.ANALYSIS_REQUIRED.value
        ):
            continue
        assignments.append(
            (
                str(capsule["node_id"]),
                "private_structural_chunk",
                structural_chunk_name(index),
            )
        )
    assignment_hash = hashlib.sha256(
        _canonical_json_bytes(sorted(assignments))
    ).hexdigest()
    if (
        assignment_hash != plan["node_assignment_sha256"]
        or len(semantic_nodes) != plan["analysis_target_count"]
        or len(capsules) - len(semantic_nodes)
        != plan["structural_owner_count"]
    ):
        raise SemanticWorkError("semantic work ownership is invalid")
    return verified_units


def _validate_plan_structure(
    plan: dict[str, object],
    manifest: dict[str, object],
    *,
    owner_sink: Callable[[str, str], None],
) -> tuple[_VerifiedUnit, ...]:
    if (
        set(plan) != _PLAN_KEYS
        or plan.get("version") != 2
        or plan.get("revision_id") != manifest["revision_id"]
        or plan.get("policy_hash") != manifest["policy_hash"]
        or plan.get("protocol") != "codex-semantic-v2"
        or plan.get("node_count") != manifest["node_count"]
        or plan.get("analysis_target_count")
        != manifest["analysis_target_count"]
        or plan.get("structural_owner_count")
        != manifest["structural_only_count"]
        or plan.get("work_unit_count") != manifest["work_unit_count"]
        or plan.get("max_work_unit_bytes") != MAX_WORK_UNIT_BYTES
        or plan.get("reduce_fan_in") != REDUCE_FAN_IN
        or not _is_sha256(plan.get("node_assignment_sha256"))
        or type(plan.get("units")) is not list
    ):
        raise SemanticWorkError("semantic work plan is invalid")
    verified_units: list[_VerifiedUnit] = []
    known_unit_ids: set[str] = set()
    for index, raw_unit in enumerate(plan["units"], start=1):
        if type(raw_unit) is not dict or set(raw_unit) != _UNIT_KEYS:
            raise SemanticWorkError("semantic work plan is invalid")
        unit_id = raw_unit.get("unit_id")
        stage = raw_unit.get("stage")
        target_node_ids = raw_unit.get("target_node_ids")
        dependency_unit_ids = raw_unit.get("dependency_unit_ids")
        if (
            unit_id != f"unit-{index:06}"
            or unit_id in known_unit_ids
            or stage not in _VALID_STAGES
            or type(target_node_ids) is not list
            or type(dependency_unit_ids) is not list
            or not all(_is_nonempty_text(value) for value in target_node_ids)
            or not all(
                _is_unit_id(value) and value in known_unit_ids
                for value in dependency_unit_ids
            )
            or len(set(target_node_ids)) != len(target_node_ids)
            or len(set(dependency_unit_ids)) != len(dependency_unit_ids)
            or len(dependency_unit_ids) > REDUCE_FAN_IN
            or not _is_sha256(raw_unit.get("template_sha256"))
            or not _is_positive_int(raw_unit.get("template_byte_count"))
        ):
            raise SemanticWorkError("semantic work plan is invalid")
        if stage == SemanticWorkStage.REDUCE.value:
            if target_node_ids:
                raise SemanticWorkError("semantic work plan is invalid")
        elif len(target_node_ids) != 1:
            raise SemanticWorkError("semantic work plan is invalid")
        for node_id in target_node_ids:
            owner_sink(str(node_id), str(unit_id))
        verified_unit = _VerifiedUnit(
            unit_id=unit_id,
            stage=str(stage),
            target_node_ids=tuple(target_node_ids),
            dependency_unit_ids=tuple(dependency_unit_ids),
            template_sha256=str(raw_unit["template_sha256"]),
            template_byte_count=int(raw_unit["template_byte_count"]),
        )
        if not verified_unit.target_node_ids:
            _validate_unit_template(verified_unit, (), manifest)
        verified_units.append(verified_unit)
        known_unit_ids.add(unit_id)
    return tuple(verified_units)


def _validate_unit_capsule_binding(
    unit: _VerifiedUnit,
    capsule: dict[str, object],
    manifest: dict[str, object],
) -> None:
    if (
        capsule.get("semantic_disposition")
        != SemanticDisposition.ANALYSIS_REQUIRED.value
        or not _stage_matches_capsule(unit.stage, capsule)
    ):
        raise SemanticWorkError("semantic work plan is invalid")
    _validate_unit_template(unit, (capsule,), manifest)


def _validate_unit_template(
    unit: _VerifiedUnit,
    capsules: tuple[dict[str, object], ...],
    manifest: dict[str, object],
) -> None:
    template = {
        "version": 2,
        "revision_id": manifest["revision_id"],
        "unit_id": unit.unit_id,
        "stage": unit.stage,
        "target_node_ids": list(unit.target_node_ids),
        "dependency_unit_ids": list(unit.dependency_unit_ids),
        "capsules": [
            _semantic_target_projection(capsule)
            for capsule in capsules
        ],
    }
    template_bytes = _canonical_json_bytes(template)
    if (
        len(template_bytes) != unit.template_byte_count
        or len(template_bytes) > MAX_WORK_UNIT_BYTES
        or hashlib.sha256(template_bytes).hexdigest()
        != unit.template_sha256
    ):
        raise SemanticWorkError("semantic work plan is invalid")


def _validate_staging_revision(
    repository: SQLiteRepository,
    manifest: dict[str, object],
) -> None:
    with repository._read_connection() as connection:
        row = connection.execute(
            """
            SELECT source_id, policy_hash, analyzer_version, status
            FROM semantic_index_revisions
            WHERE id = ?
            """,
            (manifest["revision_id"],),
        ).fetchone()
    if (
        row is None
        or row["source_id"] != manifest["source_id"]
        or row["policy_hash"] != manifest["policy_hash"]
        or row["analyzer_version"] != manifest["analyzer_version"]
        or row["status"] != "staging"
    ):
        raise SemanticWorkError("semantic work revision is not staging")


def _evaluate_lifecycle(
    state: _VerifiedState,
    receipt_store: MutableMapping[str, _ReceiptBinding] | None = None,
) -> _LifecycleEvaluation:
    input_names = _directory_entry_names(state.paths.semantic_work_input_dir)
    output_names = _directory_entry_names(state.paths.semantic_work_output_dir)
    receipt_names = _directory_entry_names(state.paths.semantic_work_receipts_dir)
    expected_names = {f"{unit.unit_id}.json" for unit in state.units}
    undeclared_artifact_count = sum(
        len(names - expected_names)
        for names in (input_names, output_names, receipt_names)
    )
    valid_receipts: MutableMapping[str, _ReceiptBinding] = (
        {} if receipt_store is None else receipt_store
    )
    invalid_units: set[str] = set()
    ready_unit_ids: list[str] = []
    for unit in state.units:
        filename = f"{unit.unit_id}.json"
        dependencies_valid = all(
            dependency_id in valid_receipts
            for dependency_id in unit.dependency_unit_ids
        )
        if filename in receipt_names:
            try:
                if not dependencies_valid:
                    raise SemanticWorkError("semantic work receipt is stale")
                valid_receipts[unit.unit_id] = _read_valid_receipt(
                    state,
                    unit,
                    valid_receipts,
                )
            except SemanticWorkError:
                invalid_units.add(unit.unit_id)
                if dependencies_valid:
                    ready_unit_ids.append(unit.unit_id)
        else:
            if dependencies_valid:
                ready_unit_ids.append(unit.unit_id)
                try:
                    if filename in input_names:
                        input_payload, _ = _read_expected_input(
                            state,
                            unit,
                            valid_receipts,
                        )
                    else:
                        input_payload = None
                    if filename in output_names:
                        if input_payload is None:
                            raise SemanticWorkError(
                                "semantic work output is stale"
                            )
                        _read_valid_output(
                            state,
                            unit,
                            input_payload,
                            valid_receipts,
                        )
                except SemanticWorkError:
                    invalid_units.add(unit.unit_id)
            elif filename in input_names or filename in output_names:
                invalid_units.add(unit.unit_id)
    return _LifecycleEvaluation(
        valid_receipts=valid_receipts,
        ready_unit_ids=tuple(ready_unit_ids),
        invalid_count=undeclared_artifact_count + len(invalid_units),
        undeclared_artifact_count=undeclared_artifact_count,
    )


def _materialized_input(
    state: _VerifiedState,
    unit: _VerifiedUnit,
    valid_receipts: Mapping[str, _ReceiptBinding],
) -> dict[str, object]:
    dependencies = [
        {
            "unit_id": dependency_id,
            "output_sha256": valid_receipts[
                dependency_id
            ].output.output_sha256,
        }
        for dependency_id in unit.dependency_unit_ids
    ]
    targets = [
        _semantic_target_projection(state.capsules[node_id])
        for node_id in unit.target_node_ids
    ]
    child_results = [
        {
            "unit_id": dependency_id,
            "output_sha256": valid_receipts[
                dependency_id
            ].output.output_sha256,
            "coverage": valid_receipts[dependency_id].output.payload["coverage"],
            "results": valid_receipts[dependency_id].output.payload["results"],
        }
        for dependency_id in unit.dependency_unit_ids
    ]
    coverage = (
        targets[0]["coverage"]
        if targets
        else _dependency_coverage(child_results)
    )
    payload: dict[str, object] = {
        "version": 2,
        "revision_id": state.revision_id,
        "unit_id": unit.unit_id,
        "stage": unit.stage,
        "dependencies": dependencies,
        "coverage": coverage,
        "targets": targets,
        "child_results": child_results,
    }
    input_sha256 = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return {
        **payload,
        "input_sha256": input_sha256,
    }


def _dependency_coverage(
    child_results: list[dict[str, object]],
) -> dict[str, object]:
    node_count = 0
    digests: list[str] = []
    for result in child_results:
        coverage = result["coverage"]
        if (
            type(coverage) is not dict
            or not _is_nonnegative_int(coverage.get("node_count"))
            or not _is_sha256(coverage.get("node_ids_sha256"))
        ):
            raise SemanticWorkError("semantic work dependency output is invalid")
        node_count += int(coverage["node_count"])
        digests.append(str(coverage["node_ids_sha256"]))
    return {
        "node_count": node_count,
        "node_ids_sha256": hashlib.sha256(
            _canonical_json_bytes(digests)
        ).hexdigest(),
    }


def _read_expected_input(
    state: _VerifiedState,
    unit: _VerifiedUnit,
    valid_receipts: Mapping[str, _ReceiptBinding],
) -> tuple[dict[str, object], bytes]:
    expected = _materialized_input(state, unit, valid_receipts)
    expected_content = _canonical_json_bytes(expected)
    if len(expected_content) > state.max_work_unit_bytes:
        raise SemanticWorkError("semantic work input exceeds byte budget")
    content = _read_required(
        _unit_path(state.paths.semantic_work_input_dir, unit.unit_id)
    )
    parsed = _decode_object(content, "work input")
    if (
        set(parsed) != _INPUT_KEYS
        or parsed != expected
        or content != expected_content
    ):
        raise SemanticWorkError("semantic work input is stale")
    return parsed, content


def _read_valid_output(
    state: _VerifiedState,
    unit: _VerifiedUnit,
    input_payload: dict[str, object],
    valid_receipts: Mapping[str, _ReceiptBinding],
) -> _OutputBinding:
    content = _read_required(
        _unit_path(state.paths.semantic_work_output_dir, unit.unit_id),
        output=True,
    )
    payload = _decode_object(content, "work output", output=True)
    if set(payload) != _OUTPUT_KEYS:
        raise SemanticWorkError("semantic work output is invalid")
    output_sha256 = payload.get("output_sha256")
    unsigned = dict(payload)
    unsigned.pop("output_sha256", None)
    expected_dependency_hashes = [
        valid_receipts[dependency_id].output.output_sha256
        for dependency_id in unit.dependency_unit_ids
    ]
    if (
        payload.get("version") != 2
        or payload.get("revision_id") != state.revision_id
        or payload.get("unit_id") != unit.unit_id
        or payload.get("stage") != unit.stage
        or payload.get("input_sha256") != input_payload["input_sha256"]
        or payload.get("dependency_output_sha256s")
        != expected_dependency_hashes
        or payload.get("analysis_method") != "codex-semantic-v2"
        or payload.get("coverage") != input_payload["coverage"]
        or type(payload.get("results")) is not list
        or not _is_sha256(output_sha256)
        or hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
        != output_sha256
    ):
        raise SemanticWorkError("semantic work output is invalid")
    try:
        validated = validate_semantic_work_output(input_payload, payload)
    except SemanticQualityError as error:
        raise SemanticWorkError(
            "semantic work output fails quality validation"
        ) from error
    return _OutputBinding(
        output_sha256=hashlib.sha256(content).hexdigest(),
        payload=payload,
        content=content,
        validated=validated,
    )


def _read_valid_receipt(
    state: _VerifiedState,
    unit: _VerifiedUnit,
    valid_receipts: Mapping[str, _ReceiptBinding],
) -> _ReceiptBinding:
    input_payload, _ = _read_expected_input(state, unit, valid_receipts)
    output = _read_valid_output(
        state,
        unit,
        input_payload,
        valid_receipts,
    )
    path = _unit_path(state.paths.semantic_work_receipts_dir, unit.unit_id)
    content = _read_required(path)
    receipt = _decode_object(content, "receipt")
    unsigned = dict(receipt)
    receipt_sha256 = unsigned.pop("receipt_sha256", None)
    dependency_hashes = tuple(
        valid_receipts[dependency_id].output.output_sha256
        for dependency_id in unit.dependency_unit_ids
    )
    if (
        set(receipt) != _RECEIPT_KEYS
        or receipt.get("version") != 2
        or receipt.get("revision_id") != state.revision_id
        or receipt.get("unit_id") != unit.unit_id
        or receipt.get("input_sha256") != input_payload["input_sha256"]
        or receipt.get("output_sha256") != output.output_sha256
        or receipt.get("dependency_output_sha256s") != list(dependency_hashes)
        or not _is_rfc3339(receipt.get("validated_at"))
        or not _is_sha256(receipt_sha256)
        or hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()
        != receipt_sha256
        or content != _canonical_json_bytes(receipt)
    ):
        raise SemanticWorkError("semantic work receipt is invalid")
    return _ReceiptBinding(
        input_sha256=str(input_payload["input_sha256"]),
        output=output,
        dependency_output_sha256s=dependency_hashes,
        content=content,
    )


def _receipt_payload(
    state: _VerifiedState,
    unit: _VerifiedUnit,
    input_payload: dict[str, object],
    output: _OutputBinding,
    valid_receipts: Mapping[str, _ReceiptBinding],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 2,
        "revision_id": state.revision_id,
        "unit_id": unit.unit_id,
        "input_sha256": input_payload["input_sha256"],
        "output_sha256": output.output_sha256,
        "dependency_output_sha256s": [
            valid_receipts[dependency_id].output.output_sha256
            for dependency_id in unit.dependency_unit_ids
        ],
        "validated_at": utc_now(),
    }
    return {
        **payload,
        "receipt_sha256": hashlib.sha256(
            _canonical_json_bytes(payload)
        ).hexdigest(),
    }


def _read_required(path: Path, *, output: bool = False) -> bytes:
    try:
        return read_managed_bytes(path)
    except (OSError, UnicodeError) as error:
        label = "output" if output else "state"
        raise SemanticWorkError(
            f"semantic work {label} is unsafe or unavailable"
        ) from error


class _DuplicateKeyError(ValueError):
    pass


def _decode_object(
    content: bytes,
    label: str,
    *,
    output: bool = False,
) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateKeyError
            result[key] = value
        return result

    try:
        parsed = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError) as error:
        kind = "output" if output else label
        raise SemanticWorkError(
            f"semantic work {kind} is invalid"
        ) from error
    if type(parsed) is not dict:
        kind = "output" if output else label
        raise SemanticWorkError(f"semantic work {kind} is invalid")
    return parsed


def _unit_path(directory: Path, unit_id: str) -> Path:
    if not _is_unit_id(unit_id):
        raise SemanticWorkError("semantic work unit is invalid")
    return directory / f"{unit_id}.json"


def _directory_entry_names(directory: Path) -> set[str]:
    try:
        return {path.name for path in directory.iterdir()}
    except OSError as error:
        raise SemanticWorkError("semantic work managed directory is invalid") from error


def _stage_matches_capsule(stage: object, capsule: dict[str, object]) -> bool:
    kind = capsule.get("kind")
    return (
        (stage == SemanticWorkStage.LEAF.value and kind == "file")
        or (stage == SemanticWorkStage.DIRECTORY.value and kind == "directory")
        or (stage == SemanticWorkStage.ROOT.value and kind == "source")
    )


def _is_unit_id(value: object) -> bool:
    return type(value) is str and _UNIT_ID.fullmatch(value) is not None


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _is_nonempty_text(value: object) -> bool:
    return type(value) is str and bool(value)


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_valid_directory_timeout(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(value)
        and 0 < value <= MAX_DIRECTORY_TIMEOUT_SECONDS
    )


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _is_rfc3339(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _canonical_json(payload: object) -> str:
    return _canonical_json_bytes(payload).decode("utf-8")


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hierarchy_depth(relative_hierarchy: str) -> int:
    return 0 if relative_hierarchy == "." else relative_hierarchy.count("/") + 1
