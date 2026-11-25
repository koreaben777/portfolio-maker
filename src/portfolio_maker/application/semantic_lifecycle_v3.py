from __future__ import annotations

from contextlib import contextmanager
import os
import hashlib
import json
import re
import stat
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

from portfolio_maker.application.approval import SourceApproval, load_approval
from portfolio_maker.application.models import (
    ApproveSemanticExecutionRequest,
    ApproveSemanticExecutionResult,
    NextSemanticWorkV3Request,
    NextSemanticWorkV3Result,
    PauseSemanticWorkRequest,
    ResumeSemanticWorkRequest,
    SemanticWorkLifecycleResult,
    SemanticIndexStatusRequest,
    SemanticIndexStatusV3Result,
    SemanticIndexStatusV3Request,
    ValidateSemanticWorkV3Request,
    ValidateSemanticWorkV3Result,
)
from portfolio_maker.application.semantic_frontier_v3 import (
    AuditCheckpointBinding,
    FrontierSelectionError,
    SEMANTIC_INPUT_SCHEMA_V3,
    freeze_audit_sample,
    transition_frontier_with_authority,
)
from portfolio_maker.application.semantic_quality_v3 import (
    SemanticQualityV3Error,
    validate_v3_semantic_output,
)
from portfolio_maker.application.semantic_prepare_v3 import _parse_canonical_v3_manifest, _authenticated_seal
from portfolio_maker.infrastructure.semantic_authenticated_catalog import AuthenticatedCatalogReader
from portfolio_maker.infrastructure.semantic_auth_index import MAX_VALUE_BYTES
from portfolio_maker.infrastructure.semantic_pack import _open_existing_file, PACK_MAGIC
from portfolio_maker.domain.semantic_frontier import (
    PackRecordRef,
    FrontierPhase,
    SemanticAuditMember,
    SemanticBudget,
    SemanticCompletion,
    SemanticExecutionProposal,
    SemanticReceiptCommit,
    SemanticRunState,
    SemanticRuntimeDeferredRow,
    SemanticPlannedUnit,
    semantic_canonical_json_bytes,
    semantic_proposal_sha256,
)
from portfolio_maker.domain.semantic_models import stable_source_id
from portfolio_maker.infrastructure.managed_files import read_managed_bytes, remove_managed_file
from portfolio_maker.infrastructure.policy import FilePolicy, mask_secrets
from portfolio_maker.infrastructure.semantic_catalog import CatalogSeal, SemanticCatalogReader
from portfolio_maker.infrastructure.semantic_exchange import (
    SemanticExchange,
    SemanticExchangeError,
)
from portfolio_maker.infrastructure.semantic_pack import (
    PMPackReader,
    PMPackWriter,
    PackSeal,
    recover_committed_prefix,
    verify_pack,
    verify_pack_prefix,
)
from portfolio_maker.infrastructure.semantic_source_reader import (
    SemanticSourceChangedError,
    SemanticSourceReadError,
    read_verified_representative,
)
from portfolio_maker.infrastructure.sqlite_repository import (
    RepositoryError,
    SQLiteRepository,
    SemanticV3ExecutionAuthority,
    SemanticV3ExternalAuthorityBinding,
    SemanticV3LeaseReservation,
)
from portfolio_maker.workspace import WorkspacePaths


class SemanticLifecycleV3Error(ValueError):
    pass


_REVISION_ID = re.compile(r"[0-9a-f]{32}")
_RUN_ID = re.compile(r"[0-9a-f]{32}(?:[0-9a-f]{32})?")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LOCATOR_TOKEN = re.compile(
    r"(?i)(?:file://|https?://|(?<![A-Za-z0-9_.-])(?:/|~[/\\\\]|[A-Za-z]:[/\\\\]|\\\\\\\\))[^\s'\"`<>)}\]]+"
)
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


