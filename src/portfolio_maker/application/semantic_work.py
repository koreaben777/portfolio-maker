from __future__ import annotations

import hashlib
import json

from portfolio_maker.domain.semantic_work import (
    SemanticDisposition,
    SemanticWorkPlan,
    SemanticWorkStage,
    SemanticWorkUnit,
)


MAX_WORK_UNIT_BYTES = 262_144
REDUCE_FAN_IN = 64
STRUCTURAL_CHUNK_SIZE = 1_000
_VALID_DISPOSITIONS = {disposition.value for disposition in SemanticDisposition}


class SemanticWorkError(ValueError):
    pass


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
            "capsules": [capsule_by_id[node_id] for node_id in target_node_ids],
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


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hierarchy_depth(relative_hierarchy: str) -> int:
    return 0 if relative_hierarchy == "." else relative_hierarchy.count("/") + 1
