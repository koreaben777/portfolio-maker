from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
import sqlite3
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.managed_files import (
    ensure_managed_directory,
    open_managed_directory,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('local_file', 'github_repository')),
    uri TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    owner TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'discovered', 'approved', 'ingested', 'skipped_policy', 'extract_failed', 'stale_source'
    )),
    discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS source_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    snapshot_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    extractor TEXT NOT NULL,
    extracted_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS github_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES sources(id),
    repo TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    author TEXT NOT NULL,
    created_at TEXT NOT NULL,
    merged_at TEXT,
    UNIQUE(repo, activity_type, url)
);

"""


class RepositoryError(RuntimeError):
    pass


_DATABASE_SIDECARS = ("-journal", "-wal", "-shm")


@dataclass(frozen=True)
class _DatabaseIdentity:
    device: int
    inode: int


class SQLiteRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        directory_descriptor, identity = self._open_database_family()
        try:
            return self._connect_validated(directory_descriptor, identity)
        except OSError as error:
            raise self._unsafe_path_error(self.db_path.name) from error
        finally:
            os.close(directory_descriptor)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        directory_descriptor, identity = self._open_database_family()
        conn: sqlite3.Connection | None = None
        try:
            conn = self._connect_validated(directory_descriptor, identity)
            yield conn
            self._validate_database_family(directory_descriptor, identity, "before commit")
            conn.commit()
        except RepositoryError:
            self._rollback(conn)
            raise
        except sqlite3.Error as error:
            self._rollback(conn)
            raise self._controlled_error() from error
        except OSError as error:
            self._rollback(conn)
            raise self._unsafe_path_error(self.db_path.name) from error
        finally:
            if conn is not None:
                conn.close()
            os.close(directory_descriptor)

    def _open_database_family(self) -> tuple[int, _DatabaseIdentity]:
        try:
            ensure_managed_directory(self.db_path.parent)
            return self._prepare_database_family()
        except RepositoryError:
            raise
        except OSError as error:
            raise self._unsafe_path_error(self.db_path.name) from error

    def _prepare_database_family(self) -> tuple[int, _DatabaseIdentity]:
        directory_descriptor = open_managed_directory(self.db_path.parent)
        try:
            self._verify_current_database_directory(directory_descriptor)
            identity = self._validate_or_create_main_database(directory_descriptor)
            self._validate_database_family(directory_descriptor, identity, "before connect")
            return directory_descriptor, identity
        except Exception:
            os.close(directory_descriptor)
            raise

    def _connect_validated(
        self,
        directory_descriptor: int,
        identity: _DatabaseIdentity,
    ) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = None
        try:
            self._verify_current_database_directory(directory_descriptor)
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = sqlite3.Row
            self._validate_database_family(directory_descriptor, identity, "before first write")
            return conn
        except RepositoryError:
            if conn is not None:
                conn.close()
            raise
        except sqlite3.Error as error:
            if conn is not None:
                conn.close()
            raise self._controlled_error() from error

    def _validate_or_create_main_database(self, directory_descriptor: int) -> _DatabaseIdentity:
        name = self.db_path.name
        stat_result = self._validated_family_entry(directory_descriptor, name)
        if stat_result is None:
            descriptor = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_descriptor,
            )
            try:
                os.fchmod(descriptor, 0o600)
                stat_result = os.fstat(descriptor)
            finally:
                os.close(descriptor)
        else:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=directory_descriptor,
            )
            try:
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (stat_result.st_dev, stat_result.st_ino):
                    raise self._changed_database_error("before connect")
                os.fchmod(descriptor, 0o600)
                stat_result = opened
            finally:
                os.close(descriptor)
        if not S_ISREG(stat_result.st_mode) or stat_result.st_nlink != 1:
            raise self._unsafe_path_error(name)
        return _DatabaseIdentity(stat_result.st_dev, stat_result.st_ino)

    def _validate_database_family(
        self,
        directory_descriptor: int,
        identity: _DatabaseIdentity,
        stage: str,
    ) -> None:
        self._verify_current_database_directory(directory_descriptor)
        main = self._validated_family_entry(directory_descriptor, self.db_path.name)
        if main is None or (main.st_dev, main.st_ino) != (identity.device, identity.inode):
            raise self._changed_database_error(stage)
        for suffix in _DATABASE_SIDECARS:
            self._validated_family_entry(directory_descriptor, f"{self.db_path.name}{suffix}")

    def _validated_family_entry(self, directory_descriptor: int, name: str) -> os.stat_result | None:
        try:
            stat_result = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not S_ISREG(stat_result.st_mode) or stat_result.st_nlink != 1:
            raise self._unsafe_path_error(name)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        try:
            opened = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if not S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise self._unsafe_path_error(name)
        if (opened.st_dev, opened.st_ino) != (stat_result.st_dev, stat_result.st_ino):
            raise self._changed_database_error("during validation")
        return opened

    def _verify_current_database_directory(self, directory_descriptor: int) -> None:
        current = os.stat(self.db_path.parent, follow_symlinks=False)
        descriptor = os.fstat(directory_descriptor)
        if (
            not S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (descriptor.st_dev, descriptor.st_ino)
        ):
            raise self._changed_database_error("before connect")

    def _rollback(self, conn: sqlite3.Connection | None) -> None:
        if conn is None:
            return
        try:
            conn.rollback()
        except sqlite3.Error:
            pass

    def _unsafe_path_error(self, name: str) -> RepositoryError:
        return RepositoryError(
            f"Unsafe managed database path: {name}. Preserve or back up the workspace state, "
            "remove the unsafe managed path, and rerun the command"
        )

    def _changed_database_error(self, stage: str) -> RepositoryError:
        return RepositoryError(
            f"Managed database changed {stage}; preserve or back up the workspace state and rerun"
        )

    def _controlled_error(self) -> RepositoryError:
        return RepositoryError(
            f"Portfolio database is invalid or unavailable: {self.db_path}. "
            "Preserve or back up the workspace state, then repair or replace the damaged database"
        )

    def _semantic_error(self) -> RepositoryError:
        return RepositoryError(
            "stored data is invalid; preserve or back up the workspace state, "
            "then repair or replace the damaged database"
        )

    def initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript(SCHEMA)

    def table_names(self) -> set[str]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        return {row["name"] for row in rows}

    def upsert_source(self, source: Source) -> int:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO sources (type, uri, display_name, owner, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(uri) DO UPDATE SET
                    type = excluded.type,
                    display_name = excluded.display_name,
                    owner = excluded.owner,
                    status = CASE
                        WHEN sources.status = ? AND excluded.status = ? THEN sources.status
                        ELSE excluded.status
                    END
                """,
                (
                    source.type.value,
                    source.uri,
                    source.display_name,
                    source.owner,
                    source.status.value,
                    SourceStatus.INGESTED.value,
                    SourceStatus.DISCOVERED.value,
                ),
            )
            row = conn.execute(
                "SELECT id FROM sources WHERE uri = ?",
                (source.uri,),
            ).fetchone()
        return int(row["id"])

    def insert_github_activity(self, activity: GitHubActivity) -> int:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO github_activities
                    (source_id, repo, activity_type, url, title, state, author, created_at, merged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo, activity_type, url) DO UPDATE SET
                    source_id = excluded.source_id,
                    title = excluded.title,
                    state = excluded.state,
                    author = excluded.author,
                    created_at = excluded.created_at,
                    merged_at = excluded.merged_at
                """,
                (
                    activity.source_id,
                    activity.repo,
                    activity.activity_type,
                    activity.url,
                    activity.title,
                    activity.state,
                    activity.author,
                    activity.created_at,
                    activity.merged_at,
                ),
            )
            row = conn.execute(
                """
                SELECT id FROM github_activities
                WHERE repo = ? AND activity_type = ? AND url = ?
                """,
                (activity.repo, activity.activity_type, activity.url),
            ).fetchone()
        return int(row["id"])

    def insert_source_snapshot(
        self,
        source_id: int,
        snapshot_path: Path,
        content_hash: str,
        extractor: str,
    ) -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO source_snapshots
                    (source_id, snapshot_path, content_hash, extractor)
                VALUES (?, ?, ?, ?)
                """,
                (source_id, str(snapshot_path), content_hash, extractor),
            )
        return int(cursor.lastrowid)

    def update_source_snapshot(
        self,
        snapshot_id: int,
        snapshot_path: Path,
        content_hash: str,
        extractor: str,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE source_snapshots
                SET snapshot_path = ?, content_hash = ?, extractor = ?, extracted_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(snapshot_path), content_hash, extractor, snapshot_id),
            )

    def delete_source_snapshot(self, snapshot_id: int) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM source_snapshots WHERE id = ?", (snapshot_id,))

    def snapshot_metadata_for_source(self, source_id: int) -> list[tuple[int, Path, str, str]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, snapshot_path, content_hash, extractor
                FROM source_snapshots
                WHERE source_id = ?
                ORDER BY id
                """,
                (source_id,),
            ).fetchall()
        try:
            return [
                (
                    int(row["id"]),
                    Path(row["snapshot_path"]),
                    str(row["content_hash"]),
                    str(row["extractor"]),
                )
                for row in rows
            ]
        except (TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def latest_snapshot_metadata_by_source_id(self) -> dict[int, tuple[int, Path, str, str]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, source_id, snapshot_path, content_hash, extractor
                FROM source_snapshots
                WHERE id IN (
                    SELECT MAX(id)
                    FROM source_snapshots
                    GROUP BY source_id
                )
                """
            ).fetchall()
        try:
            return {
                int(row["source_id"]): (
                    int(row["id"]),
                    Path(row["snapshot_path"]),
                    str(row["content_hash"]),
                    str(row["extractor"]),
                )
                for row in rows
            }
        except (TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def list_sources(self, status: SourceStatus | None = None) -> list[Source]:
        sql = "SELECT id, type, uri, display_name, owner, status FROM sources"
        params: tuple[str, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status.value,)
        sql += " ORDER BY id"

        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()

        try:
            return [
                Source(
                    id=row["id"],
                    type=SourceType(row["type"]),
                    uri=row["uri"],
                    display_name=row["display_name"],
                    owner=row["owner"],
                    status=SourceStatus(row["status"]),
                )
                for row in rows
            ]
        except (TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def update_source_status(self, source_id: int, status: SourceStatus) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE sources SET status = ? WHERE id = ?",
                (status.value, source_id),
            )
