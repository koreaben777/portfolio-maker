from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
from typing import Any


SEMANTIC_PROTOCOL_V2 = "codex-semantic-v2"
SEMANTIC_PROTOCOL_V3 = "codex-semantic-v3"


class FrontierPhase(StrEnum):
    ORIENTATION = "orientation"
    EXPANSION = "expansion"
    AUDIT = "audit"
    DEEPENING = "deepening"


class SemanticRunState(StrEnum):
    PREPARED = "prepared"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class SemanticLeaseState(StrEnum):
    AVAILABLE = "available"
    CLAIMED = "claimed"
    EXPIRED = "expired"
    RELEASED = "released"


@dataclass(frozen=True)
class SemanticBudget:
    unit_limit: int
    byte_limit: int
    time_limit_seconds: int


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
    estimated_budget: SemanticBudget
    hard_budget: SemanticBudget


@dataclass(frozen=True)
class SemanticFrontierPlan:
    revision_id: str
    phase_bindings: tuple[SemanticPhaseBinding, ...]
    budget: SemanticBudget


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
        expected_bindings = (
            FRONTIER_PHASE_HARD_BUDGETS
            if self.project_id is None
            else ((FrontierPhase.DEEPENING, DEEPENING_BUDGET),)
        )
        phase_hard_budgets = tuple(
            (binding.phase, binding.hard_budget) for binding in self.phase_bindings
        )
        expected_budget = FRONTIER_BUDGET if self.project_id is None else DEEPENING_BUDGET
        if phase_hard_budgets != expected_bindings or self.budget != expected_budget:
            raise ValueError("semantic proposal phase bindings do not match its hard budget")


@dataclass(frozen=True)
class SemanticProposalEnvelope:
    proposal: SemanticExecutionProposal
    proposal_sha256: str


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


def semantic_proposal_sha256(proposal: SemanticExecutionProposal) -> str:
    canonical_json = json.dumps(
        _canonical_value(proposal),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


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
    return value
