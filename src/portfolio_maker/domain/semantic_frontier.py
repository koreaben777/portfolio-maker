from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
from typing import Any


SEMANTIC_PROTOCOL_V2 = "codex-semantic-v2"
SEMANTIC_PROTOCOL_V3 = "codex-semantic-v3"
MAX_UNIT_BYTES = 262_144
MAX_DEPENDENCIES = 64
CHECKPOINT_UNITS = 16


class BaselineCoverage(StrEnum):
    COMPLETE = "complete"
    SAMPLED = "sampled"
    UNSUPPORTED = "unsupported"
    UNREADABLE = "unreadable"
    FAILED = "failed"


class FrontierPhase(StrEnum):
    ORIENTATION = "orientation"
    EXPANSION = "expansion"
    AUDIT = "audit"
    DEEPENING = "deepening"


class FrontierAction(StrEnum):
    ACCEPT_BOUNDARY = "accept_boundary"
    EXPAND_CHILDREN = "expand_children"
    STOP_NON_PROJECT = "stop_non_project"
    DEFER_ON_DEMAND = "defer_on_demand"


class SemanticRunState(StrEnum):
    PREPARED = "prepared"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    PAUSED_USER = "paused_user"
    PAUSED_BUDGET = "paused_budget"
    BLOCKED = "blocked"
    CONVERGED = "converged"
    FINALIZING = "finalizing"
    FINALIZED = "finalized"
    FAILED = "failed"


class SemanticLeaseState(StrEnum):
    MATERIALIZING = "materializing"
    ISSUED = "issued"
    EXPIRED = "expired"


class SemanticUnitState(StrEnum):
    READY = "ready"
    LEASED = "leased"
    VALIDATED = "validated"
    DEFERRED = "deferred"
    ERROR = "error"


class DeepeningOverlayState(StrEnum):
    PROPOSED = "proposed"
    INACTIVE_REVIEW_REQUIRED = "inactive_review_required"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"


@dataclass(frozen=True)
class SemanticBudget:
    max_units: int
    max_input_bytes: int
    max_wall_seconds: int
    max_unit_input_bytes: int = MAX_UNIT_BYTES
    max_unit_output_bytes: int = MAX_UNIT_BYTES
    max_unit_wall_seconds: int = 300
    checkpoint_units: int = CHECKPOINT_UNITS


FRONTIER_BUDGET = SemanticBudget(512, 128 * 1024**2, 7_200)
ORIENTATION_BUDGET = SemanticBudget(64, 16 * 1024**2, 15 * 60)
EXPANSION_BUDGET = SemanticBudget(384, 96 * 1024**2, 90 * 60)
AUDIT_BUDGET = SemanticBudget(64, 16 * 1024**2, 15 * 60)
DEEPENING_BUDGET = SemanticBudget(256, 64 * 1024**2, 3_600)
FRONTIER_PHASE_HARD_BUDGETS = (
    (FrontierPhase.ORIENTATION, ORIENTATION_BUDGET),
    (FrontierPhase.EXPANSION, EXPANSION_BUDGET),
    (FrontierPhase.AUDIT, AUDIT_BUDGET),
)


@dataclass(frozen=True)
class SemanticCompletion:
    structural_complete: bool
    local_baseline_complete: bool
    planned_receipts_complete: bool
    frontier_converged: bool
    paused_budget: bool
    project_discovery_ready: bool
    deepening_complete: bool

    def __post_init__(self) -> None:
        if self.frontier_converged and self.paused_budget:
            raise ValueError("frontier_converged and paused_budget cannot both be true")


@dataclass(frozen=True)
class SemanticPhaseBinding:
    phase: FrontierPhase
    hard_budget: SemanticBudget
    estimated_units: int
    estimated_input_bytes: int
    estimated_wall_seconds: int


@dataclass(frozen=True)
class SemanticPlannedUnit:
    unit_id: str
    phase: FrontierPhase
    target_node_ids: tuple[str, ...]
    dependency_unit_ids: tuple[str, ...]
    estimated_input_bytes: int
    priority: int
    fairness_lane: str


@dataclass(frozen=True)
class SemanticDeferredNode:
    node_id: str
    reason: str
    node_digest: str
    cursor_json: str


@dataclass(frozen=True)
class SemanticDeferredAuditRow:
    node_id: str
    reason: str
    node_digest: str
    cursor_json: str
    cursor_order: int
    source_id: str
    coverage_stratum: str
    relevance_rank: int
    selector_version: str
    selector_feature_digest: str


