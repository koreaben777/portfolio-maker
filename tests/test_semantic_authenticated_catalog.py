from dataclasses import asdict
import json
import sqlite3

import pytest

from portfolio_maker.domain.semantic_frontier import BaselineCoverage
from portfolio_maker.infrastructure.semantic_catalog import (
    SemanticCatalogWriter, SemanticCatalogReader, NodeRecord, BaselineRecord, CatalogSeal,
)
from portfolio_maker.infrastructure.semantic_pack import PMPackWriter, PackSeal
from portfolio_maker.infrastructure.semantic_auth_index import AuthIndexError, AuthIndexReader, AuthEntry, build_auth_index
from portfolio_maker.infrastructure.semantic_authenticated_catalog import (
    AuthenticatedCatalogReader, build_authenticated_catalog, verify_authenticated_catalog_full,
)
from portfolio_maker.workspace import WorkspacePaths
from test_semantic_lifecycle_v3 import _prepared_frontier_run


@pytest.fixture
def authenticated_three_children(tmp_path):
    with SemanticCatalogWriter(tmp_path / "catalog.sqlite") as writer:
        writer.add_node(NodeRecord("parent", None, "source", "", "source-1"))
        for node in ("c", "a", "b"):
            writer.add_node(NodeRecord(node, "parent", "file", node, "source-1"))
            writer.add_baseline(BaselineRecord(node, BaselineCoverage.COMPLETE, ("code",), (), 0, None))
        catalog = writer.seal()
    with PMPackWriter(tmp_path / "packs" / "structural.pm-pack") as writer:
        pack = writer.seal()
    seal = build_authenticated_catalog(tmp_path, catalog, pack, "0" * 32)
    return tmp_path, catalog, pack, seal


def test_typed_children_keep_exact_order_without_runtime_sql(authenticated_three_children, monkeypatch):
    revision, catalog, pack, seal = authenticated_three_children
    verify_authenticated_catalog_full(revision, catalog, pack, seal)
    def forbidden(*args, **kwargs):
        raise AssertionError("runtime SQL forbidden")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with AuthenticatedCatalogReader(revision, seal) as reader:
        assert [n.node_id for n in reader.children_page("parent", None, 2)] == ["a", "b"]
        assert [n.node_id for n in reader.children_page("parent", "b", 2)] == ["c"]
        assert [n.node_id for n in reader.representative_page("parent", None, 2)] == ["a", "b"]
        assert reader.node("a") == NodeRecord("a", "parent", "file", "a", "source-1", display_name="a")
        assert reader.baseline("a").roles == ("code",)
        assert reader.node("missing") is None


def test_real_frontier_projection_matches_full_reader(workspace):
    _, prepared = _prepared_frontier_run(workspace, extra_project=True)
    path = WorkspacePaths.from_root(workspace).semantic_v3_manifest_path(prepared.revision_id)
    manifest = json.loads(path.read_text())
    catalog = CatalogSeal(**{k:manifest["catalog"][k] for k in CatalogSeal.__dataclass_fields__})
    pack = PackSeal(**{k:manifest["pack"][k] for k in PackSeal.__dataclass_fields__})
    from portfolio_maker.application.semantic_prepare_v3 import _authenticated_seal
    seal = _authenticated_seal(manifest)
    selector = "adaptive-frontier-v1"
    with SemanticCatalogReader(path.parent / "catalog.sqlite", catalog) as full, AuthenticatedCatalogReader(path.parent, seal) as reader:
        nodes = full.nodes_page(None, 64)
        for node in nodes:
            assert reader.node(node.node_id) == node
            assert reader.baseline(node.node_id) == full.baseline(node.node_id)
            assert reader.representative_page(node.node_id, None, 64) == full.representative_page(node.node_id, None, 64)
        expected = list(full.iter_frontier_deferred_for_audit(selector_version=selector, fetch_size=2))
        assert expected
        assert list(reader.iter_frontier_deferred_for_audit(selector_version=selector, fetch_size=2)) == expected
        ids = tuple(r.node_id for r in reversed(expected))
        assert reader.runtime_deferred_projection(selector, ids) == full.runtime_deferred_projection(selector, ids)
        assert reader.runtime_deferred_projection(selector, ()) == ()
        assert reader.deferred_summary(selector_version=selector) == full.deferred_summary(selector_version=selector)
        with pytest.raises(ValueError):
            reader.runtime_deferred_projection(selector, ("absent",))
        with pytest.raises(ValueError):
            reader.runtime_deferred_projection("absent", ())
    verify_authenticated_catalog_full(path.parent, catalog, pack, seal)


