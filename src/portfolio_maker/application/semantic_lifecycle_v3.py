from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from portfolio_maker.application.approval import SourceApproval, load_approval
from portfolio_maker.application.models import (
    ApproveSemanticExecutionRequest,
    ApproveSemanticExecutionResult,
    SemanticIndexStatusRequest,
    SemanticIndexStatusV3Result,
    SemanticIndexStatusV3Request,
)
from portfolio_maker.application.semantic_prepare_v3 import _parse_canonical_v3_manifest
from portfolio_maker.domain.semantic_frontier import (
    SemanticBudget,
    SemanticCompletion,
    SemanticExecutionProposal,
    SemanticRunState,
    semantic_canonical_json_bytes,
)
from portfolio_maker.infrastructure.managed_files import read_managed_bytes
from portfolio_maker.infrastructure.semantic_catalog import CatalogSeal, SemanticCatalogReader
from portfolio_maker.infrastructure.semantic_pack import PackSeal, verify_pack
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError, SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


class SemanticLifecycleV3Error(ValueError):
    pass


_REVISION_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PLAN_KEYS = frozenset(
    {
        "deferred_count",
        "deferred_cursor_root",
        "deferred_root",
        "input_schema_version",
        "output_schema_version",
        "policy_sha256",
        "purpose",
        "revision_id",
        "run_id",
        "selector_version",
        "target_projection_contract",
        "units",
        "validator_version",
    }
)


