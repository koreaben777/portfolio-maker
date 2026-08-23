from __future__ import annotations

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
from portfolio_maker.domain.semantic_models import stable_source_id
from portfolio_maker.infrastructure.managed_files import read_managed_bytes
from portfolio_maker.infrastructure.policy import FilePolicy, mask_secrets
from portfolio_maker.infrastructure.semantic_catalog import CatalogSeal, SemanticCatalogReader
from portfolio_maker.infrastructure.semantic_exchange import (
    SemanticExchange,
    SemanticExchangeError,
)
from portfolio_maker.infrastructure.semantic_pack import PMPackReader, PackSeal, verify_pack
from portfolio_maker.infrastructure.semantic_source_reader import (
    SemanticSourceChangedError,
    SemanticSourceReadError,
    read_verified_representative,
)
from portfolio_maker.infrastructure.sqlite_repository import (
    RepositoryError,
    SQLiteRepository,
    SemanticV3ExecutionAuthority,
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
        reservation = repository.reserve_next_semantic_v3_unit(
            run_id,
            uuid4().hex,
            expected_authority_root=authority.proposal.authority_root,
        )
        if reservation is None:
            raise SemanticLifecycleV3Error("semantic execution is paused_budget")
        # The singleton DB claim precedes all managed/source reads. A losing
        # concurrent run therefore never opens authority, source, or exchange files.
        _validate_execution_authority(paths, authority)
        exchange = SemanticExchange(
            paths.semantic_v3_exchange_input_path(
                reservation.revision_id, reservation.run_id
            ),
            paths.semantic_v3_exchange_output_path(
                reservation.revision_id, reservation.run_id
            ),
        )
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
        binding = exchange.publish_input(
            reservation.run_id, reservation.lease_nonce, payload
        )
        issued = repository.issue_semantic_v3_input(
            reservation.run_id,
            reservation.lease_nonce,
            binding.sha256,
            binding.byte_count,
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
) -> None:
    _validate_reopened_authority(paths, authority.proposal)


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
    verify_pack(revision / "packs" / "structural.pm-pack", pack_seal)
    approval = load_approval(paths)
    root = _approved_root_for_source(approval, proposal, manifest)
    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    pack_reader = PMPackReader(revision / "packs" / "structural.pm-pack")
    targets: list[dict[str, object]] = []
    with SemanticCatalogReader(revision / "catalog.sqlite", catalog_seal) as reader:
        for node_id in target_node_ids:
            node = reader.node(node_id)
            if node is None or node.source_id != manifest["source_id"]:
                raise SemanticLifecycleV3Error("semantic execution authority is invalid")
            structural = _read_structural_record(pack_reader, node_id, node.structural_ref)
            baseline = reader.baseline(node_id)
            excerpt = ""
            if baseline is not None:
                excerpt = _read_verified_masked_excerpt(
                    root,
                    node.relative_hierarchy,
                    node.node_kind,
                    baseline.full_content_sha256,
                    baseline.sampled_ranges,
                    baseline.payload_ref,
                    pack_reader,
                    policy,
                )
            targets.append(
                {
                    "coverage": baseline.coverage.value if baseline is not None else None,
                    "kind": structural["node_kind"],
                    "node_id": node_id,
                    "roles": list(baseline.roles) if baseline is not None else [],
                    "summary": excerpt,
                    "topics": list(baseline.topics) if baseline is not None else [],
                }
            )
    payload: dict[str, object] = {
        "dependencies": [
            {"digest": digest, "unit_id": dependency_id}
            for dependency_id, digest in dependencies
        ],
        "lease_nonce": reservation.lease_nonce,
        "protocol": "codex-semantic-v3",
        "revision_id": reservation.revision_id,
        "run_id": reservation.run_id,
        "targets": targets,
        "unit_id": reservation.unit_id,
    }
    # Keep the public handoff free of private locators even when a historical
    # baseline predates the current redaction boundary.
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
    value = pack_reader.read(reference)
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
) -> str:
    payload = pack_reader.read(reference) if reference is not None else None
    if (
        type(payload) is not dict
        or payload.get("kind") != "local-baseline"
        or not isinstance(payload.get("masked_excerpt"), str)
    ):
        raise SemanticLifecycleV3Error("semantic baseline authority is invalid")
    if node_kind != "file":
        return _safe_excerpt(str(payload["masked_excerpt"]))
    if (
        not isinstance(expected_full_sha256, str)
        or _SHA256.fullmatch(expected_full_sha256) is None
        or not _safe_relative_hierarchy(relative_hierarchy)
    ):
        raise SemanticLifecycleV3Error("semantic source authority is invalid")
    source_path = root.joinpath(*relative_hierarchy.split("/"))
    if policy.classify_path(source_path) != "candidate":
        raise SemanticLifecycleV3Error("semantic source authority is stale")
    representative = read_verified_representative(
        source_path,
        ranges,
        262_144,
    )
    if representative.full_content_sha256 != expected_full_sha256:
        raise SemanticLifecycleV3Error("semantic source authority is stale")
    return _safe_excerpt(str(payload["masked_excerpt"]))


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
        or _authority_root(manifest, proposal) != proposal.authority_root
    ):
        raise SemanticLifecycleV3Error("semantic approval authority is stale")
    if plan is not None and (
        deferred.count != plan["deferred_count"]
        or deferred.root != plan["deferred_root"]
        or deferred.cursor_root != plan["deferred_cursor_root"]
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
