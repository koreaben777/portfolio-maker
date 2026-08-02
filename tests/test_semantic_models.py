from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from enum import StrEnum

import pytest

from portfolio_maker.domain.semantic_models import (
    AnalysisStatus,
    RevisionStatus,
    SemanticEdge,
    SemanticNode,
    SemanticNodeKind,
    SemanticRevision,
    boundary_fingerprint,
    stable_node_id,
    stable_source_id,
)
from portfolio_maker.domain.semantic_frontier import (
    AUDIT_BUDGET,
    CHECKPOINT_UNITS,
    DEEPENING_BUDGET,
    EXPANSION_BUDGET,
    FRONTIER_BUDGET,
    FRONTIER_PHASE_HARD_BUDGETS,
    MAX_DEPENDENCIES,
    MAX_UNIT_BYTES,
    ORIENTATION_BUDGET,
    BaselineCoverage,
    DeepeningAuthorityContext,
    DeepeningOverlayState,
    FrontierAction,
    FrontierPhase,
    PackRecordRef,
    SemanticBudget,
    SemanticCompletion,
    SemanticDeepeningPlan,
    SemanticDeferredAuditRow,
    SemanticDeferredNode,
    SemanticExecutionProposal,
    SemanticFrontierPlan,
    SemanticLeaseState,
    SemanticPhaseBinding,
    SemanticPlannedUnit,
    SemanticProposalEnvelope,
    SemanticRunAuthority,
    SemanticRunState,
    SemanticRuntimeDeferredRow,
    SemanticUnitState,
    SealedProposalAuthority,
    semantic_proposal_sha256,
)
from portfolio_maker.workspace import WorkspacePaths


def test_semantic_ids_are_stable_and_do_not_contain_locator() -> None:
    source_id = stable_source_id("local", "approved-root-key")
    node_id = stable_node_id(source_id, "1048577:99123")

    assert source_id == stable_source_id("local", "approved-root-key")
    assert node_id == stable_node_id(source_id, "1048577:99123")
    assert source_id.startswith("sha256:")
    assert node_id.startswith("sha256:")
    assert "/Users/" not in node_id


def test_boundary_fingerprint_is_order_independent() -> None:
    assert boundary_fingerprint("directory_root", ("b", "a")) == boundary_fingerprint(
        "directory_root", ("a", "b")
    )


def test_semantic_enums_expose_frozen_contract_values() -> None:
    assert [item.value for item in SemanticNodeKind] == [
        "source",
        "directory",
        "file",
        "github_activity",
    ]
    assert [item.value for item in AnalysisStatus] == [
        "pending",
        "complete",
        "partial",
        "unsupported",
        "unreadable",
        "failed",
    ]
    assert [item.value for item in RevisionStatus] == [
        "staging",
        "active",
        "superseded",
        "failed",
    ]


def test_semantic_value_types_are_frozen_and_locator_free() -> None:
    node = SemanticNode(
        node_id="node-1",
        source_id="source-1",
        node_kind=SemanticNodeKind.FILE,
        parent_node_id=None,
        display_name="README.md",
        relative_hierarchy="README.md",
        content_fingerprint="sha256:fingerprint",
        semantic_summary="Project overview",
        semantic_roles=("docs",),
        topics=("portfolio",),
        evidence_ids=(1,),
        analysis_status=AnalysisStatus.COMPLETE,
        analyzer_version="semantic-v1",
        updated_at="2026-07-14T00:00:00Z",
    )
    edge = SemanticEdge(
        revision_id="revision-1",
        from_node_id="node-1",
        relation="contains",
        to_node_id="node-2",
        confidence="high",
    )
    revision = SemanticRevision(
        id="revision-1",
        source_id="source-1",
        policy_hash="sha256:policy",
        analyzer_version="semantic-v1",
        status=RevisionStatus.STAGING,
    )

    assert "locator" not in {field.name for field in fields(node)}
    assert node.semantic_roles == ("docs",)
    assert edge.relation == "contains"
    assert revision.status is RevisionStatus.STAGING
    with pytest.raises(FrozenInstanceError):
        node.display_name = "changed"  # type: ignore[misc]


