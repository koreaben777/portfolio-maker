from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import stat
import sys
import time
from pathlib import Path
from uuid import uuid4

from portfolio_maker.application.approval import SourceApproval, load_approval
from portfolio_maker.application.models import (
    PrepareSemanticIndexRequest,
    PrepareSemanticIndexResult,
)
from portfolio_maker.application.semantic_frontier_v3 import (
    FRONTIER_SELECTOR_VERSION,
    build_frontier_plan,
)
from portfolio_maker.domain.semantic_frontier import (
    FRONTIER_BUDGET,
    FRONTIER_PHASE_HARD_BUDGETS,
    BaselineCoverage,
    SealedProposalAuthority,
    SemanticExecutionProposal,
    SemanticPhaseBinding,
    SemanticProposalEnvelope,
    SemanticRevisionBinding,
    semantic_canonical_json_bytes,
    semantic_proposal_sha256,
)
from portfolio_maker.infrastructure.managed_files import (
    ensure_managed_directory,
    read_managed_bytes,
    write_managed_text,
)
from portfolio_maker.infrastructure.policy import FilePolicy
from portfolio_maker.infrastructure.presentation import normalize_label
from portfolio_maker.infrastructure.semantic_crawler import (
    CrawlObserver,
    StructuralEventKind,
    StreamingStructureSink,
    iter_local_structure_events,
)
from portfolio_maker.infrastructure.semantic_catalog import CatalogSeal, SemanticCatalogReader
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError, SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


class SemanticPrepareV3Error(ValueError):
    pass


_MAX_MANAGED_PREPARE_BYTES = 3 * 1024**3
_MAX_PREPARE_WALL_SECONDS = 3_600.0
_MAX_PREPARE_RSS_BYTES = 4 * 1024**3