@pytest.mark.parametrize("filename", ["catalog.sqlite", "packs/structural.pm-pack"])
def test_unused_original_corruption_partial_allowed_full_rejects(authenticated_three_children, filename):
    revision, catalog, pack, seal = authenticated_three_children
    with (revision / filename).open("ab") as stream:
        stream.write(b"corrupt")
    with AuthenticatedCatalogReader(revision, seal) as reader:
        assert reader.node("a").node_id == "a"
    with pytest.raises(ValueError):
        verify_authenticated_catalog_full(revision, catalog, pack, seal)


def test_full_rejects_self_consistent_index_with_missing_projection(authenticated_three_children):
    revision, catalog, pack, seal = authenticated_three_children
    with AuthIndexReader(revision / "authenticated.pm-pack", seal) as reader:
        entries = reader.page((), None, 64).entries
    replacement = revision / "replacement"
    wrong = build_auth_index(replacement, (entry for entry in entries if entry.key != ("children", "parent", "b")), generation_id=seal.generation_id)
    replacement.replace(revision / "authenticated.pm-pack")
    with pytest.raises(AuthIndexError):
        verify_authenticated_catalog_full(revision, catalog, pack, wrong)


def test_full_rejects_unreferenced_pack_tail(authenticated_three_children):
    revision, catalog, pack, seal = authenticated_three_children
    with (revision / "authenticated.pm-pack").open("ab") as stream:
        stream.write(b"tail")
    with pytest.raises(ValueError):
        verify_authenticated_catalog_full(revision, catalog, pack, seal)


@pytest.mark.parametrize("namespace", ["node", "baseline", "children", "representative"])
def test_authenticated_projection_rejects_key_node_mismatch(authenticated_three_children, namespace):
    revision, _, _, seal = authenticated_three_children
    with AuthIndexReader(revision / "authenticated.pm-pack", seal) as reader:
        entries = reader.page((), None, 64).entries
    key = (namespace, "parent", "a") if namespace in ("children", "representative") else (namespace, "a")
    altered = [AuthEntry(e.key, {**e.value, "node_id": "b"}) if e.key == key else e for e in entries]
    replacement = revision / "replacement"
    wrong = build_auth_index(replacement, altered, generation_id=seal.generation_id)
    replacement.replace(revision / "authenticated.pm-pack")
    with AuthenticatedCatalogReader(revision, wrong) as reader, pytest.raises(AuthIndexError):
        if namespace == "node":
            reader.node("a")
        elif namespace == "baseline":
            reader.baseline("a")
        elif namespace == "children":
            reader.children_page("parent", None, 2)
        else:
            reader.representative_page("parent", None, 2)


def test_authenticated_children_reject_wrong_parent_binding(authenticated_three_children):
    revision, _, _, seal = authenticated_three_children
    with AuthIndexReader(revision / "authenticated.pm-pack", seal) as reader:
        entries = reader.page((), None, 64).entries
    altered = [AuthEntry(e.key, {**e.value, "parent_node_id": "elsewhere"}) if e.key == ("children", "parent", "a") else e for e in entries]
    replacement = revision / "replacement"
    wrong = build_auth_index(replacement, altered, generation_id=seal.generation_id)
    replacement.replace(revision / "authenticated.pm-pack")
    with AuthenticatedCatalogReader(revision, wrong) as reader, pytest.raises(AuthIndexError):
        reader.children_page("parent", None, 2)


