from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, replace
from multiprocessing import get_context
from multiprocessing.connection import Connection, wait
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
from pathlib import Path
from time import monotonic
from typing import Any

from portfolio_maker.application.approval import SourceApproval
from portfolio_maker.domain.semantic_models import (
    AnalysisStatus,
    SemanticNodeKind,
    stable_node_id,
    stable_source_id,
)
from portfolio_maker.infrastructure.policy import FilePolicy
from portfolio_maker.infrastructure.presentation import normalize_label


class StructuralCrawlRootError(ValueError):
    pass


class StructuralCrawlRootTimeoutError(StructuralCrawlRootError):
    pass


class StructuralCrawlTimeoutBudgetError(StructuralCrawlRootError):
    def __init__(
        self,
        *,
        source_id: str,
        timed_out_relative_hierarchies: tuple[str, ...],
        timed_out_directory_count: int,
        unreadable_directory_count: int,
        consumed_timeout_wait_seconds: float,
    ) -> None:
        super().__init__("Structural crawl timeout budget exhausted")
        self.source_id = source_id
        self.timed_out_relative_hierarchies = timed_out_relative_hierarchies
        self.timed_out_directory_count = timed_out_directory_count
        self.unreadable_directory_count = unreadable_directory_count
        self.consumed_timeout_wait_seconds = consumed_timeout_wait_seconds


class StructuralCrawlWorkerError(ValueError):
    pass


_DIRECTORY_WORKER_PROTOCOL = "portfolio-semantic-directory-v1"
DEFAULT_DIRECTORY_TIMEOUT_SECONDS = 30.0
MAX_DIRECTORY_TIMEOUT_SECONDS = 300.0
DEFAULT_DIRECTORY_TIMEOUT_BUDGET_SECONDS = 120.0
MIN_DIRECTORY_TIMEOUT_BUDGET_SECONDS = 0.01
MAX_DIRECTORY_TIMEOUT_BUDGET_SECONDS = 3600.0
_WORKER_START_SECONDS = 5.0
_WORKER_JOIN_SECONDS = 1.0


@dataclass(frozen=True)
class _MaterializedDirectoryEntry:
    name: str
    st_mode: int | None
    st_dev: int | None
    st_ino: int | None
    readable: bool


@dataclass(frozen=True)
class _DirectoryEnumeration:
    entries: tuple[_MaterializedDirectoryEntry, ...]
    unreadable: bool = False
    timed_out: bool = False
    timeout_wait_seconds: float = 0.0


def _materialize_directory(path: Path) -> tuple[_MaterializedDirectoryEntry, ...]:
    entries: list[_MaterializedDirectoryEntry] = []
    with os.scandir(path) as scan:
        for child in scan:
            try:
                child_stat = child.stat(follow_symlinks=False)
            except OSError:
                entries.append(
                    _MaterializedDirectoryEntry(
                        name=child.name,
                        st_mode=None,
                        st_dev=None,
                        st_ino=None,
                        readable=False,
                    )
                )
                continue
            entries.append(
                _MaterializedDirectoryEntry(
                    name=child.name,
                    st_mode=child_stat.st_mode,
                    st_dev=(
                        child_stat.st_dev
                        if type(child_stat.st_dev) is int
                        else None
                    ),
                    st_ino=(
                        child_stat.st_ino
                        if type(child_stat.st_ino) is int
                        else None
                    ),
                    readable=True,
                )
            )
    return tuple(sorted(entries, key=lambda entry: entry.name))


