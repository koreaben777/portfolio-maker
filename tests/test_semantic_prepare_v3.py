from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import portfolio_maker.infrastructure.semantic_source_reader as source_reader
from portfolio_maker.application.approval import SourceApproval
from portfolio_maker.domain.semantic_frontier import PackRecordRef
from portfolio_maker.infrastructure.semantic_catalog import SemanticCatalogReader
from portfolio_maker.infrastructure.semantic_crawler import (
    StructuralEventKind,
    StreamingStructureSink,
    iter_local_structure_events,
)
from portfolio_maker.infrastructure.semantic_pack import PMPackReader
from portfolio_maker.infrastructure.policy import SourcePathPolicyError
from portfolio_maker.infrastructure.semantic_source_reader import (
    SemanticSourceReadError,
    SemanticSourceChangedError,
    read_verified_representative,
)


def _approval_for(root: Path) -> SourceApproval:
    return SourceApproval(
        approved_source_uris=(root.resolve().as_uri(),),
        legacy_forbidden_paths=(),
        excluded_directories=(),
        excluded_repositories=(),
        private_sources_allowed=False,
        allowed_repositories=(),
        excluded_file_patterns=(),
        approved_github_activity_urls=(),
        approved_private_github_activity_urls=(),
    )


def test_verified_representative_uses_same_descriptor_for_hash_and_slice(
    tmp_path: Path,
) -> None:
    """Breaks if the hash and sample are read through separate path opens."""
    source = tmp_path / "source.md"
    source.write_text("alpha\nbeta\ngamma", encoding="utf-8")

    observed = read_verified_representative(
        source,
        ranges=((0, 5),),
        max_materialized_bytes=64,
    )

    assert observed.full_content_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert observed.slices == (b"alpha",)
    assert observed.pre_identity == observed.post_identity


