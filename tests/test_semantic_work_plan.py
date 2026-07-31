from __future__ import annotations

import hashlib
import json

import pytest

from portfolio_maker.application.semantic_work import (
    STRUCTURAL_CHUNK_SIZE,
    SemanticWorkError,
    build_semantic_work_plan,
)
from portfolio_maker.domain.semantic_work import SemanticWorkStage


def _capsule(
    node_id: str,
    *,
    parent_node_id: str | None = None,
    kind: str = "file",
    disposition: str = "analysis_required",
    signal_size: int = 16,
    hierarchy: str | None = None,
) -> dict[str, object]:
    return {
        "node_id": node_id,
        "parent_node_id": parent_node_id,
        "kind": kind,
        "display_name": node_id,
        "relative_hierarchy": hierarchy or node_id,
        "analysis_status": "complete",
        "semantic_disposition": disposition,
        "roles": [],
        "child_node_ids": [],
        "signals": (
            [
                {
                    "signal_id": f"sha256:{node_id:0<64}"[:71],
                    "kind": "masked_text",
                    "text": "x" * signal_size,
                }
            ]
            if disposition == "analysis_required"
            else []
        ),
        "skip_reason": None,
        "evidence_ids": [],
        "coverage": {
            "node_count": 1,
            "content_node_count": int(disposition == "analysis_required"),
            "structural_only_count": int(disposition != "analysis_required"),
            "failed_count": 0,
            "node_ids_sha256": "a" * 64,
        },
    }


def _recompute_owner_digest(
    capsules: tuple[dict[str, object], ...],
    plan_units: tuple[object, ...],
) -> str:
    assignments = [
        (node_id, "semantic_work_unit", unit.unit_id)
        for unit in plan_units
        for node_id in unit.target_node_ids
    ]
    ordered = sorted(
        capsules,
        key=lambda capsule: (
            str(capsule["relative_hierarchy"]),
            str(capsule["node_id"]),
        ),
    )
    for index, capsule in enumerate(ordered):
        if capsule["semantic_disposition"] == "analysis_required":
            continue
        assignments.append(
            (
                str(capsule["node_id"]),
                "private_structural_chunk",
                f"chunk-{index // STRUCTURAL_CHUNK_SIZE + 1:04}.json",
            )
        )
    canonical = json.dumps(
        sorted(assignments),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_work_plan_assigns_every_node_once_and_has_no_global_cap() -> None:
    capsules = tuple(_capsule(f"node-{index:05}") for index in range(10_001))

    plan = build_semantic_work_plan(
        capsules,
        revision_id="revision",
        policy_hash="a" * 64,
        max_work_unit_bytes=262_144,
        reduce_fan_in=64,
    )

    semantic_targets = [
        node_id
        for unit in plan.units
        for node_id in unit.target_node_ids
    ]
    assert sorted(semantic_targets) == sorted(
        str(capsule["node_id"]) for capsule in capsules
    )
    assert len(semantic_targets) == len(set(semantic_targets)) == 10_001
    assert plan.analysis_target_count == 10_001
    assert plan.structural_owner_count == 0
    assert plan.node_count == 10_001
    assert plan.node_assignment_sha256 == _recompute_owner_digest(
        capsules,
        plan.units,
    )


def test_wide_tree_uses_bounded_reduce_units_and_exact_template_sizes() -> None:
    children = tuple(
        _capsule(
            f"child-{index:04}",
            parent_node_id="root",
            signal_size=8_000,
            hierarchy=f"child-{index:04}.md",
        )
        for index in range(512)
    )
    root = _capsule(
        "root",
        kind="source",
        signal_size=0,
        hierarchy=".",
    )
    root["signals"] = []
    root["child_node_ids"] = [child["node_id"] for child in children]
    capsules = (root, *children)

    plan = build_semantic_work_plan(
        capsules,
        revision_id="revision",
        policy_hash="a" * 64,
        max_work_unit_bytes=262_144,
        reduce_fan_in=64,
    )

    reduce_units = [
        unit for unit in plan.units if unit.stage is SemanticWorkStage.REDUCE
    ]
    root_unit = next(
        unit
        for unit in plan.units
        if unit.stage is SemanticWorkStage.ROOT
    )
    assert reduce_units
    assert all(len(unit.dependency_unit_ids) <= 64 for unit in reduce_units)
    assert len(root_unit.dependency_unit_ids) <= 64
    assert all(unit.template_byte_count <= 262_144 for unit in plan.units)
    assert all(len(unit.template_sha256) == 64 for unit in plan.units)
    assert [unit.unit_id for unit in plan.units] == sorted(
        unit.unit_id for unit in plan.units
    )


def test_work_plan_accounts_for_structural_nodes_in_owner_digest() -> None:
    capsules = (
        _capsule("semantic"),
        _capsule("empty", disposition="structural_only"),
        _capsule("binary", disposition="unsupported"),
        _capsule("blocked", disposition="unreadable"),
    )

    plan = build_semantic_work_plan(
        capsules,
        revision_id="revision",
        policy_hash="a" * 64,
    )

    assert plan.analysis_target_count == 1
    assert plan.structural_owner_count == 3
    assert plan.node_assignment_sha256 == _recompute_owner_digest(
        capsules,
        plan.units,
    )


@pytest.mark.parametrize(
    "capsules",
    (
        (_capsule("duplicate"), _capsule("duplicate")),
        (_capsule("unknown", disposition="not-a-disposition"),),
    ),
)
def test_work_plan_rejects_duplicate_or_missing_ownership(
    capsules: tuple[dict[str, object], ...],
) -> None:
    with pytest.raises(SemanticWorkError, match="ownership"):
        build_semantic_work_plan(
            capsules,
            revision_id="revision",
            policy_hash="a" * 64,
        )
