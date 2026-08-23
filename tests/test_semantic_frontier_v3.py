from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from portfolio_maker.application.semantic_frontier_v3 import (
    AuditCheckpointBinding,
    AuditSelectionObserver,
    FRONTIER_SELECTOR_VERSION,
    FrontierSelectionError,
    RepresentativeSelectionObserver,
    build_frontier_plan,
    freeze_audit_sample,
    representative_nodes,
    transition_frontier,
    transition_frontier_with_authority,
)
from portfolio_maker.application.semantic_quality_v3 import ValidatedV3SemanticOutput
from portfolio_maker.domain.semantic_frontier import (
    BaselineCoverage,
    FrontierAction,
    MAX_UNIT_BYTES,
    FrontierPhase,
    SemanticPlannedUnit,
    SemanticDeferredAuditRow,
    SemanticRuntimeDeferredRow,
)
from portfolio_maker.infrastructure.semantic_catalog import (
    BaselineRecord,
    CatalogError,
    CatalogSeal,
    DeferredSummary,
    FrontierFeatureRecord,
    FrontierPlanningRow,
    FrontierSelectorResult,
    NodeRecord,
    PlanningNodeProjection,
    PlanningQueryObserver,
    RuntimeDeferredProjectionObserver,
    SemanticCatalogPlanningSession,
    SemanticCatalogReader,
    SemanticCatalogWriter,
    _file_sha256,
    _logical_root,
    canonical_json_text,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _add_feature(
    writer: SemanticCatalogWriter,
    node_id: str,
    *,
    parent_node_id: str | None = None,
    source_id: str,
    lane: str,
    project_like: bool = True,
    ambiguous: bool = False,
    independent_child: bool = False,
    evidence_linked: bool = False,
    uncertainty: bool = False,
    roles: tuple[str, ...] = ("documentation", "entrypoint"),
    feature_digest: str | None = None,
) -> None:
    writer.add_node(
        NodeRecord(node_id, parent_node_id, "directory", node_id, source_id)
    )
    writer.add_baseline(
        BaselineRecord(
            node_id,
            BaselineCoverage.COMPLETE,
            roles,
            (),
            128,
            None,
            full_content_sha256=_digest(f"content:{node_id}"),
        )
    )
    writer.add_frontier_feature(
        FrontierFeatureRecord(
            node_id,
            "frontier-features-v1",
            project_like,
            ambiguous,
            independent_child,
            evidence_linked,
            uncertainty,
            lane,
            feature_digest or _digest(f"feature:{node_id}"),
        )
    )


def _build_three_lane_plan(path: Path):
    with SemanticCatalogWriter(path) as writer:
        for lane in ("source-a", "source-b", "source-c"):
            _add_feature(
                writer,
                f"{lane}/root",
                source_id=lane,
                lane=lane,
            )
        session = writer.planning_session(FRONTIER_SELECTOR_VERSION)
        plan = build_frontier_plan(
            session,
            revision_id="revision-1",
            policy_hash="a" * 64,
        )
        seal = writer.seal()
    return plan, seal


def _build_deferred_catalog(path: Path, count: int = 3):
    with SemanticCatalogWriter(path) as writer:
        for index in range(count):
            node_id = f"deferred-{index:03}"
            _add_feature(
                writer,
                node_id,
                source_id=f"source-{index % 2}",
                lane=f"lane-{index % 2}",
                project_like=False,
            )
        plan = build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-deferred",
            policy_hash="a" * 64,
        )
        seal = writer.seal()
    return plan, seal


def _rehashed_catalog_seal(path: Path) -> CatalogSeal:
    with sqlite3.connect(path) as connection:
        return CatalogSeal(
            byte_length=path.stat().st_size,
            file_sha256=_file_sha256(path),
            logical_root=_logical_root(connection),
            node_count=int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]),
        )


def _validated_output(
    action: FrontierAction,
    targets: tuple[str, ...],
    requested_children: tuple[str, ...] = (),
    cursor: str | None = None,
    *,
    run_id: str = "run-1",
    unit_id: str = "unit-1",
) -> ValidatedV3SemanticOutput:
    return ValidatedV3SemanticOutput(
        "revision-1",
        run_id,
        unit_id,
        "a" * 64,
        action,
        targets,
        ("signal-1",),
        ("b" * 64,),
        "grounded synthetic semantic rationale",
        (),
        0.75,
        requested_children,
        cursor,
    )


def test_runtime_deferred_projection_is_exact_bounded_and_in_caller_order(
    tmp_path: Path,
) -> None:
    """Catches lifecycle reconstruction of selector/source/coverage authority."""
    path = tmp_path / "deferred.sqlite"
    _, seal = _build_deferred_catalog(path, count=64)
    requested = tuple(f"deferred-{index:03}" for index in range(63, -1, -1))

    with SemanticCatalogReader(path, seal) as reader:
        expected_by_id = {
            row.node_id: row
            for row in reader.frontier_deferred_page(
                FRONTIER_SELECTOR_VERSION, None, 64
            )
        }
        observer = RuntimeDeferredProjectionObserver()
        projected = reader.runtime_deferred_projection(
            FRONTIER_SELECTOR_VERSION,
            requested,
            observer=observer,
        )

    assert projected == tuple(expected_by_id[node_id] for node_id in requested)
    assert observer.query_count == 1
    assert observer.max_python_rows == 64
    assert observer.max_query_rows == 64
    assert all("/" not in row.source_id for row in projected)


def test_runtime_deferred_projection_rejects_missing_duplicate_and_selected_nodes(
    tmp_path: Path,
) -> None:
    """Catches partial or selected rows entering runtime-deferred authority."""
    path = tmp_path / "projection-rejections.sqlite"
    with SemanticCatalogWriter(path) as writer:
        _add_feature(
            writer,
            "selected",
            source_id="source-a",
            lane="lane-a",
            project_like=True,
        )
        _add_feature(
            writer,
            "deferred",
            source_id="source-b",
            lane="lane-b",
            project_like=False,
        )
        build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-projection",
            policy_hash="a" * 64,
        )
        seal = writer.seal()

    with SemanticCatalogReader(path, seal) as reader:
        for requested in (
            ("missing",),
            ("deferred", "deferred"),
            ("selected",),
        ):
            with pytest.raises(CatalogError, match="runtime deferred authority"):
                reader.runtime_deferred_projection(
                    FRONTIER_SELECTOR_VERSION, requested
                )
        with pytest.raises(CatalogError, match="runtime deferred authority"):
            reader.runtime_deferred_projection("wrong-selector", ("deferred",))


