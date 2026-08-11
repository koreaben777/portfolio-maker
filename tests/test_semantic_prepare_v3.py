from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path

import pytest

import portfolio_maker.infrastructure.semantic_analyzers as semantic_analyzers_module
import portfolio_maker.infrastructure.semantic_crawler as semantic_crawler_module
import portfolio_maker.infrastructure.semantic_source_reader as source_reader
from portfolio_maker.application.approval import SourceApproval
from portfolio_maker.domain.semantic_frontier import PackRecordRef
from portfolio_maker.infrastructure.semantic_catalog import SemanticCatalogReader
from portfolio_maker.infrastructure.semantic_crawler import (
    StructuralEventKind,
    StreamingStructureSink,
    StreamingStructureSinkState,
    iter_local_structure_events,
)
from portfolio_maker.infrastructure.semantic_pack import PMPackReader
from portfolio_maker.infrastructure.policy import SourcePathPolicyError
from portfolio_maker.infrastructure.semantic_source_reader import (
    SemanticSourceReadError,
    SemanticSourceChangedError,
    read_verified_sample,
    read_verified_representative,
)


def _approval_for(root: Path, *, excluded: tuple[Path, ...] = ()) -> SourceApproval:
    return SourceApproval(
        approved_source_uris=(root.resolve().as_uri(),),
        legacy_forbidden_paths=(),
        excluded_directories=excluded,
        excluded_repositories=(),
        private_sources_allowed=False,
        allowed_repositories=(),
        excluded_file_patterns=(),
        approved_github_activity_urls=(),
        approved_private_github_activity_urls=(),
    )


def _completed_sink(root: Path, pack_path: Path, catalog_path: Path) -> StreamingStructureSink:
    sink = StreamingStructureSink(pack_path, catalog_path)
    list(iter_local_structure_events(root, _approval_for(root), sink=sink))
    return sink


def _force_cross_device_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_link(*_args, **_kwargs) -> None:
        raise OSError(errno.EXDEV, "synthetic cross-device publish")

    monkeypatch.setattr(semantic_crawler_module.os, "link", fail_link)


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


def test_verified_sample_rejects_same_inode_tail_rewrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if bounded sampling skips the pre/post identity check."""
    source = tmp_path / "source.md"
    source.write_bytes(b"alpha\nbeta\ngamma")
    original_read = source_reader.os.read

    def rewrite_tail_after_sample(descriptor: int, max_bytes: int) -> bytes:
        result = original_read(descriptor, max_bytes)
        with source.open("r+b") as handle:
            handle.seek(-5, os.SEEK_END)
            handle.write(b"delta")
            handle.flush()
            os.fsync(handle.fileno())
        return result

    monkeypatch.setattr(source_reader.os, "read", rewrite_tail_after_sample)

    with pytest.raises(SemanticSourceChangedError, match="changed during verified read"):
        read_verified_sample(source, 5)


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
        child_rows = catalog.connection.execute(
            """
            SELECT parent_node_id, child_node_id, traversal_order
            FROM children ORDER BY parent_node_id, traversal_order
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

    child_orders = {row[1]: row[2] for row in child_rows}
    for row in edge_rows:
        edge = pack_reader.read(PackRecordRef(row[2], row[3], row[4]))
        assert edge["traversal_order"] == child_orders[row[1]]

    assert len(references) == artifacts.pack_seal.record_count
    assert len({(ref.offset, ref.length, ref.sha256) for ref in references}) == len(references)


def test_streaming_baseline_payload_persists_only_the_masked_excerpt(
    tmp_path: Path,
) -> None:
    """Breaks if baseline authority omits the excerpt or stores its raw secret."""
    root = tmp_path / "source"
    root.mkdir()
    raw_secret = "synthetic-round3-secret"
    (root / "note.md").write_text(
        f"API_TOKEN={raw_secret}\nproject evidence",
        encoding="utf-8",
    )
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = StreamingStructureSink(pack_path, catalog_path)

    events = list(iter_local_structure_events(root, _approval_for(root), sink=sink))
    artifacts = sink.seal()
    baseline_event = next(
        event
        for event in events
        if event.kind is StructuralEventKind.FILE_ANALYZED
        and event.node.relative_hierarchy == "note.md"
    )
    assert baseline_event.baseline is not None
    with SemanticCatalogReader(catalog_path, artifacts.catalog_seal) as catalog:
        baseline = catalog.baseline(baseline_event.node.node_id)
    assert baseline is not None and baseline.payload_ref is not None
    payload = PMPackReader(pack_path).read(baseline.payload_ref)

    assert payload["masked_excerpt"] == baseline_event.baseline.masked_excerpt
    assert raw_secret not in payload["masked_excerpt"]
    assert raw_secret.encode("utf-8") not in pack_path.read_bytes()
    assert raw_secret.encode("utf-8") not in catalog_path.read_bytes()


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
    original_read = source_reader.os.read

    def rewrite_after_sample(descriptor: int, max_bytes: int) -> bytes:
        result = original_read(descriptor, max_bytes)
        with source.open("r+b") as handle:
            handle.seek(-5, os.SEEK_END)
            handle.write(b"delta")
            handle.flush()
            os.fsync(handle.fileno())
        return result

    monkeypatch.setattr(source_reader.os, "read", rewrite_after_sample)
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


