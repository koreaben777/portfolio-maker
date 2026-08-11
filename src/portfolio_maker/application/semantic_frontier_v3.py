from __future__ import annotations

import hashlib
import heapq
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from portfolio_maker.domain.semantic_frontier import (
    EXPANSION_BUDGET,
    FRONTIER_BUDGET,
    MAX_DEPENDENCIES,
    ORIENTATION_BUDGET,
    FrontierAction,
    FrontierPhase,
    SemanticDeferredAuditRow,
    SemanticFrontierPlan,
    SemanticPlannedUnit,
    SemanticRuntimeDeferredRow,
)
from portfolio_maker.infrastructure.semantic_catalog import (
    FrontierPlanningRow,
    FrontierSelectorResult,
    PlanningNodeProjection,
    SemanticCatalogReader,
    SemanticCatalogPlanningSession,
    canonical_json_text,
)


FRONTIER_SELECTOR_VERSION = "adaptive-frontier-v1"
TARGET_PROJECTION_CONTRACT = "semantic-frontier-projection-v1"
SEMANTIC_INPUT_SCHEMA_V3 = "semantic-frontier-input-v3"
SEMANTIC_OUTPUT_SCHEMA_V3 = "semantic-frontier-output-v3"
SEMANTIC_VALIDATOR_V3 = "semantic-frontier-validator-v1"
REPRESENTATIVE_ROLE_ORDER = (
    "project-description",
    "entrypoint",
    "package-manifest",
    "test",
    "deployment",
    "documentation",
    "data",
    "generated-output",
)


@dataclass
class RepresentativeSelectionObserver:
    max_python_rows: int = 0
    max_retained_candidates: int = 0


@dataclass(frozen=True)
class RepresentativeNode:
    node_id: str
    node_digest: str
    role_reason: str


@dataclass(frozen=True)
class FrontierTransition:
    action: FrontierAction
    accepted_node_ids: tuple[str, ...] = ()
    stopped_node_ids: tuple[str, ...] = ()
    deferred_node_ids: tuple[str, ...] = ()
    enqueued_child_node_ids: tuple[str, ...] = ()
    next_child_cursor: str | None = None
    paused_budget: bool = False
    converged: bool = False


class FrontierSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class AuditCheckpointBinding:
    run_id: str
    revision_id: str
    policy_sha256: str
    selector_version: str
    catalog_root: str
    approved_revision_id: str
    approved_policy_sha256: str
    approved_selector_version: str
    approved_catalog_root: str


@dataclass
class AuditSelectionObserver:
    max_relevance_candidates: int = 0
    max_random_candidates: int = 0
    max_coverage_stratum_candidates: int = 0
    max_pending_rows: int = 0
    population_count: int = 0


@dataclass(frozen=True)
class AuditMember:
    ordinal: int
    stratum: str
    node_id: str
    node_digest: str


@dataclass(frozen=True)
class AuditSample:
    members: tuple[AuditMember, ...]
    sample_digest: str
    population_digest: str
    selector_version: str