def next_semantic_work_v3(
    request: NextSemanticWorkV3Request,
) -> NextSemanticWorkV3Result:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    try:
        repository.initialize()
        run_id = request.run_id or repository.select_runnable_semantic_v3_run()
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise SemanticLifecycleV3Error("semantic execution request is invalid")
        authority = repository.load_semantic_v3_execution_authority(run_id)
        input_path = paths.semantic_v3_exchange_input_path(
            authority.revision_id, authority.run_id
        )
        output_path = paths.semantic_v3_exchange_output_path(
            authority.revision_id, authority.run_id
        )
        exchange = SemanticExchange(input_path, output_path)
        reservation = repository.reserve_next_semantic_v3_unit(
            run_id,
            uuid4().hex,
            expected_authority_root=authority.proposal.authority_root,
        )
        if reservation is None:
            if input_path.exists() or output_path.exists():
                exchange.discard_slots()
            raise SemanticLifecycleV3Error("semantic execution is paused_budget")
        # The singleton DB claim precedes all managed/source reads. A losing
        # concurrent run therefore never opens authority, source, or exchange files.
        _validate_execution_authority(paths, authority)
        if reservation.issued:
            binding = exchange.replay_input(
                reservation.run_id,
                reservation.lease_nonce,
                _required_digest(reservation.input_sha256),
                _required_nonnegative_int(reservation.input_bytes),
            )
            return _next_result(reservation, binding)
        try:
            payload = _materialize_bounded_v3_input(paths, authority, reservation)
        except SemanticLifecycleV3Error as error:
            if "source authority" in str(error):
                raise SemanticLifecycleV3Error(
                    "semantic execution authority is stale"
                ) from error
            raise
        _validate_execution_authority(paths, authority)
        try:
            revalidated_payload = _materialize_bounded_v3_input(
                paths, authority, reservation
            )
        except SemanticLifecycleV3Error as error:
            if "source authority" in str(error):
                raise SemanticLifecycleV3Error(
                    "semantic execution authority is stale"
                ) from error
            raise
        if semantic_canonical_json_bytes(revalidated_payload) != semantic_canonical_json_bytes(
            payload
        ):
            raise SemanticLifecycleV3Error("semantic execution authority is stale")
        # Check expiry immediately before the first managed write. This check
        # intentionally does not charge time, so an oversized unpublished
        # canonical payload leaves no budget mutation behind.
        may_publish = repository.revalidate_semantic_v3_materialization_for_publication(
            reservation.run_id,
            reservation.lease_nonce,
            expected_authority_root=authority.proposal.authority_root,
        )
        if not may_publish:
            if input_path.exists() or output_path.exists():
                exchange.discard_slots()
            raise SemanticLifecycleV3Error("semantic execution is paused_budget")
        try:
            binding = exchange.publish_input(
                reservation.run_id, reservation.lease_nonce, payload
            )
        except SemanticExchangeError as error:
            if "byte budget" not in str(error):
                raise
            repository.abandon_semantic_v3_materialization_for_budget(
                reservation.run_id,
                reservation.lease_nonce,
                expected_authority_root=authority.proposal.authority_root,
            )
            if input_path.exists() or output_path.exists():
                exchange.discard_slots()
            raise SemanticLifecycleV3Error("semantic execution is paused_budget") from error
        issued = repository.issue_semantic_v3_input(
            reservation.run_id,
            reservation.lease_nonce,
            binding.sha256,
            binding.byte_count,
            validator_input_sha256=_required_digest(payload["input_sha256"]),
            expected_authority_root=authority.proposal.authority_root,
        )
        if not issued:
            exchange.discard_slots()
            raise SemanticLifecycleV3Error("semantic execution is paused_budget")
        return _next_result(reservation, binding)
    except SemanticLifecycleV3Error:
        raise
    except (
        OSError,
        RepositoryError,
        SemanticExchangeError,
        SemanticSourceChangedError,
        SemanticSourceReadError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        message = str(error)
        if "busy" in message:
            raise SemanticLifecycleV3Error("semantic execution is run_busy") from error
        if "paused_budget" in message:
            raise SemanticLifecycleV3Error("semantic execution is paused_budget") from error
        if "changed" in message or "stale" in message:
            raise SemanticLifecycleV3Error("semantic execution authority is stale") from error
        raise SemanticLifecycleV3Error("semantic execution is unavailable") from error


def validate_semantic_work_v3(
    request: ValidateSemanticWorkV3Request,
) -> ValidateSemanticWorkV3Result:
    if (
        not isinstance(request.run_id, str)
        or _RUN_ID.fullmatch(request.run_id) is None
        or not isinstance(request.unit_id, str)
        or not request.unit_id
        or not isinstance(request.lease_nonce, str)
        or _RUN_ID.fullmatch(request.lease_nonce) is None
    ):
        raise SemanticLifecycleV3Error("semantic validation request is invalid")
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    try:
        repository.initialize()
        authority = repository.load_semantic_v3_execution_authority(request.run_id)
        _validate_execution_authority(paths, authority)
        existing = repository.semantic_v3_receipt_for_unit(
            request.run_id, request.unit_id
        )
        result_path = paths.semantic_v3_result_pack_path(
            authority.revision_id, authority.run_id
        )
        output_path = paths.semantic_v3_exchange_output_path(
            authority.revision_id, authority.run_id
        )
        exchange = SemanticExchange(
            paths.semantic_v3_exchange_input_path(
                authority.revision_id, authority.run_id
            ),
            output_path,
        )
        if existing is not None:
            if (
                existing["lease_nonce"] != request.lease_nonce
                or existing["revision_id"] != authority.revision_id
            ):
                raise SemanticLifecycleV3Error("semantic receipt replay conflicts")
            _verify_result_pack_binding(repository, request.run_id, result_path)
            _verify_receipted_output(result_path, existing)
            if output_path.exists():
                if hashlib.sha256(exchange.read_output()).hexdigest() != existing["output_sha256"]:
                    raise SemanticLifecycleV3Error("semantic receipt replay conflicts")
            _recover_uncommitted_result_tail(repository, request.run_id, result_path)
            if output_path.exists():
                exchange.clear_output_if_matches(str(existing["output_sha256"]))
            return ValidateSemanticWorkV3Result(
                revision_id=authority.revision_id,
                run_id=request.run_id,
                unit_id=request.unit_id,
                receipt_sequence=int(existing["sequence"]),
                result_sha256=str(existing["result_sha256"]),
            )

        _recover_uncommitted_result_tail(repository, request.run_id, result_path)
        input_bytes = exchange.read_input()
        output_bytes = exchange.read_output()
        validated = validate_v3_semantic_output(input_bytes, output_bytes)
        whole_input_sha256, validator_input_sha256 = repository.semantic_v3_issued_input_binding(
            request.run_id, request.unit_id, request.lease_nonce,
        )
        if (
            hashlib.sha256(input_bytes).hexdigest() != whole_input_sha256
            or validated.input_sha256 != validator_input_sha256
        ):
            raise SemanticLifecycleV3Error("semantic validation binding is invalid")
        if (
            validated.revision_id != authority.revision_id
            or validated.run_id != request.run_id
            or validated.unit_id != request.unit_id
        ):
            raise SemanticLifecycleV3Error("semantic validation binding is invalid")
        transition, current_unit = _validated_frontier_transition(
            paths, authority, repository, request, validated,
        )
        audit_members, audit_units, audit_population_sha256 = (
            _final_expansion_audit_freeze(
                paths, authority, repository, transition, current_unit,
            )
        )
        # Re-read the sealed authority and bounded source projection immediately
        # before the first durable result-pack append.  The receipt transaction
        # independently rechecks the issued unit projection.
        _validate_execution_authority(paths, authority)
        current_reservation = repository.reserve_next_semantic_v3_unit(
            request.run_id,
            request.lease_nonce,
            expected_authority_root=authority.proposal.authority_root,
        )
        if current_reservation is None:
            exchange.discard_slots()
            raise SemanticLifecycleV3Error("semantic execution is paused_budget")
        if (
            not current_reservation.issued
            or current_reservation.unit_id != request.unit_id
        ):
            raise SemanticLifecycleV3Error("semantic execution authority is stale")
        if semantic_canonical_json_bytes(
            _materialize_bounded_v3_input(
                paths, authority, current_reservation,
            )
        ) != input_bytes:
            raise SemanticLifecycleV3Error("semantic execution authority is stale")
        output_payload = json.loads(output_bytes)
        # The quality envelope binds its own unsigned payload hash. The durable
        # lease/receipt binding is deliberately the whole canonical exchange
        # input, which is what the issued slot commits to on disk.
        input_sha256 = whole_input_sha256
        output_sha256 = hashlib.sha256(output_bytes).hexdigest()
        receipt_sha256 = hashlib.sha256(
            semantic_canonical_json_bytes(
                {
                    "input_sha256": input_sha256,
                    "lease_nonce": request.lease_nonce,
                    "output_sha256": output_sha256,
                    "run_id": request.run_id,
                    "unit_id": request.unit_id,
                    "validator_version": authority.proposal.validator_version,
                }
            )
        ).hexdigest()
        try:
            with PMPackWriter(result_path, seal_on_close=False) as writer:
                reference = writer.append(output_payload)
                writer.fsync()
                external_authority = _validate_execution_authority(paths, authority)
                seal = verify_pack_prefix(result_path, writer.byte_length)
            repository.commit_semantic_v3_receipt(
                SemanticReceiptCommit(
                    run_id=request.run_id,
                    unit_id=request.unit_id,
                    lease_nonce=request.lease_nonce,
                    input_sha256=input_sha256,
                    output_sha256=output_sha256,
                    result=reference,
                    action=validated.action,
                    validator_version=authority.proposal.validator_version,
                    receipt_sha256=receipt_sha256,
                    active_seconds=0.0,
                    result_pack_length=seal.byte_length,
                    result_pack_root=seal.record_root,
                    runtime_deferred=tuple(
                        SemanticRuntimeDeferredRow(
                            audit=row,
                            source_transition_unit_id=request.unit_id,
                            created_sequence=0,
                        )
                        for row in transition.pending_runtime_deferred
                    ),
                ),
                external_authority=external_authority,
                validator_input_sha256=validator_input_sha256,
                opened_units=(
                    tuple(item.unit for item in transition.opened_units)
                    + audit_units
                ),
                expected_unit=current_unit,
                audit_members=audit_members,
                audit_population_sha256=audit_population_sha256,
            )
        except Exception:
            _recover_uncommitted_result_tail(repository, request.run_id, result_path)
            raise
        committed = repository.semantic_v3_receipt_for_unit(
            request.run_id, request.unit_id
        )
        if committed is None:
            _recover_uncommitted_result_tail(repository, request.run_id, result_path)
            exchange.discard_slots()
            raise SemanticLifecycleV3Error("semantic receipt authority is invalid")
        exchange.clear_output_if_matches(output_sha256)
        return ValidateSemanticWorkV3Result(
            revision_id=authority.revision_id,
            run_id=request.run_id,
            unit_id=request.unit_id,
            receipt_sequence=int(committed["sequence"]),
            result_sha256=str(committed["result_sha256"]),
        )
    except SemanticLifecycleV3Error:
        raise
    except (
        OSError,
        RepositoryError,
        SemanticExchangeError,
        SemanticQualityV3Error,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        FrontierSelectionError,
    ) as error:
        raise SemanticLifecycleV3Error("semantic validation is unavailable") from error


def _validated_frontier_transition(
    paths: WorkspacePaths,
    authority: SemanticV3ExecutionAuthority,
    repository: SQLiteRepository,
    request: ValidateSemanticWorkV3Request,
    validated: object,
) -> tuple[object, object]:
    """Use only the sealed prerequisite projection for receipt-side expansion."""
    current_unit = repository.semantic_v3_issued_unit(
        request.run_id, request.unit_id, request.lease_nonce,
    )
    manifest = _parse_canonical_v3_manifest(
        read_managed_bytes(paths.semantic_v3_manifest_path(authority.revision_id))
    )
    catalog = manifest["catalog"]
    seal = CatalogSeal(
        byte_length=_required_nonnegative_int(catalog.get("byte_length")),
        file_sha256=_required_digest(catalog.get("file_sha256")),
        logical_root=_required_digest(catalog.get("logical_root")),
        node_count=_required_nonnegative_int(catalog.get("node_count")),
    )
    with _open_execution_catalog(paths, authority.proposal, manifest) as reader:
        transition = transition_frontier_with_authority(
            validated,
            run_id=request.run_id,
            current_unit=current_unit,
            reader=reader,
            selector_version=authority.proposal.selector_version,
            budget_exhausted=False,
        )
    return transition, current_unit


def _final_expansion_audit_freeze(
    paths: WorkspacePaths,
    authority: SemanticV3ExecutionAuthority,
    repository: SQLiteRepository,
    transition: object,
    current_unit: object,
) -> tuple[
    tuple[SemanticAuditMember, ...],
    tuple[SemanticPlannedUnit, ...],
    str | None,
]:
    """Build a bounded audit sample only when the sealed expansion closes."""
    if (
        not getattr(transition, "requires_expansion_queue_check", False)
        or not isinstance(current_unit, SemanticPlannedUnit)
        or current_unit.phase is not FrontierPhase.EXPANSION
        or getattr(transition, "opened_units", ())
        or repository.semantic_v3_has_remaining_expansion(
            authority.run_id, current_unit.unit_id,
        )
    ):
        return (), (), None
    manifest = _parse_canonical_v3_manifest(
        read_managed_bytes(paths.semantic_v3_manifest_path(authority.revision_id))
    )
    catalog = manifest["catalog"]
    seal = CatalogSeal(
        byte_length=_required_nonnegative_int(catalog.get("byte_length")),
        file_sha256=_required_digest(catalog.get("file_sha256")),
        logical_root=_required_digest(catalog.get("logical_root")),
        node_count=_required_nonnegative_int(catalog.get("node_count")),
    )
    pending = tuple(
        SemanticRuntimeDeferredRow(row, current_unit.unit_id, 0)
        for row in getattr(transition, "pending_runtime_deferred", ())
    )
    proposal = authority.proposal
    checkpoint = AuditCheckpointBinding(
        run_id=authority.run_id,
        revision_id=authority.revision_id,
        policy_sha256=proposal.policy_sha256,
        selector_version=proposal.selector_version,
        catalog_root=proposal.catalog_root,
        approved_revision_id=authority.revision_id,
        approved_policy_sha256=proposal.policy_sha256,
        approved_selector_version=proposal.selector_version,
        approved_catalog_root=proposal.catalog_root,
    )
    with _open_execution_catalog(paths, authority.proposal, manifest) as reader:
        sample = freeze_audit_sample(
            catalog_deferred=reader.iter_frontier_deferred_for_audit(
                selector_version=proposal.selector_version, fetch_size=64,
            ),
            runtime_deferred=repository.iter_semantic_v3_runtime_deferred(
                authority.run_id, fetch_size=64,
            ),
            pending_runtime_deferred=pending,
            checkpoint=checkpoint,
        )
    if len(sample.members) != 300:
        raise SemanticLifecycleV3Error("semantic audit authority is invalid")
    units: list[SemanticPlannedUnit] = []
    members: list[SemanticAuditMember] = []
    for group_start in range(0, len(sample.members), 5):
        group = sample.members[group_start:group_start + 5]
        unit_id = "audit-" + hashlib.sha256(
            semantic_canonical_json_bytes(
                {
                    "run_id": authority.run_id,
                    "nodes": tuple(member.node_id for member in group),
                }
            )
        ).hexdigest()[:24]
        units.append(
            SemanticPlannedUnit(
                unit_id=unit_id,
                phase=FrontierPhase.AUDIT,
                target_node_ids=tuple(member.node_id for member in group),
                dependency_unit_ids=(current_unit.unit_id,),
                estimated_input_bytes=max(1, current_unit.estimated_input_bytes),
                priority=group_start // 5,
                fairness_lane="audit",
            )
        )
        members.extend(
            SemanticAuditMember(
                ordinal=member.ordinal,
                stratum=member.stratum,
                node_id=member.node_id,
                node_digest=member.node_digest,
                audit_unit_id=unit_id,
            )
            for member in group
        )
    return tuple(members), tuple(units), sample.population_digest


def pause_semantic_work(request: PauseSemanticWorkRequest) -> SemanticWorkLifecycleResult:
    return _change_semantic_work_state(request.workspace, request.run_id, pause=True)


def resume_semantic_work(request: ResumeSemanticWorkRequest) -> SemanticWorkLifecycleResult:
    return _change_semantic_work_state(request.workspace, request.run_id, pause=False)


def _change_semantic_work_state(
    workspace: Path, run_id: str, *, pause: bool,
) -> SemanticWorkLifecycleResult:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise SemanticLifecycleV3Error("semantic lifecycle request is invalid")
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    try:
        repository.initialize()
        authority = repository.load_semantic_v3_execution_authority(run_id)
        _validate_execution_authority(paths, authority)
        result_path = paths.semantic_v3_result_pack_path(
            authority.revision_id, authority.run_id
        )
        if pause:
            _verify_result_pack_binding(repository, run_id, result_path)
            forced = repository.pause_semantic_v3_run(run_id, "user")
            if forced:
                SemanticExchange(
                    paths.semantic_v3_exchange_input_path(
                        authority.revision_id, authority.run_id,
                    ),
                    paths.semantic_v3_exchange_output_path(
                        authority.revision_id, authority.run_id,
                    ),
                ).discard_slots()
        else:
            _recover_uncommitted_result_tail(repository, run_id, result_path)
            recovered = repository.recover_semantic_v3_interrupted_lease(run_id)
            if recovered:
                SemanticExchange(
                    paths.semantic_v3_exchange_input_path(
                        authority.revision_id, authority.run_id,
                    ),
                    paths.semantic_v3_exchange_output_path(
                        authority.revision_id, authority.run_id,
                    ),
                ).discard_slots()
            _verify_result_pack_binding(repository, run_id, result_path)
            repository.resume_semantic_v3_run(run_id)
        state = repository.semantic_v3_status(run_id).run_state
        return SemanticWorkLifecycleResult(
            authority.revision_id, run_id, state,
        )
    except SemanticLifecycleV3Error:
        raise
    except (OSError, RepositoryError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise SemanticLifecycleV3Error("semantic lifecycle authority is invalid") from error


def _verify_result_pack_binding(
    repository: SQLiteRepository, run_id: str, result_path: Path,
) -> None:
    _revision_id, length, root = repository.semantic_v3_result_pack_binding(run_id)
    if length == 0:
        if result_path.exists():
            raise SemanticLifecycleV3Error("semantic result pack authority is invalid")
        return
    observed = verify_pack_prefix(result_path, length)
    if observed.record_root != root:
        raise SemanticLifecycleV3Error("semantic result pack authority is invalid")


def _recover_uncommitted_result_tail(
    repository: SQLiteRepository, run_id: str, result_path: Path,
) -> None:
    _revision_id, length, root = repository.semantic_v3_result_pack_binding(run_id)
    if length == 0:
        if result_path.exists():
            remove_managed_file(result_path)
        return
    recover_committed_prefix(result_path, length, root)


def _verify_receipted_output(result_path: Path, receipt: dict[str, object]) -> None:
    try:
        reference = PackRecordRef(
            offset=int(receipt["result_offset"]),
            length=int(receipt["result_length"]),
            sha256=str(receipt["result_sha256"]),
        )
        output = PMPackReader(result_path).read(reference)
        if (
            hashlib.sha256(semantic_canonical_json_bytes(output)).hexdigest()
            != receipt["result_sha256"]
            or receipt["output_sha256"] != receipt["result_sha256"]
        ):
            raise ValueError("result binding")
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise SemanticLifecycleV3Error("semantic receipt authority is invalid") from error


def _validate_approval_request(request: ApproveSemanticExecutionRequest) -> None:
    if (
        not isinstance(request.revision_id, str)
        or _REVISION_ID.fullmatch(request.revision_id) is None
        or not isinstance(request.proposal_sha256, str)
        or _SHA256.fullmatch(request.proposal_sha256) is None
    ):
        raise SemanticLifecycleV3Error("semantic approval authority is invalid")


def _validate_execution_authority(
    paths: WorkspacePaths,
    authority: SemanticV3ExecutionAuthority,
) -> SemanticV3ExternalAuthorityBinding:
    return _validate_reopened_authority(paths, authority.proposal)


@contextmanager
def _open_execution_catalog(paths, proposal, manifest):
    # Each consumer rechecks the binding on the exact manifest it just loaded.
    if manifest["manifest_sha256"] != proposal.manifest_sha256 or manifest["revision_id"] != proposal.revision_id:
        raise SemanticLifecycleV3Error("semantic approval authority is stale")
    revision = paths.semantic_revision_raw_dir(proposal.revision_id)
    if "authenticated_index" in manifest:
        reader = AuthenticatedCatalogReader(revision, _authenticated_seal(manifest))
    else:
        reader = SemanticCatalogReader(revision / "catalog.sqlite", CatalogSeal(**manifest["catalog"]))
    with reader:
        yield reader


def _verify_execution_pack(revision, manifest, seal):
    path = revision / "packs" / "structural.pm-pack"
    if "authenticated_index" not in manifest:
        verify_pack(path, seal)
        return
    descriptor = _open_existing_file(path, os.O_RDONLY)
    try:
        before = os.fstat(descriptor)
        if before.st_size != seal.byte_length or os.pread(descriptor, 8, 0) != PACK_MAGIC:
            raise SemanticLifecycleV3Error("semantic structural pack binding is invalid")
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise SemanticLifecycleV3Error("semantic structural pack changed")
    finally:
        os.close(descriptor)


def _materialize_bounded_v3_input(
    paths: WorkspacePaths,
    authority: SemanticV3ExecutionAuthority,
    reservation: SemanticV3LeaseReservation,
) -> dict[str, object]:
    target_node_ids = _parse_target_ref(
        reservation.target_ref_json, reservation.target_ref_sha256
    )
    dependencies = SQLiteRepository(paths.db_path).semantic_v3_unit_dependencies(
        reservation.run_id,
        reservation.unit_id,
        reservation.dependency_root,
    )
    proposal = authority.proposal
    manifest = _parse_canonical_v3_manifest(
        read_managed_bytes(paths.semantic_v3_manifest_path(proposal.revision_id))
    )
    catalog = manifest["catalog"]
    pack = manifest["pack"]
    catalog_seal = CatalogSeal(
        byte_length=_required_nonnegative_int(catalog.get("byte_length")),
        file_sha256=_required_digest(catalog.get("file_sha256")),
        logical_root=_required_digest(catalog.get("logical_root")),
        node_count=_required_nonnegative_int(catalog.get("node_count")),
    )
    pack_seal = PackSeal(
        byte_length=_required_nonnegative_int(pack.get("byte_length")),
        file_sha256=_required_digest(pack.get("file_sha256")),
        record_count=_required_nonnegative_int(pack.get("record_count")),
        record_root=_required_digest(pack.get("record_root")),
    )
    revision = paths.semantic_revision_raw_dir(proposal.revision_id)
    _verify_execution_pack(revision, manifest, pack_seal)
    approval = load_approval(paths)
    root = _approved_root_for_source(approval, proposal, manifest)
    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    pack_reader = PMPackReader(revision / "packs" / "structural.pm-pack")
    representatives: list[dict[str, object]] = []
    exposed_children: list[str] = []
    seen_representative_digests: set[str] = set()
    with _open_execution_catalog(paths, proposal, manifest) as reader:
        for node_id in target_node_ids:
            node = reader.node(node_id)
            if node is None or node.source_id != manifest["source_id"]:
                raise SemanticLifecycleV3Error("semantic execution authority is invalid")
            pending = [node]
            cursor = 0
            while cursor < len(pending) and len(pending) <= 64:
                candidate_node = pending[cursor]
                cursor += 1
                candidate_baseline = reader.baseline(candidate_node.node_id)
                if (
                    candidate_baseline is not None
                    and candidate_baseline.coverage.value == "complete"
                    and candidate_node.node_kind == "file"
                ):
                    _, content_sha256 = _read_verified_masked_excerpt(
                        root,
                        candidate_node.relative_hierarchy,
                        candidate_node.node_kind,
                        candidate_baseline.full_content_sha256,
                        candidate_baseline.sampled_ranges,
                        candidate_baseline.payload_ref,
                        pack_reader,
                        policy,
                    )
                    if content_sha256 not in seen_representative_digests:
                        representatives.append(
                            {
                                "node_id": candidate_node.node_id,
                                "full_content_sha256": content_sha256,
                                "coverage": "complete",
                                "role_reason": "verified-representative",
                            }
                        )
                        seen_representative_digests.add(content_sha256)
                children = reader.children_page(candidate_node.node_id, None, 64)
                if candidate_node.node_id == node_id:
                    for child in children:
                        if child.node_id not in exposed_children:
                            exposed_children.append(child.node_id)
                        if len(exposed_children) == 64:
                            break
                for child in children:
                    if len(pending) == 64:
                        break
                    pending.append(child)
                if len(representatives) == 64:
                    break
            if len(representatives) == 64:
                break
    if not representatives:
        raise SemanticLifecycleV3Error("semantic execution authority is invalid")
    signal_ids = [
        f"signal-{item['full_content_sha256']}" for item in representatives
    ]
    unsigned: dict[str, object] = {
        "schema_version": SEMANTIC_INPUT_SCHEMA_V3,
        "revision_id": reservation.revision_id,
        "run_id": reservation.run_id,
        "unit_id": reservation.unit_id,
        "target_node_ids": list(target_node_ids),
        "approved_local_signal_ids": signal_ids,
        "representatives": representatives,
        "exposed_child_node_ids": exposed_children,
        "next_child_cursor": None,
    }
    payload = {
        **unsigned,
        "input_sha256": hashlib.sha256(semantic_canonical_json_bytes(unsigned)).hexdigest(),
    }
    if _contains_unsafe_public_value(semantic_canonical_json_bytes(payload).decode("utf-8")):
        raise SemanticLifecycleV3Error("semantic execution input is unsafe")
    return payload


def _next_result(
    reservation: SemanticV3LeaseReservation,
    binding: object,
) -> NextSemanticWorkV3Result:
    input_path = getattr(binding, "input_path", None)
    input_sha256 = getattr(binding, "sha256", None)
    input_byte_count = getattr(binding, "byte_count", None)
    if (
        not isinstance(input_path, Path)
        or not isinstance(input_sha256, str)
        or type(input_byte_count) is not int
    ):
        raise SemanticLifecycleV3Error("semantic exchange binding is invalid")
    return NextSemanticWorkV3Result(
        revision_id=reservation.revision_id,
        run_id=reservation.run_id,
        unit_id=reservation.unit_id,
        lease_nonce=reservation.lease_nonce,
        input_path=input_path,
        input_sha256=input_sha256,
        input_byte_count=input_byte_count,
        target_node_ids=_parse_target_ref(
            reservation.target_ref_json, reservation.target_ref_sha256
        ),
    )


def _parse_target_ref(target_ref_json: str, target_ref_sha256: str) -> tuple[str, ...]:
    if (
        not isinstance(target_ref_json, str)
        or hashlib.sha256(target_ref_json.encode("utf-8")).hexdigest()
        != target_ref_sha256
    ):
        raise SemanticLifecycleV3Error("semantic target authority is invalid")
    try:
        targets = json.loads(target_ref_json, object_pairs_hook=_no_duplicates)
    except (json.JSONDecodeError, ValueError) as error:
        raise SemanticLifecycleV3Error("semantic target authority is invalid") from error
    if (
        type(targets) is not list
        or not 1 <= len(targets) <= 64
        or any(not isinstance(node_id, str) or not _SHA256.fullmatch(node_id.removeprefix("sha256:")) for node_id in targets)
        or len(set(targets)) != len(targets)
        or semantic_canonical_json_bytes(targets).decode("utf-8") != target_ref_json
    ):
        raise SemanticLifecycleV3Error("semantic target authority is invalid")
    return tuple(targets)


def _approved_root_for_source(
    approval: SourceApproval,
    proposal: SemanticExecutionProposal,
    manifest: dict[str, object],
) -> Path:
    source_id = manifest.get("source_id")
    if not isinstance(source_id, str):
        raise SemanticLifecycleV3Error("semantic source authority is invalid")
    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    for uri in approval.approved_source_uris:
        parsed = urlparse(uri)
        if parsed.scheme != "file" or parsed.netloc:
            continue
        root = Path(unquote(parsed.path))
        try:
            root_stat = root.lstat()
            if (
                not root.is_absolute()
                or root.as_uri() != uri
                or not stat.S_ISDIR(root_stat.st_mode)
                or stat.S_ISLNK(root_stat.st_mode)
                or policy.classify_path(root) != "candidate"
            ):
                continue
            root_key = (
                f"{root_stat.st_dev}:{root_stat.st_ino}"
                if type(root_stat.st_dev) is int
                and type(root_stat.st_ino) is int
                and root_stat.st_ino > 0
                else "."
            )
        except OSError:
            continue
        if stable_source_id("local", root_key) == source_id:
            return root
    raise SemanticLifecycleV3Error("semantic source authority is stale")


def _read_structural_record(
    pack_reader: PMPackReader,
    node_id: str,
    reference: object,
) -> dict[str, object]:
    if reference is None:
        raise SemanticLifecycleV3Error("semantic structural authority is invalid")
    value = pack_reader.read(reference, max_record_bytes=MAX_VALUE_BYTES)
    if (
        type(value) is not dict
        or value.get("kind") != "structural-node"
        or value.get("node_id") != node_id
        or not isinstance(value.get("node_kind"), str)
    ):
        raise SemanticLifecycleV3Error("semantic structural authority is invalid")
    return value


def _read_verified_masked_excerpt(
    root: Path,
    relative_hierarchy: str,
    node_kind: str,
    expected_full_sha256: str | None,
    ranges: tuple[tuple[int, int], ...],
    reference: object,
    pack_reader: PMPackReader,
    policy: FilePolicy,
) -> tuple[str, str]:
    payload = pack_reader.read(reference, max_record_bytes=MAX_VALUE_BYTES) if reference is not None else None
    if (
        type(payload) is not dict
        or payload.get("kind") != "local-baseline"
        or not isinstance(payload.get("masked_excerpt"), str)
    ):
        raise SemanticLifecycleV3Error("semantic baseline authority is invalid")
    if node_kind != "file":
        raise SemanticLifecycleV3Error("semantic source authority is invalid")
    if (
        expected_full_sha256 is not None
        and (
            not isinstance(expected_full_sha256, str)
            or _SHA256.fullmatch(expected_full_sha256) is None
        )
    ) or not _safe_relative_hierarchy(relative_hierarchy):
        raise SemanticLifecycleV3Error("semantic source authority is invalid")
    source_path = root.joinpath(*relative_hierarchy.split("/"))
    if policy.classify_path(source_path) != "candidate":
        raise SemanticLifecycleV3Error("semantic source authority is stale")
    expected_excerpt = _safe_excerpt(str(payload["masked_excerpt"]))
    read_ranges = ranges
    if not read_ranges:
        inspected = payload.get("inspected_bytes")
        if type(inspected) is not int or not 0 <= inspected <= 262_144:
            raise SemanticLifecycleV3Error("semantic baseline authority is invalid")
        read_ranges = ((0, inspected),)
    representative = read_verified_representative(
        source_path,
        read_ranges,
        262_144,
    )
    if expected_full_sha256 is not None and representative.full_content_sha256 != expected_full_sha256:
        raise SemanticLifecycleV3Error("semantic source authority is stale")
    if not representative.slices:
        raise SemanticLifecycleV3Error("semantic source authority is invalid")
    try:
        observed_excerpt = _safe_excerpt(representative.slices[0].decode("utf-8"))
    except UnicodeDecodeError as error:
        raise SemanticLifecycleV3Error("semantic source authority is stale") from error
    if observed_excerpt != expected_excerpt:
        raise SemanticLifecycleV3Error("semantic source authority is stale")
    return expected_excerpt, representative.full_content_sha256


def _safe_relative_hierarchy(value: object) -> bool:
    if not isinstance(value, str) or not value or value == ".":
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} and "\\" not in part for part in parts)