def _directory_worker_main(
    connection: Connection,
    enumerator: Callable[[Path], tuple[_MaterializedDirectoryEntry, ...]],
) -> None:
    try:
        connection.send(
            {
                "protocol": _DIRECTORY_WORKER_PROTOCOL,
                "status": "ready",
            }
        )
        while True:
            request = connection.recv()
            if request == {
                "protocol": _DIRECTORY_WORKER_PROTOCOL,
                "command": "close",
            }:
                return
            if (
                type(request) is not dict
                or set(request) != {"protocol", "command", "request_id", "path"}
                or request.get("protocol") != _DIRECTORY_WORKER_PROTOCOL
                or request.get("command") != "enumerate"
                or type(request.get("request_id")) is not int
                or type(request.get("path")) is not str
            ):
                return
            request_id = request["request_id"]
            try:
                materialized = enumerator(Path(request["path"]))
                for entry in materialized:
                    connection.send(
                        {
                            "protocol": _DIRECTORY_WORKER_PROTOCOL,
                            "request_id": request_id,
                            "status": "entry",
                            "entry": {
                                "name": entry.name,
                                "st_mode": entry.st_mode,
                                "st_dev": entry.st_dev,
                                "st_ino": entry.st_ino,
                                "readable": entry.readable,
                            },
                        }
                    )
                response: dict[str, object] = {
                    "protocol": _DIRECTORY_WORKER_PROTOCOL,
                    "request_id": request_id,
                    "status": "ok",
                    "entry_count": len(materialized),
                }
            except OSError:
                response = {
                    "protocol": _DIRECTORY_WORKER_PROTOCOL,
                    "request_id": request_id,
                    "status": "unreadable",
                }
            except BaseException:
                response = {
                    "protocol": _DIRECTORY_WORKER_PROTOCOL,
                    "request_id": request_id,
                    "status": "internal_error",
                }
            connection.send(response)
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        connection.close()