@dataclass(frozen=True)
class SemanticRuntimeDeferredRow:
    audit: SemanticDeferredAuditRow
    source_transition_unit_id: str
    created_sequence: int


@dataclass(frozen=True)
class SemanticFrontierPlan:
    run_id: str
    revision_id: str
    policy_sha256: str
    selector_version: str
    target_projection_contract: str
    input_schema_version: str
    output_schema_version: str
    validator_version: str
    purpose: str
    units: tuple[SemanticPlannedUnit, ...]
    deferred_count: int
    deferred_root: str
    deferred_cursor_root: str


@dataclass(frozen=True)
class SemanticDeepeningPlan:
    run_id: str
    revision_id: str
    project_id: str
    authority_root: str
    parent_run_id: str
    parent_proposal_sha256: str
    parent_frontier_checkpoint_root: str
    project_boundary_sha256: str
    source_policy_sha256: str
    selector_version: str
    catalog_root: str
    private_context_scope: str
    evidence_policy_sha256: str
    target_projection_contract: str
    input_schema_version: str
    output_schema_version: str
    validator_version: str
    purpose: str
    units: tuple[SemanticPlannedUnit, ...]
    deferred_count: int
    deferred_root: str
    deferred_cursor_root: str


@dataclass(frozen=True)
class SealedProposalAuthority:
    revision_id: str
    source_approval_sha256: str
    source_policy_sha256: str
    manifest_sha256: str
    catalog_root: str
    safe_source_label: str
    private_context_scope: str
    structural_node_count: int
    baseline_coverage_counts: tuple[tuple[str, int], ...]
    selector_version: str
    lifecycle_genesis_root: str
    target_projection_contract: str
    input_schema_version: str
    output_schema_version: str
    validator_version: str
    root: str


@dataclass(frozen=True)
class DeepeningAuthorityContext:
    base: SealedProposalAuthority
    parent_run_id: str
    parent_proposal_sha256: str
    parent_checkpoint_root: str
    project_id: str
    project_boundary_sha256: str
    evidence_policy_sha256: str
    purpose: str
    root: str


@dataclass(frozen=True)
class PackRecordRef:
    offset: int
    length: int
    sha256: str


@dataclass(frozen=True)
class SemanticExecutionProposal:
    run_id: str
    revision_id: str
    approval_kind: str
    source_approval_sha256: str
    manifest_sha256: str
    policy_sha256: str
    selector_version: str
    catalog_root: str
    safe_source_label: str
    private_context_scope: str
    authority_root: str
    plan_sha256: str
    parent_checkpoint_root: str
    target_projection_contract: str
    input_schema_version: str
    output_schema_version: str
    validator_version: str
    purpose: str
    structural_node_count: int
    baseline_coverage_counts: tuple[tuple[str, int], ...]
    phase_bindings: tuple[SemanticPhaseBinding, ...]
    audit_selector_rule: str
    deferred_semantics: str
    budget: SemanticBudget
    project_id: str | None = None
    project_boundary_sha256: str | None = None
    parent_proposal_sha256: str | None = None
    evidence_policy_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.approval_kind == "frontier":
            if any(
                value is not None
                for value in (
                    self.project_id,
                    self.project_boundary_sha256,
                    self.parent_proposal_sha256,
                    self.evidence_policy_sha256,
                )
            ):
                raise ValueError("frontier proposal cannot carry deepening authority")
            expected_bindings = FRONTIER_PHASE_HARD_BUDGETS
            expected_budget = FRONTIER_BUDGET
        elif self.approval_kind == "project_deepening":
            if not all(
                (
                    self.project_id,
                    self.project_boundary_sha256,
                    self.parent_proposal_sha256,
                    self.evidence_policy_sha256,
                )
            ):
                raise ValueError("deepening proposal requires project authority")
            expected_bindings = ((FrontierPhase.DEEPENING, DEEPENING_BUDGET),)
            expected_budget = DEEPENING_BUDGET
        else:
            raise ValueError("approval_kind must be frontier or project_deepening")
        phase_hard_budgets = tuple(
            (binding.phase, binding.hard_budget) for binding in self.phase_bindings
        )
        estimates_exceed_hard_budget = any(
            binding.estimated_units > binding.hard_budget.max_units
            or binding.estimated_input_bytes > binding.hard_budget.max_input_bytes
            or binding.estimated_wall_seconds > binding.hard_budget.max_wall_seconds
            for binding in self.phase_bindings
        )
        if (
            phase_hard_budgets != expected_bindings
            or estimates_exceed_hard_budget
            or self.budget != expected_budget
        ):
            raise ValueError("semantic proposal phase bindings do not match its hard budget")