def prepare_semantic_index_v3(
    request: PrepareSemanticIndexRequest,
) -> PrepareSemanticIndexResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    started_at = time.monotonic()
    approval = load_approval(paths)
    root = _approved_root(request.root, approval)
    policy_sha256 = _policy_sha256(approval)
    source_approval_sha256 = _approval_sha256(approval)
    revision_id = uuid4().hex
    temporary = paths.semantic_revision_raw_dir(f".{revision_id}.prepare")
    final = paths.semantic_revision_raw_dir(revision_id)
    if final.exists():
        raise SemanticPrepareV3Error("semantic v3 staging target is unavailable")

    ensure_managed_directory(temporary / "packs")
    sink = StreamingStructureSink(
        temporary / "packs" / "structural.pm-pack",
        temporary / "catalog.sqlite",
    )
    observer = CrawlObserver(collect_errors=False)
    coverage_counts = {coverage.value: 0 for coverage in BaselineCoverage}
    node_count = 0
    source_id: str | None = None
    published = False
    try:
        for event in iter_local_structure_events(
            root,
            approval,
            directory_timeout_seconds=request.directory_timeout_seconds,
            directory_timeout_budget_seconds=request.directory_timeout_budget_seconds,
            observer=observer,
            sink=sink,
        ):
            if source_id is None:
                source_id = event.node.source_id
            if event.kind is StructuralEventKind.NODE_SEEN:
                node_count += 1
            elif event.kind is StructuralEventKind.FILE_ANALYZED and event.baseline is not None:
                coverage_counts[event.baseline.coverage.value] += 1
        if source_id is None or node_count == 0:
            raise SemanticPrepareV3Error("semantic v3 structure is unavailable")

        plan = build_frontier_plan(
            sink.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id,
            policy_sha256,
        )
        artifacts = sink.seal()
        resource_observation = _assert_prepare_resources(temporary, started_at)
        _assert_current_approval(paths, root, approval, policy_sha256, source_approval_sha256)
        plan_bytes = semantic_canonical_json_bytes(plan)
        plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()
        lifecycle_genesis_root = _sha256(
            {
                "catalog_root": artifacts.catalog_seal.logical_root,
                "plan_sha256": plan_sha256,
                "revision_id": revision_id,
                "source_approval_sha256": source_approval_sha256,
                "source_policy_sha256": policy_sha256,
            }
        )
        safe_source_label = normalize_label(root.name)
        private_context_scope = "approved_local_source"
        manifest_without_hash = {
            "baseline_coverage_counts": sorted(coverage_counts.items()),
            "catalog": {
                "byte_length": artifacts.catalog_seal.byte_length,
                "file_sha256": artifacts.catalog_seal.file_sha256,
                "logical_root": artifacts.catalog_seal.logical_root,
                "node_count": artifacts.catalog_seal.node_count,
            },
            "frontier_plan_sha256": plan_sha256,
            "lifecycle_genesis_root": lifecycle_genesis_root,
            "pack": {
                "byte_length": artifacts.pack_seal.byte_length,
                "file_sha256": artifacts.pack_seal.file_sha256,
                "record_count": artifacts.pack_seal.record_count,
                "record_root": artifacts.pack_seal.record_root,
            },
            "private_context_scope": private_context_scope,
            "protocol": "codex-semantic-v3",
            "resource_observation": resource_observation,
            "revision_id": revision_id,
            "safe_source_label": safe_source_label,
            "schema_version": "codex-semantic-v3",
            "source_approval_sha256": source_approval_sha256,
            "source_id": source_id,
            "source_policy_sha256": policy_sha256,
            "structural_node_count": node_count,
        }
        manifest_sha256 = _sha256(manifest_without_hash)
        manifest = {**manifest_without_hash, "manifest_sha256": manifest_sha256}
        manifest_text = _canonical_text(manifest)
        write_managed_text(temporary / "manifest.v3.json", manifest_text, overwrite=False)
        _publish_revision_directory(temporary, final)
        published = True

        authority = _reopen_sealed_authority(
            paths=paths,
            revision_directory=final,
            root=root,
            initial_approval=approval,
            policy_sha256=policy_sha256,
            source_approval_sha256=source_approval_sha256,
            plan=plan,
            lifecycle_genesis_root=lifecycle_genesis_root,
        )
        phase_bindings = _phase_bindings(plan.units)
        proposal = SemanticExecutionProposal(
            run_id=plan.run_id,
            revision_id=revision_id,
            approval_kind="frontier",
            source_approval_sha256=source_approval_sha256,
            manifest_sha256=authority.manifest_sha256,
            policy_sha256=policy_sha256,
            selector_version=plan.selector_version,
            catalog_root=authority.catalog_root,
            safe_source_label=authority.safe_source_label,
            private_context_scope=authority.private_context_scope,
            authority_root=authority.root,
            plan_sha256=plan_sha256,
            parent_checkpoint_root=lifecycle_genesis_root,
            target_projection_contract=plan.target_projection_contract,
            input_schema_version=plan.input_schema_version,
            output_schema_version=plan.output_schema_version,
            validator_version=plan.validator_version,
            purpose=plan.purpose,
            structural_node_count=authority.structural_node_count,
            baseline_coverage_counts=authority.baseline_coverage_counts,
            phase_bindings=phase_bindings,
            audit_selector_rule="adaptive-frontier-audit-v1",
            deferred_semantics="catalog_deferred_preserved",
            budget=FRONTIER_BUDGET,
        )
        proposal_bytes = semantic_canonical_json_bytes(proposal)
        envelope = SemanticProposalEnvelope(proposal, semantic_proposal_sha256(proposal))
        binding = SemanticRevisionBinding(
            revision_id=revision_id,
            manifest_sha256=authority.manifest_sha256,
            source_policy_sha256=policy_sha256,
            catalog_file_sha256=artifacts.catalog_seal.file_sha256,
            catalog_logical_root=authority.catalog_root,
            lifecycle_schema_version="semantic-v3-lifecycle-v1",
            lifecycle_genesis_root=lifecycle_genesis_root,
            current_checkpoint_root=lifecycle_genesis_root,
            source_approval_sha256=source_approval_sha256,
        )
        repository = SQLiteRepository(paths.db_path)
        repository.initialize()
        repository.bind_semantic_v3_revision_genesis_proposal_and_units(
            revision_id=revision_id,
            source_id=source_id,
            binding=binding,
            envelope=envelope,
            proposal_bytes=proposal_bytes,
            plan_bytes=plan_bytes,
            units=plan.units,
            deferred_count=plan.deferred_count,
            deferred_root=plan.deferred_root,
            deferred_cursor_root=plan.deferred_cursor_root,
        )
    except (OSError, RepositoryError, ValueError) as error:
        sink.abort()
        if not published:
            _remove_owned_temporary_staging(temporary, paths.raw_dir / "semantic-index")
        raise SemanticPrepareV3Error("semantic v3 preparation failed safely") from error

    return PrepareSemanticIndexResult(
        manifest_path=final / "manifest.v3.json",
        revision_id=revision_id,
        node_count=node_count,
        chunk_count=0,
        partial_count=coverage_counts[BaselineCoverage.SAMPLED.value],
        work_unit_count=len(plan.units),
        managed_input_bytes=0,
        analysis_target_count=len(plan.units),
        structural_only_count=max(0, node_count - len(plan.units)),
        directory_timeout_seconds=request.directory_timeout_seconds,
        directory_timeout_budget_seconds=request.directory_timeout_budget_seconds,
        timed_out_directory_count=observer.timed_out_directory_count,
        unreadable_directory_count=observer.unreadable_directory_count,
        protocol="codex-semantic-v3",
        run_id=plan.run_id,
        proposal_sha256=envelope.proposal_sha256,
        structural_node_count=node_count,
        baseline_coverage_counts=tuple(sorted(coverage_counts.items())),
        phase_bindings=phase_bindings,
    )