@pytest.mark.parametrize(
    "mutation",
    (
        "cursor",
        "node_digest",
        "feature_digest",
        "source_locator",
        "source_unc_locator",
        "reason",
    ),
)
def test_runtime_deferred_projection_rejects_rehashed_malformed_or_drifted_rows(
    tmp_path: Path, mutation: str
) -> None:
    """Catches a coherent replacement catalog whose selector authority disagrees."""
    path = tmp_path / f"projection-{mutation}.sqlite"
    _build_deferred_catalog(path, count=1)
    with sqlite3.connect(path) as connection:
        if mutation == "cursor":
            connection.execute(
                "UPDATE frontier_selector_results SET cursor_json = '{}'"
            )
            connection.execute("UPDATE frontier_deferred SET cursor_json = '{}'")
        elif mutation == "node_digest":
            connection.execute(
                "UPDATE frontier_selector_results SET node_digest = ?",
                (_digest("replaced-node"),),
            )
            connection.execute(
                "UPDATE frontier_deferred SET node_digest = ?",
                (_digest("replaced-node"),),
            )
        elif mutation == "feature_digest":
            connection.execute(
                "UPDATE frontier_features SET feature_digest = ?",
                (_digest("replaced-feature"),),
            )
        elif mutation in {"source_locator", "source_unc_locator"}:
            unsafe = (
                "file:///private/synthetic"
                if mutation == "source_locator"
                else "\\\\server\\private"
            )
            row = connection.execute(
                """
                SELECT node.node_id, node.parent_node_id, baseline.coverage,
                       baseline.full_content_sha256, feature.feature_digest
                FROM nodes AS node
                JOIN local_baselines AS baseline ON baseline.node_id = node.node_id
                JOIN frontier_features AS feature ON feature.node_id = node.node_id
                """
            ).fetchone()
            assert row is not None
            replaced_digest = hashlib.sha256(
                canonical_json_text(
                    {
                        "coverage": row[2],
                        "feature_digest": row[4],
                        "full_content_sha256": row[3],
                        "node_id": row[0],
                        "parent_node_id": row[1],
                        "source_id": unsafe,
                    }
                ).encode("utf-8")
            ).hexdigest()
            connection.execute("UPDATE nodes SET source_id = ?", (unsafe,))
            connection.execute(
                "UPDATE frontier_selector_results SET source_id = ?, node_digest = ?",
                (unsafe, replaced_digest),
            )
            connection.execute(
                "UPDATE frontier_deferred SET source_id = ?, node_digest = ?",
                (unsafe, replaced_digest),
            )
        else:
            connection.execute(
                "UPDATE frontier_selector_results SET reason = 'unexpected'"
            )
            connection.execute("UPDATE frontier_deferred SET reason = 'unexpected'")
        connection.commit()
    seal = _rehashed_catalog_seal(path)

    with SemanticCatalogReader(path, seal) as reader:
        with pytest.raises(CatalogError, match="runtime deferred authority"):
            reader.runtime_deferred_projection(
                FRONTIER_SELECTOR_VERSION, ("deferred-000",)
            )


def test_typed_transition_projects_opened_units_and_pending_deferred_rows(
    tmp_path: Path,
) -> None:
    """Catches caller-provided child/deferred metadata bypassing sealed authority."""
    path = tmp_path / "typed-transition.sqlite"
    with SemanticCatalogWriter(path) as writer:
        _add_feature(
            writer,
            "parent",
            source_id="source-a",
            lane="lane-a",
            project_like=True,
        )
        _add_feature(
            writer,
            "child",
            parent_node_id="parent",
            source_id="source-a",
            lane="lane-a",
            project_like=False,
        )
        plan = build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-transition",
            policy_hash="a" * 64,
        )
        seal = writer.seal()

    parent_unit = next(
        unit for unit in plan.units if unit.target_node_ids == ("parent",)
    )
    expansion_unit = SemanticPlannedUnit(
        parent_unit.unit_id,
        FrontierPhase.EXPANSION,
        parent_unit.target_node_ids,
        parent_unit.dependency_unit_ids,
        parent_unit.estimated_input_bytes,
        parent_unit.priority,
        parent_unit.fairness_lane,
    )
    with SemanticCatalogReader(path, seal) as reader:
        expanded = transition_frontier_with_authority(
            _validated_output(
                FrontierAction.EXPAND_CHILDREN,
                ("parent",),
                ("child",),
                run_id=plan.run_id,
                unit_id=expansion_unit.unit_id,
            ),
            run_id=plan.run_id,
            current_unit=expansion_unit,
            reader=reader,
            selector_version=FRONTIER_SELECTOR_VERSION,
            budget_exhausted=False,
        )
        repeated_expanded = transition_frontier_with_authority(
            _validated_output(
                FrontierAction.EXPAND_CHILDREN,
                ("parent",),
                ("child",),
                run_id=plan.run_id,
                unit_id=expansion_unit.unit_id,
            ),
            run_id=plan.run_id,
            current_unit=expansion_unit,
            reader=reader,
            selector_version=FRONTIER_SELECTOR_VERSION,
            budget_exhausted=False,
        )
        deferred = transition_frontier_with_authority(
            _validated_output(
                FrontierAction.DEFER_ON_DEMAND,
                ("child",),
                run_id=plan.run_id,
                unit_id="runtime-unit",
            ),
            run_id=plan.run_id,
            current_unit=SemanticPlannedUnit(
                "runtime-unit",
                FrontierPhase.EXPANSION,
                ("child",),
                (),
                expansion_unit.estimated_input_bytes,
                expansion_unit.priority,
                expansion_unit.fairness_lane,
            ),
            reader=reader,
            selector_version=FRONTIER_SELECTOR_VERSION,
            budget_exhausted=False,
        )
        budget = transition_frontier_with_authority(
            _validated_output(
                FrontierAction.ACCEPT_BOUNDARY,
                ("parent",),
                run_id=plan.run_id,
                unit_id=expansion_unit.unit_id,
            ),
            run_id=plan.run_id,
            current_unit=expansion_unit,
            reader=reader,
            selector_version=FRONTIER_SELECTOR_VERSION,
            budget_exhausted=True,
            remaining_deferred_node_ids=("child",),
            resume_cursor='{"after":"child"}',
        )

    assert len(expanded.opened_units) == 1
    assert expanded.opened_units[0].unit.target_node_ids == ("child",)
    assert expanded.opened_units[0].unit.dependency_unit_ids == (parent_unit.unit_id,)
    assert expanded.requires_expansion_queue_check is True
    assert repeated_expanded == expanded
    assert [row.node_id for row in deferred.pending_runtime_deferred] == ["child"]
    assert [row.node_id for row in budget.pending_runtime_deferred] == ["child"]
    assert budget.transition.paused_budget is True


def test_typed_transition_opens_at_most_64_deterministic_child_units(
    tmp_path: Path,
) -> None:
    """Catches an expansion helper that bypasses the bounded quality contract."""
    path = tmp_path / "bounded-opened-units.sqlite"
    child_ids = tuple(f"child-{index:03}" for index in range(64))
    with SemanticCatalogWriter(path) as writer:
        _add_feature(
            writer,
            "parent",
            source_id="source-a",
            lane="lane-a",
            project_like=True,
        )
        for child_id in child_ids:
            _add_feature(
                writer,
                child_id,
                parent_node_id="parent",
                source_id="source-a",
                lane="lane-a",
                project_like=False,
            )
        plan = build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-bounded-open",
            policy_hash="a" * 64,
        )
        seal = writer.seal()
    parent_unit = next(
        unit for unit in plan.units if unit.target_node_ids == ("parent",)
    )
    with SemanticCatalogReader(path, seal) as reader:
        first = transition_frontier_with_authority(
            _validated_output(
                FrontierAction.EXPAND_CHILDREN,
                ("parent",),
                child_ids,
                run_id=plan.run_id,
                unit_id=parent_unit.unit_id,
            ),
            run_id=plan.run_id,
            current_unit=parent_unit,
            reader=reader,
            selector_version=FRONTIER_SELECTOR_VERSION,
            budget_exhausted=False,
        )
        second = transition_frontier_with_authority(
            _validated_output(
                FrontierAction.EXPAND_CHILDREN,
                ("parent",),
                child_ids,
                run_id=plan.run_id,
                unit_id=parent_unit.unit_id,
            ),
            run_id=plan.run_id,
            current_unit=parent_unit,
            reader=reader,
            selector_version=FRONTIER_SELECTOR_VERSION,
            budget_exhausted=False,
        )

    assert len(first.opened_units) == 64
    assert first == second
    assert [item.unit.target_node_ids[0] for item in first.opened_units] == list(child_ids)


def test_existing_pure_transition_contract_is_unchanged() -> None:
    """Catches a Task 10 prerequisite changing accepted Task 6 transition behavior."""
    accepted = transition_frontier(
        _validated_output(FrontierAction.ACCEPT_BOUNDARY, ("target",)),
        budget_exhausted=False,
    )
    deferred = transition_frontier(
        _validated_output(FrontierAction.DEFER_ON_DEMAND, ("target",)),
        budget_exhausted=True,
        remaining_deferred_node_ids=("remaining",),
        resume_cursor='{"after":"remaining"}',
    )

    assert accepted.accepted_node_ids == ("target",)
    assert accepted.deferred_node_ids == ()
    assert deferred.deferred_node_ids == ("target", "remaining")
    assert deferred.paused_budget is True


