from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from portfolio_maker.domain.history_models import CleanupExecutionState
from portfolio_maker.domain.semantic_frontier import (
    SEMANTIC_PROTOCOL_V2,
    SEMANTIC_PROTOCOL_V3,
    SemanticBudget,
    SemanticCompletion,
    SemanticPhaseBinding,
    SemanticRunState,
)


ArtifactKind = Literal[
    "master_profile",
    "portfolio_draft",
    "portfolio_public_manifest",
    "portfolio_html",
]
ArtifactDeliveryScope = Literal["restricted", "open_public"]


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    message: str
    count: int | None = None


@dataclass(frozen=True)
class DiscoverSourcesRequest:
    workspace: Path
    home: Path
    include_github: bool = True
    forbidden_paths: tuple[Path, ...] = ()
    excluded_directories: tuple[Path, ...] = ()


@dataclass(frozen=True)
class DiscoverSourcesResult:
    report_path: Path
    discovered_count: int
    skipped_count: int
    events: tuple[ProgressEvent, ...] = ()


@dataclass(frozen=True)
class IngestSourcesRequest:
    workspace: Path


@dataclass(frozen=True)
class IngestSourcesResult:
    ingested_count: int
    skipped_count: int
    snapshot_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class BuildProfileRequest:
    workspace: Path
    invalidate_portfolio_draft: bool = True
    write_artifacts: bool = True


@dataclass(frozen=True)
class BuildProfileResult:
    json_path: Path
    markdown_path: Path
    claim_count: int
    claim_ids: tuple[int, ...] = ()
    evidence_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class DraftPortfolioRequest:
    workspace: Path


@dataclass(frozen=True)
class DraftPortfolioResult:
    markdown_path: Path
    project_count: int


@dataclass(frozen=True)
class PublicPortfolioRequest:
    workspace: Path


@dataclass(frozen=True)
class PublicPortfolioResult:
    manifest_path: Path
    project_count: int
    claim_count: int
    evidence_count: int
    claim_ids: tuple[int, ...] = ()
    evidence_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class RenderHtmlRequest:
    workspace: Path


@dataclass(frozen=True)
class RenderHtmlResult:
    manifest_path: Path
    html_path: Path


@dataclass(frozen=True)
class PrepareProjectReviewRequest:
    workspace: Path


@dataclass(frozen=True)
class PrepareProjectReviewResult:
    input_path: Path
    evidence_count: int
    chunk_count: int = 1


@dataclass(frozen=True)
class ComposeProjectsRequest:
    workspace: Path


@dataclass(frozen=True)
class ComposeProjectsResult:
    project_count: int
    unassigned_evidence_count: int


@dataclass(frozen=True)
class ComposeProjectsV2Request:
    workspace: Path
    mode: Literal["review", "automatic"] = "review"
    manual_include_ids: tuple[str, ...] = ()
    manual_exclude_ids: tuple[str, ...] = ()
    manual_review_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComposeProjectsV2Result:
    project_count: int
    review_required_count: int
    excluded_project_count: int


@dataclass(frozen=True)
class PrepareSemanticIndexRequest:
    workspace: Path
    root: Path
    protocol: str = SEMANTIC_PROTOCOL_V3
    chunk_size: int = 100
    directory_timeout_seconds: float = 30.0
    directory_timeout_budget_seconds: float = 120.0


@dataclass(frozen=True)
class PrepareSemanticIndexResult:
    manifest_path: Path
    revision_id: str
    node_count: int
    chunk_count: int
    partial_count: int
    work_unit_count: int
    managed_input_bytes: int
    analysis_target_count: int
    structural_only_count: int
    directory_timeout_seconds: float = 30.0
    directory_timeout_budget_seconds: float = 120.0
    timed_out_directory_count: int = 0
    unreadable_directory_count: int = 0
    protocol: str = SEMANTIC_PROTOCOL_V2
    run_id: str | None = None
    proposal_sha256: str | None = None
    structural_node_count: int = 0
    baseline_coverage_counts: tuple[tuple[str, int], ...] = ()
    phase_bindings: tuple[SemanticPhaseBinding, ...] = ()