def build_frontier_plan(
    session: SemanticCatalogPlanningSession,
    revision_id: str,
    policy_hash: str,
) -> SemanticFrontierPlan:
    """Stream one deterministic bounded plan into pre-seal catalog authority."""

    _require_text(revision_id, "revision_id")
    _require_digest(policy_hash, "policy_hash")
    if session.selector_version != FRONTIER_SELECTOR_VERSION:
        raise ValueError("frontier selector version does not match")
    run_id = _stable_id(
        "run",
        revision_id,
        policy_hash,
        FRONTIER_SELECTOR_VERSION,
        session.selector_digest,
    )
    units: list[SemanticPlannedUnit] = []
    orientation_bytes = 0
    expansion_bytes = 0
    cursor_order = 0
    cursor: tuple[int, str] | None = None
    while True:
        page = session.fair_seed_page(cursor, 64)
        if not page:
            break
        for seed in page:
            selected = False
            reason = "single_signal_or_no_frontier_authority"
            estimated_bytes = 0
            eligible = _eligible(seed)
            closure = (
                session.ancestor_projection(seed.node_id, limit=MAX_DEPENDENCIES)
                if eligible
                else None
            )
            phase = (
                FrontierPhase.ORIENTATION
                if len(units) < ORIENTATION_BUDGET.max_units
                else FrontierPhase.EXPANSION
            )
            if eligible and closure is None:
                reason = "ancestor_closure_limit"
            elif eligible:
                estimated_bytes = _projection_size(closure)
                phase_units = sum(1 for unit in units if unit.phase is phase)
                phase_bytes = (
                    orientation_bytes
                    if phase is FrontierPhase.ORIENTATION
                    else expansion_bytes
                )
                hard_budget = (
                    ORIENTATION_BUDGET
                    if phase is FrontierPhase.ORIENTATION
                    else EXPANSION_BUDGET
                )
                within_budget = (
                    len(units) < FRONTIER_BUDGET.max_units
                    and phase_units < hard_budget.max_units
                    and estimated_bytes <= hard_budget.max_unit_input_bytes
                    and phase_bytes + estimated_bytes <= hard_budget.max_input_bytes
                    and len(closure) <= MAX_DEPENDENCIES
                )
                if within_budget:
                    target_ids = tuple(item.node_id for item in closure)
                    unit_id = _stable_id(
                        "unit",
                        run_id,
                        phase.value,
                        canonical_json_text(target_ids),
                        seed.feature_digest,
                    )
                    units.append(
                        SemanticPlannedUnit(
                            unit_id=unit_id,
                            phase=phase,
                            target_node_ids=target_ids,
                            dependency_unit_ids=(),
                            estimated_input_bytes=estimated_bytes,
                            priority=cursor_order,
                            fairness_lane=seed.top_level_lane,
                        )
                    )
                    selected = True
                    reason = "selected_frontier_seed"
                    if phase is FrontierPhase.ORIENTATION:
                        orientation_bytes += estimated_bytes
                    else:
                        expansion_bytes += estimated_bytes
                else:
                    reason = "frontier_budget_or_unit_limit"
            node_digest = _seed_digest(seed)
            cursor_json = canonical_json_text(
                {
                    "fair_round": seed.fair_round,
                    "lane": seed.top_level_lane,
                    "node_id": seed.node_id,
                    "order": cursor_order,
                    "selector_feature_digest": seed.feature_digest,
                }
            )
            result = FrontierSelectorResult(
                selected=selected,
                node_id=seed.node_id,
                reason=reason,
                node_digest=node_digest,
                cursor_json=cursor_json,
                cursor_order=cursor_order,
                source_id=seed.source_id,
                coverage_stratum=seed.coverage.value,
                relevance_rank=_relevance_rank(seed),
                selector_version=FRONTIER_SELECTOR_VERSION,
                selector_feature_digest=seed.feature_digest,
            )
            session.append_selector_result(result)
            if not selected:
                session.append_deferred(
                    SemanticDeferredAuditRow(
                        node_id=result.node_id,
                        reason=result.reason,
                        node_digest=result.node_digest,
                        cursor_json=result.cursor_json,
                        cursor_order=result.cursor_order,
                        source_id=result.source_id,
                        coverage_stratum=result.coverage_stratum,
                        relevance_rank=result.relevance_rank,
                        selector_version=result.selector_version,
                        selector_feature_digest=result.selector_feature_digest,
                    )
                )
            cursor_order += 1
        cursor = page[-1].cursor
    deferred = session.finish()
    return SemanticFrontierPlan(
        run_id=run_id,
        revision_id=revision_id,
        policy_sha256=policy_hash,
        selector_version=FRONTIER_SELECTOR_VERSION,
        target_projection_contract=TARGET_PROJECTION_CONTRACT,
        input_schema_version=SEMANTIC_INPUT_SCHEMA_V3,
        output_schema_version=SEMANTIC_OUTPUT_SCHEMA_V3,
        validator_version=SEMANTIC_VALIDATOR_V3,
        purpose="project_discovery",
        units=tuple(units),
        deferred_count=deferred.count,
        deferred_root=deferred.root,
        deferred_cursor_root=deferred.cursor_root,
    )