def test_atomic_path_replacement_before_reader_open_aborts_both_authorities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if crawler metadata can be paired with replacement-file content."""
    root = tmp_path / "source"
    root.mkdir()
    source = root / "changing.md"
    source.write_text("original evidence", encoding="utf-8")
    replacement = root / "replacement.md"
    replacement.write_text("replacement evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    original_read = semantic_analyzers_module.read_verified_sample
    replaced = False

    def replace_before_open(
        path: Path,
        max_bytes: int,
        *,
        expected_device: int | None = None,
        expected_inode: int | None = None,
    ):
        nonlocal replaced
        if path == source and not replaced:
            os.replace(replacement, source)
            replaced = True
        return original_read(
            path,
            max_bytes,
            expected_device=expected_device,
            expected_inode=expected_inode,
        )

    monkeypatch.setattr(
        semantic_analyzers_module,
        "read_verified_sample",
        replace_before_open,
    )
    sink = StreamingStructureSink(pack_path, catalog_path)
    events = list(iter_local_structure_events(root, _approval_for(root), sink=sink))

    assert any(
        event.node.relative_hierarchy == "changing.md"
        and event.baseline is not None
        and event.baseline.coverage.value == "failed"
        and event.baseline.source_changed
        for event in events
        if event.kind is StructuralEventKind.FILE_ANALYZED
    )
    with pytest.raises(SemanticSourceChangedError):
        sink.seal()
    assert not pack_path.exists()
    assert not catalog_path.exists()


def test_sink_rolls_back_pack_when_catalog_seal_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a catalog-seal failure leaves a published pack orphan."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = StreamingStructureSink(pack_path, catalog_path)
    list(iter_local_structure_events(root, _approval_for(root), sink=sink))

    def fail_catalog_seal(_writer):
        raise RuntimeError("synthetic catalog seal failure")

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.semantic_crawler.SemanticCatalogWriter.seal",
        fail_catalog_seal,
    )

    with pytest.raises(RuntimeError, match="synthetic catalog seal failure"):
        sink.seal()

    assert not pack_path.exists()
    assert not catalog_path.exists()


def test_sink_cleans_exact_pack_temp_when_first_writer_init_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a failed first writer constructor leaks its known temp file."""
    unrelated = tmp_path / ".authority.pm-pack.stream-unrelated.tmp"
    unrelated.write_bytes(b"preserve")
    original_flush = semantic_crawler_module.PMPackWriter._flush
    original_receipt_close = semantic_crawler_module._SinkInvariantStore.close
    receipt_close_count = 0

    def flush_then_fail(writer) -> None:
        original_flush(writer)
        raise OSError("synthetic pack init failure")

    def record_receipt_close(store) -> None:
        nonlocal receipt_close_count
        receipt_close_count += 1
        original_receipt_close(store)

    monkeypatch.setattr(
        semantic_crawler_module.PMPackWriter,
        "_flush",
        flush_then_fail,
    )
    monkeypatch.setattr(
        semantic_crawler_module._SinkInvariantStore,
        "close",
        record_receipt_close,
    )

    with pytest.raises(OSError, match="synthetic pack init failure"):
        StreamingStructureSink(
            tmp_path / "authority.pm-pack",
            tmp_path / "authority.catalog.sqlite",
        )

    assert receipt_close_count == 1
    assert unrelated.read_bytes() == b"preserve"
    assert list(tmp_path.iterdir()) == [unrelated]


