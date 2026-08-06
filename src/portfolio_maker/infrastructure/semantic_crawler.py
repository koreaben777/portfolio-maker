from __future__ import annotations

import errno
import os
import sqlite3
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from multiprocessing import get_context
from multiprocessing.connection import Connection, wait
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
from pathlib import Path
from queue import Full
from time import monotonic
from typing import Any
from uuid import uuid4

from portfolio_maker.application.approval import SourceApproval
from portfolio_maker.domain.semantic_models import (
    AnalysisStatus,
    SemanticNodeKind,
    stable_node_id,
    stable_source_id,
)
from portfolio_maker.infrastructure.policy import FilePolicy
from portfolio_maker.infrastructure.presentation import normalize_label
from portfolio_maker.infrastructure.semantic_catalog import (
    BaselineRecord,
    CatalogSeal,
    NodeRecord,
    PayloadRefRecord,
    SemanticCatalogWriter,
)
from portfolio_maker.infrastructure.semantic_pack import (
    PMPackWriter,
    PackSeal,
)


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
DEFAULT_STREAMING_WORKER_QUEUE_SIZE = 256


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


_DEFAULT_DIRECTORY_ENUMERATION_WORKER = _DirectoryEnumerationWorker
_DEFAULT_DIRECTORY_WORKER_REPLACE = _DirectoryEnumerationWorker.replace_after_timeout


def _stream_directory_entries(path: Path):
    """Yield directory entries one at a time for the v3 crawl path."""
    with os.scandir(path) as scan:
        for child in scan:
            try:
                child_stat = child.stat(follow_symlinks=False)
            except OSError:
                yield _MaterializedDirectoryEntry(
                    name=child.name,
                    st_mode=None,
                    st_dev=None,
                    st_ino=None,
                    readable=False,
                )
                continue
            yield _MaterializedDirectoryEntry(
                name=child.name,
                st_mode=child_stat.st_mode,
                st_dev=(child_stat.st_dev if type(child_stat.st_dev) is int else None),
                st_ino=(child_stat.st_ino if type(child_stat.st_ino) is int else None),
                readable=True,
            )