@dataclass(frozen=True)
class SemanticProposalEnvelope:
    proposal: SemanticExecutionProposal
    proposal_sha256: str

    def __post_init__(self) -> None:
        if self.proposal_sha256 != semantic_proposal_sha256(self.proposal):
            raise ValueError("proposal_sha256 does not match the proposal")


@dataclass(frozen=True)
class SemanticRunAuthority:
    revision_id: str
    run_id: str
    source_approval_sha256: str
    source_policy_sha256: str
    manifest_sha256: str
    catalog_root: str
    proposal_sha256: str
    approval_receipt_sha256: str
    checkpoint_root: str
    unit_queue_root: str
    dependency_root: str
    budget_binding_sha256: str
    root: str


@dataclass(frozen=True)
class SemanticStatusSnapshot:
    revision_id: str
    run_id: str | None
    run_state: SemanticRunState
    completion: SemanticCompletion


@dataclass(frozen=True)
class SemanticRevisionBinding:
    """Private v3 ledger binding for one staging semantic revision."""

    revision_id: str
    manifest_sha256: str
    source_policy_sha256: str
    catalog_file_sha256: str
    catalog_logical_root: str
    lifecycle_schema_version: str
    lifecycle_genesis_root: str
    current_checkpoint_root: str
    source_approval_sha256: str


@dataclass(frozen=True)
class SemanticReceiptCommit:
    """The already-validated, pack-appended result committed by the ledger."""

    run_id: str
    unit_id: str
    lease_nonce: str
    input_sha256: str
    output_sha256: str
    result: PackRecordRef
    action: FrontierAction
    validator_version: str
    receipt_sha256: str
    active_seconds: float
    result_pack_length: int = 0
    result_pack_root: str = ""
    runtime_deferred: tuple[SemanticRuntimeDeferredRow, ...] = ()


@dataclass(frozen=True)
class SemanticCheckpoint:
    run_id: str
    sequence: int
    receipt_sequence: int
    result_pack_length: int
    result_pack_root: str
    queue_root: str
    aggregate_root: str
    resume_cursor_json: str
    selector_version: str
    previous_checkpoint_root: str
    checkpoint_root: str
    audit_ordered_set_sha256: str | None = None
    audit_population_sha256: str | None = None


@dataclass(frozen=True)
class SemanticAuditMember:
    ordinal: int
    stratum: str
    node_id: str
    node_digest: str
    audit_unit_id: str


@dataclass(frozen=True)
class SemanticAuditFreeze:
    """A caller-streamed audit population and its deterministically selected members."""

    run_id: str
    initial_deferred: tuple[SemanticDeferredAuditRow, ...]
    runtime_deferred: tuple[SemanticRuntimeDeferredRow, ...]
    members: tuple[SemanticAuditMember, ...]
    checkpoint: SemanticCheckpoint


def semantic_canonical_json_bytes(value: object) -> bytes:
    """Canonical bytes shared by proposal storage and cross-process approval."""

    try:
        return json.dumps(
            _canonical_value(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("semantic value cannot be canonically encoded") from error


def semantic_execution_proposal_from_payload(
    payload: object,
) -> SemanticExecutionProposal:
    """Rebuild the frozen authority only after its stored bytes are canonical."""

    if not isinstance(payload, dict):
        raise ValueError("semantic proposal payload is invalid")
    values = dict(payload)
    try:
        values["phase_bindings"] = tuple(
            SemanticPhaseBinding(
                FrontierPhase(item["phase"]),
                SemanticBudget(**item["hard_budget"]),
                item["estimated_units"],
                item["estimated_input_bytes"],
                item["estimated_wall_seconds"],
            )
            for item in values["phase_bindings"]
        )
        values["budget"] = SemanticBudget(**values["budget"])
        values["baseline_coverage_counts"] = tuple(
            tuple(item) for item in values["baseline_coverage_counts"]
        )
        return SemanticExecutionProposal(**values)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("semantic proposal payload is invalid") from error


def semantic_proposal_sha256(proposal: SemanticExecutionProposal) -> str:
    return hashlib.sha256(semantic_canonical_json_bytes(proposal)).hexdigest()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            key: _canonical_value(item)
            for key, item in asdict(value).items()
        }
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in value.items()}
    return value