def test_typed_transition_rejects_output_from_another_run_or_unit(
    tmp_path: Path,
) -> None:
    """Catches a validated result being replayed against another run authority."""
    path = tmp_path / "transition-output-binding.sqlite"
    with SemanticCatalogWriter(path) as writer:
        _add_feature(
            writer,
            "target",
            source_id="source-a",
            lane="lane-a",
            project_like=True,
        )
        plan = build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-output-binding",
            policy_hash="a" * 64,
        )
        seal = writer.seal()
    unit = next(unit for unit in plan.units if unit.target_node_ids == ("target",))

    with SemanticCatalogReader(path, seal) as reader:
        for output in (
            _validated_output(
                FrontierAction.ACCEPT_BOUNDARY,
                ("target",),
                run_id="other-run",
                unit_id=unit.unit_id,
            ),
            _validated_output(
                FrontierAction.ACCEPT_BOUNDARY,
                ("target",),
                run_id=plan.run_id,
                unit_id="other-unit",
            ),
            _validated_output(
                FrontierAction.ACCEPT_BOUNDARY,
                ("other-target",),
                run_id=plan.run_id,
                unit_id=unit.unit_id,
            ),
        ):
            with pytest.raises(FrontierSelectionError, match="output authority"):
                transition_frontier_with_authority(
                    output,
                    run_id=plan.run_id,
                    current_unit=unit,
                    reader=reader,
                    selector_version=FRONTIER_SELECTOR_VERSION,
                    budget_exhausted=False,
                )
        with pytest.raises(CatalogError, match="runtime deferred authority"):
            transition_frontier_with_authority(
                _validated_output(
                    FrontierAction.ACCEPT_BOUNDARY,
                    ("target",),
                    run_id=plan.run_id,
                    unit_id=unit.unit_id,
                ),
                run_id=plan.run_id,
                current_unit=unit,
                reader=reader,
                selector_version="wrong-selector",
                budget_exhausted=False,
            )


def test_typed_transition_keeps_64_dependencies_for_terminal_actions(
    tmp_path: Path,
) -> None:
    """Catches an inclusive dependency limit rejecting a valid terminal unit."""
    path = tmp_path / "transition-dependency-limit.sqlite"
    with SemanticCatalogWriter(path) as writer:
        _add_feature(
            writer,
            "target",
            source_id="source-a",
            lane="lane-a",
            project_like=True,
        )
        plan = build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-dependency-limit",
            policy_hash="a" * 64,
        )
        seal = writer.seal()
    current = SemanticPlannedUnit(
        "terminal-unit",
        FrontierPhase.EXPANSION,
        ("target",),
        tuple(f"dependency-{index:03}" for index in range(64)),
        1024,
        1,
        "lane-a",
    )
    terminal = _validated_output(
        FrontierAction.ACCEPT_BOUNDARY,
        ("target",),
        run_id=plan.run_id,
        unit_id=current.unit_id,
    )
    expansion = _validated_output(
        FrontierAction.EXPAND_CHILDREN,
        ("target",),
        ("target",),
        run_id=plan.run_id,
        unit_id=current.unit_id,
    )

    with SemanticCatalogReader(path, seal) as reader:
        authority = transition_frontier_with_authority(
            terminal,
            run_id=plan.run_id,
            current_unit=current,
            reader=reader,
            selector_version=FRONTIER_SELECTOR_VERSION,
            budget_exhausted=False,
        )
        with pytest.raises(FrontierSelectionError, match="dependency"):
            transition_frontier_with_authority(
                expansion,
                run_id=plan.run_id,
                current_unit=current,
                reader=reader,
                selector_version=FRONTIER_SELECTOR_VERSION,
                budget_exhausted=False,
            )

    assert authority.transition.accepted_node_ids == ("target",)


def test_frontier_plan_is_deterministic_and_breadth_before_depth_fair(
    tmp_path: Path,
) -> None:
    """Catches nondeterministic IDs or draining one fair lane before peers."""
    first, _ = _build_three_lane_plan(tmp_path / "first.sqlite")
    second, _ = _build_three_lane_plan(tmp_path / "second.sqlite")

    assert first == second
    assert first.selector_version == FRONTIER_SELECTOR_VERSION
    assert [unit.fairness_lane for unit in first.units[:3]] == [
        "source-a",
        "source-b",
        "source-c",
    ]
    assert [unit.target_node_ids[-1] for unit in first.units[:3]] == [
        "source-a/root",
        "source-b/root",
        "source-c/root",
    ]


def test_frontier_plan_includes_ancestor_closure_and_defers_oversized_closure(
    tmp_path: Path,
) -> None:
    """Catches orphan child targets and silent truncation at the unit byte limit."""
    path = tmp_path / "catalog.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", ".", "source-1"))
        writer.add_baseline(
            BaselineRecord(
                "root", BaselineCoverage.COMPLETE, (), (), 0, None
            )
        )
        _add_feature(
            writer,
            "root/project",
            parent_node_id="root",
            source_id="source-1",
            lane="source-1/root",
        )
        _add_feature(
            writer,
            "too-large",
            source_id="source-2",
            lane="source-2",
            roles=("x" * (MAX_UNIT_BYTES + 1),),
        )
        plan = build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-1",
            policy_hash="a" * 64,
        )
        seal = writer.seal()

    selected = {node_id for unit in plan.units for node_id in unit.target_node_ids}
    assert {"root", "root/project"}.issubset(selected)
    assert "too-large" not in selected
    assert not hasattr(plan, "deferred")
    with SemanticCatalogReader(path, seal) as reader:
        deferred = reader.frontier_deferred_page(
            FRONTIER_SELECTOR_VERSION, None, 64
        )
        assert [row.node_id for row in deferred] == ["too-large"]
        assert plan.deferred_count == 1
        assert plan.deferred_root == reader.deferred_summary(
            selector_version=FRONTIER_SELECTOR_VERSION
        ).root


def test_single_signal_decoy_is_durably_deferred_not_promoted(
    tmp_path: Path,
) -> None:
    """Catches treating one filename/manifest-like signal as project authority."""
    path = tmp_path / "decoy.sqlite"
    with SemanticCatalogWriter(path) as writer:
        _add_feature(
            writer,
            "README-only",
            source_id="source-decoy",
            lane="source-decoy",
            project_like=False,
            roles=("project-description",),
        )
        plan = build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-1",
            policy_hash="a" * 64,
        )
        seal = writer.seal()

    assert plan.units == ()
    assert plan.deferred_count == 1
    with SemanticCatalogReader(path, seal) as reader:
        assert [
            row.node_id
            for row in reader.frontier_deferred_page(
                FRONTIER_SELECTOR_VERSION, None, 64
            )
        ] == ["README-only"]


def test_planning_session_is_file_backed_and_never_returns_over_64_rows(
    tmp_path: Path,
) -> None:
    """Catches OFFSET/global Python planning or an oversized Python page."""
    observer = PlanningQueryObserver()
    path = tmp_path / "bounded.sqlite"
    with SemanticCatalogWriter(path) as writer:
        for index in range(130):
            lane = f"lane-{index % 3}"
            _add_feature(
                writer,
                f"node-{index:03}",
                source_id=lane,
                lane=lane,
            )
        plan = build_frontier_plan(
            writer.planning_session(
                FRONTIER_SELECTOR_VERSION, observer=observer
            ),
            revision_id="revision-1",
            policy_hash="a" * 64,
        )
        writer.seal()

    assert len(plan.units) == 130
    assert observer.temp_store_file is True
    assert observer.max_python_rows <= 64
    assert observer.sequence_sort_calls == 0
    assert observer.page_calls > 2