def _streaming_directory_worker_main(
    connection: Connection,
    event_queue: Any,
    enumerator: Callable[[Path], Any],
    producer_progress: Any,
    producer_backpressure: Any,
    producer_backpressure_seconds: Any,
) -> None:
    try:
        connection.send({"protocol": _DIRECTORY_WORKER_PROTOCOL, "status": "ready"})
        while True:
            request = connection.recv()
            if request == {"protocol": _DIRECTORY_WORKER_PROTOCOL, "command": "close"}:
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
            count = 0
            try:
                for entry in enumerator(Path(request["path"])):
                    payload = {
                        "name": entry.name,
                        "st_mode": entry.st_mode,
                        "st_dev": entry.st_dev,
                        "st_ino": entry.st_ino,
                        "readable": entry.readable,
                    }
                    try:
                        event_queue.put(payload, block=False)
                    except Full:
                        blocked_at = monotonic()
                        with producer_backpressure.get_lock():
                            producer_backpressure.value += 1
                        event_queue.put(payload)
                        with producer_backpressure_seconds.get_lock():
                            producer_backpressure_seconds.value += max(
                                0.0,
                                monotonic() - blocked_at,
                            )
                    with producer_progress.get_lock():
                        producer_progress.value += 1
                    count += 1
                response: dict[str, object] = {
                    "protocol": _DIRECTORY_WORKER_PROTOCOL,
                    "request_id": request_id,
                    "status": "ok",
                    "entry_count": count,
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


class _StreamingDirectoryEnumerationWorker:
    """A bounded event queue adapter for the v3 traversal path."""

    def __init__(
        self,
        *,
        process_context: BaseContext | None,
        enumerator: Callable[[Path], Any],
        queue_size: int,
        observer: CrawlObserver,
    ) -> None:
        self._context = process_context or get_context("spawn")
        self._enumerator = enumerator
        self._queue_size = queue_size
        self._observer = observer
        self._observer.queue_maxsize = max(
            self._observer.queue_maxsize,
            queue_size,
        )
        self._request_id = 0
        self._connection: Connection | None = None
        self._process: BaseProcess | None = None
        self._event_queue: Any | None = None
        self._producer_progress: Any | None = None
        self._producer_backpressure: Any | None = None
        self._producer_backpressure_seconds: Any | None = None
        self._active_observed = False
        self.last_enumeration: _DirectoryEnumeration | None = None

    def __enter__(self) -> _StreamingDirectoryEnumerationWorker:
        self._start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def iter_entries(
        self,
        path: Path,
        *,
        timeout_seconds: float,
        monotonic_clock: Callable[[], float],
    ):
        if self._process is None or self._connection is None or self._event_queue is None:
            raise StructuralCrawlWorkerError("semantic directory worker failed safely")
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
            raise StructuralCrawlWorkerError("semantic directory worker failed safely") from error

        started_at = monotonic_clock()
        backpressure_started_at = self._backpressure_seconds()
        received = 0
        expected_count: int | None = None
        completed = False
        try:
            while expected_count is None or received < expected_count:
                elapsed = self._elapsed_without_backpressure(
                    started_at,
                    backpressure_started_at,
                    monotonic_clock,
                )
                remaining = timeout_seconds - elapsed
                if remaining <= 0:
                    self._timeout(
                        started_at,
                        backpressure_started_at,
                        monotonic_clock,
                    )
                    return
                ready = wait(
                    [self._connection, self._process.sentinel, self._event_queue._reader],
                    timeout=remaining,
                )
                if not ready:
                    self._timeout(
                        started_at,
                        backpressure_started_at,
                        monotonic_clock,
                    )
                    return
                if self._event_queue._reader in ready:
                    try:
                        raw_entry = self._event_queue.get()
                    except (EOFError, OSError) as error:
                        raise StructuralCrawlWorkerError(
                            "semantic directory worker failed safely"
                        ) from error
                    self._observe_queue_depth()
                    received += 1
                    yield _DirectoryEnumerationWorker._validate_entry(raw_entry)
                if self._connection in ready:
                    try:
                        response = self._connection.recv()
                    except (BrokenPipeError, EOFError, OSError) as error:
                        raise StructuralCrawlWorkerError(
                            "semantic directory worker failed safely"
                        ) from error
                    expected_count = self._terminal_count(response, request_id, received)
                    if expected_count is None:
                        completed = True
                        return
                    if received == expected_count:
                        completed = True
                        self.last_enumeration = _DirectoryEnumeration(entries=())
                        return
                if self._connection not in ready and self._process.sentinel in ready:
                    raise StructuralCrawlWorkerError("semantic directory worker failed safely")
            completed = True
            self.last_enumeration = _DirectoryEnumeration(entries=())
        finally:
            if not completed and self._process is not None:
                self._stop(graceful=False)

    def replace_after_timeout(self) -> None:
        if self._process is not None or self._connection is not None:
            raise StructuralCrawlWorkerError("semantic directory worker failed safely")
        self._start()

    def close(self) -> None:
        self._stop(graceful=True)

    def _timeout(
        self,
        started_at: float,
        backpressure_started_at: float,
        monotonic_clock: Callable[[], float],
    ) -> None:
        elapsed = self._elapsed_without_backpressure(
            started_at,
            backpressure_started_at,
            monotonic_clock,
        )
        self._stop(graceful=False)
        self.last_enumeration = _DirectoryEnumeration(
            entries=(), timed_out=True, timeout_wait_seconds=elapsed
        )

    def _terminal_count(
        self,
        response: object,
        request_id: int,
        received: int,
    ) -> int | None:
        if (
            type(response) is not dict
            or response.get("protocol") != _DIRECTORY_WORKER_PROTOCOL
            or response.get("request_id") != request_id
        ):
            raise StructuralCrawlWorkerError("semantic directory worker failed safely")
        status = response.get("status")
        if status == "unreadable":
            if set(response) != {"protocol", "request_id", "status"} or received:
                raise StructuralCrawlWorkerError("semantic directory worker failed safely")
            self.last_enumeration = _DirectoryEnumeration(entries=(), unreadable=True)
            return None
        if status != "ok" or set(response) != {
            "protocol", "request_id", "status", "entry_count"
        }:
            raise StructuralCrawlWorkerError("semantic directory worker failed safely")
        count = response.get("entry_count")
        if type(count) is not int or count < received:
            raise StructuralCrawlWorkerError("semantic directory worker failed safely")
        return count

    def _start(self) -> None:
        if not hasattr(self._context, "Queue"):
            raise StructuralCrawlWorkerError("semantic directory worker failed safely")
        parent, child = self._context.Pipe(duplex=True)
        event_queue = self._context.Queue(maxsize=self._queue_size)
        producer_progress = self._context.Value("L", 0)
        producer_backpressure = self._context.Value("L", 0)
        producer_backpressure_seconds = self._context.Value("d", 0.0)
        process = self._context.Process(
            target=_streaming_directory_worker_main,
            args=(
                child,
                event_queue,
                self._enumerator,
                producer_progress,
                producer_backpressure,
                producer_backpressure_seconds,
            ),
            daemon=True,
        )
        self._connection = parent
        self._process = process
        self._event_queue = event_queue
        self._producer_progress = producer_progress
        self._producer_backpressure = producer_backpressure
        self._producer_backpressure_seconds = producer_backpressure_seconds
        try:
            process.start()
            self._active_observed = True
            self._observer.worker_start_count += 1
            self._observer.active_worker_count += 1
            self._observer.max_active_worker_count = max(
                self._observer.max_active_worker_count,
                self._observer.active_worker_count,
            )
            self._observer._producer_progress_counter = producer_progress
            self._observer._producer_backpressure_counter = producer_backpressure
            child.close()
            ready = wait([parent, process.sentinel], timeout=_WORKER_START_SECONDS)
            if parent not in ready or parent.recv() != {
                "protocol": _DIRECTORY_WORKER_PROTOCOL,
                "status": "ready",
            }:
                raise StructuralCrawlWorkerError("semantic directory worker failed safely")
        except BaseException as error:
            try:
                child.close()
            except BaseException:
                pass
            self._stop(graceful=False)
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            if isinstance(error, StructuralCrawlWorkerError):
                raise
            raise StructuralCrawlWorkerError("semantic directory worker failed safely") from error

    def _stop(self, *, graceful: bool) -> None:
        process = self._process
        connection = self._connection
        event_queue = self._event_queue
        self._process = None
        self._connection = None
        self._event_queue = None
        self._producer_progress = None
        self._producer_backpressure = None
        self._producer_backpressure_seconds = None
        if self._active_observed:
            self._observer.active_worker_count = max(
                0,
                self._observer.active_worker_count - 1,
            )
            self._active_observed = False
        if process is not None:
            try:
                if graceful and process.is_alive() and connection is not None:
                    connection.send({"protocol": _DIRECTORY_WORKER_PROTOCOL, "command": "close"})
                    process.join(_WORKER_JOIN_SECONDS)
                if process.is_alive():
                    process.terminate()
                    process.join(_WORKER_JOIN_SECONDS)
                if process.is_alive():
                    process.kill()
                    process.join(_WORKER_JOIN_SECONDS)
            finally:
                if connection is not None:
                    connection.close()
                process.close()
        elif connection is not None:
            connection.close()
        if event_queue is not None:
            event_queue.close()
            event_queue.join_thread()

    def _observe_queue_depth(self) -> None:
        if self._event_queue is None:
            return
        try:
            queued = self._event_queue.qsize()
        except (NotImplementedError, OSError):
            queued = 0
        self._observer.max_buffered_events = max(
            self._observer.max_buffered_events,
            min(self._queue_size, max(0, queued)),
        )

    def _backpressure_seconds(self) -> float:
        counter = self._producer_backpressure_seconds
        return 0.0 if counter is None else float(counter.value)

    def _elapsed_without_backpressure(
        self,
        started_at: float,
        backpressure_started_at: float,
        monotonic_clock: Callable[[], float],
    ) -> float:
        return max(
            0.0,
            monotonic_clock()
            - started_at
            - max(0.0, self._backpressure_seconds() - backpressure_started_at),
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


class StructuralEventKind(StrEnum):
    NODE_SEEN = "node_seen"
    FILE_ANALYZED = "file_analyzed"
    DIRECTORY_CLOSED = "directory_closed"
    SOURCE_CLOSED = "source_closed"


@dataclass(frozen=True)
class StructuralEvent:
    kind: StructuralEventKind
    node: StructuralEntry
    baseline: LocalBaseline | None = None


@dataclass(frozen=True)
class StreamingStructureArtifacts:
    pack_seal: PackSeal
    catalog_seal: CatalogSeal


class StreamingStructureSinkState(StrEnum):
    ACTIVE = "active"
    FAILED = "failed"
    COMPLETE = "complete"
    SEALED = "sealed"
    ABORTED = "aborted"


class _SinkInvariantStore:
    """Ephemeral indexed receipt state for one streaming authority build."""

    def __init__(self) -> None:
        self._directory = tempfile.TemporaryDirectory(
            prefix="portfolio-semantic-sink-"
        )
        self._connection = sqlite3.connect(
            Path(self._directory.name) / "receipts.sqlite3"
        )
        self._connection.executescript(
            """
            CREATE TABLE nodes (
                node_id TEXT PRIMARY KEY,
                parent_node_id TEXT,
                node_kind TEXT NOT NULL,
                terminal_count INTEGER NOT NULL DEFAULT 0,
                directory_closed INTEGER NOT NULL DEFAULT 0,
                source_closed INTEGER NOT NULL DEFAULT 0
            ) WITHOUT ROWID;
            CREATE TABLE parent_orders (
                parent_node_id TEXT PRIMARY KEY,
                next_order INTEGER NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE edges (
                child_node_id TEXT PRIMARY KEY,
                parent_node_id TEXT NOT NULL,
                traversal_order INTEGER NOT NULL,
                UNIQUE(parent_node_id, traversal_order)
            ) WITHOUT ROWID;
            """
        )

    def add_node(self, entry: StructuralEntry) -> int | None:
        try:
            self._connection.execute(
                """
                INSERT INTO nodes(node_id, parent_node_id, node_kind)
                VALUES (?, ?, ?)
                """,
                (entry.node_id, entry.parent_node_id, entry.kind.value),
            )
        except sqlite3.IntegrityError as error:
            raise ValueError("streaming structural node is duplicated") from error
        if entry.parent_node_id is None:
            return None
        row = self._connection.execute(
            "SELECT next_order FROM parent_orders WHERE parent_node_id = ?",
            (entry.parent_node_id,),
        ).fetchone()
        if row is None:
            traversal_order = 0
            self._connection.execute(
                """
                INSERT INTO parent_orders(parent_node_id, next_order) VALUES (?, ?)
                """,
                (entry.parent_node_id, 1),
            )
        else:
            traversal_order = int(row[0])
            self._connection.execute(
                """
                UPDATE parent_orders SET next_order = ? WHERE parent_node_id = ?
                """,
                (traversal_order + 1, entry.parent_node_id),
            )
        self._connection.execute(
            """
            INSERT INTO edges(child_node_id, parent_node_id, traversal_order)
            VALUES (?, ?, ?)
            """,
            (entry.node_id, entry.parent_node_id, traversal_order),
        )
        return traversal_order

    def add_terminal(self, node_id: str) -> None:
        cursor = self._connection.execute(
            """
            UPDATE nodes SET terminal_count = 1
            WHERE node_id = ? AND terminal_count = 0
            """,
            (node_id,),
        )
        if cursor.rowcount != 1:
            raise ValueError("streaming terminal baseline is invalid")

    def add_directory_close(self, node_id: str) -> None:
        cursor = self._connection.execute(
            """
            UPDATE nodes SET directory_closed = 1
            WHERE node_id = ?
              AND node_kind IN ('source', 'directory')
              AND directory_closed = 0
            """,
            (node_id,),
        )
        if cursor.rowcount != 1:
            raise ValueError("streaming directory closure is invalid")

    def add_source_close(self, node_id: str) -> None:
        cursor = self._connection.execute(
            """
            UPDATE nodes SET source_closed = 1
            WHERE node_id = ? AND node_kind = 'source' AND source_closed = 0
            """,
            (node_id,),
        )
        if cursor.rowcount != 1:
            raise ValueError("streaming source closure is invalid")

    def validate(self) -> tuple[int, int, int]:
        source_count = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM nodes WHERE node_kind = 'source'"
            ).fetchone()[0]
        )
        invalid = self._connection.execute(
            """
            SELECT node_id FROM nodes
            WHERE terminal_count != 1
               OR (node_kind IN ('source', 'directory') AND directory_closed != 1)
               OR (node_kind NOT IN ('source', 'directory') AND directory_closed != 0)
            LIMIT 1
            """
        ).fetchone()
        source_closed = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM nodes WHERE source_closed = 1"
            ).fetchone()[0]
        )
        if source_count != 1 or source_closed != 1 or invalid is not None:
            raise ValueError("streaming authority receipts are incomplete")
        node_count = int(
            self._connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        )
        edge_count = int(
            self._connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        )
        if edge_count != node_count - 1:
            raise ValueError("streaming authority edge receipts are invalid")
        return node_count, edge_count, node_count

    def close(self) -> None:
        self._connection.close()
        self._directory.cleanup()