class _DirectoryEnumerationWorker:
    def __init__(
        self,
        *,
        process_context: BaseContext | None = None,
        enumerator: Callable[
            [Path], tuple[_MaterializedDirectoryEntry, ...]
        ] = _materialize_directory,
        worker_target: Callable[[Connection, Callable[..., Any]], None] = (
            _directory_worker_main
        ),
    ) -> None:
        self._context = process_context or get_context("spawn")
        self._enumerator = enumerator
        self._worker_target = worker_target
        self._request_id = 0
        self._connection: Connection | None = None
        self._process: BaseProcess | None = None

    def __enter__(self) -> _DirectoryEnumerationWorker:
        self._start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def enumerate(
        self,
        path: Path,
        *,
        timeout_seconds: float,
        replace_after_timeout: bool = True,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> _DirectoryEnumeration:
        if self._process is None or self._connection is None:
            raise StructuralCrawlWorkerError(
                "semantic directory worker failed safely"
            )
        self._request_id += 1
        request_id = self._request_id
        try:
            self._connection.send(
                {
                    "protocol": _DIRECTORY_WORKER_PROTOCOL,
                    "command": "enumerate",
                    "request_id": request_id,
                    "path": os.fspath(path),
                }
            )
        except (BrokenPipeError, EOFError, OSError) as error:
            raise StructuralCrawlWorkerError(
                "semantic directory worker failed safely"
            ) from error

        started_at = monotonic_clock()
        deadline = started_at + timeout_seconds
        entries: list[_MaterializedDirectoryEntry] = []
        while True:
            remaining = deadline - monotonic_clock()
            if remaining <= 0:
                timeout_wait_seconds = max(0.0, monotonic_clock() - started_at)
                self._stop(graceful=False)
                if replace_after_timeout:
                    self.replace_after_timeout()
                return _DirectoryEnumeration(
                    entries=(),
                    timed_out=True,
                    timeout_wait_seconds=timeout_wait_seconds,
                )
            ready = wait(
                [self._connection, self._process.sentinel],
                timeout=remaining,
            )
            if not ready:
                timeout_wait_seconds = max(0.0, monotonic_clock() - started_at)
                self._stop(graceful=False)
                if replace_after_timeout:
                    self.replace_after_timeout()
                return _DirectoryEnumeration(
                    entries=(),
                    timed_out=True,
                    timeout_wait_seconds=timeout_wait_seconds,
                )
            if self._connection not in ready:
                self._stop(graceful=False)
                raise StructuralCrawlWorkerError(
                    "semantic directory worker failed safely"
                )
            try:
                response = self._connection.recv()
            except (BrokenPipeError, EOFError, OSError) as error:
                self._stop(graceful=False)
                raise StructuralCrawlWorkerError(
                    "semantic directory worker failed safely"
                ) from error
            terminal = self._consume_response(
                response,
                request_id,
                entries,
            )
            if terminal is not None:
                return terminal

    def close(self) -> None:
        self._stop(graceful=True)

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def _start(self) -> None:
        parent, child = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=self._worker_target,
            args=(child, self._enumerator),
            daemon=True,
        )
        self._connection = parent
        self._process = process
        try:
            process.start()
            child.close()
            ready = wait(
                [parent, process.sentinel],
                timeout=_WORKER_START_SECONDS,
            )
            if parent not in ready:
                raise StructuralCrawlWorkerError(
                    "semantic directory worker failed safely"
                )
            response = parent.recv()
            if response != {
                "protocol": _DIRECTORY_WORKER_PROTOCOL,
                "status": "ready",
            }:
                raise StructuralCrawlWorkerError(
                    "semantic directory worker failed safely"
                )
        except BaseException as error:
            try:
                child.close()
            except BaseException:
                pass
            cleanup_error: BaseException | None = None
            try:
                self._stop(graceful=False)
            except BaseException as caught_cleanup_error:
                cleanup_error = caught_cleanup_error
            if self._process is not None or self._connection is not None:
                raise StructuralCrawlWorkerError(
                    "semantic directory worker could not be recovered"
                ) from (cleanup_error or error)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(cleanup_error, (KeyboardInterrupt, SystemExit)):
                raise cleanup_error
            if isinstance(error, StructuralCrawlWorkerError):
                raise
            if cleanup_error is not None:
                if isinstance(cleanup_error, StructuralCrawlWorkerError):
                    raise cleanup_error
                raise StructuralCrawlWorkerError(
                    "semantic directory worker could not be recovered"
                ) from cleanup_error
            raise StructuralCrawlWorkerError(
                "semantic directory worker failed safely"
            ) from error

    def _replace(self) -> None:
        self._stop(graceful=False)
        self._start()

    def replace_after_timeout(self) -> None:
        if self._process is not None or self._connection is not None:
            raise StructuralCrawlWorkerError(
                "semantic directory worker failed safely"
            )
        self._start()

    def _stop(self, *, graceful: bool) -> None:
        process = self._process
        connection = self._connection
        if process is None:
            if connection is not None:
                connection.close()
                self._connection = None
            return

        errors: list[BaseException] = []

        def record(action: Callable[[], None]) -> bool:
            try:
                action()
                return True
            except BaseException as error:
                errors.append(error)
                return False

        def is_alive() -> bool:
            try:
                return process.is_alive()
            except BaseException as error:
                errors.append(error)
                return True

        try:
            started = process.pid is not None
        except BaseException as error:
            errors.append(error)
            started = True

        if started and graceful and is_alive() and connection is not None:
            try:
                connection.send(
                    {
                        "protocol": _DIRECTORY_WORKER_PROTOCOL,
                        "command": "close",
                    }
                )
            except (BrokenPipeError, EOFError, OSError):
                pass
            except BaseException as error:
                errors.append(error)
            record(lambda: process.join(_WORKER_JOIN_SECONDS))

        if started and is_alive():
            record(process.terminate)
            record(lambda: process.join(_WORKER_JOIN_SECONDS))
        if started and is_alive():
            record(process.kill)
            record(lambda: process.join(_WORKER_JOIN_SECONDS))
        if started and is_alive():
            record(lambda: process.join(_WORKER_JOIN_SECONDS))

        worker_alive = started and is_alive()
        connection_closed = connection is None or record(connection.close)
        process_closed = not worker_alive and record(process.close)
        if worker_alive or not connection_closed or not process_closed:
            raise StructuralCrawlWorkerError(
                "semantic directory worker could not be recovered"
            ) from (errors[0] if errors else None)

        self._process = None
        self._connection = None
        for error in errors:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise error
        if errors:
            raise StructuralCrawlWorkerError(
                "semantic directory worker could not be recovered"
            ) from errors[0]

    @classmethod
    def _consume_response(
        cls,
        response: object,
        request_id: int,
        entries: list[_MaterializedDirectoryEntry],
    ) -> _DirectoryEnumeration | None:
        if (
            type(response) is not dict
            or response.get("protocol") != _DIRECTORY_WORKER_PROTOCOL
            or response.get("request_id") != request_id
            or response.get("status")
            not in {"entry", "ok", "unreadable", "internal_error"}
        ):
            raise StructuralCrawlWorkerError(
                "semantic directory worker failed safely"
            )
        status = response["status"]
        if status == "entry":
            if set(response) != {"protocol", "request_id", "status", "entry"}:
                raise StructuralCrawlWorkerError(
                    "semantic directory worker failed safely"
                )
            entries.append(cls._validate_entry(response["entry"]))
            return None
        if response["status"] == "internal_error":
            if set(response) != {"protocol", "request_id", "status"}:
                raise StructuralCrawlWorkerError(
                    "semantic directory worker failed safely"
                )
            raise StructuralCrawlWorkerError(
                "semantic directory worker failed safely"
            )
        if response["status"] == "unreadable":
            if (
                set(response) != {"protocol", "request_id", "status"}
                or entries
            ):
                raise StructuralCrawlWorkerError(
                    "semantic directory worker failed safely"
                )
            return _DirectoryEnumeration(entries=(), unreadable=True)
        if (
            set(response) != {"protocol", "request_id", "status", "entry_count"}
            or response.get("entry_count") != len(entries)
        ):
            raise StructuralCrawlWorkerError(
                "semantic directory worker failed safely"
            )
        if [entry.name for entry in entries] != sorted(
            entry.name for entry in entries
        ) or len({entry.name for entry in entries}) != len(entries):
            raise StructuralCrawlWorkerError(
                "semantic directory worker failed safely"
            )
        return _DirectoryEnumeration(entries=tuple(entries))

    @staticmethod
    def _validate_entry(raw_entry: object) -> _MaterializedDirectoryEntry:
        if (
            type(raw_entry) is not dict
            or set(raw_entry)
            != {"name", "st_mode", "st_dev", "st_ino", "readable"}
            or type(raw_entry.get("name")) is not str
            or not raw_entry["name"]
            or raw_entry["name"] in {".", ".."}
            or os.sep in raw_entry["name"]
            or os.altsep is not None
            and os.altsep in raw_entry["name"]
            or type(raw_entry.get("readable")) is not bool
            or raw_entry["readable"]
            and (
                type(raw_entry.get("st_mode")) is not int
                or raw_entry.get("st_dev") is not None
                and type(raw_entry.get("st_dev")) is not int
                or raw_entry.get("st_ino") is not None
                and type(raw_entry.get("st_ino")) is not int
            )
            or not raw_entry["readable"]
            and any(
                raw_entry.get(field) is not None
                for field in ("st_mode", "st_dev", "st_ino")
            )
        ):
            raise StructuralCrawlWorkerError(
                "semantic directory worker failed safely"
            )
        return _MaterializedDirectoryEntry(
            name=raw_entry["name"],
            st_mode=raw_entry["st_mode"],
            st_dev=raw_entry["st_dev"],
            st_ino=raw_entry["st_ino"],
            readable=raw_entry["readable"],
        )