def representative_nodes(
    reader: SemanticCatalogReader,
    parent_node_id: str,
    *,
    observer: RepresentativeSelectionObserver | None = None,
) -> tuple[RepresentativeNode, ...]:
    """Keep one stable semantic winner per role while paging every child."""

    _require_text(parent_node_id, "parent_node_id")
    measurement = observer or RepresentativeSelectionObserver()
    winners: dict[str, tuple[tuple[object, ...], RepresentativeNode]] = {}
    cursor: str | None = None
    while True:
        page = reader.representative_page(parent_node_id, cursor, 64)
        measurement.max_python_rows = max(measurement.max_python_rows, len(page))
        if not page:
            break
        for candidate in page:
            coverage_change = candidate.coverage.value != "complete"
            for role in candidate.roles:
                if role not in REPRESENTATIVE_ROLE_ORDER:
                    continue
                digest = candidate.full_content_sha256 or candidate.sample_sha256
                if digest is None:
                    continue
                rank = (
                    -int(candidate.independent_child),
                    -int(candidate.uncertainty),
                    -int(candidate.evidence_linked),
                    -int(coverage_change),
                    candidate.node_id,
                )
                representative = RepresentativeNode(
                    candidate.node_id, digest, role
                )
                current = winners.get(role)
                if current is None or rank < current[0]:
                    winners[role] = (rank, representative)
            measurement.max_retained_candidates = max(
                measurement.max_retained_candidates, len(winners)
            )
        cursor = page[-1].node_id
    return tuple(
        winners[role][1]
        for role in REPRESENTATIVE_ROLE_ORDER
        if role in winners
    )


def transition_frontier(
    validated_output: object, *, budget_exhausted: bool
) -> FrontierTransition:
    """Apply one already-validated action without reading or mutating authority."""

    from portfolio_maker.application.semantic_quality_v3 import (
        ValidatedV3SemanticOutput,
    )

    if not isinstance(validated_output, ValidatedV3SemanticOutput):
        raise ValueError("frontier transition requires validated bounded output")
    action = getattr(validated_output, "action", None)
    targets = getattr(validated_output, "target_node_ids", None)
    requested = getattr(validated_output, "requested_child_node_ids", None)
    next_cursor = getattr(validated_output, "next_child_cursor", None)
    if (
        not isinstance(action, FrontierAction)
        or not isinstance(targets, tuple)
        or not isinstance(requested, tuple)
        or len(targets) > MAX_DEPENDENCIES
        or len(requested) > MAX_DEPENDENCIES
        or not isinstance(budget_exhausted, bool)
    ):
        raise ValueError("frontier transition requires validated bounded output")
    if action is FrontierAction.ACCEPT_BOUNDARY:
        return FrontierTransition(action, accepted_node_ids=targets)
    if action is FrontierAction.STOP_NON_PROJECT:
        return FrontierTransition(action, stopped_node_ids=targets)
    if action is FrontierAction.DEFER_ON_DEMAND:
        return FrontierTransition(action, deferred_node_ids=targets)
    if budget_exhausted:
        return FrontierTransition(
            action,
            deferred_node_ids=requested,
            next_child_cursor=next_cursor,
            paused_budget=True,
        )
    return FrontierTransition(
        action,
        enqueued_child_node_ids=requested,
        next_child_cursor=next_cursor,
    )