def test_high_fanout_representative_finds_project_child_after_index_9000(
    tmp_path: Path,
) -> None:
    """Catches reusing the structural attestation's first 64 child IDs."""
    path = tmp_path / "high-fanout.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("parent", None, "directory", "parent", "source"))
        writer.add_baseline(
            BaselineRecord("parent", BaselineCoverage.COMPLETE, (), (), 0, None)
        )
        for index in range(10_000):
            node_id = f"child-{index:05}"
            writer.add_node(
                NodeRecord(node_id, "parent", "file", node_id, "source")
            )
            role = "project-description" if index == 9_000 else "documentation"
            writer.add_baseline(
                BaselineRecord(
                    node_id,
                    BaselineCoverage.COMPLETE,
                    (role,),
                    (),
                    64,
                    None,
                    full_content_sha256=_digest(f"content:{node_id}"),
                )
            )
        seal = writer.seal()

    observer = RepresentativeSelectionObserver()
    with SemanticCatalogReader(path, seal) as reader:
        representatives = representative_nodes(
            reader, "parent", observer=observer
        )

    node_ids = tuple(item.node_id for item in representatives)
    assert "child-09000" in node_ids
    assert node_ids != tuple(f"child-{index:05}" for index in range(64))
    assert len(node_ids) <= 8
    assert observer.max_python_rows <= 64
    assert observer.max_retained_candidates <= 8


def _audit_row(
    index: int,
    *,
    cursor_order: int | None = None,
    selector_version: str = FRONTIER_SELECTOR_VERSION,
    source_id: str | None = None,
    coverage_stratum: str | None = None,
    depth_band: str = "shallow",
    size_band: str = "small",
    role_band: str = "documentation",
    relevance_rank: int | None = None,
) -> SemanticDeferredAuditRow:
    order = index if cursor_order is None else cursor_order
    node_id = f"audit-{index:04}"
    coverage = coverage_stratum or (
        "complete", "sampled", "unsupported", "unreadable", "failed"
    )[index % 5]
    return SemanticDeferredAuditRow(
        node_id=node_id,
        reason="budget",
        node_digest=_digest(f"node:{node_id}"),
        cursor_json=canonical_json_text(
            {
                "depth_band": depth_band,
                "fair_round": order,
                "lane": "audit-lane",
                "node_id": node_id,
                "order": order,
                "role_band": role_band,
                "selector_feature_digest": _digest(f"feature:{node_id}"),
                "size_band": size_band,
            }
        ),
        cursor_order=order,
        source_id=source_id or f"source-{index % 7}",
        coverage_stratum=coverage,
        relevance_rank=index if relevance_rank is None else relevance_rank,
        selector_version=selector_version,
        selector_feature_digest=_digest(f"feature:{node_id}"),
    )


def _audit_checkpoint(**changes: object) -> AuditCheckpointBinding:
    values: dict[str, object] = {
        "run_id": "run-v3",
        "revision_id": "revision-v3",
        "policy_sha256": "a" * 64,
        "selector_version": FRONTIER_SELECTOR_VERSION,
        "catalog_root": "b" * 64,
        "approved_revision_id": "revision-v3",
        "approved_policy_sha256": "a" * 64,
        "approved_selector_version": FRONTIER_SELECTOR_VERSION,
        "approved_catalog_root": "b" * 64,
    }
    values.update(changes)
    return AuditCheckpointBinding(**values)


def _mutated_audit_row(
    row: SemanticDeferredAuditRow, **changes: object
) -> SemanticDeferredAuditRow:
    values = dict(row.__dict__)
    values.update(changes)
    cursor = json.loads(row.cursor_json)
    if "node_id" in changes:
        cursor["node_id"] = changes["node_id"]
    if "cursor_order" in changes:
        cursor["order"] = changes["cursor_order"]
    if "selector_feature_digest" in changes:
        cursor["selector_feature_digest"] = changes["selector_feature_digest"]
    values["cursor_json"] = canonical_json_text(cursor)
    return SemanticDeferredAuditRow(**values)


def test_audit_freeze_returns_exact_deterministic_300_unique_members_boundedly() -> None:
    """Catches population materialization, overlap loss, or unstable membership."""
    rows = tuple(_audit_row(index) for index in range(450))
    first_observer = AuditSelectionObserver()
    first = freeze_audit_sample(
        catalog_deferred=iter(rows),
        runtime_deferred=iter(()),
        pending_runtime_deferred=(),
        checkpoint=_audit_checkpoint(),
        observer=first_observer,
    )
    second = freeze_audit_sample(
        catalog_deferred=iter(rows),
        runtime_deferred=iter(()),
        pending_runtime_deferred=(),
        checkpoint=_audit_checkpoint(),
    )

    assert first == second
    assert len(first.members) == 300
    assert len({member.node_id for member in first.members}) == 300
    assert [member.stratum for member in first.members].count("relevance") == 100
    assert [member.stratum for member in first.members].count("coverage") == 100
    assert [member.stratum for member in first.members].count("random") == 100
    assert len(first.sample_digest) == len(first.population_digest) == 64
    assert first_observer.max_relevance_candidates <= 300
    assert first_observer.max_random_candidates <= 300
    assert first_observer.max_coverage_stratum_candidates <= 300
    assert first_observer.max_total_python_candidates <= 2_100
    assert first_observer.axis_index_file_backed is True
    assert first_observer.temporary_state_closed is True
    assert first_observer.max_pending_rows == 0


@pytest.mark.parametrize("stream", ("catalog", "runtime", "pending"))
@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"cursor_order": -1}, "cursor order"),
        ({"cursor_order": True}, "cursor order"),
        ({"relevance_rank": -1}, "relevance"),
        ({"relevance_rank": True}, "relevance"),
        ({"reason": " "}, "reason"),
        ({"source_id": " source "}, "source"),
        ({"node_digest": "A" * 64}, "digest"),
    ),
)
def test_all_audit_streams_reject_noncanonical_row_payloads(
    stream: str, changes: dict[str, object], message: str
) -> None:
    catalog = tuple(_audit_row(index) for index in range(299))
    bad = _mutated_audit_row(_audit_row(299), **changes)
    kwargs: dict[str, object] = {
        "catalog_deferred": iter((*catalog, bad)),
        "runtime_deferred": iter(()),
        "pending_runtime_deferred": (),
    }
    runtime = SemanticRuntimeDeferredRow(bad, "unit-a", 0)
    if stream == "runtime":
        kwargs["catalog_deferred"] = iter(catalog)
        kwargs["runtime_deferred"] = iter((runtime,))
    elif stream == "pending":
        kwargs["catalog_deferred"] = iter(catalog)
        kwargs["pending_runtime_deferred"] = (runtime,)
    observer = AuditSelectionObserver()
    with pytest.raises(FrontierSelectionError, match=message):
        freeze_audit_sample(
            checkpoint=_audit_checkpoint(), observer=observer, **kwargs
        )
    assert observer.temporary_state_closed is True


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"node_id": ""}, "node"),
        ({"node_id": " node "}, "node"),
        ({"reason": ""}, "reason"),
        ({"reason": " reason "}, "reason"),
        ({"reason": "bad\nreason"}, "reason"),
        ({"reason": "x" * 4_097}, "reason"),
        ({"source_id": ""}, "source"),
        ({"source_id": "bad\nsource"}, "source"),
        ({"selector_version": " "}, "selector"),
        ({"selector_feature_digest": "B" * 64}, "digest"),
    ),
)
def test_audit_row_rejects_empty_control_oversized_or_noncanonical_fields(
    changes: dict[str, object], message: str
) -> None:
    rows = [_audit_row(index) for index in range(300)]
    rows[-1] = _mutated_audit_row(rows[-1], **changes)
    with pytest.raises(FrontierSelectionError, match=message):
        freeze_audit_sample(
            catalog_deferred=iter(rows),
            runtime_deferred=iter(()),
            pending_runtime_deferred=(),
            checkpoint=_audit_checkpoint(),
        )


