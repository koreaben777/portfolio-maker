from __future__ import annotations

import multiprocessing
import os
import signal
import threading
from pathlib import Path

import pytest

import portfolio_maker.infrastructure.semantic_crawler as semantic_crawler_module
from portfolio_maker.application.approval import SourceApproval
from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.infrastructure.semantic_crawler import (
    StructuralCrawlRootError,
    crawl_local_structure,
)


def _blocking_directory_enumerator(path: Path):
    if path.name == "blocked":
        threading.Event().wait()
    return semantic_crawler_module._materialize_directory(path)


def _always_blocking_directory_enumerator(_path: Path):
    threading.Event().wait()


def _termination_resistant_directory_enumerator(_path: Path):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    threading.Event().wait()


def _crashing_directory_enumerator(_path: Path):
    os._exit(73)


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