def test_sink_receipt_cleanup_failure_prevents_both_final_publishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if receipt cleanup can fail after either final becomes visible."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = _completed_sink(root, pack_path, catalog_path)
    publish_calls: list[tuple[Path, Path]] = []

    def fail_cleanup() -> None:
        raise OSError("synthetic receipt cleanup failure")

    def record_publish(stage_path: Path, final_path: Path) -> None:
        publish_calls.append((stage_path, final_path))

    monkeypatch.setattr(sink._receipt_store, "close", fail_cleanup)
    monkeypatch.setattr(
        StreamingStructureSink,
        "_publish_file",
        staticmethod(record_publish),
    )

    with pytest.raises(OSError, match="synthetic receipt cleanup failure"):
        sink.seal()

    assert publish_calls == []
    assert sink.state is StreamingStructureSinkState.FAILED
    assert sink._artifacts is None
    assert not pack_path.exists()
    assert not catalog_path.exists()
    assert not list(tmp_path.glob(".authority.*.stream-*"))


def test_sink_cleanup_close_then_error_remains_repeatably_abortable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a post-close cleanup error leaves finals or a stuck COMPLETE state."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = _completed_sink(root, pack_path, catalog_path)
    receipt_store = sink._receipt_store
    original_close = receipt_store.close

    def close_then_fail() -> None:
        original_close()
        raise OSError("synthetic post-close cleanup failure")

    monkeypatch.setattr(receipt_store, "close", close_then_fail)

    with pytest.raises(OSError, match="synthetic post-close cleanup failure"):
        sink.seal()

    assert sink.state is StreamingStructureSinkState.FAILED
    assert sink._artifacts is None
    assert not pack_path.exists()
    assert not catalog_path.exists()
    sink.abort()
    sink.abort()
    sink.close()
    assert sink.state is StreamingStructureSinkState.ABORTED


def test_sink_artifact_construction_failure_prevents_final_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a post-publish artifact error leaves finals with no artifact."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = _completed_sink(root, pack_path, catalog_path)

    def fail_artifacts(*_args, **_kwargs):
        raise RuntimeError("synthetic artifact construction failure")

    monkeypatch.setattr(
        semantic_crawler_module,
        "StreamingStructureArtifacts",
        fail_artifacts,
    )

    with pytest.raises(RuntimeError, match="synthetic artifact construction failure"):
        sink.seal()

    assert sink.state is StreamingStructureSinkState.FAILED
    assert sink._artifacts is None
    assert not pack_path.exists()
    assert not catalog_path.exists()


def test_generator_cancel_aborts_active_sink_staging(
    tmp_path: Path,
) -> None:
    """Breaks if closing the event generator leaves an ACTIVE authority sink."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = StreamingStructureSink(pack_path, catalog_path)
    stream = iter_local_structure_events(root, _approval_for(root), sink=sink)

    assert next(stream).kind is StructuralEventKind.NODE_SEEN
    stream.close()

    assert sink.state is StreamingStructureSinkState.ABORTED
    assert not pack_path.exists()
    assert not catalog_path.exists()
    with pytest.raises(ValueError, match="closed"):
        sink.seal()


def test_generator_cancel_aborts_sink_on_legacy_worker_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if the legacy worker branch bypasses cancellation cleanup."""
    root = tmp_path / "source"
    root.mkdir()
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    original_worker = semantic_crawler_module._DirectoryEnumerationWorker

    class LegacyWorker:
        def __init__(self, *args, **kwargs) -> None:
            self._delegate = original_worker(*args, **kwargs)

        def __enter__(self):
            self._delegate.__enter__()
            return self

        def __exit__(self, *args) -> None:
            self._delegate.__exit__(*args)

        def enumerate(self, *args, **kwargs):
            return self._delegate.enumerate(*args, **kwargs)

        def replace_after_timeout(self) -> None:
            self._delegate.replace_after_timeout()

    monkeypatch.setattr(
        semantic_crawler_module,
        "_DirectoryEnumerationWorker",
        LegacyWorker,
    )
    sink = StreamingStructureSink(pack_path, catalog_path)
    stream = iter_local_structure_events(root, _approval_for(root), sink=sink)

    assert next(stream).kind is StructuralEventKind.NODE_SEEN
    stream.close()

    assert sink.state is StreamingStructureSinkState.ABORTED
    assert not list(tmp_path.glob(".authority.*.stream-*"))


def test_generator_cancel_aborts_sink_for_policy_pruned_source(tmp_path: Path) -> None:
    """Breaks if the no-worker policy branch bypasses sink cancellation cleanup."""
    root = tmp_path / "source"
    root.mkdir()
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = StreamingStructureSink(pack_path, catalog_path)
    stream = iter_local_structure_events(
        root,
        _approval_for(root, excluded=(root,)),
        sink=sink,
    )

    assert next(stream).kind is StructuralEventKind.NODE_SEEN
    stream.close()

    assert sink.state is StreamingStructureSinkState.ABORTED
    assert not pack_path.exists()
    assert not catalog_path.exists()