def test_audit_row_rejects_cursor_binding_mismatch() -> None:
    rows = [_audit_row(index) for index in range(300)]
    cursor = json.loads(rows[-1].cursor_json)
    cursor["order"] += 1
    rows[-1] = SemanticDeferredAuditRow(
        **{**rows[-1].__dict__, "cursor_json": canonical_json_text(cursor)}
    )
    with pytest.raises(FrontierSelectionError, match="cursor.*binding"):
        freeze_audit_sample(
            catalog_deferred=iter(rows),
            runtime_deferred=iter(()),
            pending_runtime_deferred=(),
            checkpoint=_audit_checkpoint(),
        )


@pytest.mark.parametrize("stream", ("runtime", "pending"))
def test_runtime_audit_zero_sequence_and_zero_row_order_are_valid(stream: str) -> None:
    catalog = tuple(_audit_row(index) for index in range(1, 300))
    zero = SemanticRuntimeDeferredRow(_audit_row(0), "unit-zero", 0)
    sample = freeze_audit_sample(
        catalog_deferred=iter(catalog),
        runtime_deferred=iter((zero,)) if stream == "runtime" else iter(()),
        pending_runtime_deferred=(zero,) if stream == "pending" else (),
        checkpoint=_audit_checkpoint(),
    )
    assert len(sample.members) == 300


@pytest.mark.parametrize(
    ("axis", "dominant", "minority"),
    (
        ("source_id", "source-dominant", "source-minority"),
        ("depth_band", "shallow", "deep"),
        ("size_band", "small", "large"),
        ("role_band", "documentation", "test"),
        ("coverage_stratum", "complete", "failed"),
    ),
)
def test_coverage_sample_includes_each_minority_axis(
    axis: str, dominant: str, minority: str
) -> None:
    """Catches coverage selection that only stratifies coverage/error."""
    rows = []
    for index in range(500):
        value = dominant if index < 400 else minority
        kwargs: dict[str, object] = {
            "source_id": "source-dominant",
            "coverage_stratum": "complete",
            "depth_band": "shallow",
            "size_band": "small",
            "role_band": "documentation",
        }
        kwargs[axis] = value
        rows.append(_audit_row(index, **kwargs))

    sample = freeze_audit_sample(
        catalog_deferred=iter(rows),
        runtime_deferred=iter(()),
        pending_runtime_deferred=(),
        checkpoint=_audit_checkpoint(),
    )

    coverage_ids = {
        member.node_id for member in sample.members if member.stratum == "coverage"
    }
    assert any(int(node_id.rsplit("-", 1)[1]) >= 400 for node_id in coverage_ids)


@pytest.mark.parametrize("axis", ("depth_band", "size_band", "role_band"))
def test_coverage_membership_is_bound_to_each_semantic_axis(axis: str) -> None:
    values = {
        "depth_band": ("shallow", "deep"),
        "size_band": ("small", "large"),
        "role_band": ("documentation", "test"),
    }

    def sample(minority_start: int) -> set[str]:
        rows = []
        for index in range(500):
            kwargs: dict[str, object] = {
                "source_id": "source",
                "coverage_stratum": "complete",
                "depth_band": "shallow",
                "size_band": "small",
                "role_band": "documentation",
            }
            kwargs[axis] = values[axis][int(index >= minority_start)]
            rows.append(_audit_row(index, **kwargs))
        frozen = freeze_audit_sample(
            catalog_deferred=iter(rows),
            runtime_deferred=iter(()),
            pending_runtime_deferred=(),
            checkpoint=_audit_checkpoint(),
        )
        return {
            member.node_id
            for member in frozen.members
            if member.stratum == "coverage"
        }

    assert sample(400) != sample(100)


@pytest.mark.parametrize("mutation", ("missing", "invented", "noncanonical"))
def test_audit_cursor_rejects_missing_invented_or_noncanonical_axes(
    mutation: str,
) -> None:
    rows = [_audit_row(index) for index in range(300)]
    cursor = {
        "depth_band": "shallow",
        "fair_round": rows[0].cursor_order,
        "lane": "audit-lane",
        "node_id": rows[0].node_id,
        "order": rows[0].cursor_order,
        "role_band": "documentation",
        "selector_feature_digest": rows[0].selector_feature_digest,
        "size_band": "small",
    }
    if mutation == "missing":
        cursor.pop("depth_band")
    elif mutation == "invented":
        cursor["invented_axis"] = "value"
    else:
        cursor["depth_band"] = "Shallow"
    rows[0] = SemanticDeferredAuditRow(
        **{**rows[0].__dict__, "cursor_json": canonical_json_text(cursor)}
    )

    with pytest.raises(FrontierSelectionError, match="axis|cursor"):
        freeze_audit_sample(
            catalog_deferred=iter(rows),
            runtime_deferred=iter(()),
            pending_runtime_deferred=(),
            checkpoint=_audit_checkpoint(),
        )


def test_audit_many_unique_sources_keeps_fixed_python_state_and_cleans_temp_state() -> None:
    observer = AuditSelectionObserver()
    rows = tuple(
        _audit_row(index, source_id=f"source-{index:04}") for index in range(500)
    )
    freeze_audit_sample(
        catalog_deferred=iter(rows),
        runtime_deferred=iter(()),
        pending_runtime_deferred=(),
        checkpoint=_audit_checkpoint(),
        observer=observer,
    )
    assert observer.max_total_python_candidates <= 2_100
    assert observer.max_coverage_axis_values == 0
    assert observer.axis_index_file_backed is True
    assert observer.temporary_state_closed is True

    failure_observer = AuditSelectionObserver()
    with pytest.raises(FrontierSelectionError, match="duplicate"):
        freeze_audit_sample(
            catalog_deferred=iter(rows),
            runtime_deferred=iter(
                (SemanticRuntimeDeferredRow(rows[-1], "unit-duplicate", 0),)
            ),
            pending_runtime_deferred=(),
            checkpoint=_audit_checkpoint(),
            observer=failure_observer,
        )
    assert failure_observer.temporary_state_closed is True


def test_audit_equal_rank_overlap_still_returns_exact_unique_sample() -> None:
    rows = tuple(
        _audit_row(
            index,
            source_id="source",
            coverage_stratum="complete",
            relevance_rank=0,
        )
        for index in range(450)
    )
    sample = freeze_audit_sample(
        catalog_deferred=iter(rows),
        runtime_deferred=iter(()),
        pending_runtime_deferred=(),
        checkpoint=_audit_checkpoint(),
    )
    assert len(sample.members) == len({member.node_id for member in sample.members}) == 300


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("duplicate", "duplicate"),
        ("cursor", "canonical"),
        ("selector", "selector"),
        ("feature", "feature"),
        ("order", "cursor order"),
        ("checkpoint", "checkpoint"),
    ),
)
def test_audit_freeze_rejects_population_or_checkpoint_drift(
    mutation: str, message: str
) -> None:
    """Catches accepting a population that is not the approved canonical stream."""
    rows = [_audit_row(index) for index in range(301)]
    runtime: tuple[SemanticRuntimeDeferredRow, ...] = ()
    checkpoint = _audit_checkpoint()
    if mutation == "duplicate":
        runtime = (SemanticRuntimeDeferredRow(rows[0], "unit-1", 1),)
    elif mutation == "cursor":
        rows[5] = SemanticDeferredAuditRow(
            **{**rows[5].__dict__, "cursor_json": '{"order": 5, "node_id":"audit-0005"}'}
        )
    elif mutation == "selector":
        rows[5] = SemanticDeferredAuditRow(
            **{**rows[5].__dict__, "selector_version": "other-selector"}
        )
    elif mutation == "feature":
        rows[5] = SemanticDeferredAuditRow(
            **{**rows[5].__dict__, "selector_feature_digest": "not-a-digest"}
        )
    elif mutation == "order":
        rows[5] = _audit_row(5, cursor_order=3)
    else:
        checkpoint = _audit_checkpoint(approved_policy_sha256="c" * 64)

    with pytest.raises(FrontierSelectionError, match=message):
        freeze_audit_sample(
            catalog_deferred=iter(rows),
            runtime_deferred=iter(runtime),
            pending_runtime_deferred=(),
            checkpoint=checkpoint,
        )