def freeze_audit_sample(
    *,
    catalog_deferred: Iterable[SemanticDeferredAuditRow],
    runtime_deferred: Iterable[
        SemanticRuntimeDeferredRow | SemanticDeferredAuditRow
    ],
    pending_runtime_deferred: Sequence[
        SemanticRuntimeDeferredRow | SemanticDeferredAuditRow
    ],
    checkpoint: AuditCheckpointBinding,
    observer: AuditSelectionObserver | None = None,
) -> AuditSample:
    """Freeze a checkpoint-bound 100/100/100 sample with bounded reservoirs."""

    _validate_audit_checkpoint(checkpoint)
    if len(pending_runtime_deferred) > MAX_DEPENDENCIES:
        raise FrontierSelectionError("audit pending runtime rows exceed the bound")
    measurement = observer or AuditSelectionObserver()
    measurement.max_pending_rows = len(pending_runtime_deferred)
    relevance: list[tuple[tuple[object, ...], SemanticDeferredAuditRow]] = []
    random_reservoir: list[
        tuple[tuple[object, ...], SemanticDeferredAuditRow]
    ] = []
    coverage: dict[
        str, list[tuple[tuple[object, ...], SemanticDeferredAuditRow]]
    ] = {name: [] for name in _AUDIT_COVERAGE_ORDER}
    population = bytes(32)
    streams = (
        _audit_stream("catalog", catalog_deferred, checkpoint),
        _audit_stream("runtime", runtime_deferred, checkpoint),
        _audit_stream("pending", iter(pending_runtime_deferred), checkpoint),
    )
    with _audit_seen_index() as seen:
        for _, set_kind, row in heapq.merge(*streams):
            seen(row)
            encoded = canonical_json_text(
                {"set_kind": set_kind, "row": row.__dict__}
            ).encode("utf-8")
            population = hashlib.sha256(
                b"semantic-v3-deferred-population\0" + population + encoded
            ).digest()
            _retain_smallest(
                relevance,
                ((row.relevance_rank, row.node_id), row),
                300,
            )
            random_key = hashlib.sha256(
                (
                    checkpoint.revision_id
                    + "\0"
                    + checkpoint.policy_sha256
                    + "\0"
                    + row.node_id
                ).encode("utf-8")
            ).hexdigest()
            _retain_smallest(
                random_reservoir,
                ((random_key, row.node_id), row),
                300,
            )
            coverage_key = hashlib.sha256(
                (
                    row.source_id
                    + "\0"
                    + row.coverage_stratum
                    + "\0"
                    + row.node_id
                ).encode("utf-8")
            ).hexdigest()
            bucket = coverage[row.coverage_stratum]
            _retain_smallest(bucket, ((coverage_key, row.node_id), row), 300)
            measurement.population_count += 1
            measurement.max_relevance_candidates = max(
                measurement.max_relevance_candidates, len(relevance)
            )
            measurement.max_random_candidates = max(
                measurement.max_random_candidates, len(random_reservoir)
            )
            measurement.max_coverage_stratum_candidates = max(
                measurement.max_coverage_stratum_candidates,
                max(len(items) for items in coverage.values()),
            )
    if measurement.population_count < 300:
        raise FrontierSelectionError("audit population cannot provide 300 unique members")
    chosen: set[str] = set()
    ordered: list[tuple[str, SemanticDeferredAuditRow]] = []
    _take_unique(
        "relevance",
        (row for _, row in sorted(relevance, key=lambda item: item[0])),
        100,
        chosen,
        ordered,
    )
    coverage_candidates = {
        name: [row for _, row in sorted(coverage[name], key=lambda item: item[0])]
        for name in _AUDIT_COVERAGE_ORDER
    }
    _take_coverage_unique(coverage_candidates, 100, chosen, ordered)
    _take_unique(
        "random",
        (row for _, row in sorted(random_reservoir, key=lambda item: item[0])),
        100,
        chosen,
        ordered,
    )
    if len(ordered) != 300:
        raise FrontierSelectionError("audit reservoirs cannot provide 300 unique members")
    members = tuple(
        AuditMember(index, stratum, row.node_id, row.node_digest)
        for index, (stratum, row) in enumerate(ordered)
    )
    population_digest = population.hex()
    sample_digest = hashlib.sha256(
        canonical_json_text(
            {
                "catalog_root": checkpoint.catalog_root,
                "members": [member.__dict__ for member in members],
                "policy_sha256": checkpoint.policy_sha256,
                "population_digest": population_digest,
                "revision_id": checkpoint.revision_id,
                "run_id": checkpoint.run_id,
                "selector_version": checkpoint.selector_version,
            }
        ).encode("utf-8")
    ).hexdigest()
    return AuditSample(
        members,
        sample_digest,
        population_digest,
        checkpoint.selector_version,
    )


_AUDIT_COVERAGE_ORDER = (
    "complete",
    "sampled",
    "unsupported",
    "unreadable",
    "failed",
)


