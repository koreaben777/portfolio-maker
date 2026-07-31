from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from portfolio_maker.application.models import (
    FinalizeSemanticIndexRequest,
    FinalizeSemanticIndexResult,
    NextSemanticWorkRequest,
    NextSemanticWorkResult,
    PrepareSemanticIndexResult,
    SemanticIndexStatusRequest,
    SemanticIndexStatusResult,
    ValidateSemanticWorkRequest,
    ValidateSemanticWorkResult,
)
from portfolio_maker.domain.semantic_work import (
    SemanticDisposition,
    SemanticWorkPlan,
    SemanticWorkStage,
    SemanticWorkUnit,
)
from portfolio_maker.workspace import WorkspacePaths


def test_semantic_work_protocol_uses_exact_enum_values() -> None:
    assert tuple(stage.value for stage in SemanticWorkStage) == (
        "leaf",
        "reduce",
        "directory",
        "root",
    )
    assert tuple(disposition.value for disposition in SemanticDisposition) == (
        "analysis_required",
        "structural_only",
        "unsupported",
        "unreadable",
    )


def test_semantic_work_models_are_frozen_and_preserve_tuple_order() -> None:
    first = SemanticWorkUnit(
        unit_id="unit-000002",
        stage=SemanticWorkStage.DIRECTORY,
        target_node_ids=("node-b", "node-a"),
        dependency_unit_ids=("unit-000001", "unit-000000"),
        template_sha256="a" * 64,
        template_byte_count=512,
    )
    second = SemanticWorkUnit(
        unit_id="unit-000001",
        stage=SemanticWorkStage.LEAF,
        target_node_ids=("node-c",),
        dependency_unit_ids=(),
        template_sha256="b" * 64,
        template_byte_count=256,
    )
    plan = SemanticWorkPlan(
        revision_id="rev-1",
        policy_hash="c" * 64,
        protocol="portfolio-semantic-index-v2",
        node_count=3,
        analysis_target_count=2,
        structural_owner_count=1,
        max_work_unit_bytes=262_144,
        reduce_fan_in=8,
        node_assignment_sha256="d" * 64,
        units=(first, second),
    )

    assert first.target_node_ids == ("node-b", "node-a")
    assert first.dependency_unit_ids == ("unit-000001", "unit-000000")
    assert first.template_byte_count == 512
    assert plan.units == (first, second)
    with pytest.raises(FrozenInstanceError):
        first.unit_id = "changed"
    with pytest.raises(FrozenInstanceError):
        plan.protocol = "changed"


def test_semantic_application_contracts_have_exact_fields_and_are_frozen(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    status_request = SemanticIndexStatusRequest(workspace)
    status_result = SemanticIndexStatusResult("rev-1", 9, 4, 2, 1, 1, False)
    next_request = NextSemanticWorkRequest(workspace)
    next_result = NextSemanticWorkResult("rev-1", None, None, 3)
    validate_request = ValidateSemanticWorkRequest(workspace, "unit-000001")
    validate_result = ValidateSemanticWorkResult(
        "unit-000001",
        workspace / "receipt.json",
        "a" * 64,
    )
    finalize_request = FinalizeSemanticIndexRequest(workspace)
    finalize_result = FinalizeSemanticIndexResult(
        "rev-1",
        workspace / "manifest.json",
        4,
        9,
    )
    prepare_result = PrepareSemanticIndexResult(
        workspace / "input-manifest.json",
        "rev-1",
        9,
        4,
        1,
        6,
        40_000,
        5,
        4,
    )

    expected_fields = (
        (status_request, ("workspace",)),
        (
            status_result,
            (
                "revision_id",
                "node_count",
                "work_unit_count",
                "ready_count",
                "receipted_count",
                "invalid_count",
                "complete",
            ),
        ),
        (next_request, ("workspace",)),
        (
            next_result,
            ("revision_id", "unit_id", "input_path", "remaining_count"),
        ),
        (validate_request, ("workspace", "unit_id")),
        (
            validate_result,
            ("unit_id", "receipt_path", "output_sha256"),
        ),
        (finalize_request, ("workspace",)),
        (
            finalize_result,
            ("revision_id", "manifest_path", "chunk_count", "node_count"),
        ),
        (
            prepare_result,
            (
                "manifest_path",
                "revision_id",
                "node_count",
                "chunk_count",
                "partial_count",
                "work_unit_count",
                "managed_input_bytes",
                "analysis_target_count",
                "structural_only_count",
            ),
        ),
    )
    for instance, field_names in expected_fields:
        instance_fields = fields(instance)
        assert tuple(field.name for field in instance_fields) == field_names
        with pytest.raises(FrozenInstanceError):
            setattr(instance, instance_fields[0].name, object())

    assert next_result.unit_id is None
    assert next_result.input_path is None
    assert (
        status_request.workspace
        == next_request.workspace
        == validate_request.workspace
        == finalize_request.workspace
    )
    assert status_result.revision_id == "rev-1"
    assert validate_result.unit_id == "unit-000001"
    assert validate_result.receipt_path == workspace / "receipt.json"
    assert finalize_result.manifest_path == workspace / "manifest.json"


def test_workspace_paths_include_semantic_protocol_v2_surfaces(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    paths = WorkspacePaths.from_root(workspace)

    assert paths.semantic_work_plan_path == (
        workspace.resolve()
        / ".portfolio-maker"
        / "reviews"
        / "semantic-index"
        / "work-plan.json"
    )
    assert paths.semantic_work_input_dir.name == "work-input"
    assert paths.semantic_work_output_dir.name == "work-output"
    assert paths.semantic_work_receipts_dir.name == "receipts"
    assert paths.semantic_final_dir.name == "final"
    assert paths.semantic_revision_raw_dir("rev-1") == (
        workspace.resolve()
        / ".portfolio-maker"
        / "raw"
        / "semantic-index"
        / "rev-1"
    )
    assert paths.semantic_revision_structural_dir("rev-1").name == "structural"
    assert paths.semantic_revision_locator_dir("rev-1").name == "locators"
    assert paths.semantic_revision_raw_dir("a" * 32).name == "a" * 32


@pytest.mark.parametrize(
    "revision_id",
    ("", ".", "..", "rev/1", "rev\\1", "rev\x001"),
)
def test_semantic_revision_paths_reject_unsafe_identifiers(
    tmp_path: Path,
    revision_id: str,
) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "workspace")

    with pytest.raises(ValueError, match="revision"):
        paths.semantic_revision_raw_dir(revision_id)
    with pytest.raises(ValueError, match="revision"):
        paths.semantic_revision_structural_dir(revision_id)
    with pytest.raises(ValueError, match="revision"):
        paths.semantic_revision_locator_dir(revision_id)