def test_audit_digest_binds_population_changes_and_pending_rows_stay_bounded() -> None:
    """Catches a sample digest detached from the post-expansion population."""
    rows = tuple(_audit_row(index) for index in range(300))
    first = freeze_audit_sample(
        catalog_deferred=iter(rows),
        runtime_deferred=iter(()),
        pending_runtime_deferred=(),
        checkpoint=_audit_checkpoint(),
    )
    changed = list(rows)
    changed[-1] = SemanticDeferredAuditRow(
        **{**changed[-1].__dict__, "node_digest": _digest("changed-node")}
    )
    second = freeze_audit_sample(
        catalog_deferred=iter(changed),
        runtime_deferred=iter(()),
        pending_runtime_deferred=(),
        checkpoint=_audit_checkpoint(),
    )

    assert first.population_digest != second.population_digest
    assert first.sample_digest != second.sample_digest
    with pytest.raises(FrontierSelectionError, match="pending"):
        freeze_audit_sample(
            catalog_deferred=iter(rows),
            runtime_deferred=iter(()),
            pending_runtime_deferred=tuple(_audit_row(1_000 + i) for i in range(65)),
            checkpoint=_audit_checkpoint(),
        )


def test_audit_rejects_dynamic_coverage_strata_without_growing_metadata() -> None:
    """Catches an attacker turning coverage labels into an unbounded reservoir map."""
    observer = AuditSelectionObserver()
    rows = []
    for index in range(301):
        row = _audit_row(index)
        rows.append(
            SemanticDeferredAuditRow(
                **{**row.__dict__, "coverage_stratum": f"invented-{index:04}"}
            )
        )

    with pytest.raises(FrontierSelectionError, match="coverage"):
        freeze_audit_sample(
            catalog_deferred=iter(rows),
            runtime_deferred=iter(()),
            pending_runtime_deferred=(),
            checkpoint=_audit_checkpoint(),
            observer=observer,
        )

    assert observer.max_coverage_stratum_candidates == 0
    assert observer.population_count == 0


def test_audit_population_digest_binds_runtime_transition_authority() -> None:
    """Catches two expansion sequences collapsing to one population authority."""
    catalog = tuple(_audit_row(index) for index in range(299))
    runtime_row = _audit_row(299)
    first = freeze_audit_sample(
        catalog_deferred=iter(catalog),
        runtime_deferred=iter(
            (SemanticRuntimeDeferredRow(runtime_row, "unit-a", 1),)
        ),
        pending_runtime_deferred=(),
        checkpoint=_audit_checkpoint(),
    )
    second = freeze_audit_sample(
        catalog_deferred=iter(catalog),
        runtime_deferred=iter(
            (SemanticRuntimeDeferredRow(runtime_row, "unit-b", 2),)
        ),
        pending_runtime_deferred=(),
        checkpoint=_audit_checkpoint(),
    )

    assert first.population_digest != second.population_digest
    assert first.sample_digest != second.sample_digest


@pytest.mark.parametrize("stream", ("catalog", "runtime", "pending"))
def test_audit_streams_require_their_exact_authority_row_types(stream: str) -> None:
    """Catches runtime/pending rows losing transition unit and sequence authority."""
    catalog = tuple(_audit_row(index) for index in range(299))
    audit = _audit_row(299)
    kwargs: dict[str, object] = {
        "catalog_deferred": iter(catalog),
        "runtime_deferred": iter((SemanticRuntimeDeferredRow(audit, "unit-a", 1),)),
        "pending_runtime_deferred": (),
    }
    if stream == "catalog":
        kwargs["catalog_deferred"] = iter(
            (SemanticRuntimeDeferredRow(catalog[0], "unit-a", 0), *catalog[1:])
        )
        kwargs["runtime_deferred"] = iter(
            (SemanticRuntimeDeferredRow(audit, "unit-a", 1),)
        )
    elif stream == "runtime":
        kwargs["runtime_deferred"] = iter((audit,))
    else:
        kwargs["runtime_deferred"] = iter(())
        kwargs["pending_runtime_deferred"] = (audit,)

    with pytest.raises(FrontierSelectionError, match="authority row type"):
        freeze_audit_sample(checkpoint=_audit_checkpoint(), **kwargs)


@pytest.mark.parametrize(
    ("rows", "message"),
    (
        ((SemanticRuntimeDeferredRow(_audit_row(298), "unit-a", -1),), "sequence"),
        ((SemanticRuntimeDeferredRow(_audit_row(298), "unit-a", True),), "sequence"),
        (
            (
                SemanticRuntimeDeferredRow(_audit_row(298), "unit-a", 2),
                SemanticRuntimeDeferredRow(_audit_row(299), "unit-b", 1),
            ),
            "sequence",
        ),
        ((SemanticRuntimeDeferredRow(_audit_row(298), "", 1),), "unit"),
        ((SemanticRuntimeDeferredRow(_audit_row(298), "x" * 257, 1),), "unit"),
        ((SemanticRuntimeDeferredRow(_audit_row(298), "unit\nbad", 1),), "unit"),
    ),
)
def test_runtime_audit_authority_is_nonempty_nonnegative_and_canonical(
    rows: tuple[SemanticRuntimeDeferredRow, ...], message: str
) -> None:
    catalog = tuple(_audit_row(index) for index in range(298))
    with pytest.raises(FrontierSelectionError, match=message):
        freeze_audit_sample(
            catalog_deferred=iter(catalog),
            runtime_deferred=iter(rows),
            pending_runtime_deferred=(),
            checkpoint=_audit_checkpoint(),
        )


def test_pending_runtime_row_uses_same_transition_authority_contract() -> None:
    catalog = tuple(_audit_row(index) for index in range(299))
    pending = SemanticRuntimeDeferredRow(_audit_row(299), "unit-pending", 7)

    sample = freeze_audit_sample(
        catalog_deferred=iter(catalog),
        runtime_deferred=iter(()),
        pending_runtime_deferred=(pending,),
        checkpoint=_audit_checkpoint(),
    )

    assert len(sample.members) == 300


def test_frontier_run_and_unit_ids_bind_revision_policy_and_feature_digest(
    tmp_path: Path,
) -> None:
    """Catches stable IDs that omit one of the sealed selector authorities."""
    def build(name: str, revision: str, policy: str, feature_seed: str):
        path = tmp_path / f"{name}.sqlite"
        with SemanticCatalogWriter(path) as writer:
            _add_feature(
                writer,
                "project",
                source_id="source",
                lane="source",
                feature_digest=_digest(feature_seed),
            )
            plan = build_frontier_plan(
                writer.planning_session(FRONTIER_SELECTOR_VERSION),
                revision_id=revision,
                policy_hash=policy,
            )
            writer.seal()
        return plan

    base = build("base", "revision-1", "a" * 64, "feature-1")
    revision = build("revision", "revision-2", "a" * 64, "feature-1")
    policy = build("policy", "revision-1", "b" * 64, "feature-1")
    feature = build("feature", "revision-1", "a" * 64, "feature-2")

    assert len({base.run_id, revision.run_id, policy.run_id, feature.run_id}) == 4
    assert len(
        {
            base.units[0].unit_id,
            revision.units[0].unit_id,
            policy.units[0].unit_id,
            feature.units[0].unit_id,
        }
    ) == 4