def approve_semantic_execution(
    request: ApproveSemanticExecutionRequest,
) -> ApproveSemanticExecutionResult:
    _validate_approval_request(request)
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    try:
        repository.initialize()
        envelope, proposal_bytes, plan_bytes = repository.get_semantic_v3_proposal_by_digest(
            request.revision_id,
            request.proposal_sha256,
        )
        proposal = envelope.proposal
        if (
            proposal.revision_id != request.revision_id
            or envelope.proposal_sha256 != request.proposal_sha256
            or proposal_bytes != semantic_canonical_json_bytes(proposal)
            or hashlib.sha256(plan_bytes).hexdigest() != proposal.plan_sha256
        ):
            raise SemanticLifecycleV3Error("semantic approval authority is invalid")
        plan = _parse_canonical_plan(plan_bytes, proposal)
        _validate_reopened_authority(paths, proposal, plan)
        state = repository.approve_semantic_v3_run(
            proposal.run_id,
            envelope.proposal_sha256,
        )
    except SemanticLifecycleV3Error:
        raise
    except (OSError, RepositoryError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise SemanticLifecycleV3Error("semantic approval authority is invalid") from error
    if state is not SemanticRunState.PREPARED:
        raise SemanticLifecycleV3Error("semantic approval authority is invalid")
    return ApproveSemanticExecutionResult(
        revision_id=proposal.revision_id,
        run_id=proposal.run_id,
        proposal_sha256=envelope.proposal_sha256,
        run_state=state,
    )


def semantic_index_status_v3(
    request: SemanticIndexStatusV3Request,
) -> SemanticIndexStatusV3Result:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    try:
        snapshot = repository.semantic_v3_aggregate_status(request.run_id)
        completion = SemanticCompletion(**snapshot["completion"])
        total_budget = SemanticBudget(**snapshot["total_budget"])
        used_units = snapshot["used_units"]
        used_input_bytes = snapshot["used_input_bytes"]
        used_active_seconds = snapshot["used_active_seconds"]
        remaining_budget = SemanticBudget(
            max(0, total_budget.max_units - used_units),
            max(0, total_budget.max_input_bytes - used_input_bytes),
            max(0, total_budget.max_wall_seconds - used_active_seconds),
            total_budget.max_unit_input_bytes,
            total_budget.max_unit_output_bytes,
            total_budget.max_unit_wall_seconds,
            total_budget.checkpoint_units,
        )
        return SemanticIndexStatusV3Result(
            revision_id=snapshot["revision_id"],
            run_id=snapshot["run_id"],
            run_state=SemanticRunState(snapshot["run_state"]),
            completion=completion,
            phase_usage=tuple(sorted(snapshot["phase_usage"].items())),
            ready_count=snapshot["ready_count"],
            leased_count=snapshot["leased_count"],
            validated_count=snapshot["validated_count"],
            receipted_count=snapshot["receipted_count"],
            deferred_count=snapshot["deferred_count"],
            error_count=snapshot["error_count"],
            last_checkpoint_root=snapshot["last_checkpoint_root"],
            last_verified_at=snapshot["last_verified_at"],
            remaining_budget=remaining_budget,
            resumable=SemanticRunState(snapshot["run_state"])
            in {
                SemanticRunState.AWAITING_APPROVAL,
                SemanticRunState.PREPARED,
                SemanticRunState.PAUSED_USER,
                SemanticRunState.PAUSED_BUDGET,
            },
        )
    except (OSError, RepositoryError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SemanticLifecycleV3Error("semantic v3 status authority is invalid") from error


def _validate_approval_request(request: ApproveSemanticExecutionRequest) -> None:
    if (
        not isinstance(request.revision_id, str)
        or _REVISION_ID.fullmatch(request.revision_id) is None
        or not isinstance(request.proposal_sha256, str)
        or _SHA256.fullmatch(request.proposal_sha256) is None
    ):
        raise SemanticLifecycleV3Error("semantic approval authority is invalid")


def _parse_canonical_plan(
    plan_bytes: bytes,
    proposal: SemanticExecutionProposal,
) -> dict[str, object]:
    try:
        plan = json.loads(plan_bytes.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SemanticLifecycleV3Error("semantic approval authority is invalid") from error
    if (
        type(plan) is not dict
        or set(plan) != _PLAN_KEYS
        or semantic_canonical_json_bytes(plan) != plan_bytes
        or plan.get("run_id") != proposal.run_id
        or plan.get("revision_id") != proposal.revision_id
        or plan.get("policy_sha256") != proposal.policy_sha256
        or plan.get("selector_version") != proposal.selector_version
        or plan.get("target_projection_contract") != proposal.target_projection_contract
        or plan.get("input_schema_version") != proposal.input_schema_version
        or plan.get("output_schema_version") != proposal.output_schema_version
        or plan.get("validator_version") != proposal.validator_version
        or plan.get("purpose") != proposal.purpose
        or type(plan.get("units")) is not list
        or not _is_nonnegative_int(plan.get("deferred_count"))
        or not all(
            isinstance(plan.get(field), str) and _SHA256.fullmatch(plan[field])
            for field in ("deferred_root", "deferred_cursor_root")
        )
    ):
        raise SemanticLifecycleV3Error("semantic approval authority is invalid")
    return plan


def _validate_reopened_authority(
    paths: WorkspacePaths,
    proposal: SemanticExecutionProposal,
    plan: dict[str, object],
) -> None:
    revision = paths.semantic_revision_raw_dir(proposal.revision_id)
    manifest = _parse_canonical_v3_manifest(
        read_managed_bytes(revision / "manifest.v3.json")
    )
    approval = load_approval(paths)
    policy_sha256 = _policy_sha256(approval)
    source_approval_sha256 = _approval_sha256(approval)
    if (
        manifest["revision_id"] != proposal.revision_id
        or manifest["manifest_sha256"] != proposal.manifest_sha256
        or manifest["source_policy_sha256"] != proposal.policy_sha256
        or manifest["source_approval_sha256"] != proposal.source_approval_sha256
        or policy_sha256 != proposal.policy_sha256
        or source_approval_sha256 != proposal.source_approval_sha256
        or manifest["frontier_plan_sha256"] != proposal.plan_sha256
        or manifest["private_context_scope"] != proposal.private_context_scope
        or manifest["safe_source_label"] != proposal.safe_source_label
        or manifest["lifecycle_genesis_root"] != proposal.parent_checkpoint_root
    ):
        raise SemanticLifecycleV3Error("semantic approval authority is stale")
    catalog = manifest["catalog"]
    pack = manifest["pack"]
    try:
        catalog_seal = CatalogSeal(
            byte_length=catalog["byte_length"],
            file_sha256=catalog["file_sha256"],
            logical_root=catalog["logical_root"],
            node_count=catalog["node_count"],
        )
        pack_seal = PackSeal(
            byte_length=pack["byte_length"],
            file_sha256=pack["file_sha256"],
            record_count=pack["record_count"],
            record_root=pack["record_root"],
        )
        verify_pack(revision / "packs" / "structural.pm-pack", pack_seal)
        with SemanticCatalogReader(revision / "catalog.sqlite", catalog_seal) as reader:
            deferred = reader.deferred_summary(selector_version=proposal.selector_version)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise SemanticLifecycleV3Error("semantic approval authority is invalid") from error
    if (
        catalog_seal.logical_root != proposal.catalog_root
        or deferred.count != plan["deferred_count"]
        or deferred.root != plan["deferred_root"]
        or deferred.cursor_root != plan["deferred_cursor_root"]
        or _authority_root(manifest, proposal) != proposal.authority_root
    ):
        raise SemanticLifecycleV3Error("semantic approval authority is stale")


def _authority_root(
    manifest: dict[str, object],
    proposal: SemanticExecutionProposal,
) -> str:
    values = {
        "baseline_coverage_counts": manifest["baseline_coverage_counts"],
        "catalog_root": proposal.catalog_root,
        "lifecycle_genesis_root": proposal.parent_checkpoint_root,
        "manifest_sha256": proposal.manifest_sha256,
        "private_context_scope": proposal.private_context_scope,
        "revision_id": proposal.revision_id,
        "safe_source_label": proposal.safe_source_label,
        "selector_version": proposal.selector_version,
        "source_approval_sha256": proposal.source_approval_sha256,
        "source_policy_sha256": proposal.policy_sha256,
        "structural_node_count": proposal.structural_node_count,
        "target_projection_contract": proposal.target_projection_contract,
        "input_schema_version": proposal.input_schema_version,
        "output_schema_version": proposal.output_schema_version,
        "validator_version": proposal.validator_version,
    }
    return hashlib.sha256(semantic_canonical_json_bytes(values)).hexdigest()


def _policy_sha256(approval: SourceApproval) -> str:
    return _sha256(
        {
            "allowed_repositories": sorted(approval.allowed_repositories),
            "approved_github_activity_urls": sorted(approval.approved_github_activity_urls),
            "approved_private_github_activity_urls": sorted(approval.approved_private_github_activity_urls),
            "approved_source_uris": sorted(approval.approved_source_uris),
            "excluded_directories": sorted(str(path) for path in approval.excluded_directories),
            "excluded_file_patterns": sorted(approval.excluded_file_patterns),
            "excluded_repositories": sorted(approval.excluded_repositories),
            "private_sources_allowed": approval.private_sources_allowed,
        }
    )


def _approval_sha256(approval: SourceApproval) -> str:
    return _sha256({"approved_source_uris": sorted(approval.approved_source_uris)})


def _sha256(value: object) -> str:
    return hashlib.sha256(semantic_canonical_json_bytes(value)).hexdigest()


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0