def test_verified_representative_rejects_same_inode_tail_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if post-read identity ignores an in-place tail rewrite."""
    source = tmp_path / "source.md"
    source.write_bytes(b"alpha\nbeta\ngamma")
    original = source_reader._hash_and_collect_ranges

    def rewrite_tail_after_read(
        descriptor: int,
        ranges: tuple[tuple[int, int], ...],
        max_materialized_bytes: int,
    ):
        result = original(descriptor, ranges, max_materialized_bytes)
        with source.open("r+b") as handle:
            handle.seek(-5, os.SEEK_END)
            handle.write(b"delta")
            handle.flush()
            os.fsync(handle.fileno())
        return result

    monkeypatch.setattr(source_reader, "_hash_and_collect_ranges", rewrite_tail_after_read)

    with pytest.raises(SemanticSourceChangedError, match="changed during verified read"):
        read_verified_representative(
            source,
            ranges=((0, 5),),
            max_materialized_bytes=64,
        )


def test_verified_representative_does_not_follow_symbolic_link(tmp_path: Path) -> None:
    """Breaks if the verified reader follows a replaced representative path."""
    target = tmp_path / "target.md"
    target.write_text("outside", encoding="utf-8")
    link = tmp_path / "source.md"
    link.symlink_to(target)

    with pytest.raises(SourcePathPolicyError):
        read_verified_representative(
            link,
            ranges=((0, 5),),
            max_materialized_bytes=64,
        )


def test_verified_representative_masks_os_error_locator_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if public source-reader errors disclose source locators."""
    source = tmp_path / "private-source.md"
    private_locator = str(source)
    relative_locator = "private-source.md"

    def fail_open(_path: Path) -> int:
        raise OSError(f"cannot open {private_locator} or {relative_locator}")

    monkeypatch.setattr(source_reader, "_open_regular_file", fail_open)

    with pytest.raises(SemanticSourceReadError) as captured:
        read_verified_representative(
            source,
            ranges=((0, 5),),
            max_materialized_bytes=64,
        )

    error = captured.value
    assert private_locator not in str(error)
    assert private_locator not in repr(error)
    assert relative_locator not in str(error)
    assert relative_locator not in repr(error)
    assert error.args == ("semantic source could not be read",)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_streaming_sink_persists_exact_node_edge_and_baseline_authorities(
    tmp_path: Path,
) -> None:
    """Breaks if the production stream only yields events without Task 3 authority."""
    root = tmp_path / "source"
    (root / "project" / "src").mkdir(parents=True)
    (root / "project" / "README.md").write_text("purpose", encoding="utf-8")
    (root / "project" / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = StreamingStructureSink(pack_path, catalog_path)

    events = list(iter_local_structure_events(root, _approval_for(root), sink=sink))
    artifacts = sink.seal()

    node_events = [event for event in events if event.kind is StructuralEventKind.NODE_SEEN]
    pack_reader = PMPackReader(pack_path)
    with SemanticCatalogReader(catalog_path, artifacts.catalog_seal) as catalog:
        nodes = catalog.nodes_page(None, 64)
        baselines = [catalog.baseline(node.node_id) for node in nodes]
        edge_rows = catalog.connection.execute(
            """
            SELECT payload_ref_id, node_id, byte_offset, byte_length, payload_sha256
            FROM payload_refs WHERE pack_kind = 'structural-edge' ORDER BY payload_ref_id
            """
        ).fetchall()

    assert len(nodes) == len(node_events)
    assert all(node.structural_ref is not None for node in nodes)
    assert all(baseline is not None and baseline.payload_ref is not None for baseline in baselines)
    assert len(edge_rows) == len(nodes) - 1

    nodes_by_id = {node.node_id: node for node in nodes}
    references = []
    for node in nodes:
        assert node.structural_ref is not None
        references.append(node.structural_ref)
        assert pack_reader.read(node.structural_ref)["kind"] == "structural-node"
    for baseline in baselines:
        assert baseline is not None and baseline.payload_ref is not None
        references.append(baseline.payload_ref)
        assert pack_reader.read(baseline.payload_ref)["kind"] == "local-baseline"
    for row in edge_rows:
        reference = PackRecordRef(row[2], row[3], row[4])
        references.append(reference)
        edge = pack_reader.read(reference)
        assert edge["kind"] == "structural-edge"
        assert edge["child_node_id"] == row[1]
        assert edge["parent_node_id"] == nodes_by_id[row[1]].parent_node_id

    assert len(references) == artifacts.pack_seal.record_count
    assert len({(ref.offset, ref.length, ref.sha256) for ref in references}) == len(references)


def test_source_change_aborts_streaming_catalog_and_pack_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if TOCTOU content can leave any structural authority published."""
    root = tmp_path / "source"
    root.mkdir()
    source = root / "changing.md"
    source.write_bytes(b"alpha\nbeta\ngamma")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    original = source_reader._hash_and_collect_ranges

    def rewrite_after_hash(
        descriptor: int,
        ranges: tuple[tuple[int, int], ...],
        max_materialized_bytes: int,
    ):
        result = original(descriptor, ranges, max_materialized_bytes)
        with source.open("r+b") as handle:
            handle.seek(-5, os.SEEK_END)
            handle.write(b"delta")
            handle.flush()
            os.fsync(handle.fileno())
        return result

    monkeypatch.setattr(source_reader, "_hash_and_collect_ranges", rewrite_after_hash)
    sink = StreamingStructureSink(pack_path, catalog_path)
    events = list(iter_local_structure_events(root, _approval_for(root), sink=sink))

    assert any(
        event.baseline is not None and event.baseline.source_changed
        for event in events
        if event.kind is StructuralEventKind.FILE_ANALYZED
    )
    with pytest.raises(SemanticSourceChangedError):
        sink.seal()
    assert not pack_path.exists()
    assert not catalog_path.exists()
    assert not list(tmp_path.glob(".authority.pm-pack.*.tmp"))
    assert not list(tmp_path.glob(".authority.catalog.sqlite.*.tmp"))