def test_planning_session_rejects_wrong_version_finished_and_sealed_mutation(
    tmp_path: Path,
) -> None:
    """Catches selector writes escaping their one pre-seal planning authority."""
    path = tmp_path / "session.sqlite"
    with SemanticCatalogWriter(path) as writer:
        _add_feature(writer, "project", source_id="source", lane="source")
        session = writer.planning_session(FRONTIER_SELECTOR_VERSION)
        row = session.fair_seed_page(None, 1)[0]
        with pytest.raises(CatalogError, match="does not match"):
            session.append_selector_result(
                FrontierSelectorResult(
                    True,
                    row.node_id,
                    "selected",
                    _digest("node"),
                    canonical_json_text({"order": 0}),
                    0,
                    row.source_id,
                    row.coverage.value,
                    0,
                    "wrong-selector",
                    row.feature_digest,
                )
            )
        with pytest.raises(CatalogError, match="failed"):
            session.fair_seed_page(None, 1)
        session = writer.planning_session(FRONTIER_SELECTOR_VERSION)
        build_frontier_plan(
            session, revision_id="revision", policy_hash="a" * 64
        )
        with pytest.raises(CatalogError, match="finished"):
            session.fair_seed_page(None, 1)
        seal = writer.seal()
        with pytest.raises(CatalogError, match="sealed"):
            writer.planning_session(FRONTIER_SELECTOR_VERSION)

    with SemanticCatalogReader(path, seal) as reader:
        assert reader.frontier_page(None, 1)[0].node_id == "project"


@pytest.mark.parametrize(
    ("fault_point", "project_like"),
    (
        ("after_authority", True),
        ("after_selector_result", True),
        ("after_deferred", False),
        ("after_finish_summary", True),
    ),
)
def test_planning_session_rolls_back_every_owned_write_and_allows_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_point: str,
    project_like: bool,
) -> None:
    """Catches partial planning authority blocking a same-writer retry."""
    path = tmp_path / f"rollback-{fault_point}.sqlite"
    fired = False

    def inject(_session: object, point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError(f"fault:{point}")

    monkeypatch.setattr(
        SemanticCatalogPlanningSession, "_fault_point", inject, raising=False
    )
    with SemanticCatalogWriter(path) as writer:
        _add_feature(
            writer,
            "project",
            source_id="source",
            lane="source",
            project_like=project_like,
        )
        failed_session: SemanticCatalogPlanningSession | None = None
        with pytest.raises(RuntimeError, match=fault_point):
            failed_session = writer.planning_session(FRONTIER_SELECTOR_VERSION)
            build_frontier_plan(
                failed_session,
                revision_id="revision-failed",
                policy_hash="a" * 64,
            )
        if failed_session is not None:
            with pytest.raises(CatalogError, match="failed"):
                failed_session.fair_seed_page(None, 1)

        plan = build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-retry",
            policy_hash="a" * 64,
        )
        seal = writer.seal()

    assert fired is True
    assert plan.deferred_count == (0 if project_like else 1)
    with SemanticCatalogReader(path, seal) as reader:
        assert reader.frontier_page(None, 1)[0].node_id == "project"


def test_planning_session_abort_is_idempotent_and_exposes_no_connection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "abort.sqlite"
    with SemanticCatalogWriter(path) as writer:
        _add_feature(writer, "project", source_id="source", lane="source")
        session = writer.planning_session(FRONTIER_SELECTOR_VERSION)
        assert not hasattr(session, "connection")
        session.abort()
        session.abort()
        with pytest.raises(CatalogError, match="aborted"):
            session.fair_seed_page(None, 1)
        build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-retry",
            policy_hash="a" * 64,
        )


def test_failed_empty_planning_session_can_close_without_corrupting_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "close-after-failure.sqlite"
    writer = SemanticCatalogWriter(path)
    writer.add_node(NodeRecord("root", None, "source", ".", "source"))
    writer.add_baseline(
        BaselineRecord("root", BaselineCoverage.COMPLETE, (), (), 0, None)
    )

    def inject(_session: object, point: str) -> None:
        if point == "after_authority":
            raise RuntimeError("fault:after_authority")

    monkeypatch.setattr(
        SemanticCatalogPlanningSession, "_fault_point", inject, raising=False
    )
    with pytest.raises(RuntimeError, match="after_authority"):
        writer.planning_session(FRONTIER_SELECTOR_VERSION)
    writer.close()
    seal = writer.seal_value
    assert seal is not None
    with SemanticCatalogReader(path, seal) as reader:
        assert reader.node("root") is not None


def test_build_failure_aborts_planning_ownership_before_any_page(
    tmp_path: Path,
) -> None:
    path = tmp_path / "build-failure.sqlite"
    with SemanticCatalogWriter(path) as writer:
        _add_feature(writer, "project", source_id="source", lane="source")
        failed_session = writer.planning_session(FRONTIER_SELECTOR_VERSION)
        with pytest.raises(ValueError, match="revision_id"):
            build_frontier_plan(
                failed_session, revision_id="", policy_hash="a" * 64
            )
        with pytest.raises(CatalogError, match="aborted"):
            failed_session.fair_seed_page(None, 1)
        build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-retry",
            policy_hash="a" * 64,
        )


def test_active_planning_session_exclusively_owns_writer_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "exclusive-session.sqlite"
    with SemanticCatalogWriter(path) as writer:
        _add_feature(writer, "project", source_id="source", lane="source")
        session = writer.planning_session(FRONTIER_SELECTOR_VERSION)
        with pytest.raises(CatalogError, match="planning session is active"):
            writer.add_node(NodeRecord("intruder", None, "source", ".", "source"))
        session.abort()
        writer.add_node(NodeRecord("allowed", None, "source", ".", "source"))
        writer.add_baseline(
            BaselineRecord("allowed", BaselineCoverage.COMPLETE, (), (), 0, None)
        )
        build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-retry",
            policy_hash="a" * 64,
        )


@pytest.mark.parametrize(
    "failure_mode",
    ("rollback", "release_after_rollback", "release_on_finish"),
)
def test_savepoint_recovery_sql_failure_poisons_and_removes_private_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_mode: str,
) -> None:
    path = tmp_path / f"poison-{failure_mode}.sqlite"
    writer = SemanticCatalogWriter(path)
    _add_feature(writer, "project", source_id="source", lane="source")
    session = writer.planning_session(FRONTIER_SELECTOR_VERSION)

    def inject(_session: object, point: str) -> None:
        if failure_mode != "release_on_finish" and point == "after_selector_result":
            raise RuntimeError("primary selector failure")
        if failure_mode == "rollback" and point == "before_rollback_savepoint":
            raise sqlite3.OperationalError("injected rollback failure")
        if (
            failure_mode == "release_after_rollback"
            and point == "before_recovery_release"
        ):
            raise sqlite3.OperationalError("injected recovery release failure")
        if failure_mode == "release_on_finish" and point == "before_finish_release":
            raise sqlite3.OperationalError("injected finish release failure")

    monkeypatch.setattr(SemanticCatalogPlanningSession, "_fault_point", inject)
    message = "release" if failure_mode == "release_on_finish" else "recovery"
    with pytest.raises(CatalogError, match=message) as caught:
        build_frontier_plan(
            session,
            revision_id="revision-poison",
            policy_hash="a" * 64,
        )

    assert isinstance(caught.value.__cause__, sqlite3.Error)
    with pytest.raises(CatalogError, match="failed"):
        session.fair_seed_page(None, 1)
    assert writer.seal_value is None
    with pytest.raises(CatalogError, match="poisoned"):
        writer.add_node(NodeRecord("later", None, "source", ".", "source"))
    with pytest.raises(CatalogError, match="poisoned"):
        writer.planning_session(FRONTIER_SELECTOR_VERSION)
    with pytest.raises(CatalogError, match="poisoned"):
        writer.seal()
    writer.close()
    assert not path.exists()
    assert not tuple(tmp_path.glob(f".{path.name}.*"))

    with SemanticCatalogWriter(path) as fresh:
        fresh.add_node(NodeRecord("fresh-root", None, "source", ".", "source"))
        fresh.add_baseline(
            BaselineRecord(
                "fresh-root", BaselineCoverage.COMPLETE, (), (), 0, None
            )
        )
        seal = fresh.seal()
    with SemanticCatalogReader(path, seal) as reader:
        assert reader.node("fresh-root") is not None