def test_sink_poisoned_by_catalog_insert_failure_aborts_both_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a pack append survives a later catalog insert failure."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = StreamingStructureSink(pack_path, catalog_path)

    def fail_add_node(_writer, _record) -> None:
        raise RuntimeError("synthetic catalog insert failure")

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.semantic_crawler.SemanticCatalogWriter.add_node",
        fail_add_node,
    )
    stream = iter_local_structure_events(root, _approval_for(root), sink=sink)

    with pytest.raises(RuntimeError, match="synthetic catalog insert failure"):
        next(stream)

    assert sink.state is StreamingStructureSinkState.ABORTED
    assert not pack_path.exists()
    assert not catalog_path.exists()
    assert not list(tmp_path.glob(".authority.*.stream-*"))


def test_sink_poisoned_by_pack_append_failure_aborts_both_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if an append failure leaves either staging authority behind."""
    root = tmp_path / "source"
    root.mkdir()
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = StreamingStructureSink(pack_path, catalog_path)

    def fail_append(_writer, _payload):
        raise RuntimeError("synthetic pack append failure")

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.semantic_crawler.PMPackWriter.append",
        fail_append,
    )
    stream = iter_local_structure_events(root, _approval_for(root), sink=sink)

    with pytest.raises(RuntimeError, match="synthetic pack append failure"):
        next(stream)

    assert sink.state is StreamingStructureSinkState.ABORTED
    assert not pack_path.exists()
    assert not catalog_path.exists()
    assert not list(tmp_path.glob(".authority.*.stream-*"))


def test_sink_rolls_back_stages_when_pack_seal_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a pack-seal failure retains a sealed staging file."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = _completed_sink(root, pack_path, catalog_path)

    def fail_pack_seal(_writer):
        raise RuntimeError("synthetic pack seal failure")

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.semantic_crawler.PMPackWriter.seal",
        fail_pack_seal,
    )

    with pytest.raises(RuntimeError, match="synthetic pack seal failure"):
        sink.seal()

    assert not pack_path.exists()
    assert not catalog_path.exists()
    assert not list(tmp_path.glob(".authority.*.stream-*"))


def test_sink_rolls_back_stages_when_first_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a first-publish failure leaks staged or final authority files."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = _completed_sink(root, pack_path, catalog_path)

    def fail_first_publish(_stage_path: Path, _final_path: Path) -> None:
        raise OSError("synthetic first publish failure")

    monkeypatch.setattr(
        StreamingStructureSink,
        "_publish_file",
        staticmethod(fail_first_publish),
    )

    with pytest.raises(OSError, match="synthetic first publish failure"):
        sink.seal()

    assert not pack_path.exists()
    assert not catalog_path.exists()
    assert not list(tmp_path.glob(".authority.*.stream-*"))


def test_sink_rolls_back_first_publish_when_second_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if the first final authority survives the second publish failure."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = _completed_sink(root, pack_path, catalog_path)
    original_publish = StreamingStructureSink._publish_file
    calls = 0

    def fail_second_publish(stage_path: Path, final_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic second publish failure")
        original_publish(stage_path, final_path)

    monkeypatch.setattr(
        StreamingStructureSink,
        "_publish_file",
        staticmethod(fail_second_publish),
    )

    with pytest.raises(OSError, match="synthetic second publish failure"):
        sink.seal()

    assert not pack_path.exists()
    assert not catalog_path.exists()
    assert not list(tmp_path.glob(".authority.*.stream-*"))


def test_cross_device_publish_retries_partial_writes_until_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a short os.write silently publishes a truncated final."""
    stage_path = tmp_path / "stage.pack"
    final_path = tmp_path / "final.pack"
    payload = b"partial-write-evidence" * 4096
    stage_path.write_bytes(payload)
    original_write = semantic_crawler_module.os.write

    def partial_write(descriptor: int, content) -> int:
        written = max(1, len(content) // 3)
        return original_write(descriptor, content[:written])

    _force_cross_device_publish(monkeypatch)
    monkeypatch.setattr(semantic_crawler_module.os, "write", partial_write)

    StreamingStructureSink._publish_file(stage_path, final_path)

    assert final_path.read_bytes() == payload


@pytest.mark.parametrize("progress", (0, -1))
def test_cross_device_publish_rejects_nonpositive_write_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    progress: int,
) -> None:
    """Breaks if a stalled write is accepted as a successful publication."""
    stage_path = tmp_path / "stage.pack"
    final_path = tmp_path / "final.pack"
    stage_path.write_bytes(b"evidence")
    _force_cross_device_publish(monkeypatch)
    monkeypatch.setattr(
        semantic_crawler_module.os,
        "write",
        lambda _descriptor, _content: progress,
    )

    with pytest.raises(OSError, match="progress"):
        StreamingStructureSink._publish_file(stage_path, final_path)

    assert not final_path.exists()


