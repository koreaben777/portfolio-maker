from __future__ import annotations

import multiprocessing
import os
import signal
import threading
import time
from pathlib import Path

import pytest

import portfolio_maker.infrastructure.semantic_crawler as semantic_crawler_module
from portfolio_maker.application.approval import SourceApproval
from portfolio_maker.domain.semantic_frontier import BaselineCoverage
from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.infrastructure.semantic_crawler import (
    CrawlObserver,
    StructuralCrawlRootError,
    StructuralEventKind,
    StreamingStructureSink,
    StreamingStructureSinkState,
    crawl_local_structure,
    iter_local_structure_events,
)


def _blocking_directory_enumerator(path: Path):
    if path.name == "blocked":
        threading.Event().wait()
    return semantic_crawler_module._materialize_directory(path)


def _always_blocking_directory_enumerator(_path: Path):
    threading.Event().wait()


def _multiple_blocking_directory_enumerator(path: Path):
    if path.name.startswith("blocked"):
        threading.Event().wait()
    return semantic_crawler_module._materialize_directory(path)


def _termination_resistant_directory_enumerator(_path: Path):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    threading.Event().wait()


def _crashing_directory_enumerator(_path: Path):
    os._exit(73)


def _child_crashing_directory_enumerator(path: Path):
    if path.name == "fault":
        os._exit(73)
    return semantic_crawler_module._materialize_directory(path)


def _unreadable_child_entry_enumerator(path: Path):
    if path.name == "root":
        yield semantic_crawler_module._MaterializedDirectoryEntry(
            name="gone.md",
            st_mode=None,
            st_dev=None,
            st_ino=None,
            readable=False,
        )


def _zero_inode_child_entry_enumerator(path: Path):
    if path.name == "root":
        yield semantic_crawler_module._MaterializedDirectoryEntry(
            name="zero.md",
            st_mode=0o100644,
            st_dev=17,
            st_ino=0,
            readable=True,
        )


def _trickling_directory_enumerator(path: Path):
    for entry in semantic_crawler_module._materialize_directory(path):
        yield entry
        time.sleep(0.035)


def _malformed_worker_main(connection, _enumerator):
    connection.send(
        {
            "protocol": "portfolio-semantic-directory-v1",
            "status": "ready",
        }
    )
    connection.recv()
    connection.send({"protocol": "wrong", "status": "ok", "entries": []})


class _LifecycleConnection:
    def __init__(self) -> None:
        self.closed = False
        self.sent: list[object] = []

    def send(self, value: object) -> None:
        self.sent.append(value)

    def close(self) -> None:
        self.closed = True


class _LifecycleProcess:
    def __init__(
        self,
        *,
        start_error: BaseException | None = None,
        join_interruptions: tuple[int, ...] = (),
        ignore_terminate: bool = False,
    ) -> None:
        self.start_error = start_error
        self.join_interruptions = set(join_interruptions)
        self.ignore_terminate = ignore_terminate
        self.started = False
        self.alive = False
        self.closed = False
        self.join_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        self.kill_requested = False

    @property
    def pid(self) -> int | None:
        return 4242 if self.started else None

    @property
    def sentinel(self) -> int:
        return 73

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, _timeout: float) -> None:
        self.join_calls += 1
        if self.join_calls in self.join_interruptions:
            raise KeyboardInterrupt
        if self.kill_requested or (
            self.terminate_calls and not self.ignore_terminate
        ):
            self.alive = False

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.kill_requested = True

    def close(self) -> None:
        if self.alive:
            raise AssertionError("cannot close a live process")
        self.closed = True


class _LifecycleContext:
    def __init__(self, process: _LifecycleProcess) -> None:
        self.process = process
        self.parent = _LifecycleConnection()
        self.child = _LifecycleConnection()

    def Pipe(self, *, duplex: bool):
        assert duplex is True
        return self.parent, self.child

    def Process(self, **_kwargs):
        return self.process


class _BudgetWorker:
    instances: list["_BudgetWorker"] = []

    def __init__(self, **_kwargs) -> None:
        self.requests: list[tuple[str, float]] = []
        self.replacement_count = 0
        self.closed = False
        type(self).instances.append(self)

    def __enter__(self) -> "_BudgetWorker":
        return self

    def __exit__(self, *_args) -> None:
        self.closed = True

    def enumerate(
        self,
        directory: Path,
        *,
        timeout_seconds: float,
        replace_after_timeout: bool = True,
        monotonic_clock,
    ):
        del replace_after_timeout
        self.requests.append((directory.name, timeout_seconds))
        started_at = monotonic_clock()
        elapsed = max(0.0, monotonic_clock() - started_at)
        if directory.name.startswith("blocked"):
            return semantic_crawler_module._DirectoryEnumeration(
                entries=(),
                timed_out=True,
                timeout_wait_seconds=elapsed,
            )
        return semantic_crawler_module._DirectoryEnumeration(
            entries=semantic_crawler_module._materialize_directory(directory),
        )

    def replace_after_timeout(self) -> None:
        self.replacement_count += 1