def test_catalog_seal_rejects_adaptive_selector_cursor_not_bound_to_fair_order(
    tmp_path: Path,
) -> None:
    """Catches canonical-looking selector cursors reordered away from fair authority."""
    path = tmp_path / "cursor-mismatch.sqlite"
    with pytest.raises(CatalogError, match="cursor"):
        with SemanticCatalogWriter(path) as writer:
            _add_feature(writer, "project", source_id="source", lane="source")
            session = writer.planning_session(FRONTIER_SELECTOR_VERSION)
            row = session.fair_seed_page(None, 1)[0]
            session.append_selector_result(
                FrontierSelectorResult(
                    True,
                    row.node_id,
                    "selected_frontier_seed",
                    _digest("node"),
                    canonical_json_text(
                        {
                            "fair_round": row.fair_round,
                            "lane": row.top_level_lane,
                            "node_id": row.node_id,
                            "order": 7,
                            "selector_feature_digest": row.feature_digest,
                        }
                    ),
                    7,
                    row.source_id,
                    row.coverage.value,
                    0,
                    FRONTIER_SELECTOR_VERSION,
                    row.feature_digest,
                )
            )
            session.finish()


class _SyntheticMillionFeatureSession:
    selector_version = FRONTIER_SELECTOR_VERSION
    selector_digest = _digest("million-feature-selector")

    def __init__(self, decoy_count: int) -> None:
        self._decoy_count = decoy_count
        self._position = 0
        self._position_written = 0
        self._deferred_count = 0
        self._deferred_root = bytes(32)
        self._cursor_root = bytes(32)
        self.max_python_rows = 0
        self.sequence_sort_calls = 0
        self.temp_store_file = True

    def fair_seed_page(
        self, cursor: tuple[int, str] | None, limit: int
    ) -> list[FrontierPlanningRow]:
        del cursor
        end = min(self._position + limit, self._decoy_count + 2)
        page = [self._row(position) for position in range(self._position, end)]
        self._position = end
        self.max_python_rows = max(self.max_python_rows, len(page))
        return page

    def ancestor_projection(
        self, node_id: str, *, limit: int
    ) -> tuple[PlanningNodeProjection, ...]:
        assert limit == 64
        return (
            PlanningNodeProjection(
                node_id,
                None,
                "directory",
                node_id,
                "source-project",
                BaselineCoverage.COMPLETE,
                ("documentation", "entrypoint"),
                (),
                128,
                _digest(f"content:{node_id}"),
            ),
        )

    def append_selector_result(self, result: FrontierSelectorResult) -> None:
        assert result.cursor_order == self._position_written
        self._position_written += 1

    def append_deferred(self, row: SemanticDeferredAuditRow) -> None:
        self._deferred_count += 1
        self._deferred_root = hashlib.sha256(
            self._deferred_root + row.node_id.encode("utf-8")
        ).digest()
        self._cursor_root = hashlib.sha256(
            self._cursor_root + row.cursor_json.encode("utf-8")
        ).digest()

    def finish(self) -> DeferredSummary:
        return DeferredSummary(
            self._deferred_count,
            self._deferred_root.hex(),
            self._cursor_root.hex(),
        )

    def _row(self, position: int) -> FrontierPlanningRow:
        if position == 0:
            node_id, lane, project_like, fair_round = (
                "decoy-0000000",
                "lane-a-decoy",
                False,
                0,
            )
        elif position == 1:
            node_id, lane, project_like, fair_round = (
                "공모전-project",
                "lane-b-korean",
                True,
                0,
            )
        elif position == 2:
            node_id, lane, project_like, fair_round = (
                "small-project",
                "lane-c-small",
                True,
                0,
            )
        else:
            decoy_index = position - 2
            node_id, lane, project_like, fair_round = (
                f"decoy-{decoy_index:07}",
                "lane-a-decoy",
                False,
                decoy_index,
            )
        return FrontierPlanningRow(
            node_id,
            None,
            "directory",
            node_id,
            lane,
            fair_round,
            lane,
            project_like,
            False,
            False,
            False,
            False,
            _digest(f"feature:{node_id}"),
            BaselineCoverage.COMPLETE,
            ("documentation",) if not project_like else ("documentation", "entrypoint"),
            (),
            128,
            _digest(f"content:{node_id}"),
        )


def test_million_feature_stream_keeps_pages_bounded_and_decoy_cannot_starve() -> None:
    """Catches materializing all lane members or draining a million-row decoy first."""
    session = _SyntheticMillionFeatureSession(1_000_000)

    plan = build_frontier_plan(
        session, revision_id="revision-scale", policy_hash="a" * 64
    )

    assert [unit.target_node_ids[-1] for unit in plan.units] == [
        "공모전-project",
        "small-project",
    ]
    assert plan.deferred_count == 1_000_000
    assert not hasattr(plan, "deferred")
    assert session.temp_store_file is True
    assert session.max_python_rows <= 64
    assert session.sequence_sort_calls == 0


def test_phase_unit_byte_and_dependency_budgets_do_not_borrow(
    tmp_path: Path,
) -> None:
    """Catches expansion borrowing reserved audit capacity or silently oversizing units."""
    path = tmp_path / "budgets.sqlite"
    with SemanticCatalogWriter(path) as writer:
        for index in range(600):
            _add_feature(
                writer,
                f"project-{index:03}",
                source_id=f"source-{index % 3}",
                lane=f"lane-{index % 3}",
            )
        plan = build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-budget",
            policy_hash="a" * 64,
        )
        seal = writer.seal()

    counts = Counter(unit.phase for unit in plan.units)
    assert counts == {
        FrontierPhase.ORIENTATION: 64,
        FrontierPhase.EXPANSION: 384,
    }
    assert len(plan.units) == 448
    assert sum(
        unit.estimated_input_bytes
        for unit in plan.units
        if unit.phase is FrontierPhase.ORIENTATION
    ) <= 16 * 1024**2
    assert sum(
        unit.estimated_input_bytes
        for unit in plan.units
        if unit.phase is FrontierPhase.EXPANSION
    ) <= 96 * 1024**2
    assert all(unit.estimated_input_bytes <= MAX_UNIT_BYTES for unit in plan.units)
    assert all(len(unit.target_node_ids) <= 64 for unit in plan.units)
    assert all(len(unit.dependency_unit_ids) <= 64 for unit in plan.units)
    with SemanticCatalogReader(path, seal) as reader:
        assert plan.deferred_count == 152
        assert reader.deferred_summary(
            selector_version=FRONTIER_SELECTOR_VERSION
        ).count == 152


def test_audit_freeze_consumes_sealed_catalog_canonical_deferred_iterator(
    tmp_path: Path,
) -> None:
    """Catches audit ordering that disagrees with the catalog's canonical keyset order."""
    path = tmp_path / "audit-integration.sqlite"
    with SemanticCatalogWriter(path) as writer:
        for index in range(350):
            _add_feature(
                writer,
                f"decoy-{index:03}",
                source_id=f"source-{index % 2}",
                lane=f"lane-{index % 2}",
                project_like=False,
                roles=("documentation",),
            )
        plan = build_frontier_plan(
            writer.planning_session(FRONTIER_SELECTOR_VERSION),
            revision_id="revision-audit",
            policy_hash="a" * 64,
        )
        seal = writer.seal()

    with SemanticCatalogReader(path, seal) as reader:
        sample = freeze_audit_sample(
            catalog_deferred=reader.iter_frontier_deferred_for_audit(
                selector_version=FRONTIER_SELECTOR_VERSION,
                fetch_size=64,
            ),
            runtime_deferred=iter(()),
            pending_runtime_deferred=(),
            checkpoint=_audit_checkpoint(revision_id="revision-audit", approved_revision_id="revision-audit"),
        )

    assert plan.deferred_count == 350
    assert len(sample.members) == 300
