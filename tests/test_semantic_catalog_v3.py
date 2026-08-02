from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from portfolio_maker.domain.semantic_frontier import (
    BaselineCoverage,
    SemanticDeferredAuditRow,
)
from portfolio_maker.infrastructure.semantic_catalog import (
    BaselineRecord,
    CatalogError,
    FrontierFeatureRecord,
    FrontierDeferredRecord,
    NodeRecord,
    FrontierSelectorAuthority,
    FrontierSelectorResult,
    SemanticCatalogReader,
    SemanticCatalogWriter,
    canonical_json_text,
)


def _selector_result(
    node_id: str,
    *,
    selected: bool,
    selector_version: str = "selector-v1",
    source_id: str = "source-1",
    coverage: str = "complete",
    cursor_order: int = 0,
    relevance_rank: int = 0,
    feature_digest: str = "f" * 64,
) -> FrontierSelectorResult:
    return FrontierSelectorResult(
        node_id=node_id,
        selected=selected,
        reason="budget",
        node_digest="a" * 64,
        cursor_json=canonical_json_text({"lane": source_id, "offset": cursor_order}),
        cursor_order=cursor_order,
        source_id=source_id,
        coverage_stratum=coverage,
        relevance_rank=relevance_rank,
        selector_version=selector_version,
        selector_feature_digest=feature_digest,
    )


def _deferred_from_result(result: FrontierSelectorResult) -> FrontierDeferredRecord:
    return FrontierDeferredRecord(
        result.node_id,
        result.reason,
        result.node_digest,
        result.cursor_json,
        result.cursor_order,
        result.source_id,
        result.coverage_stratum,
        result.relevance_rank,
        result.selector_version,
        result.selector_feature_digest,
    )