def _approved_root(root: Path, approval: SourceApproval) -> Path:
    try:
        candidate = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise SemanticPrepareV3Error("semantic v3 root is invalid") from error
    if not candidate.is_dir() or candidate.as_uri() not in approval.approved_source_uris:
        raise SemanticPrepareV3Error("semantic v3 root is not approved")
    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    if policy.classify_path(candidate) != "candidate":
        raise SemanticPrepareV3Error("semantic v3 root is not approved")
    return candidate


def _assert_current_approval(
    paths: WorkspacePaths,
    root: Path,
    initial_approval: SourceApproval,
    policy_sha256: str,
    source_approval_sha256: str,
) -> None:
    current = load_approval(paths)
    if (
        current != initial_approval
        or _policy_sha256(current) != policy_sha256
        or _approval_sha256(current) != source_approval_sha256
    ):
        raise SemanticPrepareV3Error("semantic v3 approval changed during preparation")
    _approved_root(root, current)


def _reopen_sealed_authority(
    *,
    paths: WorkspacePaths,
    revision_directory: Path,
    root: Path,
    initial_approval: SourceApproval,
    policy_sha256: str,
    source_approval_sha256: str,
    plan: object,
    lifecycle_genesis_root: str,
) -> SealedProposalAuthority:
    _assert_current_approval(
        paths,
        root,
        initial_approval,
        policy_sha256,
        source_approval_sha256,
    )
    try:
        manifest = json.loads(read_managed_bytes(revision_directory / "manifest.v3.json"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SemanticPrepareV3Error("semantic v3 sealed authority is invalid") from error
    if not isinstance(manifest, dict):
        raise SemanticPrepareV3Error("semantic v3 sealed authority is invalid")
    stored_hash = manifest.pop("manifest_sha256", None)
    if not isinstance(stored_hash, str) or _sha256(manifest) != stored_hash:
        raise SemanticPrepareV3Error("semantic v3 sealed authority is invalid")
    catalog = manifest.get("catalog")
    coverage_counts = manifest.get("baseline_coverage_counts")
    if (
        not isinstance(catalog, dict)
        or not isinstance(coverage_counts, list)
        or manifest.get("protocol") != "codex-semantic-v3"
        or manifest.get("schema_version") != "codex-semantic-v3"
        or manifest.get("revision_id") != revision_directory.name
        or manifest.get("source_approval_sha256") != source_approval_sha256
        or manifest.get("source_policy_sha256") != policy_sha256
        or manifest.get("frontier_plan_sha256")
        != hashlib.sha256(semantic_canonical_json_bytes(plan)).hexdigest()
        or manifest.get("lifecycle_genesis_root") != lifecycle_genesis_root
        or not isinstance(manifest.get("safe_source_label"), str)
        or not isinstance(manifest.get("private_context_scope"), str)
        or not isinstance(manifest.get("structural_node_count"), int)
    ):
        raise SemanticPrepareV3Error("semantic v3 sealed authority is invalid")
    try:
        seal = CatalogSeal(
            byte_length=int(catalog["byte_length"]),
            file_sha256=str(catalog["file_sha256"]),
            logical_root=str(catalog["logical_root"]),
            node_count=int(catalog["node_count"]),
        )
        with SemanticCatalogReader(revision_directory / "catalog.sqlite", seal) as reader:
            deferred = reader.deferred_summary(
                selector_version=getattr(plan, "selector_version")
            )
        normalized_coverage = tuple(
            (str(name), int(count)) for name, count in coverage_counts
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SemanticPrepareV3Error("semantic v3 sealed authority is invalid") from error
    if (
        len(normalized_coverage) != len(BaselineCoverage)
        or {name for name, _ in normalized_coverage}
        != {coverage.value for coverage in BaselineCoverage}
        or deferred.count != getattr(plan, "deferred_count")
        or deferred.root != getattr(plan, "deferred_root")
        or deferred.cursor_root != getattr(plan, "deferred_cursor_root")
    ):
        raise SemanticPrepareV3Error("semantic v3 sealed authority is invalid")
    authority_without_root = {
        "baseline_coverage_counts": list(normalized_coverage),
        "catalog_root": seal.logical_root,
        "lifecycle_genesis_root": lifecycle_genesis_root,
        "manifest_sha256": stored_hash,
        "private_context_scope": manifest["private_context_scope"],
        "revision_id": revision_directory.name,
        "safe_source_label": manifest["safe_source_label"],
        "selector_version": getattr(plan, "selector_version", None),
        "source_approval_sha256": source_approval_sha256,
        "source_policy_sha256": policy_sha256,
        "structural_node_count": manifest["structural_node_count"],
        "target_projection_contract": getattr(plan, "target_projection_contract", None),
        "input_schema_version": getattr(plan, "input_schema_version", None),
        "output_schema_version": getattr(plan, "output_schema_version", None),
        "validator_version": getattr(plan, "validator_version", None),
    }
    return SealedProposalAuthority(
        revision_id=revision_directory.name,
        source_approval_sha256=source_approval_sha256,
        source_policy_sha256=policy_sha256,
        manifest_sha256=stored_hash,
        catalog_root=seal.logical_root,
        safe_source_label=manifest["safe_source_label"],
        private_context_scope=manifest["private_context_scope"],
        structural_node_count=manifest["structural_node_count"],
        baseline_coverage_counts=normalized_coverage,
        selector_version=getattr(plan, "selector_version"),
        lifecycle_genesis_root=lifecycle_genesis_root,
        target_projection_contract=getattr(plan, "target_projection_contract"),
        input_schema_version=getattr(plan, "input_schema_version"),
        output_schema_version=getattr(plan, "output_schema_version"),
        validator_version=getattr(plan, "validator_version"),
        root=_sha256(authority_without_root),
    )


def _policy_sha256(approval: SourceApproval) -> str:
    return _sha256(
        {
            "allowed_repositories": sorted(approval.allowed_repositories),
            "approved_github_activity_urls": sorted(approval.approved_github_activity_urls),
            "approved_private_github_activity_urls": sorted(
                approval.approved_private_github_activity_urls
            ),
            "approved_source_uris": sorted(approval.approved_source_uris),
            "excluded_directories": sorted(str(path) for path in approval.excluded_directories),
            "excluded_file_patterns": sorted(approval.excluded_file_patterns),
            "excluded_repositories": sorted(approval.excluded_repositories),
            "private_sources_allowed": approval.private_sources_allowed,
        }
    )


def _approval_sha256(approval: SourceApproval) -> str:
    return _sha256({"approved_source_uris": sorted(approval.approved_source_uris)})


def _phase_bindings(units: tuple[object, ...]) -> tuple[SemanticPhaseBinding, ...]:
    bindings: list[SemanticPhaseBinding] = []
    for phase, budget in FRONTIER_PHASE_HARD_BUDGETS:
        phase_units = tuple(unit for unit in units if getattr(unit, "phase", None) is phase)
        bindings.append(
            SemanticPhaseBinding(
                phase=phase,
                hard_budget=budget,
                estimated_units=len(phase_units),
                estimated_input_bytes=sum(
                    int(getattr(unit, "estimated_input_bytes", 0)) for unit in phase_units
                ),
                estimated_wall_seconds=min(
                    budget.max_wall_seconds,
                    len(phase_units)
                    * max(1, budget.max_wall_seconds // max(1, budget.max_units)),
                ),
            )
        )
    return tuple(bindings)


def _canonical_text(value: object) -> str:
    return semantic_canonical_json_bytes(value).decode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(semantic_canonical_json_bytes(value)).hexdigest()


def _managed_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _assert_prepare_resources(root: Path, started_at: float) -> dict[str, int | float]:
    observation = _resource_observation(root, started_at)
    if observation["managed_bytes"] > _MAX_MANAGED_PREPARE_BYTES:
        raise SemanticPrepareV3Error("semantic v3 resource gate exceeded")
    if observation["elapsed_seconds"] > _MAX_PREPARE_WALL_SECONDS:
        raise SemanticPrepareV3Error("semantic v3 resource gate exceeded")
    if observation["peak_rss_bytes"] > observation["max_peak_rss_bytes"]:
        raise SemanticPrepareV3Error("semantic v3 resource gate exceeded")
    return observation


def _resource_observation(root: Path, started_at: float) -> dict[str, int | float]:
    physical_memory = _physical_memory_bytes()
    max_peak_rss = _MAX_PREPARE_RSS_BYTES
    if physical_memory is not None:
        max_peak_rss = min(max_peak_rss, physical_memory // 4)
    return {
        "elapsed_seconds": round(max(0.0, time.monotonic() - started_at), 6),
        "managed_bytes": _managed_size(root),
        "max_peak_rss_bytes": max_peak_rss,
        "peak_rss_bytes": _peak_rss_bytes(),
    }


def _physical_memory_bytes() -> int | None:
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None
    value = pages * page_size
    return value if value > 0 else None


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _publish_revision_directory(temporary: Path, final: Path) -> None:
    try:
        os.replace(temporary, final)
        descriptor = os.open(final.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise SemanticPrepareV3Error("semantic v3 staging publication failed") from error


def _remove_owned_temporary_staging(temporary: Path, revision_parent: Path) -> None:
    try:
        try:
            metadata = os.lstat(temporary)
        except FileNotFoundError:
            return
        if temporary.parent != revision_parent or not temporary.name.endswith(".prepare"):
            raise OSError("semantic v3 staging ownership is invalid")
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError("semantic v3 staging ownership is invalid")
        shutil.rmtree(temporary)
    except OSError as error:
        raise SemanticPrepareV3Error("semantic v3 staging cleanup failed") from error