def test_v3_default_budgets_and_completion_states_are_exact(tmp_path: Path) -> None:
    assert FRONTIER_BUDGET == SemanticBudget(512, 128 * 1024**2, 7_200)
    assert DEEPENING_BUDGET == SemanticBudget(256, 64 * 1024**2, 3_600)
    assert FRONTIER_PHASE_HARD_BUDGETS == (
        (FrontierPhase.ORIENTATION, ORIENTATION_BUDGET),
        (FrontierPhase.EXPANSION, EXPANSION_BUDGET),
        (FrontierPhase.AUDIT, AUDIT_BUDGET),
    )
    completion = SemanticCompletion(
        structural_complete=True,
        local_baseline_complete=True,
        planned_receipts_complete=True,
        frontier_converged=False,
        paused_budget=True,
        project_discovery_ready=False,
        deepening_complete=False,
    )

    assert completion.paused_budget is True
    assert completion.project_discovery_ready is False


def test_run_and_lease_states_are_disjoint() -> None:
    assert not ({state.value for state in SemanticRunState} & {state.value for state in SemanticLeaseState})


def test_frontier_converged_and_paused_budget_cannot_both_be_true() -> None:
    with pytest.raises(ValueError, match="converged.*paused_budget"):
        SemanticCompletion(
            structural_complete=True,
            local_baseline_complete=True,
            planned_receipts_complete=True,
            frontier_converged=True,
            paused_budget=True,
            project_discovery_ready=False,
            deepening_complete=False,
        )


def test_v3_workspace_paths_are_revision_and_run_scoped(tmp_path: Path) -> None:
    paths = WorkspacePaths.from_root(tmp_path / "workspace")

    assert paths.semantic_v3_catalog_path("rev-1").name == "catalog.sqlite"
    assert paths.semantic_v3_pack_path("rev-1", "structural").name == "structural.pm-pack"
    assert paths.semantic_v3_result_pack_path("rev-1", "run-1").name == "results.pm-pack"
    assert paths.semantic_v3_deepening_deferred_index_path("rev-1", "run-1").name == "deferred.sqlite"
    temp = paths.semantic_v3_deepening_deferred_temp_path("rev-1", "run-1")

    assert temp.parent == paths.semantic_v3_deepening_deferred_index_path("rev-1", "run-1").parent
    assert temp.name == "deferred.sqlite.preparing"
    with pytest.raises(ValueError):
        paths.semantic_v3_run_dir("rev-1", "../escape")


def test_frontier_proposal_rejects_reassigned_phase_budget() -> None:
    proposal = _frontier_proposal()
    with pytest.raises(ValueError, match="phase bindings"):
        replace(
            proposal,
            phase_bindings=(
                SemanticPhaseBinding(
                    FrontierPhase.ORIENTATION,
                    ORIENTATION_BUDGET,
                    EXPANSION_BUDGET.max_units,
                    EXPANSION_BUDGET.max_input_bytes,
                    EXPANSION_BUDGET.max_wall_seconds,
                ),
                *proposal.phase_bindings[1:],
            ),
        )