def _audit_stream(
    set_kind: str,
    rows: Iterable[SemanticRuntimeDeferredRow | SemanticDeferredAuditRow],
    checkpoint: AuditCheckpointBinding,
) -> Iterator[tuple[tuple[object, ...], str, SemanticDeferredAuditRow]]:
    set_rank = {"catalog": 0, "runtime": 1, "pending": 2}[set_kind]
    previous_key: tuple[object, ...] | None = None
    for raw in rows:
        if isinstance(raw, SemanticRuntimeDeferredRow):
            row = raw.audit
            population_kind = canonical_json_text(
                {
                    "created_sequence": raw.created_sequence,
                    "set_kind": set_kind,
                    "source_transition_unit_id": raw.source_transition_unit_id,
                }
            )
        else:
            row = raw
            population_kind = set_kind
        if not isinstance(row, SemanticDeferredAuditRow):
            raise FrontierSelectionError("audit population row is invalid")
        _validate_audit_row(row, checkpoint)
        canonical_key = (
            row.relevance_rank,
            row.coverage_stratum,
            row.source_id,
            row.cursor_order,
            row.node_id,
        )
        if previous_key is not None and canonical_key <= previous_key:
            raise FrontierSelectionError("audit stream order is not canonical")
        previous_key = canonical_key
        yield (
            (*canonical_key, set_rank),
            population_kind,
            row,
        )


def _validate_audit_checkpoint(checkpoint: AuditCheckpointBinding) -> None:
    if not isinstance(checkpoint, AuditCheckpointBinding):
        raise FrontierSelectionError("audit checkpoint binding is invalid")
    for value, name in (
        (checkpoint.policy_sha256, "policy"),
        (checkpoint.catalog_root, "catalog"),
        (checkpoint.approved_policy_sha256, "approved policy"),
        (checkpoint.approved_catalog_root, "approved catalog"),
    ):
        try:
            _require_digest(value, name)
        except ValueError as error:
            raise FrontierSelectionError("audit checkpoint digest is invalid") from error
    if (
        checkpoint.revision_id != checkpoint.approved_revision_id
        or checkpoint.policy_sha256 != checkpoint.approved_policy_sha256
        or checkpoint.selector_version != checkpoint.approved_selector_version
        or checkpoint.catalog_root != checkpoint.approved_catalog_root
        or checkpoint.selector_version != FRONTIER_SELECTOR_VERSION
    ):
        raise FrontierSelectionError("audit checkpoint binding drift detected")


def _validate_audit_row(
    row: SemanticDeferredAuditRow, checkpoint: AuditCheckpointBinding
) -> None:
    if row.selector_version != checkpoint.selector_version:
        raise FrontierSelectionError("audit selector version does not match")
    try:
        _require_digest(row.node_digest, "audit node digest")
        _require_digest(row.selector_feature_digest, "audit feature digest")
    except ValueError as error:
        raise FrontierSelectionError("audit feature or node digest is invalid") from error
    if row.coverage_stratum not in _AUDIT_COVERAGE_ORDER:
        raise FrontierSelectionError("audit coverage stratum is invalid")
    try:
        cursor = json.loads(row.cursor_json, object_pairs_hook=_audit_no_duplicate_keys)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise FrontierSelectionError("audit cursor JSON is invalid") from error
    if canonical_json_text(cursor) != row.cursor_json:
        raise FrontierSelectionError("audit cursor JSON must be canonical")
    if (
        not isinstance(cursor, dict)
        or cursor.get("order") != row.cursor_order
        or cursor.get("node_id") != row.node_id
        or cursor.get("selector_feature_digest") != row.selector_feature_digest
    ):
        raise FrontierSelectionError("audit cursor feature binding is invalid")


def _audit_no_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FrontierSelectionError("audit cursor contains duplicate keys")
        result[key] = value
    return result