@dataclass(frozen=True)
class StructuralEntry:
    node_id: str
    source_id: str
    parent_node_id: str | None
    kind: SemanticNodeKind
    display_name: str
    relative_hierarchy: str
    absolute_path: Path
    provider_item_key: str
    content_fingerprint: str | None
    device: int | None
    inode: int | None
    status: AnalysisStatus


@dataclass(frozen=True)
class StructuralCrawlError:
    relative_hierarchy: str
    status: AnalysisStatus
    timed_out: bool = False


@dataclass(frozen=True)
class StructuralCrawl:
    entries: tuple[StructuralEntry, ...]
    errors: tuple[StructuralCrawlError, ...]
    timed_out_directory_count: int = 0
    unreadable_directory_count: int = 0
    consumed_timeout_wait_seconds: float = 0.0


def crawl_local_structure(
    root: Path,
    approval: SourceApproval,
    prior_entries: tuple[StructuralEntry, ...] = (),
    *,
    directory_timeout_seconds: float = DEFAULT_DIRECTORY_TIMEOUT_SECONDS,
    directory_timeout_budget_seconds: float = DEFAULT_DIRECTORY_TIMEOUT_BUDGET_SECONDS,
    _process_context: BaseContext | None = None,
    _enumerator: Callable[
        [Path], tuple[_MaterializedDirectoryEntry, ...]
    ] = _materialize_directory,
    _monotonic: Callable[[], float] = monotonic,
) -> StructuralCrawl:
    root_path = _resolve_root(root)
    root_stat = _lstat(root_path)
    root_key, root_device, root_inode = _provider_identity(root_stat, ".")
    source_id = stable_source_id("local", root_key)
    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    prior_node_ids = {
        (entry.source_id, entry.provider_item_key): entry.node_id
        for entry in prior_entries
    }
    (
        reserved_prior_entries,
        renamed_prior_entries_by_inode,
        reserved_provider_keys,
    ) = _reserve_prior_entries(root_path, source_id, prior_entries, policy)
    renamed_prior_entry_indexes: dict[tuple[int, int], int] = {}
    entries: dict[str, StructuralEntry] = {}
    assigned_provider_keys: set[str] = set()
    errors: list[StructuralCrawlError] = []
    timed_out_directory_count = 0
    unreadable_directory_count = 0
    consumed_timeout_wait_seconds = 0.0
    timed_out_relative_hierarchies: list[str] = []

    def add_entry(
        path: Path,
        relative_hierarchy: str,
        parent_node_id: str | None,
        kind: SemanticNodeKind,
        status: AnalysisStatus,
        path_stat: os.stat_result,
    ) -> StructuralEntry:
        provider_item_key, device, inode = _provider_identity(path_stat, relative_hierarchy)
        if device is not None and inode is not None:
            inode_key = provider_item_key
            prior_entry = reserved_prior_entries.get(relative_hierarchy)
            if prior_entry is not None:
                provider_item_key = prior_entry.provider_item_key
            else:
                prior_inode_entries = renamed_prior_entries_by_inode.get((device, inode), ())
                prior_index = renamed_prior_entry_indexes.get((device, inode), 0)
                if prior_index < len(prior_inode_entries):
                    provider_item_key = prior_inode_entries[prior_index].provider_item_key
                    renamed_prior_entry_indexes[(device, inode)] = prior_index + 1
                elif (
                    inode_key in reserved_provider_keys
                    or inode_key in assigned_provider_keys
                ):
                    provider_item_key = relative_hierarchy
            assigned_provider_keys.add(provider_item_key)
        node_id = prior_node_ids.get(
            (source_id, provider_item_key), stable_node_id(source_id, provider_item_key)
        )
        entry = StructuralEntry(
            node_id=node_id,
            source_id=source_id,
            parent_node_id=parent_node_id,
            kind=kind,
            display_name=normalize_label(path.name),
            relative_hierarchy=relative_hierarchy,
            absolute_path=path,
            provider_item_key=provider_item_key,
            content_fingerprint=None,
            device=device,
            inode=inode,
            status=status,
        )
        entries.setdefault(node_id, entry)
        return entries[node_id]

    source = add_entry(
        root_path,
        ".",
        None,
        SemanticNodeKind.SOURCE,
        AnalysisStatus.PENDING,
        root_stat,
    )

    def mark_unreadable(
        node_id: str,
        relative_hierarchy: str,
        *,
        timed_out: bool = False,
    ) -> None:
        nonlocal timed_out_directory_count, unreadable_directory_count
        entries[node_id] = replace(entries[node_id], status=AnalysisStatus.UNREADABLE)
        errors.append(
            StructuralCrawlError(
                relative_hierarchy,
                AnalysisStatus.UNREADABLE,
                timed_out=timed_out,
            )
        )
        unreadable_directory_count += 1
        timed_out_directory_count += int(timed_out)

    def walk(directory: Path, parent_node_id: str, relative_hierarchy: str) -> None:
        nonlocal consumed_timeout_wait_seconds
        remaining_budget = max(
            0.0,
            directory_timeout_budget_seconds - consumed_timeout_wait_seconds,
        )
        requested_timeout = min(directory_timeout_seconds, remaining_budget)
        enumeration = worker.enumerate(
            directory,
            timeout_seconds=requested_timeout,
            replace_after_timeout=False,
            monotonic_clock=_monotonic,
        )
        if enumeration.timed_out:
            consumed_timeout_wait_seconds = min(
                directory_timeout_budget_seconds,
                consumed_timeout_wait_seconds + enumeration.timeout_wait_seconds,
            )
            timed_out_relative_hierarchies.append(relative_hierarchy)
            if consumed_timeout_wait_seconds >= directory_timeout_budget_seconds:
                raise StructuralCrawlTimeoutBudgetError(
                    source_id=source_id,
                    timed_out_relative_hierarchies=tuple(
                        timed_out_relative_hierarchies
                    ),
                    timed_out_directory_count=(
                        timed_out_directory_count + 1
                    ),
                    unreadable_directory_count=(
                        unreadable_directory_count + 1
                    ),
                    consumed_timeout_wait_seconds=consumed_timeout_wait_seconds,
                )
            worker.replace_after_timeout()
            if relative_hierarchy == ".":
                raise StructuralCrawlRootTimeoutError(
                    "Structural crawl root timed out"
                )
            mark_unreadable(
                parent_node_id,
                relative_hierarchy,
                timed_out=True,
            )
            return
        if enumeration.unreadable:
            mark_unreadable(parent_node_id, relative_hierarchy)
            return

        for child in enumeration.entries:
            child_path = directory / child.name
            child_relative = _relative_hierarchy(root_path, child_path)
            if not child.readable:
                errors.append(
                    StructuralCrawlError(child_relative, AnalysisStatus.UNREADABLE)
                )
                continue
            child_stat = child
            assert child_stat.st_mode is not None

            if stat.S_ISLNK(child_stat.st_mode):
                add_entry(
                    child_path,
                    child_relative,
                    parent_node_id,
                    SemanticNodeKind.FILE,
                    AnalysisStatus.UNSUPPORTED,
                    child_stat,
                )
                errors.append(
                    StructuralCrawlError(child_relative, AnalysisStatus.UNSUPPORTED)
                )
                continue

            classification = policy.classify_path(child_path)
            if stat.S_ISDIR(child_stat.st_mode):
                directory_entry = add_entry(
                    child_path,
                    child_relative,
                    parent_node_id,
                    SemanticNodeKind.DIRECTORY,
                    (
                        AnalysisStatus.UNSUPPORTED
                        if classification != "candidate"
                        else AnalysisStatus.PENDING
                    ),
                    child_stat,
                )
                if classification == "candidate":
                    walk(child_path, directory_entry.node_id, child_relative)
                continue

            if stat.S_ISREG(child_stat.st_mode):
                add_entry(
                    child_path,
                    child_relative,
                    parent_node_id,
                    SemanticNodeKind.FILE,
                    (
                        AnalysisStatus.PENDING
                        if classification == "candidate"
                        else AnalysisStatus.UNSUPPORTED
                    ),
                    child_stat,
                )
                continue

            add_entry(
                child_path,
                child_relative,
                parent_node_id,
                SemanticNodeKind.FILE,
                AnalysisStatus.UNSUPPORTED,
                child_stat,
            )
            errors.append(StructuralCrawlError(child_relative, AnalysisStatus.UNSUPPORTED))

    with _DirectoryEnumerationWorker(
        process_context=_process_context,
        enumerator=_enumerator,
    ) as worker:
        root_classification = policy.classify_path(root_path)
        if root_classification == "candidate":
            walk(root_path, source.node_id, ".")
        else:
            entries[source.node_id] = replace(
                source,
                status=AnalysisStatus.UNSUPPORTED,
            )

    return StructuralCrawl(
        entries=tuple(
            sorted(
                entries.values(),
                key=lambda entry: (entry.relative_hierarchy, entry.node_id),
            )
        ),
        errors=tuple(errors),
        timed_out_directory_count=timed_out_directory_count,
        unreadable_directory_count=unreadable_directory_count,
        consumed_timeout_wait_seconds=consumed_timeout_wait_seconds,
    )