def test_catalog_keeps_every_node_and_queries_children_by_keyset_page(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        for index in range(10_000):
            writer.add_node(
                NodeRecord(
                    f"n-{index:05}", "root", "file", f"f-{index}", "source-1"
                )
            )
        writer.add_baseline(
            BaselineRecord("root", BaselineCoverage.COMPLETE, (), (), 0, None)
        )
        seal = writer.seal()

    with SemanticCatalogReader(path, seal) as reader:
        assert reader.node_count() == 10_001
        assert [row.node_id for row in reader.children_page("root", None, 64)][:2] == [
            "n-00000",
            "n-00001",
        ]
        assert len(reader.children_page("root", "n-00063", 64)) == 64


@dataclass(frozen=True)
class _DeferredCatalog:
    reader: SemanticCatalogReader
    selector_version: str
    feature_digest: str
    expected_audit_row: SemanticDeferredAuditRow


@pytest.fixture
def catalog_with_frontier_deferred(tmp_path: Path) -> _DeferredCatalog:
    path = tmp_path / "catalog.sqlite"
    selector_version = "selector-v1"
    feature_digest = "f" * 64
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        writer.add_node(NodeRecord("child", "root", "file", "child.py", "source-1"))
        writer.add_baseline(
            BaselineRecord("child", BaselineCoverage.COMPLETE, (), (), 32, None)
        )
        writer.add_frontier_feature(
            FrontierFeatureRecord(
                "child",
                "features-v1",
                project_like=True,
                ambiguous=False,
                independent_child=True,
                evidence_linked=False,
                uncertainty=False,
                top_level_lane="source-1",
                feature_digest=feature_digest,
            )
        )
        writer.add_frontier_selector_authority(
            FrontierSelectorAuthority(selector_version, "e" * 64)
        )
        expected = SemanticDeferredAuditRow(
            "child",
            "budget",
            "a" * 64,
            canonical_json_text({"lane": "source-1", "offset": 64}),
            0,
            "source-1",
            "complete",
            0,
            selector_version,
            feature_digest,
        )
        result = FrontierSelectorResult(False, *expected.__dict__.values())
        writer.add_frontier_selector_result(result)
        writer.add_frontier_deferred(_deferred_from_result(result))
        seal = writer.seal()
    return _DeferredCatalog(
        SemanticCatalogReader(path, seal), selector_version, feature_digest, expected
    )


def test_catalog_frontier_deferred_audit_row_round_trips_exact_selector_fields(
    catalog_with_frontier_deferred: _DeferredCatalog,
) -> None:
    rows = list(
        catalog_with_frontier_deferred.reader.iter_frontier_deferred_for_audit(
            selector_version=catalog_with_frontier_deferred.selector_version,
            fetch_size=64,
        )
    )
    assert rows == [catalog_with_frontier_deferred.expected_audit_row]
    assert rows[0].cursor_json == canonical_json_text({"lane": "source-1", "offset": 64})
    assert rows[0].source_id == "source-1"
    assert rows[0].coverage_stratum == "complete"
    assert rows[0].relevance_rank == 0
    assert rows[0].selector_feature_digest == catalog_with_frontier_deferred.feature_digest


def test_catalog_builds_file_backed_fair_rounds_and_pages_by_keyset(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        for lane_index, lane in enumerate(("a", "b")):
            for index in range(3):
                node_id = f"{lane}-{index}"
                writer.add_node(NodeRecord(node_id, "root", "file", node_id, lane))
                writer.add_baseline(
                    BaselineRecord(node_id, BaselineCoverage.COMPLETE, (), (), 1, None)
                )
                writer.add_frontier_feature(
                    FrontierFeatureRecord(
                        node_id,
                        "features-v1",
                        project_like=True,
                        ambiguous=False,
                        independent_child=False,
                        evidence_linked=False,
                        uncertainty=False,
                        top_level_lane=lane,
                        feature_digest=f"{index:064x}",
                    )
                )
        writer.add_frontier_selector_authority(
            FrontierSelectorAuthority("selector-v1", "e" * 64)
        )
        for lane_index, lane in enumerate(("a", "b")):
            for index in range(3):
                writer.add_frontier_selector_result(
                    _selector_result(
                        f"{lane}-{index}",
                        selected=True,
                        cursor_order=lane_index * 3 + index,
                        source_id=lane,
                        feature_digest=f"{index:064x}",
                    )
                )
        seal = writer.seal()
    with SemanticCatalogReader(path, seal) as reader:
        first = reader.frontier_page(None, 4)
        assert [(row.fair_round, row.top_level_lane) for row in first] == [
            (0, "a"),
            (0, "b"),
            (1, "a"),
            (1, "b"),
        ]
        assert [row.node_id for row in reader.frontier_page(first[-1].cursor, 4)] == [
            "a-2",
            "b-2",
        ]


def test_catalog_rejects_noncanonical_deferred_cursor_and_feature_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    with pytest.raises(CatalogError, match="selector"):
        with SemanticCatalogWriter(path) as writer:
            writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
            writer.add_baseline(
                BaselineRecord("root", BaselineCoverage.COMPLETE, (), (), 1, None)
            )
            writer.add_frontier_feature(
                FrontierFeatureRecord(
                    "root", "features-v1", False, False, False, False, False, "source-1", "f" * 64
                )
            )
            writer.add_frontier_selector_authority(
                FrontierSelectorAuthority("selector-v1", "e" * 64)
            )
            result = _selector_result("root", selected=False)
            writer.add_frontier_selector_result(result)
            with pytest.raises(CatalogError, match="canonical"):
                writer.add_frontier_deferred(
                    FrontierDeferredRecord(
                        "root", "budget", "a" * 64, '{"offset":64, "lane":"source-1"}', 0,
                        "source-1", "complete", 0, "selector-v1", "f" * 64,
                    )
                )
            writer.add_frontier_deferred(
                FrontierDeferredRecord(
                    "root", "budget", "a" * 64, canonical_json_text({"lane": "source-1", "offset": 0}), 0,
                    "source-1", "complete", 0, "selector-v1", "0" * 64,
                )
            )


def test_catalog_reader_fails_closed_for_tamper_schema_drift_and_sidecars(tmp_path: Path) -> None:
    def sealed_path(name: str) -> tuple[Path, object]:
        path = tmp_path / name
        with SemanticCatalogWriter(path) as writer:
            writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
            seal = writer.seal()
        return path, seal

    tampered, tampered_seal = sealed_path("tampered.sqlite")
    with tampered.open("r+b") as handle:
        handle.seek(-1, 2)
        byte = handle.read(1)
        handle.seek(-1, 2)
        handle.write(bytes([byte[0] ^ 1]))
    with pytest.raises(CatalogError, match="file sha256"):
        SemanticCatalogReader(tampered, tampered_seal)

    drifted, drifted_seal = sealed_path("drifted.sqlite")
    with sqlite3.connect(drifted) as connection:
        connection.execute("CREATE TABLE drift (value TEXT)")
    with pytest.raises(CatalogError, match="schema"):
        SemanticCatalogReader(drifted, drifted_seal)

    sidecar, sidecar_seal = sealed_path("sidecar.sqlite")
    sidecar.with_name(f"{sidecar.name}-wal").write_bytes(b"not-authoritative")
    with pytest.raises(CatalogError, match="sidecar"):
        SemanticCatalogReader(sidecar, sidecar_seal)


def test_catalog_seal_blocks_later_writes_and_reader_is_query_only(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        seal = writer.seal()
        with pytest.raises(CatalogError, match="sealed"):
            writer.add_node(NodeRecord("later", None, "source", "", "source-1"))
    with SemanticCatalogReader(path, seal) as reader:
        assert reader.query_only is True
        with pytest.raises(sqlite3.OperationalError):
            reader.connection.execute("DELETE FROM nodes")


def test_catalog_seals_only_the_exact_eligible_minus_selected_deferred_set(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        writer.add_frontier_selector_authority(
            FrontierSelectorAuthority("selector-v1", "e" * 64)
        )
        expected_deferred: list[FrontierSelectorResult] = []
        for index in range(3):
            node_id = f"node-{index}"
            writer.add_node(NodeRecord(node_id, "root", "file", node_id, "source-1"))
            writer.add_baseline(
                BaselineRecord(node_id, BaselineCoverage.COMPLETE, (), (), 1, None)
            )
            writer.add_frontier_feature(
                FrontierFeatureRecord(
                    node_id, "features-v1", True, False, False, False, False,
                    "source-1", f"{index:064x}",
                )
            )
            result = _selector_result(
                node_id,
                selected=index == 0,
                cursor_order=index,
                relevance_rank=index,
                feature_digest=f"{index:064x}",
            )
            writer.add_frontier_selector_result(result)
            if not result.selected:
                writer.add_frontier_deferred(_deferred_from_result(result))
                expected_deferred.append(result)
        seal = writer.seal()
    with SemanticCatalogReader(path, seal) as reader:
        assert [row.node_id for row in reader.frontier_deferred_page("selector-v1", None, 64)] == [
            result.node_id for result in expected_deferred
        ]


@pytest.mark.parametrize(
    "mismatch",
    ("missing", "extra", "selector", "cursor", "source", "order", "relevance", "feature"),
)
def test_catalog_rejects_any_deferred_selector_set_or_row_mismatch(
    tmp_path: Path, mismatch: str
) -> None:
    path = tmp_path / f"{mismatch}.sqlite"
    with pytest.raises(CatalogError, match="selector"):
        with SemanticCatalogWriter(path) as writer:
            writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
            writer.add_frontier_selector_authority(
                FrontierSelectorAuthority("selector-v1", "e" * 64)
            )
            results: list[FrontierSelectorResult] = []
            for index in range(3):
                node_id = f"node-{index}"
                writer.add_node(NodeRecord(node_id, "root", "file", node_id, "source-1"))
                writer.add_baseline(
                    BaselineRecord(node_id, BaselineCoverage.COMPLETE, (), (), 1, None)
                )
                writer.add_frontier_feature(
                    FrontierFeatureRecord(
                        node_id, "features-v1", True, False, False, False, False,
                        "source-1", f"{index:064x}",
                    )
                )
                result = _selector_result(
                    node_id, selected=index == 0, cursor_order=index,
                    relevance_rank=index, feature_digest=f"{index:064x}",
                )
                writer.add_frontier_selector_result(result)
                results.append(result)
            deferred = [_deferred_from_result(result) for result in results[1:]]
            if mismatch == "missing":
                deferred.pop()
            elif mismatch == "extra":
                deferred.append(_deferred_from_result(results[0]))
            elif mismatch == "selector":
                deferred[0] = FrontierDeferredRecord(
                    *deferred[0].__dict__.values(),
                )
                deferred[0] = FrontierDeferredRecord(
                    deferred[0].node_id, deferred[0].reason, deferred[0].node_digest,
                    deferred[0].cursor_json, deferred[0].cursor_order, deferred[0].source_id,
                    deferred[0].coverage_stratum, deferred[0].relevance_rank, "selector-v2",
                    deferred[0].selector_feature_digest,
                )
            elif mismatch == "cursor":
                deferred[0] = FrontierDeferredRecord(
                    deferred[0].node_id, deferred[0].reason, deferred[0].node_digest,
                    canonical_json_text({"lane": "source-1", "offset": 99}),
                    deferred[0].cursor_order, deferred[0].source_id, deferred[0].coverage_stratum,
                    deferred[0].relevance_rank, deferred[0].selector_version,
                    deferred[0].selector_feature_digest,
                )
            elif mismatch == "source":
                deferred[0] = FrontierDeferredRecord(
                    deferred[0].node_id, deferred[0].reason, deferred[0].node_digest,
                    deferred[0].cursor_json, deferred[0].cursor_order, "other-source",
                    deferred[0].coverage_stratum, deferred[0].relevance_rank,
                    deferred[0].selector_version, deferred[0].selector_feature_digest,
                )
            elif mismatch == "order":
                deferred[0] = FrontierDeferredRecord(
                    deferred[0].node_id, deferred[0].reason, deferred[0].node_digest,
                    deferred[0].cursor_json, 99, deferred[0].source_id,
                    deferred[0].coverage_stratum, deferred[0].relevance_rank,
                    deferred[0].selector_version, deferred[0].selector_feature_digest,
                )
            elif mismatch == "relevance":
                deferred[0] = FrontierDeferredRecord(
                    deferred[0].node_id, deferred[0].reason, deferred[0].node_digest,
                    deferred[0].cursor_json, deferred[0].cursor_order, deferred[0].source_id,
                    deferred[0].coverage_stratum, 99, deferred[0].selector_version,
                    deferred[0].selector_feature_digest,
                )
            elif mismatch == "feature":
                deferred[0] = FrontierDeferredRecord(
                    deferred[0].node_id, deferred[0].reason, deferred[0].node_digest,
                    deferred[0].cursor_json, deferred[0].cursor_order, deferred[0].source_id,
                    deferred[0].coverage_stratum, deferred[0].relevance_rank,
                    deferred[0].selector_version, "0" * 64,
                )
            for row in deferred:
                writer.add_frontier_deferred(row)


def test_catalog_deferred_pages_resume_per_selector_without_duplicates_or_unbounded_selects(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.sqlite"
    selector_versions = ("selector-v1", "selector-v2")
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        for selector_version in selector_versions:
            writer.add_frontier_selector_authority(
                FrontierSelectorAuthority(selector_version, "e" * 64)
            )
        expected: dict[str, list[str]] = {version: [] for version in selector_versions}
        for index in range(130):
            node_id = f"node-{index:03}"
            source_id = f"source-{index % 2}"
            writer.add_node(NodeRecord(node_id, "root", "file", node_id, source_id))
            writer.add_baseline(
                BaselineRecord(node_id, BaselineCoverage.COMPLETE, (), (), 1, None)
            )
            writer.add_frontier_feature(
                FrontierFeatureRecord(
                    node_id, "features-v1", True, False, False, False, False,
                    source_id, f"{index:064x}",
                )
            )
            for selector_index, selector_version in enumerate(selector_versions):
                result = _selector_result(
                    node_id,
                    selected=index % 3 == selector_index,
                    selector_version=selector_version,
                    source_id=source_id,
                    cursor_order=index,
                    relevance_rank=index % 5,
                    feature_digest=f"{index:064x}",
                )
                writer.add_frontier_selector_result(result)
                if not result.selected:
                    writer.add_frontier_deferred(_deferred_from_result(result))
                    expected[selector_version].append(node_id)
        seal = writer.seal()
    with SemanticCatalogReader(path, seal) as reader:
        trace: list[str] = []
        reader.connection.set_trace_callback(trace.append)
        for selector_version in selector_versions:
            cursor = None
            observed: list[str] = []
            while True:
                page = reader.frontier_deferred_page(selector_version, cursor, 11)
                if not page:
                    break
                observed.extend(row.node_id for row in page)
                cursor = reader.frontier_deferred_cursor(page[-1])
            assert sorted(observed) == sorted(expected[selector_version])
            assert len(observed) == len(set(observed))
            assert reader.deferred_summary(selector_version=selector_version).count == len(observed)
        deferred_sql = [sql for sql in trace if "FROM frontier_deferred" in sql]
        assert deferred_sql and all("LIMIT" in sql.upper() for sql in deferred_sql)


def test_catalog_reader_rejects_logical_root_tamper_even_before_file_sha_check(tmp_path: Path) -> None:
    path = tmp_path / "logical-root.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        seal = writer.seal()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE nodes SET display_name = 'tampered' WHERE node_id = 'root'")
    with pytest.raises(CatalogError, match="logical root"):
        SemanticCatalogReader(path, seal)