def _safe_excerpt(value: str) -> str:
    masked = mask_secrets(value)
    return "[REDACTED]" if _contains_unsafe_public_value(masked) else masked


def _contains_unsafe_public_value(value: str) -> bool:
    folded = value.casefold()
    return bool(
        _LOCATOR_TOKEN.search(value)
        or ".portfolio-maker" in folded
        or "portfolio.db" in folded
    )


def _required_digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise SemanticLifecycleV3Error("semantic exchange binding is invalid")
    return value


def _required_nonnegative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise SemanticLifecycleV3Error("semantic exchange binding is invalid")
    return value


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
    plan: dict[str, object] | None = None,
) -> SemanticV3ExternalAuthorityBinding:
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
        _verify_execution_pack(revision, manifest, pack_seal)
        with _open_execution_catalog(paths, proposal, manifest) as reader:
            deferred = reader.deferred_summary(selector_version=proposal.selector_version)
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise SemanticLifecycleV3Error("semantic approval authority is invalid") from error
    if (
        catalog_seal.logical_root != proposal.catalog_root
        or _authority_root(manifest, proposal) != proposal.authority_root
    ):
        raise SemanticLifecycleV3Error("semantic approval authority is stale")
    if plan is not None and (
        deferred.count != plan["deferred_count"]
        or deferred.root != plan["deferred_root"]
        or deferred.cursor_root != plan["deferred_cursor_root"]
    ):
        raise SemanticLifecycleV3Error("semantic approval authority is stale")
    return SemanticV3ExternalAuthorityBinding(
        revision_id=proposal.revision_id,
        run_id=proposal.run_id,
        proposal_sha256=semantic_proposal_sha256(proposal),
        manifest_sha256=str(manifest["manifest_sha256"]),
        source_policy_sha256=policy_sha256,
        source_approval_sha256=source_approval_sha256,
        catalog_file_sha256=catalog_seal.file_sha256,
        catalog_logical_root=catalog_seal.logical_root,
    )


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