class StreamingStructureSink:
    """Fail-closed staging coordinator for streaming structural authority."""

    def __init__(self, pack_path: Path, catalog_path: Path) -> None:
        self._pack_path = Path(pack_path)
        self._catalog_path = Path(catalog_path)
        self._pack_stage_path = self._staging_path(self._pack_path, "pack")
        self._catalog_stage_path = self._staging_path(
            self._catalog_path,
            "catalog",
        )
        self._receipt_store = _SinkInvariantStore()
        self._pack_writer = PMPackWriter(self._pack_stage_path)
        try:
            self._catalog_writer = SemanticCatalogWriter(self._catalog_stage_path)
        except BaseException:
            self._pack_writer._abort()
            self._receipt_store.close()
            raise
        self._state = StreamingStructureSinkState.ACTIVE
        self._failure: BaseException | None = None
        self._artifacts: StreamingStructureArtifacts | None = None

    @property
    def state(self) -> StreamingStructureSinkState:
        return self._state

    def __enter__(self) -> StreamingStructureSink:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        if exc_type is None:
            self.close()
        else:
            self.abort()

    def consume(self, event: StructuralEvent) -> None:
        if self._state is StreamingStructureSinkState.FAILED:
            if self._is_source_changed_failure():
                return
            raise ValueError("streaming structure sink failed") from self._failure
        if self._state is not StreamingStructureSinkState.ACTIVE:
            raise ValueError("streaming structure sink is closed")
        try:
            self._consume_active(event)
        except BaseException as error:
            self._fail(error)
            raise

    def seal(self) -> StreamingStructureArtifacts:
        if self._artifacts is not None:
            return self._artifacts
        if self._state is StreamingStructureSinkState.FAILED:
            failure = self._failure
            self.abort()
            if failure is not None:
                raise failure
            raise ValueError("streaming structure sink failed")
        if self._state is not StreamingStructureSinkState.ACTIVE:
            raise ValueError("streaming structure sink is closed")
        published: list[Path] = []
        try:
            node_count, edge_count, baseline_count = self._receipt_store.validate()
            if self._pack_writer.record_count != node_count + edge_count + baseline_count:
                raise ValueError("streaming authority record count is invalid")
            self._require_missing_final_targets()
            self._state = StreamingStructureSinkState.COMPLETE
            pack_seal = self._pack_writer.seal()
            catalog_seal = self._catalog_writer.seal()
            self._pack_writer.close()
            self._catalog_writer.close()
            self._publish_file(self._pack_stage_path, self._pack_path)
            published.append(self._pack_path)
            self._publish_file(self._catalog_stage_path, self._catalog_path)
            published.append(self._catalog_path)
            self._remove_stage_files()
        except BaseException as error:
            self._rollback_published(published)
            self._fail(error)
            raise
        self._close_receipts()
        self._state = StreamingStructureSinkState.SEALED
        self._artifacts = StreamingStructureArtifacts(pack_seal, catalog_seal)
        return self._artifacts

    def close(self) -> None:
        if self._state is StreamingStructureSinkState.SEALED:
            return
        self.abort()

    def abort(self) -> None:
        if self._state in {
            StreamingStructureSinkState.SEALED,
            StreamingStructureSinkState.ABORTED,
        }:
            return
        try:
            self._catalog_writer._abort()
        finally:
            try:
                self._pack_writer._abort()
            finally:
                self._remove_stage_files()
                self._close_receipts()
                self._state = StreamingStructureSinkState.ABORTED

    def _consume_active(self, event: StructuralEvent) -> None:
        if event.kind is StructuralEventKind.NODE_SEEN:
            self._add_node(event.node)
            return
        if event.kind is StructuralEventKind.FILE_ANALYZED:
            if event.baseline is None:
                raise ValueError("streaming baseline event is invalid")
            if event.baseline.source_changed:
                from portfolio_maker.infrastructure.semantic_source_reader import (
                    SemanticSourceChangedError,
                )

                self._fail(
                    SemanticSourceChangedError(
                        "semantic source changed during streaming authority build"
                    )
                )
                return
            self._add_baseline(event)
            return
        if event.kind is StructuralEventKind.DIRECTORY_CLOSED:
            self._receipt_store.add_directory_close(event.node.node_id)
            return
        if event.kind is StructuralEventKind.SOURCE_CLOSED:
            self._receipt_store.add_source_close(event.node.node_id)
            return
        raise ValueError("streaming structural event is invalid")

    def _add_node(self, entry: StructuralEntry) -> None:
        traversal_order = self._receipt_store.add_node(entry)
        structural_ref = self._pack_writer.append(
            {
                "kind": "structural-node",
                "node_id": entry.node_id,
                "parent_node_id": entry.parent_node_id,
                "source_id": entry.source_id,
                "node_kind": entry.kind.value,
                "display_name": entry.display_name,
                "relative_hierarchy": entry.relative_hierarchy,
                "provider_item_key": entry.provider_item_key,
                "status": entry.status.value,
                "content_fingerprint": entry.content_fingerprint,
            }
        )
        self._catalog_writer.add_node(
            NodeRecord(
                entry.node_id,
                entry.parent_node_id,
                entry.kind.value,
                entry.relative_hierarchy,
                entry.source_id,
                structural_ref,
                entry.display_name,
            )
        )
        if entry.parent_node_id is not None:
            assert traversal_order is not None
            edge_ref = self._pack_writer.append(
                {
                    "kind": "structural-edge",
                    "parent_node_id": entry.parent_node_id,
                    "child_node_id": entry.node_id,
                    "traversal_order": traversal_order,
                }
            )
            self._catalog_writer.add_payload_ref(
                PayloadRefRecord(
                    f"structural-edge:{entry.node_id}",
                    entry.node_id,
                    "structural-edge",
                    edge_ref,
                )
            )

    def _add_baseline(self, event: StructuralEvent) -> None:
        baseline = event.baseline
        assert baseline is not None
        self._receipt_store.add_terminal(event.node.node_id)
        baseline_ref = self._pack_writer.append(
            {
                "kind": "local-baseline",
                "node_id": event.node.node_id,
                "coverage": baseline.coverage.value,
                "roles": baseline.roles,
                "topics": baseline.topics,
                "inspected_bytes": baseline.inspected_bytes,
                "sampled_ranges": baseline.sampled_ranges,
                "sample_sha256": baseline.sample_sha256,
                "full_content_sha256": baseline.full_content_sha256,
                "analyzer_version": "semantic-input-v2",
            }
        )
        self._catalog_writer.add_baseline(
            BaselineRecord(
                event.node.node_id,
                baseline.coverage,
                baseline.roles,
                baseline.topics,
                baseline.inspected_bytes,
                baseline.sampled_ranges,
                baseline.sample_sha256,
                baseline.full_content_sha256,
                "semantic-input-v2",
                baseline_ref,
            )
        )

    def _fail(self, error: BaseException) -> None:
        if self._state in {
            StreamingStructureSinkState.SEALED,
            StreamingStructureSinkState.ABORTED,
        }:
            return
        self._failure = error
        self._state = StreamingStructureSinkState.FAILED
        self._discard_open_state()

    def _discard_open_state(self) -> None:
        try:
            self._catalog_writer._abort()
        finally:
            try:
                self._pack_writer._abort()
            finally:
                self._remove_stage_files()
                self._close_receipts()

    @staticmethod
    def _staging_path(path: Path, kind: str) -> Path:
        return path.with_name(f".{path.name}.stream-{uuid4().hex}.{kind}")

    def _require_missing_final_targets(self) -> None:
        for path in (self._pack_path, self._catalog_path):
            try:
                os.lstat(path)
            except FileNotFoundError:
                continue
            raise ValueError("streaming authority target already exists")

    @staticmethod
    def _publish_file(stage_path: Path, final_path: Path) -> None:
        try:
            os.link(stage_path, final_path, follow_symlinks=False)
            return
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
        source = os.open(stage_path, os.O_RDONLY | os.O_NOFOLLOW)
        destination: int | None = None
        try:
            destination = os.open(
                final_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o400,
            )
            while chunk := os.read(source, 65_536):
                os.write(destination, chunk)
            os.fsync(destination)
        except BaseException:
            if destination is not None:
                os.close(destination)
                destination = None
                try:
                    os.unlink(final_path)
                except FileNotFoundError:
                    pass
            raise
        finally:
            if destination is not None:
                os.close(destination)
            os.close(source)

    def _rollback_published(self, paths: list[Path]) -> None:
        for path in reversed(paths):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def _remove_stage_files(self) -> None:
        for path in (self._pack_stage_path, self._catalog_stage_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    def _close_receipts(self) -> None:
        store = getattr(self, "_receipt_store", None)
        if store is not None:
            self._receipt_store = None  # type: ignore[assignment]
            store.close()

    def _is_source_changed_failure(self) -> bool:
        from portfolio_maker.infrastructure.semantic_source_reader import (
            SemanticSourceChangedError,
        )

        return isinstance(self._failure, SemanticSourceChangedError)


@dataclass
class CrawlObserver:
    """Optional bounded-memory evidence for the streaming crawler."""

    max_buffered_events: int = 0
    queue_maxsize: int = 0
    directory_tuple_materializations: int = 0
    timed_out_directory_count: int = 0
    unreadable_directory_count: int = 0
    consumed_timeout_wait_seconds: float = 0.0
    timed_out_relative_hierarchies: list[str] = field(default_factory=list)
    errors: list[StructuralCrawlError] = field(default_factory=list)
    collect_errors: bool = False
    worker_start_count: int = 0
    active_worker_count: int = 0
    max_active_worker_count: int = 0
    max_in_memory_pending_directories: int = 0
    max_spooled_entry_count: int = 0
    spool_count_query_count: int = 0
    ephemeral_spool_cleaned: bool = False
    ephemeral_spool_path: Path | None = None
    _producer_progress_counter: Any | None = field(default=None, repr=False)
    _producer_backpressure_counter: Any | None = field(default=None, repr=False)

    @property
    def producer_progress_markers(self) -> int:
        counter = self._producer_progress_counter
        return 0 if counter is None else int(counter.value)

    @property
    def producer_backpressure_count(self) -> int:
        counter = self._producer_backpressure_counter
        return 0 if counter is None else int(counter.value)


class _EphemeralTraversalSpool:
    """Private SQLite sibling backlog; it is never crawl restart authority."""

    def __init__(self, directory: Path, observer: CrawlObserver) -> None:
        self._path = directory / "traversal.sqlite3"
        self._observer = observer
        self._connection = sqlite3.connect(self._path)
        self._connection.execute(
            """
            CREATE TABLE entries (
                directory_token INTEGER NOT NULL,
                name TEXT NOT NULL,
                st_mode INTEGER,
                st_dev INTEGER,
                st_ino INTEGER,
                readable INTEGER NOT NULL,
                PRIMARY KEY (directory_token, name)
            ) WITHOUT ROWID
            """
        )
        self._next_token = 0
        self._entry_count = 0

    def open_directory(self) -> int:
        self._next_token += 1
        return self._next_token

    def append(self, token: int, entry: _MaterializedDirectoryEntry) -> None:
        self._connection.execute(
            """
            INSERT INTO entries(directory_token, name, st_mode, st_dev, st_ino, readable)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                entry.name,
                entry.st_mode,
                entry.st_dev,
                entry.st_ino,
                int(entry.readable),
            ),
        )
        self._entry_count += 1
        self._observer.max_spooled_entry_count = max(
            self._observer.max_spooled_entry_count,
            self._entry_count,
        )

    def pop_sorted(self, token: int):
        cursor = self._connection.execute(
            """
            SELECT name, st_mode, st_dev, st_ino, readable
            FROM entries WHERE directory_token = ? ORDER BY name
            """,
            (token,),
        )
        while row := cursor.fetchone():
            name, st_mode, st_dev, st_ino, readable = row
            self._connection.execute(
                "DELETE FROM entries WHERE directory_token = ? AND name = ?",
                (token, name),
            )
            self._entry_count -= 1
            yield _MaterializedDirectoryEntry(
                name=name,
                st_mode=st_mode,
                st_dev=st_dev,
                st_ino=st_ino,
                readable=bool(readable),
            )

    def close(self) -> None:
        self._connection.close()


class _StreamingIdentityIndex:
    """Private SQLite index for prior identities and terminal-event receipts."""

    def __init__(
        self,
        directory: Path,
        root_path: Path,
        source_id: str,
        prior_entries: tuple[StructuralEntry, ...],
        policy: FilePolicy,
    ) -> None:
        self._connection = sqlite3.connect(directory / "identities.sqlite3")
        self._connection.executescript(
            """
            CREATE TABLE node_ids (
                source_id TEXT NOT NULL,
                provider_item_key TEXT NOT NULL,
                node_id TEXT NOT NULL,
                PRIMARY KEY(source_id, provider_item_key)
            ) WITHOUT ROWID;
            CREATE TABLE reserved_entries (
                relative_hierarchy TEXT PRIMARY KEY,
                provider_item_key TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE renamed_entries (
                relative_hierarchy TEXT PRIMARY KEY,
                device INTEGER NOT NULL,
                inode INTEGER NOT NULL,
                provider_item_key TEXT NOT NULL,
                claimed INTEGER NOT NULL DEFAULT 0
            ) WITHOUT ROWID;
            CREATE INDEX renamed_identity ON renamed_entries(device, inode, claimed,
                relative_hierarchy, provider_item_key);
            CREATE TABLE reserved_provider_keys (
                provider_item_key TEXT PRIMARY KEY
            ) WITHOUT ROWID;
            CREATE TABLE assigned_provider_keys (
                provider_item_key TEXT PRIMARY KEY
            ) WITHOUT ROWID;
            CREATE TABLE terminal_nodes (
                node_id TEXT PRIMARY KEY
            ) WITHOUT ROWID;
            """
        )
        for entry in prior_entries:
            if entry.source_id != source_id:
                continue
            self._connection.execute(
                """
                INSERT OR IGNORE INTO node_ids(source_id, provider_item_key, node_id)
                VALUES (?, ?, ?)
                """,
                (entry.source_id, entry.provider_item_key, entry.node_id),
            )
            path = (
                root_path
                if entry.relative_hierarchy == "."
                else root_path / entry.relative_hierarchy
            )
            if policy.classify_path(path) != "candidate":
                continue
            try:
                _, device, inode = _provider_identity(
                    _lstat(path), entry.relative_hierarchy
                )
            except StructuralCrawlRootError:
                continue
            if (device, inode) == (entry.device, entry.inode):
                self._connection.execute(
                    """
                    INSERT INTO reserved_entries(relative_hierarchy, provider_item_key)
                    VALUES (?, ?)
                    """,
                    (entry.relative_hierarchy, entry.provider_item_key),
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO reserved_provider_keys(provider_item_key)
                    VALUES (?)
                    """,
                    (entry.provider_item_key,),
                )
        for entry in prior_entries:
            if entry.source_id != source_id or entry.device is None or entry.inode is None:
                continue
            reserved = self._connection.execute(
                "SELECT 1 FROM reserved_entries WHERE relative_hierarchy = ?",
                (entry.relative_hierarchy,),
            ).fetchone()
            if reserved is None:
                self._connection.execute(
                    """
                    INSERT INTO renamed_entries(
                        relative_hierarchy, device, inode, provider_item_key
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        entry.relative_hierarchy,
                        entry.device,
                        entry.inode,
                        entry.provider_item_key,
                    ),
                )

    def provider_key(
        self,
        relative_hierarchy: str,
        device: int | None,
        inode: int | None,
    ) -> str:
        if device is None or inode is None:
            return relative_hierarchy
        inode_key = f"{device}:{inode}"
        reserved = self._connection.execute(
            """
            SELECT provider_item_key FROM reserved_entries
            WHERE relative_hierarchy = ?
            """,
            (relative_hierarchy,),
        ).fetchone()
        if reserved is not None:
            provider_item_key = str(reserved[0])
        else:
            renamed = self._connection.execute(
                """
                SELECT relative_hierarchy, provider_item_key FROM renamed_entries
                WHERE device = ? AND inode = ? AND claimed = 0
                ORDER BY relative_hierarchy, provider_item_key LIMIT 1
                """,
                (device, inode),
            ).fetchone()
            if renamed is not None:
                provider_item_key = str(renamed[1])
                self._connection.execute(
                    "UPDATE renamed_entries SET claimed = 1 WHERE relative_hierarchy = ?",
                    (renamed[0],),
                )
            else:
                reserved_key = self._connection.execute(
                    "SELECT 1 FROM reserved_provider_keys WHERE provider_item_key = ?",
                    (inode_key,),
                ).fetchone()
                assigned_key = self._connection.execute(
                    "SELECT 1 FROM assigned_provider_keys WHERE provider_item_key = ?",
                    (inode_key,),
                ).fetchone()
                provider_item_key = (
                    relative_hierarchy
                    if reserved_key is not None or assigned_key is not None
                    else inode_key
                )
        self._connection.execute(
            "INSERT OR IGNORE INTO assigned_provider_keys(provider_item_key) VALUES (?)",
            (provider_item_key,),
        )
        return provider_item_key

    def node_id(self, source_id: str, provider_item_key: str) -> str:
        row = self._connection.execute(
            """
            SELECT node_id FROM node_ids
            WHERE source_id = ? AND provider_item_key = ?
            """,
            (source_id, provider_item_key),
        ).fetchone()
        return (
            str(row[0])
            if row is not None
            else stable_node_id(source_id, provider_item_key)
        )

    def claim_terminal(self, node_id: str) -> bool:
        try:
            self._connection.execute(
                "INSERT INTO terminal_nodes(node_id) VALUES (?)",
                (node_id,),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def close(self) -> None:
        self._connection.close()


def _crawl_local_structure_v2_materialized(
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


def iter_local_structure_events(
    root: Path,
    approval: SourceApproval,
    prior_entries: tuple[StructuralEntry, ...] = (),
    *,
    directory_timeout_seconds: float = DEFAULT_DIRECTORY_TIMEOUT_SECONDS,
    directory_timeout_budget_seconds: float = DEFAULT_DIRECTORY_TIMEOUT_BUDGET_SECONDS,
    worker_queue_size: int = DEFAULT_STREAMING_WORKER_QUEUE_SIZE,
    observer: CrawlObserver | None = None,
    sink: StreamingStructureSink | None = None,
    _process_context: BaseContext | None = None,
    _enumerator: Callable[[Path], Any] = _stream_directory_entries,
    _monotonic: Callable[[], float] = monotonic,
):
    """Yield one structural record at a time using a bounded worker queue.

    One global worker owns one bounded queue for the whole traversal. Sibling
    backlog is kept only in an ephemeral SQLite spool, so traversal state stays
    bounded by depth plus the queue instead of directory fanout.
    """
    if type(worker_queue_size) is not int or worker_queue_size <= 0:
        raise ValueError("worker_queue_size must be positive")
    root_path = _resolve_root(root)
    root_stat = _lstat(root_path)
    root_key, root_device, root_inode = _provider_identity(root_stat, ".")
    source_id = stable_source_id("local", root_key)
    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    crawl_observer = observer or CrawlObserver()
    legacy_mode = _DirectoryEnumerationWorker is not _DEFAULT_DIRECTORY_ENUMERATION_WORKER
    prior_node_ids: dict[tuple[str, str], str] = {}
    reserved_prior_entries: dict[str, StructuralEntry] = {}
    renamed_prior_entries_by_inode: dict[tuple[int, int], tuple[StructuralEntry, ...]] = {}
    reserved_provider_keys: set[str] = set()
    renamed_prior_entry_indexes: dict[tuple[int, int], int] = {}
    assigned_provider_keys: set[str] = set()
    if legacy_mode:
        prior_node_ids = {
            (entry.source_id, entry.provider_item_key): entry.node_id
            for entry in prior_entries
        }
        (
            reserved_prior_entries,
            renamed_prior_entries_by_inode,
            reserved_provider_keys,
        ) = _reserve_prior_entries(root_path, source_id, prior_entries, policy)
    stream_worker: _StreamingDirectoryEnumerationWorker | None = None
    traversal_spool: _EphemeralTraversalSpool | None = None
    identity_index: _StreamingIdentityIndex | None = None
    terminalized_node_ids: set[str] = set()

    def emit(event: StructuralEvent) -> StructuralEvent:
        if sink is not None:
            sink.consume(event)
        return event

    def make_entry(
        path: Path,
        relative_hierarchy: str,
        parent_node_id: str | None,
        kind: SemanticNodeKind,
        status: AnalysisStatus,
        path_stat: os.stat_result | _MaterializedDirectoryEntry,
    ) -> StructuralEntry:
        if isinstance(path_stat, _MaterializedDirectoryEntry):
            if (
                type(path_stat.st_dev) is int
                and type(path_stat.st_ino) is int
                and path_stat.st_ino > 0
            ):
                device = path_stat.st_dev
                inode = path_stat.st_ino
            else:
                device = None
                inode = None
        else:
            _identity_key, device, inode = _provider_identity(
                path_stat, relative_hierarchy
            )
        provider_item_key = relative_hierarchy
        if identity_index is not None:
            provider_item_key = identity_index.provider_key(
                relative_hierarchy,
                device,
                inode,
            )
        elif device is not None and inode is not None:
            provider_item_key = f"{device}:{inode}"
            inode_key = provider_item_key
            prior_entry = reserved_prior_entries.get(relative_hierarchy)
            if prior_entry is not None:
                provider_item_key = prior_entry.provider_item_key
            else:
                prior_inode_entries = renamed_prior_entries_by_inode.get(
                    (device, inode), ()
                )
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
        node_id = (
            identity_index.node_id(source_id, provider_item_key)
            if identity_index is not None
            else prior_node_ids.get(
                (source_id, provider_item_key),
                stable_node_id(source_id, provider_item_key),
            )
        )
        return StructuralEntry(
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

    def record_error(
        relative_hierarchy: str,
        status: AnalysisStatus,
        *,
        timed_out: bool = False,
    ) -> None:
        if crawl_observer.collect_errors:
            crawl_observer.errors.append(
                StructuralCrawlError(relative_hierarchy, status, timed_out=timed_out)
            )

    def terminal_events(
        entry: StructuralEntry,
        close_kind: StructuralEventKind | None,
    ):
        from portfolio_maker.infrastructure.semantic_analyzers import (
            analyze_local_baseline,
        )

        if identity_index is not None:
            if not identity_index.claim_terminal(entry.node_id):
                return
        else:
            if entry.node_id in terminalized_node_ids:
                return
            terminalized_node_ids.add(entry.node_id)
        yield emit(
            StructuralEvent(
                StructuralEventKind.FILE_ANALYZED,
                entry,
                analyze_local_baseline(entry),
            )
        )
        if close_kind is StructuralEventKind.SOURCE_CLOSED:
            yield emit(StructuralEvent(StructuralEventKind.DIRECTORY_CLOSED, entry))
        if close_kind is not None:
            yield emit(StructuralEvent(close_kind, entry))

    def failed_terminal_events(
        entry: StructuralEntry,
        close_kind: StructuralEventKind,
    ):
        resolved = replace(entry, status=AnalysisStatus.FAILED)
        record_error(entry.relative_hierarchy, AnalysisStatus.FAILED)
        yield from terminal_events(resolved, close_kind)

    def stream_directory(
        directory: Path,
        entry: StructuralEntry,
        close_kind: StructuralEventKind,
        legacy_worker: _DirectoryEnumerationWorker | None,
    ):
        yield emit(StructuralEvent(StructuralEventKind.NODE_SEEN, entry))
        remaining_budget = max(
            0.0,
            directory_timeout_budget_seconds
            - crawl_observer.consumed_timeout_wait_seconds,
        )
        requested_timeout = min(directory_timeout_seconds, remaining_budget)
        if legacy_worker is None:
            if stream_worker is None or traversal_spool is None:
                raise StructuralCrawlWorkerError(
                    "semantic directory worker failed safely"
                )
            crawl_observer.max_in_memory_pending_directories = max(
                crawl_observer.max_in_memory_pending_directories,
                1,
            )
            spool_token = traversal_spool.open_directory()
            for child in stream_worker.iter_entries(
                directory,
                timeout_seconds=requested_timeout,
                monotonic_clock=_monotonic,
            ):
                traversal_spool.append(spool_token, child)
            enumeration = stream_worker.last_enumeration
        else:
            enumeration = legacy_worker.enumerate(
                directory,
                timeout_seconds=requested_timeout,
                replace_after_timeout=False,
                monotonic_clock=_monotonic,
            )
            for child in enumeration.entries:
                yield from visit_child(directory, entry.node_id, child, legacy_worker)
            if enumeration.timed_out:
                legacy_worker.replace_after_timeout()

        if enumeration is None:
            raise StructuralCrawlWorkerError("semantic directory worker failed safely")
        if enumeration.timed_out:
            crawl_observer.consumed_timeout_wait_seconds = min(
                directory_timeout_budget_seconds,
                crawl_observer.consumed_timeout_wait_seconds
                + enumeration.timeout_wait_seconds,
            )
            crawl_observer.timed_out_relative_hierarchies.append(
                entry.relative_hierarchy
            )
            budget_exhausted = (
                crawl_observer.consumed_timeout_wait_seconds
                >= directory_timeout_budget_seconds
            )
            resolved = replace(entry, status=AnalysisStatus.UNREADABLE)
            crawl_observer.timed_out_directory_count += 1
            crawl_observer.unreadable_directory_count += 1
            record_error(
                entry.relative_hierarchy,
                AnalysisStatus.UNREADABLE,
                timed_out=True,
            )
            yield from terminal_events(resolved, close_kind)
            if budget_exhausted:
                raise StructuralCrawlTimeoutBudgetError(
                    source_id=source_id,
                    timed_out_relative_hierarchies=tuple(
                        crawl_observer.timed_out_relative_hierarchies
                    ),
                    timed_out_directory_count=(
                        crawl_observer.timed_out_directory_count
                    ),
                    unreadable_directory_count=(
                        crawl_observer.unreadable_directory_count
                    ),
                    consumed_timeout_wait_seconds=(
                        crawl_observer.consumed_timeout_wait_seconds
                    ),
                )
            if entry.kind is SemanticNodeKind.SOURCE:
                raise StructuralCrawlRootTimeoutError("Structural crawl root timed out")
            if legacy_worker is None:
                assert stream_worker is not None
                stream_worker.replace_after_timeout()
            return
        if enumeration.unreadable:
            resolved = replace(entry, status=AnalysisStatus.UNREADABLE)
            crawl_observer.unreadable_directory_count += 1
            record_error(entry.relative_hierarchy, AnalysisStatus.UNREADABLE)
            yield from terminal_events(resolved, close_kind)
            return
        if legacy_worker is None:
            assert traversal_spool is not None
            for child in traversal_spool.pop_sorted(spool_token):
                yield from visit_child(directory, entry.node_id, child, legacy_worker)
        yield from terminal_events(entry, close_kind)

    def visit_child(
        directory: Path,
        parent_node_id: str,
        child: _MaterializedDirectoryEntry,
        legacy_worker: _DirectoryEnumerationWorker | None,
    ):
        child_path = directory / child.name
        child_relative = _relative_hierarchy(root_path, child_path)
        if not child.readable:
            entry = make_entry(
                child_path,
                child_relative,
                parent_node_id,
                SemanticNodeKind.FILE,
                AnalysisStatus.UNREADABLE,
                child,
            )
            yield emit(StructuralEvent(StructuralEventKind.NODE_SEEN, entry))
            yield from terminal_events(entry, None)
            record_error(child_relative, AnalysisStatus.UNREADABLE)
            return
        assert child.st_mode is not None
        if stat.S_ISLNK(child.st_mode):
            entry = make_entry(
                child_path,
                child_relative,
                parent_node_id,
                SemanticNodeKind.FILE,
                AnalysisStatus.UNSUPPORTED,
                child,
            )
            yield emit(StructuralEvent(StructuralEventKind.NODE_SEEN, entry))
            yield from terminal_events(entry, None)
            record_error(child_relative, AnalysisStatus.UNSUPPORTED)
            return
        classification = policy.classify_path(child_path)
        if stat.S_ISDIR(child.st_mode):
            entry = make_entry(
                child_path,
                child_relative,
                parent_node_id,
                SemanticNodeKind.DIRECTORY,
                (
                    AnalysisStatus.PENDING
                    if classification == "candidate"
                    else AnalysisStatus.UNSUPPORTED
                ),
                child,
            )
            if classification == "candidate":
                try:
                    yield from stream_directory(
                        child_path,
                        entry,
                        StructuralEventKind.DIRECTORY_CLOSED,
                        legacy_worker,
                    )
                except (
                    StructuralCrawlWorkerError,
                    StructuralCrawlTimeoutBudgetError,
                ):
                    yield from failed_terminal_events(
                        entry,
                        StructuralEventKind.DIRECTORY_CLOSED,
                    )
                    raise
            else:
                yield emit(StructuralEvent(StructuralEventKind.NODE_SEEN, entry))
                yield from terminal_events(entry, StructuralEventKind.DIRECTORY_CLOSED)
            return
        entry = make_entry(
            child_path,
            child_relative,
            parent_node_id,
            SemanticNodeKind.FILE,
            (
                AnalysisStatus.PENDING
                if stat.S_ISREG(child.st_mode) and classification == "candidate"
                else AnalysisStatus.UNSUPPORTED
            ),
            child,
        )
        yield emit(StructuralEvent(StructuralEventKind.NODE_SEEN, entry))
        yield from terminal_events(entry, None)
        if not stat.S_ISREG(child.st_mode):
            record_error(child_relative, AnalysisStatus.UNSUPPORTED)

    source = StructuralEntry(
        node_id=stable_node_id(source_id, root_key),
        source_id=source_id,
        parent_node_id=None,
        kind=SemanticNodeKind.SOURCE,
        display_name=normalize_label(root_path.name),
        relative_hierarchy=".",
        absolute_path=root_path,
        provider_item_key=root_key,
        content_fingerprint=None,
        device=root_device,
        inode=root_inode,
        status=AnalysisStatus.PENDING,
    )
    if policy.classify_path(root_path) != "candidate":
        source = replace(source, status=AnalysisStatus.UNSUPPORTED)
        stream_completed = False
        try:
            yield emit(StructuralEvent(StructuralEventKind.NODE_SEEN, source))
            yield from terminal_events(source, StructuralEventKind.SOURCE_CLOSED)
            stream_completed = True
        finally:
            if sink is not None and not stream_completed:
                sink.abort()
        return

    if _DirectoryEnumerationWorker is _DEFAULT_DIRECTORY_ENUMERATION_WORKER:
        spool_database: Path | None = None
        stream_completed = False
        try:
            with tempfile.TemporaryDirectory(
                prefix="portfolio-semantic-traversal-"
            ) as spool_directory:
                spool_database = Path(spool_directory) / "traversal.sqlite3"
                crawl_observer.ephemeral_spool_path = spool_database
                traversal_spool = _EphemeralTraversalSpool(
                    Path(spool_directory),
                    crawl_observer,
                )
                identity_index = _StreamingIdentityIndex(
                    Path(spool_directory),
                    root_path,
                    source_id,
                    prior_entries,
                    policy,
                )
                try:
                    with _StreamingDirectoryEnumerationWorker(
                        process_context=_process_context,
                        enumerator=_enumerator,
                        queue_size=worker_queue_size,
                        observer=crawl_observer,
                    ) as created_worker:
                        stream_worker = created_worker
                        try:
                            yield from stream_directory(
                                root_path,
                                source,
                                StructuralEventKind.SOURCE_CLOSED,
                                None,
                            )
                        except (
                            StructuralCrawlWorkerError,
                            StructuralCrawlTimeoutBudgetError,
                            StructuralCrawlRootTimeoutError,
                        ):
                            yield from failed_terminal_events(
                                source,
                                StructuralEventKind.SOURCE_CLOSED,
                            )
                            raise
                        stream_completed = True
                finally:
                    stream_worker = None
                    if identity_index is not None:
                        identity_index.close()
                        identity_index = None
                    if traversal_spool is not None:
                        traversal_spool.close()
                        traversal_spool = None
        finally:
            crawl_observer.ephemeral_spool_cleaned = (
                spool_database is None or not spool_database.exists()
            )
            if sink is not None and not stream_completed:
                sink.abort()
        return
    stream_completed = False
    try:
        with _DirectoryEnumerationWorker(
            process_context=_process_context,
            enumerator=_enumerator,
        ) as legacy_worker:
            try:
                yield from stream_directory(
                    root_path,
                    source,
                    StructuralEventKind.SOURCE_CLOSED,
                    legacy_worker,
                )
            except (
                StructuralCrawlWorkerError,
                StructuralCrawlTimeoutBudgetError,
                StructuralCrawlRootTimeoutError,
            ):
                yield from failed_terminal_events(
                    source,
                    StructuralEventKind.SOURCE_CLOSED,
                )
                raise
            stream_completed = True
    finally:
        if sink is not None and not stream_completed:
            sink.abort()


def crawl_local_structure(
    root: Path,
    approval: SourceApproval,
    prior_entries: tuple[StructuralEntry, ...] = (),
    *,
    directory_timeout_seconds: float = DEFAULT_DIRECTORY_TIMEOUT_SECONDS,
    directory_timeout_budget_seconds: float = DEFAULT_DIRECTORY_TIMEOUT_BUDGET_SECONDS,
    _process_context: BaseContext | None = None,
    _enumerator: Callable[[Path], Any] = _stream_directory_entries,
    _monotonic: Callable[[], float] = monotonic,
) -> StructuralCrawl:
    """Legacy v2 compatibility wrapper; this is the only full entry materializer."""
    process_context = _process_context or get_context("spawn")
    if (
        _DirectoryEnumerationWorker is not _DEFAULT_DIRECTORY_ENUMERATION_WORKER
        or _DirectoryEnumerationWorker.replace_after_timeout
        is not _DEFAULT_DIRECTORY_WORKER_REPLACE
        or not hasattr(process_context, "Queue")
    ):
        return _crawl_local_structure_v2_materialized(
            root,
            approval,
            prior_entries,
            directory_timeout_seconds=directory_timeout_seconds,
            directory_timeout_budget_seconds=directory_timeout_budget_seconds,
            _process_context=process_context,
            _enumerator=_enumerator,
            _monotonic=_monotonic,
        )
    observer = CrawlObserver(collect_errors=True)
    latest_entries: dict[str, StructuralEntry] = {}
    for event in iter_local_structure_events(
        root,
        approval,
        prior_entries,
        directory_timeout_seconds=directory_timeout_seconds,
        directory_timeout_budget_seconds=directory_timeout_budget_seconds,
        _process_context=process_context,
        _enumerator=_enumerator,
        _monotonic=_monotonic,
        observer=observer,
    ):
        if event.kind is StructuralEventKind.NODE_SEEN:
            if (
                event.node.kind is SemanticNodeKind.FILE
                and event.node.status is AnalysisStatus.UNREADABLE
                and event.node.device is None
                and event.node.inode is None
            ):
                # v2 omitted a child whose directory-entry stat failed; retain
                # that materialized-result compatibility while v3 keeps its event.
                continue
            latest_entries.setdefault(event.node.node_id, event.node)
        elif event.kind in {
            StructuralEventKind.DIRECTORY_CLOSED,
            StructuralEventKind.SOURCE_CLOSED,
        }:
            latest_entries[event.node.node_id] = event.node
    return StructuralCrawl(
        entries=tuple(
            sorted(
                latest_entries.values(),
                key=lambda entry: (entry.relative_hierarchy, entry.node_id),
            )
        ),
        errors=tuple(observer.errors),
        timed_out_directory_count=observer.timed_out_directory_count,
        unreadable_directory_count=observer.unreadable_directory_count,
        consumed_timeout_wait_seconds=observer.consumed_timeout_wait_seconds,
    )