@contextmanager
def _audit_seen_index():  # type: ignore[no-untyped-def]
    connection = sqlite3.connect("")
    try:
        connection.execute("PRAGMA temp_store = FILE")
        if connection.execute("PRAGMA temp_store").fetchone()[0] != 1:
            raise FrontierSelectionError("audit duplicate index is not file-backed")
        connection.execute(
            """
            CREATE TABLE seen_audit_population(
                node_id TEXT PRIMARY KEY,
                cursor_order INTEGER NOT NULL UNIQUE
            )
            """
        )

        def add(row: SemanticDeferredAuditRow) -> None:
            try:
                connection.execute(
                    "INSERT INTO seen_audit_population(node_id, cursor_order) VALUES (?, ?)",
                    (row.node_id, row.cursor_order),
                )
            except sqlite3.IntegrityError as error:
                raise FrontierSelectionError(
                    "audit population contains a duplicate node or cursor order"
                ) from error

        yield add
    finally:
        connection.close()


def _retain_smallest(
    reservoir: list[tuple[tuple[object, ...], SemanticDeferredAuditRow]],
    candidate: tuple[tuple[object, ...], SemanticDeferredAuditRow],
    capacity: int,
) -> None:
    if len(reservoir) < capacity:
        reservoir.append(candidate)
        return
    worst_index = max(range(len(reservoir)), key=lambda index: reservoir[index][0])
    if candidate[0] < reservoir[worst_index][0]:
        reservoir[worst_index] = candidate


def _take_unique(
    stratum: str,
    candidates: Iterable[SemanticDeferredAuditRow],
    count: int,
    chosen: set[str],
    ordered: list[tuple[str, SemanticDeferredAuditRow]],
) -> None:
    added = 0
    for row in candidates:
        if row.node_id in chosen:
            continue
        chosen.add(row.node_id)
        ordered.append((stratum, row))
        added += 1
        if added == count:
            return


def _take_coverage_unique(
    candidates: dict[str, list[SemanticDeferredAuditRow]],
    count: int,
    chosen: set[str],
    ordered: list[tuple[str, SemanticDeferredAuditRow]],
) -> None:
    indexes = {name: 0 for name in _AUDIT_COVERAGE_ORDER}
    added = 0
    while added < count:
        progressed = False
        for name in _AUDIT_COVERAGE_ORDER:
            items = candidates[name]
            while indexes[name] < len(items):
                row = items[indexes[name]]
                indexes[name] += 1
                if row.node_id in chosen:
                    continue
                chosen.add(row.node_id)
                ordered.append(("coverage", row))
                added += 1
                progressed = True
                break
            if added == count:
                return
        if not progressed:
            return


def _eligible(seed: FrontierPlanningRow) -> bool:
    return any(
        (
            seed.project_like,
            seed.ambiguous,
            seed.independent_child,
            seed.evidence_linked,
            seed.uncertainty,
        )
    )


def _relevance_rank(seed: FrontierPlanningRow) -> int:
    return (
        (int(not seed.project_like) << 4)
        | (int(not seed.ambiguous) << 3)
        | (int(not seed.independent_child) << 2)
        | (int(not seed.evidence_linked) << 1)
        | int(not seed.uncertainty)
    )


def _projection_size(closure: tuple[PlanningNodeProjection, ...]) -> int:
    value = [
        {
            "coverage": None if item.coverage is None else item.coverage.value,
            "full_content_sha256": item.full_content_sha256,
            "node_id": item.node_id,
            "node_kind": item.node_kind,
            "parent_node_id": item.parent_node_id,
            "relative_hierarchy": item.relative_hierarchy,
            "roles": item.roles,
            "source_id": item.source_id,
            "topics": item.topics,
        }
        for item in closure
    ]
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _seed_digest(seed: FrontierPlanningRow) -> str:
    return hashlib.sha256(
        canonical_json_text(
            {
                "coverage": seed.coverage.value,
                "feature_digest": seed.feature_digest,
                "full_content_sha256": seed.full_content_sha256,
                "node_id": seed.node_id,
                "parent_node_id": seed.parent_node_id,
                "source_id": seed.source_id,
            }
        ).encode("utf-8")
    ).hexdigest()


def _stable_id(domain: str, *values: str) -> str:
    payload = "\0".join((domain, *values)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is invalid")


def _require_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name} is invalid")
    try:
        raw = bytes.fromhex(value)
    except ValueError as error:
        raise ValueError(f"{name} is invalid") from error
    if raw.hex() != value:
        raise ValueError(f"{name} is invalid")