def _resolve_root(root: Path) -> Path:
    root_stat = _lstat(root)
    if stat.S_ISLNK(root_stat.st_mode):
        raise StructuralCrawlRootError("Structural crawl root is a symbolic link")
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise StructuralCrawlRootError("Structural crawl root cannot be resolved") from error
    if not resolved.is_dir():
        raise StructuralCrawlRootError("Structural crawl root is not a directory")
    return resolved


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as error:
        raise StructuralCrawlRootError("Structural crawl root cannot be read") from error


def _provider_identity(path_stat: os.stat_result, relative_hierarchy: str) -> tuple[str, int | None, int | None]:
    device = path_stat.st_dev if type(path_stat.st_dev) is int else None
    inode = path_stat.st_ino if type(path_stat.st_ino) is int else None
    if device is not None and inode is not None and inode > 0:
        return f"{device}:{inode}", device, inode
    return relative_hierarchy, None, None


def _reserve_prior_entries(
    root_path: Path,
    source_id: str,
    prior_entries: tuple[StructuralEntry, ...],
    policy: FilePolicy,
) -> tuple[
    dict[str, StructuralEntry],
    dict[tuple[int, int], tuple[StructuralEntry, ...]],
    set[str],
]:
    source_prior_entries = tuple(
        entry for entry in prior_entries if entry.source_id == source_id
    )
    reserved_entries: dict[str, StructuralEntry] = {}
    for entry in source_prior_entries:
        path = (
            root_path
            if entry.relative_hierarchy == "."
            else root_path / entry.relative_hierarchy
        )
        if policy.classify_path(path) != "candidate":
            continue
        try:
            _, device, inode = _provider_identity(_lstat(path), entry.relative_hierarchy)
        except StructuralCrawlRootError:
            continue
        if (device, inode) == (entry.device, entry.inode):
            reserved_entries[entry.relative_hierarchy] = entry

    renamed_entries_by_inode: dict[tuple[int, int], list[StructuralEntry]] = {}
    for entry in source_prior_entries:
        if entry.relative_hierarchy in reserved_entries:
            continue
        if entry.device is None or entry.inode is None:
            continue
        renamed_entries_by_inode.setdefault((entry.device, entry.inode), []).append(entry)

    return (
        reserved_entries,
        {
            identity: tuple(
                sorted(
                    entries,
                    key=lambda entry: (
                        entry.relative_hierarchy,
                        entry.provider_item_key,
                    ),
                )
            )
            for identity, entries in renamed_entries_by_inode.items()
        },
        {entry.provider_item_key for entry in reserved_entries.values()},
    )


def _relative_hierarchy(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