@pytest.mark.parametrize("namespace,field,bad", [
    ("node", "source_id", False),
    ("node", "relative_hierarchy", []),
    ("node", "structural_ref", {"offset": True, "length": 4, "sha256": "0" * 64}),
    ("baseline", "inspected_bytes", True),
    ("baseline", "inspected_bytes", -1),
    ("baseline", "roles", "code"),
    ("baseline", "topics", [False]),
    ("baseline", "sampled_ranges", [[0, True]]),
    ("baseline", "sampled_ranges", [[5, 2]]),
    ("baseline", "sample_sha256", "not-a-digest"),
    ("representative", "independent_child", 1),
    ("representative", "roles", [2]),
])
def test_authenticated_projection_rejects_malformed_typed_value(authenticated_three_children, namespace, field, bad):
    revision, _, _, seal = authenticated_three_children
    with AuthIndexReader(revision / "authenticated.pm-pack", seal) as reader:
        entries = reader.page((), None, 64).entries
    key = (namespace, "parent", "a") if namespace == "representative" else (namespace, "a")
    altered = [AuthEntry(e.key, {**e.value, field: bad}) if e.key == key else e for e in entries]
    replacement = revision / "replacement"
    wrong = build_auth_index(replacement, altered, generation_id=seal.generation_id)
    replacement.replace(revision / "authenticated.pm-pack")
    with AuthenticatedCatalogReader(revision, wrong) as reader, pytest.raises(AuthIndexError):
        if namespace == "representative":
            reader.representative_page("parent", None, 2)
        else:
            getattr(reader, namespace)("a")


@pytest.mark.parametrize("kind,field,bad", [
    ("summary", "count", True), ("summary", "root", "bad"),
    ("runtime-deferred", "cursor_order", True),
    ("runtime-deferred", "node_digest", "bad"),
    ("runtime-deferred", "coverage_stratum", "bad"),
    ("runtime-deferred", "cursor_json", "{}"),
    ("audit-deferred", "node_id", "other"),
    ("audit-deferred", "relevance_rank", 3),
])
def test_authenticated_deferred_rejects_malformed_value(tmp_path, kind, field, bad):
    selector = "adaptive-frontier-v1"
    summary = {"count": 1, "root": "0" * 64, "cursor_root": "1" * 64}
    row = {"node_id": "a", "reason": "frontier_budget_or_unit_limit", "node_digest": "2" * 64,
           "cursor_json": '{"depth_band":"root","fair_round":0,"lane":"lane","node_id":"a","order":0,"role_band":"other","selector_feature_digest":"' + "3" * 64 + '","size_band":"empty"}',
           "cursor_order": 0, "source_id": "source-1", "coverage_stratum": "complete",
           "relevance_rank": 0, "selector_version": selector, "selector_feature_digest": "3" * 64}
    key = (kind, selector, "0000000000000000", "complete", "source-1", "0000000000000000", "a") if kind == "audit-deferred" else (kind, selector, "a")
    entries = [AuthEntry(("summary", selector), {**summary, field: bad} if kind == "summary" else summary)]
    if kind != "summary":
        entries.append(AuthEntry(key, {**row, field: bad}))
    seal = build_auth_index(tmp_path / "authenticated.pm-pack", sorted(entries, key=lambda e:e.key), generation_id="0" * 32)
    with AuthenticatedCatalogReader(tmp_path, seal) as reader, pytest.raises(AuthIndexError):
        if kind == "summary":
            reader.deferred_summary(selector_version=selector)
        elif kind == "runtime-deferred":
            reader.runtime_deferred_projection(selector, ("a",))
        else:
            list(reader.iter_frontier_deferred_for_audit(selector_version=selector, fetch_size=2))


@pytest.mark.parametrize("filename", ["authenticated.pm-pack", "catalog.sqlite", "packs/structural.pm-pack"])
def test_full_correspondence_rejects_file_change_after_initial_seals(authenticated_three_children, monkeypatch, filename):
    import portfolio_maker.infrastructure.semantic_authenticated_catalog as module
    revision, catalog, pack, seal = authenticated_three_children
    original = module._all_entries

    def change_after_entries(reader):
        yield from original(reader)
        path = revision / filename
        # Even a byte-identical rename invalidates this verification snapshot.
        replacement = path.with_name("snapshot-replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)

    monkeypatch.setattr(module, "_all_entries", change_after_entries)
    with pytest.raises((AuthIndexError, OSError)):
        verify_authenticated_catalog_full(revision, catalog, pack, seal)