def test_shared_interface_contract_exposes_the_fixed_v3_types() -> None:
    assert MAX_UNIT_BYTES == 262_144
    assert MAX_DEPENDENCIES == 64
    assert CHECKPOINT_UNITS == 16
    assert SemanticRunState.AWAITING_APPROVAL == "awaiting_approval"
    assert SemanticRunState.PAUSED_USER == "paused_user"
    assert SemanticLeaseState.MATERIALIZING == "materializing"
    assert SemanticLeaseState.ISSUED == "issued"
    assert SemanticUnitState.VALIDATED == "validated"
    assert DeepeningOverlayState.INACTIVE_REVIEW_REQUIRED == "inactive_review_required"

    budget = SemanticBudget(1, 2, 3)
    binding = SemanticPhaseBinding(FrontierPhase.ORIENTATION, budget, 1, 2, 3)
    unit = SemanticPlannedUnit(
        "unit-1", FrontierPhase.ORIENTATION, ("node-1",), (), 2, 1, "default"
    )
    deferred = SemanticDeferredNode("node-1", "budget", "digest", "{}")
    audit = SemanticDeferredAuditRow(
        "node-1", "budget", "digest", "{}", 1, "source-1", "sampled", 2,
        "selector-v1", "feature-digest",
    )
    runtime_deferred = SemanticRuntimeDeferredRow(audit, "unit-1", 1)
    plan = SemanticFrontierPlan(
        "run-1", "rev-1", "policy", "selector-v1", "projection-v1", "input-v1",
        "output-v1", "validator-v1", "frontier", (unit,), 1, "deferred-root",
        "cursor-root",
    )
    sealed = SealedProposalAuthority(
        "rev-1", "approval", "policy", "manifest", "catalog", "source", "private",
        1, ((BaselineCoverage.COMPLETE.value, 1),), "selector-v1", "genesis",
        "projection-v1", "input-v1", "output-v1", "validator-v1", "sealed-root",
    )
    deepening = SemanticDeepeningPlan(
        "run-2", "rev-1", "project-1", "authority", "run-1", "proposal",
        "checkpoint", "boundary", "policy", "selector-v1", "catalog", "private",
        "evidence", "projection-v1", "input-v1", "output-v1", "validator-v1",
        "deepening", (unit,), 1, "deferred-root", "cursor-root",
    )
    context = DeepeningAuthorityContext(
        sealed, "run-1", "proposal", "checkpoint", "project-1", "boundary",
        "evidence", "deepening", "context-root",
    )
    record = PackRecordRef(0, 1, "digest")

    assert budget.max_units == 1
    assert budget.max_input_bytes == 2
    assert budget.max_wall_seconds == 3
    assert binding.estimated_input_bytes == 2
    assert plan.units == (unit,)
    assert deferred.cursor_json == "{}"
    assert runtime_deferred.audit is audit
    assert deepening.project_id == "project-1"
    assert context.base is sealed
    assert record.length == 1


def test_proposal_authority_uses_approval_kind_and_envelope_digest() -> None:
    frontier = _frontier_proposal()

    with pytest.raises(ValueError, match="frontier"):
        replace(
            frontier,
            project_id="project-1",
            project_boundary_sha256="boundary",
            parent_proposal_sha256="parent",
            evidence_policy_sha256="evidence",
            phase_bindings=(
                SemanticPhaseBinding(
                    FrontierPhase.DEEPENING, DEEPENING_BUDGET, 256, 64 * 1024**2, 3_600
                ),
            ),
            budget=DEEPENING_BUDGET,
        )
    with pytest.raises(ValueError, match="deepening"):
        replace(
            frontier,
            approval_kind="project_deepening",
            phase_bindings=(
                SemanticPhaseBinding(
                    FrontierPhase.DEEPENING, DEEPENING_BUDGET, 256, 64 * 1024**2, 3_600
                ),
            ),
            budget=DEEPENING_BUDGET,
        )
    with pytest.raises(ValueError, match="proposal_sha256"):
        SemanticProposalEnvelope(frontier, "0" * 64)

    assert SemanticProposalEnvelope(
        frontier, semantic_proposal_sha256(frontier)
    ).proposal is frontier