def approval_for(root: Path, *, excluded: tuple[Path, ...] = ()) -> SourceApproval:
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


def test_structural_crawl_has_no_global_file_count_cap(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for index in range(501):
        (root / f"file-{index:03}.md").write_text("evidence", encoding="utf-8")

    result = crawl_local_structure(root, approval_for(root))

    assert sum(entry.kind == SemanticNodeKind.FILE for entry in result.entries) == 501


def test_stream_emits_every_node_then_closes_directories_bottom_up(
    tmp_path: Path,
) -> None:
    """Breaks if the v3 crawler does not emit nodes and close child directories first."""
    root = tmp_path / "root"
    (root / "project" / "src").mkdir(parents=True)
    (root / "project" / "README.md").write_text("purpose", encoding="utf-8")
    (root / "project" / "src" / "app.py").write_text(
        "print('ok')", encoding="utf-8"
    )

    events = list(iter_local_structure_events(root, approval_for(root)))

    seen = [event for event in events if event.kind is StructuralEventKind.NODE_SEEN]
    closed = [
        event.node.relative_hierarchy
        for event in events
        if event.kind is StructuralEventKind.DIRECTORY_CLOSED
    ]
    assert len(seen) == 5
    assert set(closed) == {".", "project", "project/src"}
    assert closed.index("project/src") < closed.index("project") < closed.index(".")


def test_high_fanout_worker_streams_without_directory_tuple(tmp_path: Path) -> None:
    """Breaks if v3 buffers a directory population instead of its bounded queue."""
    root = tmp_path / "high-fanout"
    root.mkdir()
    for index in range(10_000):
        (root / f"file-{index:05}.md").write_text("evidence", encoding="utf-8")
    observer = CrawlObserver()

    count = sum(
        1
        for _ in iter_local_structure_events(
            root,
            approval_for(root),
            worker_queue_size=256,
            observer=observer,
        )
    )

    assert count >= 10_002
    assert observer.queue_maxsize == 256
    assert observer.max_buffered_events <= 256
    assert observer.directory_tuple_materializations == 0
    assert observer.spool_count_query_count == 0


def test_streaming_reuses_one_global_worker_and_sqlite_sibling_backlog(
    tmp_path: Path,
) -> None:
    """Breaks if each directory depth owns another queue/process or child list."""
    root = tmp_path / "root"
    for index in range(40):
        leaf = root / f"branch-{index:03}" / "deep" / "leaf.md"
        leaf.parent.mkdir(parents=True, exist_ok=True)
        leaf.write_text("evidence", encoding="utf-8")
    observer = CrawlObserver()

    events = list(
        iter_local_structure_events(
            root,
            approval_for(root),
            worker_queue_size=2,
            observer=observer,
        )
    )

    assert events
    assert observer.worker_start_count == 1
    assert observer.max_active_worker_count == 1
    assert observer.max_in_memory_pending_directories <= 1
    assert observer.max_spooled_entry_count >= 40
    assert observer.spool_count_query_count == 0
    assert observer.ephemeral_spool_cleaned is True


def test_streaming_backpressure_uses_worker_progress_not_tuple_counter(
    tmp_path: Path,
) -> None:
    """Breaks if a paused consumer cannot prove a full bounded worker queue."""
    root = tmp_path / "root"
    root.mkdir()
    for index in range(32):
        (root / f"entry-{index:03}.md").write_text("evidence", encoding="utf-8")
    observer = CrawlObserver()
    before = {process.pid for process in multiprocessing.active_children()}
    with semantic_crawler_module._StreamingDirectoryEnumerationWorker(
        process_context=multiprocessing.get_context("fork"),
        enumerator=semantic_crawler_module._stream_directory_entries,
        queue_size=1,
        observer=observer,
    ) as worker:
        entries = worker.iter_entries(
            root,
            timeout_seconds=1.0,
            monotonic_clock=time.monotonic,
        )
        first = next(entries)
        deadline = time.monotonic() + 1.0
        while (
            observer.producer_backpressure_count == 0
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        paused_progress = observer.producer_progress_markers
        time.sleep(0.05)

        assert paused_progress >= 2
        assert observer.producer_backpressure_count > 0
        assert observer.producer_progress_markers == paused_progress
        assert next(entries).name != first.name
        deadline = time.monotonic() + 1.0
        while (
            observer.producer_progress_markers == paused_progress
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert observer.producer_progress_markers > paused_progress
        entries.close()

    assert observer.max_active_worker_count == 1
    assert {process.pid for process in multiprocessing.active_children()} == before


def test_streaming_early_close_reaps_worker_and_deletes_ephemeral_spool(
    tmp_path: Path,
) -> None:
    """Breaks if cancelling a consumer leaks the global helper or SQLite spool."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "entry.md").write_text("evidence", encoding="utf-8")
    observer = CrawlObserver()
    before = {process.pid for process in multiprocessing.active_children()}
    stream = iter_local_structure_events(root, approval_for(root), observer=observer)

    assert next(stream).kind is StructuralEventKind.NODE_SEEN
    assert observer.ephemeral_spool_path is not None
    assert observer.ephemeral_spool_path.exists()
    stream.close()

    assert {process.pid for process in multiprocessing.active_children()} == before
    assert observer.active_worker_count == 0
    assert observer.ephemeral_spool_cleaned is True
    assert observer.ephemeral_spool_path is not None
    assert not observer.ephemeral_spool_path.exists()


def test_streaming_timeout_budget_does_not_replace_exhausted_worker_and_cleans(
    tmp_path: Path,
) -> None:
    """Breaks if an exhausted budget starts a replacement helper or skips closure."""
    root = tmp_path / "root"
    for name in ("blocked-a", "blocked-b"):
        (root / name).mkdir(parents=True)
    observer = CrawlObserver(collect_errors=True)
    before = {process.pid for process in multiprocessing.active_children()}
    events = []
    stream = iter_local_structure_events(
        root,
        approval_for(root),
        directory_timeout_seconds=0.04,
        directory_timeout_budget_seconds=0.05,
        _process_context=multiprocessing.get_context("fork"),
        _enumerator=_multiple_blocking_directory_enumerator,
        observer=observer,
    )

    with pytest.raises(semantic_crawler_module.StructuralCrawlTimeoutBudgetError):
        while True:
            events.append(next(stream))

    terminal = {(event.kind, event.node.relative_hierarchy) for event in events}
    assert (StructuralEventKind.FILE_ANALYZED, "blocked-b") in terminal
    assert (StructuralEventKind.SOURCE_CLOSED, ".") in terminal
    assert observer.worker_start_count == 2
    assert observer.active_worker_count == 0
    assert observer.ephemeral_spool_cleaned is True
    assert observer.ephemeral_spool_path is not None
    assert not observer.ephemeral_spool_path.exists()
    assert {process.pid for process in multiprocessing.active_children()} == before


def test_stream_emits_one_terminal_baseline_for_every_permitted_node(
    tmp_path: Path,
) -> None:
    """Breaks if a permitted structural record can finish without coverage."""
    root = tmp_path / "root"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "note.md").write_text("API_TOKEN=secret", encoding="utf-8")

    events = list(iter_local_structure_events(root, approval_for(root)))

    node_ids = {
        event.node.node_id
        for event in events
        if event.kind is StructuralEventKind.NODE_SEEN
    }
    baselines = [
        event.baseline
        for event in events
        if event.kind is StructuralEventKind.FILE_ANALYZED
    ]
    assert {baseline.node_id for baseline in baselines if baseline is not None} == node_ids
    assert all(
        baseline is not None and "secret" not in baseline.masked_excerpt
        for baseline in baselines
    )


def test_excluded_directory_is_pruned_without_child_names(tmp_path: Path) -> None:
    root = tmp_path / "root"
    excluded = root / "private"
    excluded.mkdir(parents=True)
    (excluded / "secret-plan.md").write_text("restricted", encoding="utf-8")
    (root / "README.md").write_text("visible", encoding="utf-8")

    result = crawl_local_structure(root, approval_for(root, excluded=(excluded,)))

    entries = {entry.relative_hierarchy: entry for entry in result.entries}
    assert entries["private"].status == AnalysisStatus.UNSUPPORTED
    assert "private/secret-plan.md" not in entries
    assert all("secret-plan" not in error.relative_hierarchy for error in result.errors)


def test_unreadable_directory_is_reported_as_partial_error(tmp_path: Path) -> None:
    root = tmp_path / "root"
    blocked = root / "blocked"
    blocked.mkdir(parents=True)
    (blocked / "hidden.md").write_text("hidden", encoding="utf-8")
    def denied_enumerator(path: Path):
        if Path(path) == blocked:
            raise PermissionError("permission denied")
        return semantic_crawler_module._materialize_directory(path)

    result = crawl_local_structure(
        root,
        approval_for(root),
        _process_context=multiprocessing.get_context("fork"),
        _enumerator=denied_enumerator,
    )

    assert "blocked/hidden.md" not in {entry.relative_hierarchy for entry in result.entries}
    assert [(error.relative_hierarchy, error.status) for error in result.errors] == [
        ("blocked", AnalysisStatus.UNREADABLE)
    ]


def test_blocked_directory_is_bounded_and_sibling_continues_without_worker_leak(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    blocked = root / "blocked"
    sibling = root / "sibling"
    blocked.mkdir(parents=True)
    sibling.mkdir()
    (blocked / "hidden.md").write_text("hidden", encoding="utf-8")
    (sibling / "visible.md").write_text("visible", encoding="utf-8")
    context = multiprocessing.get_context("fork")
    before = {process.pid for process in multiprocessing.active_children()}

    result = crawl_local_structure(
        root,
        approval_for(root),
        directory_timeout_seconds=0.2,
        _process_context=context,
        _enumerator=_blocking_directory_enumerator,
    )

    relative_entries = {entry.relative_hierarchy: entry for entry in result.entries}
    assert relative_entries["blocked"].status is AnalysisStatus.UNREADABLE
    assert "blocked/hidden.md" not in relative_entries
    assert "sibling/visible.md" in relative_entries
    assert result.timed_out_directory_count == 1
    assert result.unreadable_directory_count == 1
    assert [error.timed_out for error in result.errors] == [True]
    assert {process.pid for process in multiprocessing.active_children()} == before


def test_root_timeout_is_distinct_and_reaps_worker(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    context = multiprocessing.get_context("fork")
    before = {process.pid for process in multiprocessing.active_children()}

    with pytest.raises(semantic_crawler_module.StructuralCrawlRootTimeoutError):
        crawl_local_structure(
            root,
            approval_for(root),
            directory_timeout_seconds=0.05,
            _process_context=context,
            _enumerator=_always_blocking_directory_enumerator,
        )

    assert {process.pid for process in multiprocessing.active_children()} == before


def test_streaming_directory_deadline_is_absolute_despite_entry_trickle(
    tmp_path: Path,
) -> None:
    """Breaks if every streamed entry grants a fresh directory timeout."""
    root = tmp_path / "root"
    root.mkdir()
    for index in range(4):
        (root / f"entry-{index}.md").write_text("evidence", encoding="utf-8")
    context = multiprocessing.get_context("fork")

    with pytest.raises(semantic_crawler_module.StructuralCrawlRootTimeoutError):
        list(
            iter_local_structure_events(
                root,
                approval_for(root),
                directory_timeout_seconds=0.05,
                _process_context=context,
                _enumerator=_trickling_directory_enumerator,
            )
        )


def test_streaming_worker_fault_emits_failed_baselines_and_bottom_up_closures(
    tmp_path: Path,
) -> None:
    """Breaks if a worker fault aborts the stream before terminal evidence."""
    root = tmp_path / "root"
    (root / "fault").mkdir(parents=True)
    context = multiprocessing.get_context("fork")
    observer = CrawlObserver()
    before = {process.pid for process in multiprocessing.active_children()}
    events = []
    stream = iter_local_structure_events(
        root,
        approval_for(root),
        _process_context=context,
        _enumerator=_child_crashing_directory_enumerator,
        observer=observer,
    )

    with pytest.raises(semantic_crawler_module.StructuralCrawlWorkerError):
        while True:
            events.append(next(stream))

    by_kind = [(event.kind, event.node.relative_hierarchy) for event in events]
    assert (StructuralEventKind.FILE_ANALYZED, "fault") in by_kind
    assert (StructuralEventKind.DIRECTORY_CLOSED, "fault") in by_kind
    assert (StructuralEventKind.SOURCE_CLOSED, ".") in by_kind
    assert by_kind.index((StructuralEventKind.DIRECTORY_CLOSED, "fault")) < by_kind.index(
        (StructuralEventKind.DIRECTORY_CLOSED, ".")
    ) < by_kind.index((StructuralEventKind.SOURCE_CLOSED, "."))
    failed = {
        event.node.relative_hierarchy: event.baseline.coverage
        for event in events
        if event.kind is StructuralEventKind.FILE_ANALYZED and event.baseline is not None
    }
    assert failed["fault"] is BaselineCoverage.FAILED
    assert failed["."] is BaselineCoverage.FAILED
    assert {process.pid for process in multiprocessing.active_children()} == before
    assert observer.active_worker_count == 0
    assert observer.ephemeral_spool_cleaned is True
    assert observer.ephemeral_spool_path is not None
    assert not observer.ephemeral_spool_path.exists()


def test_streaming_unreadable_child_still_emits_node_and_terminal_baseline(
    tmp_path: Path,
) -> None:
    """Breaks if a child stat failure disappears from structural authority."""
    root = tmp_path / "root"
    root.mkdir()
    events = list(
        iter_local_structure_events(
            root,
            approval_for(root),
            _process_context=multiprocessing.get_context("fork"),
            _enumerator=_unreadable_child_entry_enumerator,
        )
    )

    child_events = [event for event in events if event.node.relative_hierarchy == "gone.md"]
    assert [event.kind for event in child_events] == [
        StructuralEventKind.NODE_SEEN,
        StructuralEventKind.FILE_ANALYZED,
    ]
    assert child_events[-1].baseline is not None
    assert child_events[-1].baseline.coverage is BaselineCoverage.UNREADABLE


def test_v2_wrapper_omits_stat_failed_child_while_v3_stream_preserves_it(
    tmp_path: Path,
) -> None:
    """Breaks if the legacy materialized result adopts v3 unreadable child rows."""
    root = tmp_path / "root"
    root.mkdir()

    result = crawl_local_structure(
        root,
        approval_for(root),
        _process_context=multiprocessing.get_context("fork"),
        _enumerator=_unreadable_child_entry_enumerator,
    )

    assert "gone.md" not in {entry.relative_hierarchy for entry in result.entries}


def test_v3_uses_relative_provider_key_for_zero_inode_directory_entry(
    tmp_path: Path,
) -> None:
    """Breaks if a zero inode is treated as a stable device/inode identity."""
    root = tmp_path / "root"
    root.mkdir()

    events = list(
        iter_local_structure_events(
            root,
            approval_for(root),
            _process_context=multiprocessing.get_context("fork"),
            _enumerator=_zero_inode_child_entry_enumerator,
        )
    )
    node = next(
        event.node
        for event in events
        if event.kind is StructuralEventKind.NODE_SEEN
        and event.node.relative_hierarchy == "zero.md"
    )

    assert node.provider_item_key == "zero.md"
    assert node.device is None
    assert node.inode is None


def test_v3_prior_identity_resolution_uses_ephemeral_index_not_python_reservation_maps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if streaming identity retention rebuilds unbounded Python maps."""
    root = tmp_path / "root"
    root.mkdir()
    original = root / "alpha.md"
    alias = root / "beta.md"
    original.write_text("evidence", encoding="utf-8")
    os.link(original, alias)
    prior = crawl_local_structure(root, approval_for(root)).entries
    alias.rename(root / "gamma.md")

    def fail_legacy_reservation(*_args, **_kwargs):
        raise AssertionError("streaming crawl must not build legacy Python reservation maps")

    monkeypatch.setattr(
        semantic_crawler_module,
        "_reserve_prior_entries",
        fail_legacy_reservation,
    )
    events = list(
        iter_local_structure_events(
            root,
            approval_for(root),
            prior_entries=prior,
        )
    )
    nodes = {
        event.node.relative_hierarchy: event.node
        for event in events
        if event.kind is StructuralEventKind.NODE_SEEN
    }
    previous = {entry.relative_hierarchy: entry for entry in prior}

    assert nodes["alpha.md"].node_id == previous["alpha.md"].node_id
    assert nodes["gamma.md"].node_id == previous["beta.md"].node_id


def test_worker_crash_and_malformed_protocol_fail_closed(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    context = multiprocessing.get_context("fork")

    with semantic_crawler_module._DirectoryEnumerationWorker(
        process_context=context,
        enumerator=_crashing_directory_enumerator,
    ) as worker:
        with pytest.raises(
            semantic_crawler_module.StructuralCrawlWorkerError,
            match="failed safely",
        ):
            worker.enumerate(directory, timeout_seconds=0.2)

    with semantic_crawler_module._DirectoryEnumerationWorker(
        process_context=context,
        enumerator=semantic_crawler_module._materialize_directory,
        worker_target=_malformed_worker_main,
    ) as worker:
        with pytest.raises(
            semantic_crawler_module.StructuralCrawlWorkerError,
            match="failed safely",
        ):
            worker.enumerate(directory, timeout_seconds=0.2)


def test_worker_reuses_successful_process_and_replaces_timed_out_process(
    tmp_path: Path,
) -> None:
    responsive = tmp_path / "responsive"
    blocked = tmp_path / "blocked"
    responsive.mkdir()
    blocked.mkdir()
    context = multiprocessing.get_context("fork")

    with semantic_crawler_module._DirectoryEnumerationWorker(
        process_context=context,
        enumerator=_blocking_directory_enumerator,
    ) as worker:
        first_pid = worker.pid
        assert not worker.enumerate(
            responsive,
            timeout_seconds=0.2,
        ).timed_out
        assert worker.pid == first_pid
        assert worker.enumerate(blocked, timeout_seconds=0.05).timed_out
        assert worker.pid != first_pid
        assert not worker.enumerate(
            responsive,
            timeout_seconds=0.2,
        ).timed_out


def test_crawl_timeout_budget_exhausts_without_extra_worker_or_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    for name in ("blocked-a", "blocked-b", "sibling"):
        (root / name).mkdir(parents=True, exist_ok=True)
    _BudgetWorker.instances.clear()
    clock_values = iter((0.0, 0.0, 0.0, 0.04, 0.04, 0.05))
    monkeypatch.setattr(
        semantic_crawler_module,
        "_DirectoryEnumerationWorker",
        _BudgetWorker,
    )

    with pytest.raises(
        semantic_crawler_module.StructuralCrawlTimeoutBudgetError,
        match="^Structural crawl timeout budget exhausted$",
    ) as captured:
        crawl_local_structure(
            root,
            approval_for(root),
            directory_timeout_seconds=0.04,
            directory_timeout_budget_seconds=0.05,
            _monotonic=lambda: next(clock_values),
        )

    worker = _BudgetWorker.instances[-1]
    assert captured.value.timed_out_relative_hierarchies == (
        "blocked-a",
        "blocked-b",
    )
    assert captured.value.consumed_timeout_wait_seconds == pytest.approx(0.05)
    assert [name for name, _timeout in worker.requests] == [
        "root",
        "blocked-a",
        "blocked-b",
    ]
    assert [timeout for _name, timeout in worker.requests] == pytest.approx(
        [0.04, 0.04, 0.01]
    )
    assert worker.replacement_count == 1
    assert worker.closed is True


def test_crawl_timeout_budget_counts_only_timed_out_waits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    for name in ("blocked", "sibling"):
        (root / name).mkdir(parents=True, exist_ok=True)
    _BudgetWorker.instances.clear()
    clock_values = iter((0.0, 2.0, 2.0, 2.04, 2.04, 9.04))
    monkeypatch.setattr(
        semantic_crawler_module,
        "_DirectoryEnumerationWorker",
        _BudgetWorker,
    )

    crawl = crawl_local_structure(
        root,
        approval_for(root),
        directory_timeout_seconds=0.04,
        directory_timeout_budget_seconds=0.08,
        _monotonic=lambda: next(clock_values),
    )

    assert crawl.timed_out_directory_count == 1
    assert crawl.consumed_timeout_wait_seconds == pytest.approx(0.04)
    assert _BudgetWorker.instances[-1].replacement_count == 1


def test_crawl_timeout_budget_reaps_exhausted_worker_without_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    for name in ("blocked-a", "blocked-b", "sibling"):
        (root / name).mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("fork")
    before = {process.pid for process in multiprocessing.active_children()}
    replacement_count = 0
    original_replace = semantic_crawler_module._DirectoryEnumerationWorker.replace_after_timeout

    def record_replace(worker):
        nonlocal replacement_count
        replacement_count += 1
        return original_replace(worker)

    monkeypatch.setattr(
        semantic_crawler_module._DirectoryEnumerationWorker,
        "replace_after_timeout",
        record_replace,
    )

    with pytest.raises(
        semantic_crawler_module.StructuralCrawlTimeoutBudgetError,
        match="^Structural crawl timeout budget exhausted$",
    ):
        crawl_local_structure(
            root,
            approval_for(root),
            directory_timeout_seconds=0.05,
            directory_timeout_budget_seconds=0.10,
            _process_context=context,
            _enumerator=_multiple_blocking_directory_enumerator,
        )

    assert replacement_count == 1
    assert {process.pid for process in multiprocessing.active_children()} == before


def test_worker_start_oserror_is_controlled_and_closes_unstarted_resources() -> None:
    start_error = OSError("synthetic private process start detail")
    process = _LifecycleProcess(start_error=start_error)
    context = _LifecycleContext(process)
    worker = semantic_crawler_module._DirectoryEnumerationWorker(
        process_context=context,
    )

    with pytest.raises(
        semantic_crawler_module.StructuralCrawlWorkerError,
        match="^semantic directory worker failed safely$",
    ) as captured:
        worker.__enter__()

    assert captured.value.__cause__ is start_error
    assert context.parent.closed is True
    assert context.child.closed is True
    assert process.closed is True
    assert process.is_alive() is False
    assert worker._process is None
    assert worker._connection is None


@pytest.mark.parametrize(
    "control_error",
    (KeyboardInterrupt(), SystemExit(73)),
)
def test_worker_start_control_error_is_reraised_after_resource_cleanup(
    control_error: BaseException,
) -> None:
    process = _LifecycleProcess(start_error=control_error)
    context = _LifecycleContext(process)
    worker = semantic_crawler_module._DirectoryEnumerationWorker(
        process_context=context,
    )

    with pytest.raises(type(control_error)) as captured:
        worker.__enter__()

    assert captured.value is control_error
    assert context.parent.closed is True
    assert context.child.closed is True
    assert process.closed is True
    assert process.is_alive() is False
    assert worker._process is None
    assert worker._connection is None


def test_crawler_start_oserror_is_controlled_without_live_helper(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    process = _LifecycleProcess(start_error=OSError("private start detail"))
    context = _LifecycleContext(process)

    with pytest.raises(
        semantic_crawler_module.StructuralCrawlWorkerError,
        match="^semantic directory worker failed safely$",
    ):
        crawl_local_structure(
            root,
            approval_for(root),
            _process_context=context,
        )

    assert process.closed is True
    assert process.is_alive() is False
    assert context.parent.closed is True
    assert context.child.closed is True


def test_worker_startup_wait_interruption_reaps_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _LifecycleProcess()
    context = _LifecycleContext(process)
    worker = semantic_crawler_module._DirectoryEnumerationWorker(
        process_context=context,
    )
    monkeypatch.setattr(
        semantic_crawler_module,
        "wait",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        worker.__enter__()

    assert process.terminate_calls == 1
    assert process.is_alive() is False
    assert process.closed is True
    assert context.parent.closed is True
    assert context.child.closed is True
    assert worker._process is None
    assert worker._connection is None


@pytest.mark.parametrize(
    ("graceful", "join_interruptions", "ignore_terminate", "kill_calls"),
    (
        (True, (1,), False, 0),
        (False, (1,), True, 1),
        (False, (2,), True, 1),
    ),
    ids=(
        "graceful-join-interruption",
        "terminate-join-interruption",
        "kill-join-interruption",
    ),
)
def test_worker_join_interruption_finishes_cleanup_before_reraise(
    graceful: bool,
    join_interruptions: tuple[int, ...],
    ignore_terminate: bool,
    kill_calls: int,
) -> None:
    process = _LifecycleProcess(
        join_interruptions=join_interruptions,
        ignore_terminate=ignore_terminate,
    )
    process.start()
    connection = _LifecycleConnection()
    worker = semantic_crawler_module._DirectoryEnumerationWorker()
    worker._process = process
    worker._connection = connection

    with pytest.raises(KeyboardInterrupt):
        worker._stop(graceful=graceful)

    assert process.terminate_calls == 1
    assert process.kill_calls == kill_calls
    assert process.is_alive() is False
    assert process.closed is True
    assert connection.closed is True
    assert worker._process is None
    assert worker._connection is None


def test_worker_timeout_escalates_to_kill_and_reaps_process(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    context = multiprocessing.get_context("fork")
    before = {process.pid for process in multiprocessing.active_children()}

    with semantic_crawler_module._DirectoryEnumerationWorker(
        process_context=context,
        enumerator=_termination_resistant_directory_enumerator,
    ) as worker:
        timed_out_pid = worker.pid
        assert worker.enumerate(directory, timeout_seconds=0.05).timed_out
        assert worker.pid != timed_out_pid

    assert {process.pid for process in multiprocessing.active_children()} == before


def test_worker_is_reaped_when_caller_is_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    context = multiprocessing.get_context("fork")
    before = {process.pid for process in multiprocessing.active_children()}

    with pytest.raises(KeyboardInterrupt):
        with semantic_crawler_module._DirectoryEnumerationWorker(
            process_context=context,
            enumerator=_always_blocking_directory_enumerator,
        ) as worker:
            monkeypatch.setattr(
                semantic_crawler_module,
                "wait",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    KeyboardInterrupt()
                ),
            )
            worker.enumerate(directory, timeout_seconds=0.2)

    assert {process.pid for process in multiprocessing.active_children()} == before

def test_broken_and_directory_symlinks_are_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = tmp_path / "target"
    target.mkdir()
    (target / "outside.md").write_text("outside", encoding="utf-8")
    root.mkdir()
    (root / "broken-link").symlink_to(root / "missing")
    (root / "directory-link").symlink_to(target, target_is_directory=True)

    result = crawl_local_structure(root, approval_for(root))

    assert "directory-link/outside.md" not in {
        entry.relative_hierarchy for entry in result.entries
    }
    assert [(error.relative_hierarchy, error.status) for error in result.errors] == [
        ("broken-link", AnalysisStatus.UNSUPPORTED),
        ("directory-link", AnalysisStatus.UNSUPPORTED),
    ]


def test_symlink_crawl_root_is_rejected_without_traversing_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "outside.md").write_text("outside", encoding="utf-8")
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)

    with pytest.raises(StructuralCrawlRootError, match="symbolic link"):
        crawl_local_structure(root_link, approval_for(root_link))


def test_structural_crawl_keeps_hard_linked_files_as_distinct_entries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original = root / "original.md"
    original.write_text("evidence", encoding="utf-8")
    os.link(original, root / "alias.md")

    result = crawl_local_structure(root, approval_for(root))
    linked_entries = [
        entry
        for entry in result.entries
        if entry.relative_hierarchy in {"original.md", "alias.md"}
    ]

    assert {entry.relative_hierarchy for entry in linked_entries} == {
        "original.md",
        "alias.md",
    }
    assert len({entry.node_id for entry in linked_entries}) == 2


def test_hardlink_provider_keys_keep_sorted_base_identity_and_alias_locator(
    tmp_path: Path,
) -> None:
    """Breaks if every hard-link alias is downgraded to a relative key."""
    root = tmp_path / "root"
    root.mkdir()
    base = root / "alpha.md"
    alias = root / "beta.md"
    base.write_text("evidence", encoding="utf-8")
    os.link(base, alias)

    first = crawl_local_structure(root, approval_for(root))
    first_entries = {entry.relative_hierarchy: entry for entry in first.entries}
    identity = f"{base.stat().st_dev}:{base.stat().st_ino}"

    assert first_entries["alpha.md"].provider_item_key == identity
    assert first_entries["beta.md"].provider_item_key == "beta.md"

    alias.rename(root / "gamma.md")
    renamed = crawl_local_structure(
        root,
        approval_for(root),
        prior_entries=first.entries,
    )
    renamed_entries = {entry.relative_hierarchy: entry for entry in renamed.entries}

    assert renamed_entries["alpha.md"].provider_item_key == identity
    assert renamed_entries["gamma.md"].provider_item_key == "beta.md"
    assert renamed_entries["alpha.md"].node_id == first_entries["alpha.md"].node_id
    assert renamed_entries["gamma.md"].node_id == first_entries["beta.md"].node_id


def test_structural_crawl_retains_hard_link_alias_ids_after_alias_rename(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original = root / "original.md"
    alias = root / "alias.md"
    original.write_text("evidence", encoding="utf-8")
    os.link(original, alias)
    first = crawl_local_structure(root, approval_for(root))
    prior_entries = {entry.relative_hierarchy: entry for entry in first.entries}

    alias.rename(root / "renamed-alias.md")
    renamed = crawl_local_structure(root, approval_for(root), prior_entries=first.entries)
    renamed_entries = {entry.relative_hierarchy: entry for entry in renamed.entries}

    assert renamed_entries["original.md"].node_id == prior_entries["original.md"].node_id
    assert renamed_entries["renamed-alias.md"].node_id == prior_entries["alias.md"].node_id


def test_structural_crawl_reserves_hard_link_keys_before_alias_rename(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original = root / "original.md"
    alias = root / "zzz-alias.md"
    original.write_text("evidence", encoding="utf-8")
    os.link(original, alias)
    first = crawl_local_structure(root, approval_for(root))
    prior_entries = {entry.relative_hierarchy: entry for entry in first.entries}

    alias.rename(root / "aaa-alias.md")
    renamed = crawl_local_structure(root, approval_for(root), prior_entries=first.entries)
    renamed_entries = {entry.relative_hierarchy: entry for entry in renamed.entries}

    assert {"original.md", "aaa-alias.md"} <= renamed_entries.keys()
    assert renamed_entries["original.md"].node_id == prior_entries["original.md"].node_id
    assert (
        renamed_entries["aaa-alias.md"].node_id
        == prior_entries["zzz-alias.md"].node_id
    )
    assert (
        renamed_entries["original.md"].provider_item_key
        == prior_entries["original.md"].provider_item_key
    )
    assert (
        renamed_entries["aaa-alias.md"].provider_item_key
        == prior_entries["zzz-alias.md"].provider_item_key
    )


def test_structural_crawl_is_deterministic_and_retains_ids_after_rename(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "zeta.md").write_text("zeta", encoding="utf-8")
    (root / "alpha").mkdir()
    (root / "alpha" / "entry.md").write_text("entry", encoding="utf-8")

    first = crawl_local_structure(root, approval_for(root))
    second = crawl_local_structure(root, approval_for(root), prior_entries=first.entries)
    (root / "zeta.md").rename(root / "renamed.md")
    renamed = crawl_local_structure(root, approval_for(root), prior_entries=second.entries)

    assert [entry.relative_hierarchy for entry in first.entries] == [
        entry.relative_hierarchy for entry in second.entries
    ]
    assert [entry.relative_hierarchy for entry in first.entries] == sorted(
        entry.relative_hierarchy for entry in first.entries
    )
    first_file = next(entry for entry in first.entries if entry.relative_hierarchy == "zeta.md")
    renamed_file = next(
        entry for entry in renamed.entries if entry.relative_hierarchy == "renamed.md"
    )
    assert renamed_file.node_id == first_file.node_id


def test_streaming_sink_exposes_only_a_completed_preseal_planning_session(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "README.md").write_text("safe baseline", encoding="utf-8")
    sink = StreamingStructureSink(
        tmp_path / "authority.pm-pack",
        tmp_path / "authority.catalog.sqlite",
    )

    with pytest.raises(ValueError, match="authority receipts are incomplete"):
        sink.planning_session("adaptive-frontier-v1")

    list(iter_local_structure_events(root, approval_for(root), sink=sink))
    session = sink.planning_session("adaptive-frontier-v1")
    with pytest.raises(ValueError, match="planning session is active"):
        sink.planning_session("adaptive-frontier-v1")
    session.finish()

    artifacts = sink.seal()

    assert sink.state is StreamingStructureSinkState.SEALED
    assert artifacts.catalog_seal.node_count > 0
    assert "_catalog_writer" not in StreamingStructureSink.__dict__
    assert "connection" not in StreamingStructureSink.__dict__


def test_streaming_sink_planning_fault_aborts_without_new_authority(
    tmp_path: Path,
) -> None:
    """Breaks if an aborted selector session can still publish a partial catalog."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "README.md").write_text("safe baseline", encoding="utf-8")
    pack_path = tmp_path / "authority.pm-pack"
    catalog_path = tmp_path / "authority.catalog.sqlite"
    sink = StreamingStructureSink(pack_path, catalog_path)
    list(iter_local_structure_events(root, approval_for(root), sink=sink))

    session = sink.planning_session("adaptive-frontier-v1")
    session.abort()
    with pytest.raises(ValueError, match="aborted"):
        session.fair_seed_page(None, 1)
    # The prepare caller owns an unsuccessful session and aborts the whole staging sink.
    sink.abort()

    with pytest.raises(ValueError, match="not available"):
        sink.planning_session("adaptive-frontier-v1")
    assert sink.state is StreamingStructureSinkState.ABORTED
    assert not pack_path.exists()
    assert not catalog_path.exists()