@dataclass(frozen=True)
class ApplySemanticIndexRequest:
    workspace: Path


@dataclass(frozen=True)
class ApplySemanticIndexResult:
    revision_id: str
    active: bool
    complete_count: int
    partial_count: int
    failed_count: int


@dataclass(frozen=True)
class SemanticIndexStatusRequest:
    workspace: Path


@dataclass(frozen=True)
class SemanticIndexStatusV3Request:
    workspace: Path
    run_id: str | None = None


@dataclass(frozen=True)
class SemanticIndexStatusResult:
    revision_id: str
    node_count: int
    work_unit_count: int
    ready_count: int
    receipted_count: int
    invalid_count: int
    complete: bool
    protocol: str = SEMANTIC_PROTOCOL_V2


@dataclass(frozen=True)
class SemanticIndexStatusV3Result:
    revision_id: str
    run_id: str
    run_state: SemanticRunState
    completion: SemanticCompletion
    phase_usage: tuple[tuple[str, int], ...]
    ready_count: int
    leased_count: int
    validated_count: int
    receipted_count: int
    deferred_count: int
    error_count: int
    last_checkpoint_root: str
    last_verified_at: str | None
    remaining_budget: SemanticBudget
    resumable: bool
    integrity_level: str = "aggregate_bound"
    protocol: str = SEMANTIC_PROTOCOL_V3


@dataclass(frozen=True)
class ApproveSemanticExecutionRequest:
    workspace: Path
    revision_id: str
    proposal_sha256: str


@dataclass(frozen=True)
class ApproveSemanticExecutionResult:
    revision_id: str
    run_id: str
    proposal_sha256: str
    run_state: SemanticRunState


@dataclass(frozen=True)
class NextSemanticWorkRequest:
    workspace: Path


@dataclass(frozen=True)
class NextSemanticWorkResult:
    revision_id: str
    unit_id: str | None
    input_path: Path | None
    remaining_count: int
    protocol: str = SEMANTIC_PROTOCOL_V2


@dataclass(frozen=True)
class ValidateSemanticWorkRequest:
    workspace: Path
    unit_id: str


@dataclass(frozen=True)
class ValidateSemanticWorkResult:
    unit_id: str
    receipt_path: Path
    output_sha256: str
    protocol: str = SEMANTIC_PROTOCOL_V2


@dataclass(frozen=True)
class FinalizeSemanticIndexRequest:
    workspace: Path


@dataclass(frozen=True)
class FinalizeSemanticIndexResult:
    revision_id: str
    manifest_path: Path
    chunk_count: int
    node_count: int
    protocol: str = SEMANTIC_PROTOCOL_V2


@dataclass(frozen=True)
class HistoryPlanRequest:
    workspace: Path


@dataclass(frozen=True)
class HistoryPlanResult:
    plan_id: str | None
    candidate_count: int
    total_bytes: int
    kinds: tuple[str, ...]
    reasons: tuple[str, ...]
    report_path: Path = field(repr=False, compare=False)


@dataclass(frozen=True)
class HistoryExportRequest:
    workspace: Path
    plan_id: str
    backup_dir: Path


@dataclass(frozen=True)
class HistoryApplyRequest:
    workspace: Path
    plan_id: str
    export_receipt_hash: str | None
    confirmation: str


@dataclass(frozen=True)
class HistoryExportResult:
    plan_id: str
    receipt_hash: str
    archive_path: Path = field(repr=False, compare=False)


@dataclass(frozen=True)
class HistoryExecutionRequest:
    workspace: Path
    execution_id: str


@dataclass(frozen=True)
class HistoryApplyResult:
    execution_id: str
    state: CleanupExecutionState
    affected_count: int


@dataclass(frozen=True)
class PrepareVisionAnalysisRequest:
    workspace: Path
    source_id: int
    confirm_codex_share: bool


@dataclass(frozen=True)
class ApplyVisionAnalysisRequest:
    workspace: Path
    result_path: Path


@dataclass(frozen=True)
class SetVisionAnalysisStateRequest:
    workspace: Path
    analysis_id: str
    state: str