def test_cross_device_publish_removes_final_after_mid_copy_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protects cleanup when a later copy chunk raises."""
    stage_path = tmp_path / "stage.pack"
    final_path = tmp_path / "final.pack"
    stage_path.write_bytes(b"a" * 70_000)
    original_write = semantic_crawler_module.os.write
    calls = 0

    def fail_second_write(descriptor: int, content) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic mid-copy failure")
        return original_write(descriptor, content)

    _force_cross_device_publish(monkeypatch)
    monkeypatch.setattr(semantic_crawler_module.os, "write", fail_second_write)

    with pytest.raises(OSError, match="synthetic mid-copy failure"):
        StreamingStructureSink._publish_file(stage_path, final_path)

    assert not final_path.exists()


def test_cross_device_publish_rejects_same_length_digest_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if copied bytes are not verified against the sealed stage."""
    stage_path = tmp_path / "stage.pack"
    final_path = tmp_path / "final.pack"
    stage_path.write_bytes(b"trusted staged bytes")
    original_write = semantic_crawler_module.os.write

    def corrupt_write(descriptor: int, content) -> int:
        return original_write(descriptor, b"x" * len(content))

    _force_cross_device_publish(monkeypatch)
    monkeypatch.setattr(semantic_crawler_module.os, "write", corrupt_write)

    with pytest.raises(OSError, match="verification"):
        StreamingStructureSink._publish_file(stage_path, final_path)

    assert not final_path.exists()


def test_cross_device_publish_copies_and_verifies_exact_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_path = tmp_path / "stage.pack"
    final_path = tmp_path / "final.pack"
    payload = os.urandom(150_000)
    stage_path.write_bytes(payload)
    _force_cross_device_publish(monkeypatch)

    StreamingStructureSink._publish_file(stage_path, final_path)

    assert final_path.read_bytes() == payload


def test_sink_preserves_existing_final_targets_on_incomplete_seal(tmp_path: Path) -> None:
    """Breaks if an incomplete sink can overwrite or delete a prior authority."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    pack_path.write_bytes(b"existing-pack")
    catalog_path.write_bytes(b"existing-catalog")
    sink = StreamingStructureSink(pack_path, catalog_path)
    stream = iter_local_structure_events(root, _approval_for(root), sink=sink)
    assert next(stream).kind is StructuralEventKind.NODE_SEEN

    with pytest.raises(ValueError, match="incomplete"):
        sink.seal()

    assert pack_path.read_bytes() == b"existing-pack"
    assert catalog_path.read_bytes() == b"existing-catalog"
    sink.close()
    assert sink.state is StreamingStructureSinkState.ABORTED


def test_sink_preserves_existing_final_targets_on_complete_seal(tmp_path: Path) -> None:
    """Breaks if staging publication replaces an already-existing final target."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    pack_path.write_bytes(b"existing-pack")
    catalog_path.write_bytes(b"existing-catalog")
    sink = _completed_sink(root, pack_path, catalog_path)

    with pytest.raises(ValueError, match="target already exists"):
        sink.seal()

    assert pack_path.read_bytes() == b"existing-pack"
    assert catalog_path.read_bytes() == b"existing-catalog"
    assert not list(tmp_path.glob(".authority.*.stream-*"))


def test_successful_sink_seal_closes_writer_descriptors_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """Breaks if a successful publish retains writer directory descriptors."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "note.md").write_text("evidence", encoding="utf-8")
    sink = _completed_sink(
        root,
        tmp_path / "authority.pm-pack",
        tmp_path / "authority.catalog.sqlite",
    )

    artifacts = sink.seal()

    assert sink.state is StreamingStructureSinkState.SEALED
    assert sink.seal() == artifacts
    assert sink._pack_writer._directory_descriptor is None
    assert sink._catalog_writer._directory_descriptor is None
    sink.abort()
    sink.close()
    assert sink.state is StreamingStructureSinkState.SEALED