def test_fixed_v3_enum_wire_sets_and_project_deepening_literal() -> None:
    assert tuple(
        (name, member.value) for name, member in BaselineCoverage.__members__.items()
    ) == (
        ("COMPLETE", "complete"),
        ("SAMPLED", "sampled"),
        ("UNSUPPORTED", "unsupported"),
        ("UNREADABLE", "unreadable"),
        ("FAILED", "failed"),
    )
    assert tuple(
        (name, member.value) for name, member in FrontierPhase.__members__.items()
    ) == (
        ("ORIENTATION", "orientation"),
        ("EXPANSION", "expansion"),
        ("AUDIT", "audit"),
        ("DEEPENING", "deepening"),
    )
    assert tuple(
        (name, member.value) for name, member in FrontierAction.__members__.items()
    ) == (
        ("ACCEPT_BOUNDARY", "accept_boundary"),
        ("EXPAND_CHILDREN", "expand_children"),
        ("STOP_NON_PROJECT", "stop_non_project"),
        ("DEFER_ON_DEMAND", "defer_on_demand"),
    )
    assert tuple(
        (name, member.value) for name, member in SemanticRunState.__members__.items()
    ) == (
        ("PREPARED", "prepared"),
        ("AWAITING_APPROVAL", "awaiting_approval"),
        ("RUNNING", "running"),
        ("PAUSED_USER", "paused_user"),
        ("PAUSED_BUDGET", "paused_budget"),
        ("BLOCKED", "blocked"),
        ("CONVERGED", "converged"),
        ("FINALIZING", "finalizing"),
        ("FINALIZED", "finalized"),
        ("FAILED", "failed"),
    )
    assert tuple(
        (name, member.value) for name, member in SemanticLeaseState.__members__.items()
    ) == (
        ("MATERIALIZING", "materializing"),
        ("ISSUED", "issued"),
        ("EXPIRED", "expired"),
    )
    assert tuple(
        (name, member.value) for name, member in SemanticUnitState.__members__.items()
    ) == (
        ("READY", "ready"),
        ("LEASED", "leased"),
        ("VALIDATED", "validated"),
        ("DEFERRED", "deferred"),
        ("ERROR", "error"),
    )
    assert tuple(
        (name, member.value)
        for name, member in DeepeningOverlayState.__members__.items()
    ) == (
        ("PROPOSED", "proposed"),
        ("INACTIVE_REVIEW_REQUIRED", "inactive_review_required"),
        ("CANCELLED", "cancelled"),
        ("INVALIDATED", "invalidated"),
    )

    proposal = _project_deepening_proposal()

    assert proposal.approval_kind == "project_deepening"
    with pytest.raises(ValueError, match="approval_kind"):
        replace(proposal, approval_kind="deepening")


def test_enum_iteration_misses_an_added_alias() -> None:
    class AliasProbe(StrEnum):
        CANONICAL = "canonical"
        ALIAS = "canonical"

    assert tuple((member.name, member.value) for member in AliasProbe) == (
        ("CANONICAL", "canonical"),
    )
    assert tuple(
        (name, member.value) for name, member in AliasProbe.__members__.items()
    ) == (
        ("CANONICAL", "canonical"),
        ("ALIAS", "canonical"),
    )


def _project_deepening_proposal() -> SemanticExecutionProposal:
    return replace(
        _frontier_proposal(),
        approval_kind="project_deepening",
        project_id="project-1",
        project_boundary_sha256="boundary",
        parent_proposal_sha256="parent-proposal",
        evidence_policy_sha256="evidence-policy",
        phase_bindings=(
            SemanticPhaseBinding(
                FrontierPhase.DEEPENING,
                DEEPENING_BUDGET,
                DEEPENING_BUDGET.max_units,
                DEEPENING_BUDGET.max_input_bytes,
                DEEPENING_BUDGET.max_wall_seconds,
            ),
        ),
        budget=DEEPENING_BUDGET,
    )


def _frontier_proposal() -> SemanticExecutionProposal:
    return SemanticExecutionProposal(
        run_id="run-1",
        revision_id="rev-1",
        approval_kind="frontier",
        source_approval_sha256="a" * 64,
        manifest_sha256="b" * 64,
        policy_sha256="c" * 64,
        selector_version="selector-v1",
        catalog_root="sha256:catalog",
        safe_source_label="approved-source",
        private_context_scope="private",
        authority_root="sha256:authority",
        plan_sha256="d" * 64,
        parent_checkpoint_root="sha256:checkpoint",
        target_projection_contract="projection-v1",
        input_schema_version="input-v1",
        output_schema_version="output-v1",
        validator_version="validator-v1",
        purpose="frontier",
        structural_node_count=9,
        baseline_coverage_counts=(("file", 9),),
        phase_bindings=tuple(
            SemanticPhaseBinding(
                phase, budget, budget.max_units, budget.max_input_bytes,
                budget.max_wall_seconds,
            )
            for phase, budget in FRONTIER_PHASE_HARD_BUDGETS
        ),
        audit_selector_rule="unresolved",
        deferred_semantics="defer",
        budget=FRONTIER_BUDGET,
    )
