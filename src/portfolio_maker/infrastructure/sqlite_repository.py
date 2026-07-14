from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
import sqlite3
import threading
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from portfolio_maker.domain.models import (
    GitHubActivity,
    PublicEvidenceRecord,
    Source,
    SourceStatus,
    SourceType,
)
from portfolio_maker.infrastructure.github_connector import (
    canonical_public_github_activity_url,
    canonical_repository_name,
    is_valid_github_activity_state,
)
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
    origin_type TEXT NOT NULL DEFAULT 'local',
    origin_visibility TEXT NOT NULL DEFAULT 'unknown',
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
    is_private INTEGER NOT NULL DEFAULT 0 CHECK(is_private IN (0, 1)),
    state_field TEXT CHECK(state_field IS NULL OR state_field IN ('conclusion', 'status')),
    origin_type TEXT NOT NULL DEFAULT 'public_github',
    origin_visibility TEXT NOT NULL DEFAULT 'unknown',
    is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
    UNIQUE(repo, activity_type, url)
);

CREATE TABLE IF NOT EXISTS evidence_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES sources(id),
    snapshot_id INTEGER REFERENCES source_snapshots(id),
    github_activity_id INTEGER REFERENCES github_activities(id),
    locator TEXT NOT NULL,
    stable_id TEXT NOT NULL UNIQUE,
    content_hash TEXT,
    public_safe INTEGER NOT NULL DEFAULT 0 CHECK(public_safe IN (0, 1)),
    origin_type TEXT NOT NULL DEFAULT 'local',
    origin_visibility TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    CHECK(source_id IS NOT NULL OR github_activity_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    public_safe INTEGER NOT NULL DEFAULT 0 CHECK(public_safe IN (0, 1)),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    overview TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'approved'),
    approval_sha256 TEXT NOT NULL,
    review_input_sha256 TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_project_evidence (
    project_id TEXT NOT NULL REFERENCES portfolio_projects(id) ON DELETE CASCADE,
    evidence_id INTEGER NOT NULL REFERENCES evidence_items(id),
    support_level TEXT NOT NULL CHECK(support_level IN ('direct', 'contextual')),
    PRIMARY KEY (project_id, evidence_id),
    UNIQUE (evidence_id)
);

CREATE TABLE IF NOT EXISTS career_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id),
    text TEXT NOT NULL,
    public_safe INTEGER NOT NULL DEFAULT 0 CHECK(public_safe IN (0, 1)),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id INTEGER NOT NULL REFERENCES career_claims(id),
    evidence_id INTEGER NOT NULL REFERENCES evidence_items(id),
    support_level TEXT NOT NULL CHECK(support_level IN ('direct', 'contextual')),
    PRIMARY KEY (claim_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL,
    input_manifest TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

"""


class RepositoryError(RuntimeError):
    pass


_DATABASE_SIDECARS = ("-journal", "-wal", "-shm")
_REPOSITORY_OPERATION_LOCK = threading.RLock()
_REPOSITORY_OPERATION_STATE = threading.local()


@dataclass(frozen=True)
class _DatabaseIdentity:
    device: int
    inode: int


class SQLiteRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._database_operation() as (directory_descriptor, identity):
            conn: sqlite3.Connection | None = None
            try:
                conn = self._connect_validated(directory_descriptor, identity, read_only=False)
                self._configure_write_journal(conn)
                self._validate_database_family(directory_descriptor, identity, "after journal setup")
                conn.execute("BEGIN IMMEDIATE")
                self._validate_database_family(directory_descriptor, identity, "after write transaction")
                yield conn
                self._validate_database_family(directory_descriptor, identity, "before commit")
                conn.commit()
                self._validate_database_family(directory_descriptor, identity, "after commit")
            except RepositoryError:
                self._rollback(conn)
                raise
            except sqlite3.Error as error:
                self._rollback(conn)
                raise self._repository_error(error) from error
            except OSError as error:
                self._rollback(conn)
                raise self._unsafe_path_error(self.db_path.name) from error
            finally:
                if conn is not None:
                    conn.close()

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        with self._database_operation() as (directory_descriptor, identity):
            conn: sqlite3.Connection | None = None
            try:
                conn = self._connect_validated(directory_descriptor, identity, read_only=True)
                yield conn
            except RepositoryError:
                raise
            except sqlite3.Error as error:
                raise self._repository_error(error) from error
            except OSError as error:
                raise self._unsafe_path_error(self.db_path.name) from error
            finally:
                if conn is not None:
                    conn.close()

    @contextmanager
    def _database_operation(self) -> Iterator[tuple[int, _DatabaseIdentity]]:
        with self._repository_critical_section() as outermost:
            directory_descriptor, identity = self._open_database_family(
                ensure_directory=outermost
            )
            directory_locked = False
            try:
                if outermost:
                    self._lock_database_directory(directory_descriptor)
                    directory_locked = True
                yield directory_descriptor, identity
            finally:
                try:
                    if directory_locked:
                        self._unlock_database_directory(directory_descriptor)
                finally:
                    os.close(directory_descriptor)

    @contextmanager
    def _repository_critical_section(self) -> Iterator[bool]:
        key = str(self.db_path.parent.absolute())
        with _REPOSITORY_OPERATION_LOCK:
            depths = getattr(_REPOSITORY_OPERATION_STATE, "depths", {})
            depth = depths.get(key, 0)
            depths[key] = depth + 1
            _REPOSITORY_OPERATION_STATE.depths = depths
            lock_descriptor: int | None = None
            lock_acquired = False
            try:
                if depth == 0:
                    lock_descriptor = self._open_repository_lock()
                    try:
                        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
                    except OSError as error:
                        raise RepositoryError("Portfolio repository is busy; try again shortly") from error
                    lock_acquired = True
                yield depth == 0
            finally:
                try:
                    if lock_descriptor is not None:
                        if lock_acquired:
                            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                        os.close(lock_descriptor)
                finally:
                    if depth:
                        depths[key] = depth
                    else:
                        del depths[key]

    def _open_repository_lock(self) -> int:
        workspace = self.db_path.parent.parent
        try:
            workspace.mkdir(parents=True, mode=0o700, exist_ok=True)
            descriptor = os.open(
                workspace,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
        except OSError as error:
            raise RepositoryError("Portfolio repository is busy; try again shortly") from error
        try:
            stat_result = os.fstat(descriptor)
            if not S_ISDIR(stat_result.st_mode):
                raise RepositoryError("Unsafe repository lock directory; preserve the workspace and rerun")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _open_database_family(self, *, ensure_directory: bool) -> tuple[int, _DatabaseIdentity]:
        try:
            if ensure_directory:
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
            self._ensure_database_sidecars(directory_descriptor)
            self._validate_database_family(directory_descriptor, identity, "before connect")
            return directory_descriptor, identity
        except Exception:
            os.close(directory_descriptor)
            raise

    def _connect_validated(
        self,
        directory_descriptor: int,
        identity: _DatabaseIdentity,
        *,
        read_only: bool,
    ) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = None
        try:
            self._verify_current_database_directory(directory_descriptor)
            mode = "ro" if read_only else "rw"
            database_uri = f"{self.db_path.absolute().as_uri()}?mode={mode}"
            conn = sqlite3.connect(database_uri, uri=True)
            if not read_only:
                conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = sqlite3.Row
            self._validate_database_family(directory_descriptor, identity, "after connect")
            return conn
        except RepositoryError:
            if conn is not None:
                conn.close()
            raise
        except sqlite3.Error as error:
            if conn is not None:
                conn.close()
            raise self._repository_error(error) from error

    def _ensure_database_sidecars(self, directory_descriptor: int) -> None:
        for suffix in _DATABASE_SIDECARS:
            name = f"{self.db_path.name}{suffix}"
            if self._validated_family_entry(directory_descriptor, name) is not None:
                continue
            try:
                descriptor = os.open(
                    name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory_descriptor,
                )
            except FileExistsError:
                self._validated_family_entry(directory_descriptor, name)
                continue
            try:
                os.fchmod(descriptor, 0o600)
                created = os.fstat(descriptor)
                if not S_ISREG(created.st_mode) or created.st_nlink != 1:
                    raise self._unsafe_path_error(name)
            finally:
                os.close(descriptor)

    def _lock_database_directory(self, directory_descriptor: int) -> None:
        os.fchmod(directory_descriptor, 0o500)

    def _unlock_database_directory(self, directory_descriptor: int) -> None:
        os.fchmod(directory_descriptor, 0o700)

    def _configure_write_journal(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        if row is None or not isinstance(row[0], str):
            raise self._controlled_error()
        if row[0].casefold() != "wal":
            conn.execute("PRAGMA journal_mode = PERSIST")

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

    def _repository_error(self, error: sqlite3.Error) -> RepositoryError:
        code = getattr(error, "sqlite_errorcode", None)
        if isinstance(code, int) and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return RepositoryError("Portfolio database is busy; try again shortly")
        return self._controlled_error()

    def _semantic_error(self) -> RepositoryError:
        return RepositoryError(
            "stored data is invalid; preserve or back up the workspace state, "
            "then repair or replace the damaged database"
        )

    def initialize(self) -> None:
        with self._connection() as conn:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            self._ensure_github_activity_visibility_column(conn)
            self._ensure_github_activity_state_field_column(conn)
            self._ensure_github_activity_current_column(conn)
            self._ensure_legacy_normalized_columns(conn)
            self._ensure_origin_columns(conn)

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {
            SQLiteRepository._required_text(row, "name")
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @classmethod
    def _ensure_legacy_normalized_columns(cls, conn: sqlite3.Connection) -> None:
        projects = cls._table_columns(conn, "projects")
        if "public_safe" not in projects:
            conn.execute(
                "ALTER TABLE projects ADD COLUMN public_safe INTEGER NOT NULL DEFAULT 0 "
                "CHECK(public_safe IN (0, 1))"
            )

        claims = cls._table_columns(conn, "career_claims")
        if "project_id" not in claims:
            conn.execute(
                "ALTER TABLE career_claims ADD COLUMN project_id INTEGER "
                "REFERENCES projects(id)"
            )

        evidence = cls._table_columns(conn, "evidence_items")
        if "github_activity_id" not in evidence:
            conn.execute(
                "ALTER TABLE evidence_items ADD COLUMN github_activity_id INTEGER "
                "REFERENCES github_activities(id)"
            )
        if "stable_id" not in evidence:
            conn.execute("ALTER TABLE evidence_items ADD COLUMN stable_id TEXT")
        if "content_hash" not in evidence:
            conn.execute("ALTER TABLE evidence_items ADD COLUMN content_hash TEXT")
        if "public_safe" not in evidence:
            conn.execute(
                "ALTER TABLE evidence_items ADD COLUMN public_safe INTEGER NOT NULL DEFAULT 0 "
                "CHECK(public_safe IN (0, 1))"
            )
        conn.execute(
            "UPDATE evidence_items SET stable_id = 'legacy-evidence:' || id "
            "WHERE stable_id IS NULL OR trim(stable_id) = ''"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS evidence_items_stable_id_unique "
            "ON evidence_items(stable_id)"
        )

        artifacts = cls._table_columns(conn, "artifacts")
        if "kind" not in artifacts:
            conn.execute("ALTER TABLE artifacts ADD COLUMN kind TEXT")
        if "version" not in artifacts:
            conn.execute("ALTER TABLE artifacts ADD COLUMN version INTEGER")
        if "input_manifest" not in artifacts:
            conn.execute("ALTER TABLE artifacts ADD COLUMN input_manifest TEXT")
        if "type" in artifacts:
            conn.execute(
                "UPDATE artifacts SET kind = COALESCE(kind, type) WHERE kind IS NULL"
            )
        conn.execute("UPDATE artifacts SET kind = 'legacy' WHERE kind IS NULL")
        conn.execute("UPDATE artifacts SET version = 1 WHERE version IS NULL")
        conn.execute(
            "UPDATE artifacts SET input_manifest = '{}' WHERE input_manifest IS NULL"
        )

    @staticmethod
    def _ensure_github_activity_visibility_column(conn: sqlite3.Connection) -> None:
        columns = {
            SQLiteRepository._required_text(row, "name")
            for row in conn.execute("PRAGMA table_info(github_activities)").fetchall()
        }
        if "is_private" not in columns:
            # Existing rows predate explicit visibility. Keep them out of public artifacts.
            conn.execute(
                "ALTER TABLE github_activities "
                "ADD COLUMN is_private INTEGER NOT NULL DEFAULT 1 CHECK(is_private IN (0, 1))"
            )

    @staticmethod
    def _ensure_github_activity_state_field_column(conn: sqlite3.Connection) -> None:
        columns = {
            SQLiteRepository._required_text(row, "name")
            for row in conn.execute("PRAGMA table_info(github_activities)").fetchall()
        }
        if "state_field" not in columns:
            conn.execute(
                "ALTER TABLE github_activities "
                "ADD COLUMN state_field TEXT "
                "CHECK(state_field IS NULL OR state_field IN ('conclusion', 'status'))"
            )

    @staticmethod
    def _ensure_github_activity_current_column(conn: sqlite3.Connection) -> None:
        columns = SQLiteRepository._table_columns(conn, "github_activities")
        if "is_current" not in columns:
            conn.execute(
                "ALTER TABLE github_activities "
                "ADD COLUMN is_current INTEGER NOT NULL DEFAULT 1 "
                "CHECK(is_current IN (0, 1))"
            )

    @staticmethod
    def _ensure_origin_columns(conn: sqlite3.Connection) -> None:
        table_defaults = (
            ("sources", "origin_type", "local"),
            ("sources", "origin_visibility", "unknown"),
            ("github_activities", "origin_type", "public_github"),
            ("github_activities", "origin_visibility", "unknown"),
            ("evidence_items", "origin_type", "local"),
            ("evidence_items", "origin_visibility", "unknown"),
        )
        for table, column, default in table_defaults:
            if column not in SQLiteRepository._table_columns(conn, table):
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'"
                )
        conn.execute(
            """
            UPDATE sources
            SET origin_type = CASE
                WHEN type = 'github_repository' THEN 'public_github'
                ELSE 'local'
            END,
            origin_visibility = CASE
                WHEN type = 'local_file' THEN 'private'
                WHEN EXISTS (
                    SELECT 1 FROM github_activities
                    WHERE github_activities.source_id = sources.id
                      AND github_activities.is_private = 1
                ) THEN 'private'
                ELSE 'public'
            END
            WHERE origin_visibility = 'unknown'
            """
        )
        conn.execute(
            """
            UPDATE github_activities
            SET origin_type = CASE WHEN is_private = 1 THEN 'private_github' ELSE 'public_github' END,
                origin_visibility = CASE WHEN is_private = 1 THEN 'private' ELSE 'public' END
            WHERE origin_visibility = 'unknown'
            """
        )
        conn.execute(
            """
            UPDATE evidence_items
            SET origin_type = CASE
                    WHEN github_activity_id IS NOT NULL
                        AND EXISTS (
                            SELECT 1 FROM github_activities
                            WHERE github_activities.id = evidence_items.github_activity_id
                              AND github_activities.is_private = 1
                        ) THEN 'private_github'
                    WHEN github_activity_id IS NOT NULL THEN 'public_github'
                    ELSE 'local'
                END,
                origin_visibility = CASE
                    WHEN github_activity_id IS NOT NULL
                        AND EXISTS (
                            SELECT 1 FROM github_activities
                            WHERE github_activities.id = evidence_items.github_activity_id
                              AND github_activities.is_private = 1
                        ) THEN 'private'
                    WHEN github_activity_id IS NOT NULL THEN 'public'
                    ELSE 'private'
                END
            WHERE origin_visibility = 'unknown'
            """
        )

    def table_names(self) -> set[str]:
        with self._read_connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        return {row["name"] for row in rows}

    def upsert_source(self, source: Source) -> int:
        with self._connection() as conn:
            columns = self._table_columns(conn, "sources")
            origin_type = source.origin_type or (
                "public_github"
                if source.type == SourceType.GITHUB_REPOSITORY
                else "local"
            )
            origin_visibility = source.origin_visibility or (
                "public"
                if source.type == SourceType.GITHUB_REPOSITORY
                else "private"
            )
            if {"origin_type", "origin_visibility"} <= columns:
                conn.execute(
                    """
                    INSERT INTO sources
                        (type, uri, display_name, owner, status, origin_type, origin_visibility)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(uri) DO UPDATE SET
                        type = excluded.type,
                        display_name = excluded.display_name,
                        owner = excluded.owner,
                        origin_type = excluded.origin_type,
                        origin_visibility = excluded.origin_visibility,
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
                        origin_type,
                        origin_visibility,
                        SourceStatus.INGESTED.value,
                        SourceStatus.DISCOVERED.value,
                    ),
                )
            else:
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
        if not activity.state.strip():
            raise RepositoryError("GitHub activity state must be non-empty")
        if not is_valid_github_activity_state(
            activity.activity_type, activity.state, activity.state_field
        ):
            raise RepositoryError("GitHub activity state or provenance is invalid")
        try:
            repository_name = canonical_repository_name(activity.repo)
        except ValueError as error:
            raise RepositoryError("GitHub activity repository is invalid") from error
        activity_url = canonical_public_github_activity_url(activity.url)
        if activity_url is None:
            raise RepositoryError("GitHub activity URL is invalid")
        with self._connection() as conn:
            columns = self._table_columns(conn, "github_activities")
            origin_type = activity.origin_type or (
                "private_github" if activity.is_private else "public_github"
            )
            origin_visibility = activity.origin_visibility or (
                "private" if activity.is_private else "public"
            )
            values = (
                activity.source_id,
                repository_name,
                activity.activity_type,
                activity_url,
                activity.title,
                activity.state,
                activity.author,
                activity.created_at,
                activity.merged_at,
                int(activity.is_private),
                activity.state_field,
            )
            if {"origin_type", "origin_visibility"} <= columns:
                values = (*values, origin_type, origin_visibility, int(activity.is_current))
                if activity.source_id is not None:
                    conn.execute(
                        """
                        UPDATE sources
                        SET origin_type = ?, origin_visibility = ?
                        WHERE id = ? AND type = 'github_repository'
                        """,
                        (origin_type, origin_visibility, activity.source_id),
                    )
            candidate_rows = conn.execute(
                """
                SELECT id, url FROM github_activities
                WHERE repo COLLATE NOCASE = ?
                  AND activity_type = ?
                  AND url COLLATE NOCASE = ?
                ORDER BY id
                """,
                (repository_name, activity.activity_type, activity_url),
            ).fetchall()
            canonical_rows = [
                candidate
                for candidate in candidate_rows
                if isinstance(candidate["url"], str)
                and canonical_public_github_activity_url(candidate["url"]) == activity_url
            ]
            if canonical_rows:
                survivor_id = canonical_rows[0]["id"]
                duplicate_ids = tuple(
                    candidate["id"]
                    for candidate in canonical_rows[1:]
                )
                if duplicate_ids:
                    placeholders = ", ".join("?" for _ in duplicate_ids)
                    conn.execute(
                        f"UPDATE evidence_items SET github_activity_id = ? "
                        f"WHERE github_activity_id IN ({placeholders})",
                        (survivor_id, *duplicate_ids),
                    )
                    conn.execute(
                        f"DELETE FROM github_activities WHERE id IN ({placeholders})",
                        duplicate_ids,
                    )
                if {"origin_type", "origin_visibility"} <= columns:
                    conn.execute(
                        """
                        UPDATE github_activities SET
                            source_id = ?, repo = ?, activity_type = ?, url = ?,
                            title = ?, state = ?, author = ?, created_at = ?,
                            merged_at = ?, is_private = ?, state_field = ?,
                            origin_type = ?, origin_visibility = ?, is_current = ?
                        WHERE id = ?
                        """,
                        (*values, survivor_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE github_activities SET
                            source_id = ?, repo = ?, activity_type = ?, url = ?,
                            title = ?, state = ?, author = ?, created_at = ?,
                            merged_at = ?, is_private = ?, state_field = ?
                        WHERE id = ?
                        """,
                        (*values, survivor_id),
                    )
            else:
                if {"origin_type", "origin_visibility"} <= columns:
                    conn.execute(
                        """
                        INSERT INTO github_activities
                            (source_id, repo, activity_type, url, title, state, author,
                             created_at, merged_at, is_private, state_field,
                             origin_type, origin_visibility, is_current)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO github_activities
                            (source_id, repo, activity_type, url, title, state, author,
                             created_at, merged_at, is_private, state_field)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
            row = conn.execute(
                """
                SELECT id FROM github_activities
                WHERE repo = ? AND activity_type = ? AND url = ?
                """,
                (repository_name, activity.activity_type, activity_url),
            ).fetchone()
        return int(row["id"])

    def invalidate_github_activity_visibility(self) -> None:
        with self._connection() as conn:
            conn.execute("UPDATE github_activities SET is_private = 1")

    def invalidate_unobserved_github_activities(
        self,
        observed_activities: tuple[tuple[str, str, str], ...],
    ) -> None:
        with self._connection() as conn:
            conn.execute("DROP TABLE IF EXISTS temp.observed_github_activity_keys")
            conn.execute(
                """
                CREATE TEMP TABLE observed_github_activity_keys (
                    repo TEXT COLLATE NOCASE NOT NULL,
                    activity_type TEXT NOT NULL,
                    url TEXT COLLATE NOCASE NOT NULL,
                    PRIMARY KEY (repo, activity_type, url)
                )
                """
            )
            try:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO observed_github_activity_keys
                        (repo, activity_type, url)
                    VALUES (?, ?, ?)
                    """,
                    observed_activities,
                )
                stale_clause = """
                    NOT EXISTS (
                        SELECT 1
                        FROM temp.observed_github_activity_keys AS observed
                        WHERE observed.repo = github_activities.repo
                          AND observed.activity_type = github_activities.activity_type
                          AND observed.url = github_activities.url
                    )
                """
                conn.execute(
                    f"""
                    UPDATE github_activities
                    SET is_private = 1,
                        is_current = 0
                    WHERE {stale_clause}
                    """
                )
                conn.execute(
                    f"""
                    UPDATE evidence_items
                    SET public_safe = 0
                    WHERE github_activity_id IN (
                        SELECT id FROM github_activities
                        WHERE {stale_clause}
                    )
                    """
                )
                conn.execute(
                    f"""
                    UPDATE career_claims
                    SET public_safe = 0
                    WHERE id IN (
                        SELECT claim_evidence.claim_id
                        FROM claim_evidence
                        JOIN evidence_items
                          ON evidence_items.id = claim_evidence.evidence_id
                        JOIN github_activities
                          ON github_activities.id = evidence_items.github_activity_id
                        WHERE {stale_clause}
                    )
                    """
                )
            finally:
                conn.execute("DROP TABLE temp.observed_github_activity_keys")

    def invalidate_unconfirmed_github_activity_visibility(
        self,
        confirmed_repositories: tuple[str, ...],
    ) -> None:
        if not confirmed_repositories:
            self.invalidate_github_activity_visibility()
            return
        placeholders = ", ".join("?" for _ in confirmed_repositories)
        with self._connection() as conn:
            conn.execute(
                f"UPDATE github_activities SET is_private = 1 "
                f"WHERE lower(repo) NOT IN ({placeholders})",
                confirmed_repositories,
            )

    def invalidate_github_activity_visibility_for_repositories(
        self,
        repositories: tuple[str, ...],
    ) -> None:
        if not repositories:
            return
        placeholders = ", ".join("?" for _ in repositories)
        with self._connection() as conn:
            conn.execute(
                f"UPDATE github_activities SET is_private = 1 "
                f"WHERE lower(repo) IN ({placeholders})",
                repositories,
            )

    def invalidate_github_activity_visibility_for_endpoints(
        self,
        completed_endpoints: tuple[tuple[str, str], ...],
    ) -> None:
        if not completed_endpoints:
            return
        predicates = " OR ".join(
            "(lower(repo) = ? AND activity_type = ?)" for _ in completed_endpoints
        )
        parameters = tuple(
            value for endpoint in completed_endpoints for value in endpoint
        )
        with self._connection() as conn:
            conn.execute(
                f"""
                UPDATE github_activities
                SET is_private = 1,
                    is_current = 0
                WHERE {predicates}
                """,
                parameters,
            )
            conn.execute(
                f"""
                UPDATE evidence_items
                SET public_safe = 0
                WHERE github_activity_id IN (
                    SELECT id FROM github_activities WHERE {predicates}
                )
                """,
                parameters,
            )
            conn.execute(
                f"""
                UPDATE career_claims
                SET public_safe = 0
                WHERE id IN (
                    SELECT claim_evidence.claim_id
                    FROM claim_evidence
                    JOIN evidence_items
                      ON evidence_items.id = claim_evidence.evidence_id
                    JOIN github_activities
                      ON github_activities.id = evidence_items.github_activity_id
                    WHERE {predicates}
                )
                """,
                parameters,
            )

    def list_github_activities(self) -> list[GitHubActivity]:
        with self._read_connection() as conn:
            columns = self._table_columns(conn, "github_activities")
            origin_type = "origin_type" if "origin_type" in columns else "'public_github'"
            origin_visibility = (
                "origin_visibility" if "origin_visibility" in columns else "'unknown'"
            )
            rows = conn.execute(
                f"""
                SELECT id, source_id, repo, activity_type, url, title, state, author,
                       created_at, merged_at, is_private, state_field,
                       {origin_type} AS origin_type, {origin_visibility} AS origin_visibility,
                       is_current
                FROM github_activities
                ORDER BY id
                """
            ).fetchall()
        try:
            activities: list[GitHubActivity] = []
            for row in rows:
                raw_url = self._required_text(row, "url")
                activities.append(
                    GitHubActivity(
                        id=self._required_int(row, "id"),
                        source_id=self._optional_int(row, "source_id"),
                        repo=self._required_text(row, "repo"),
                        activity_type=self._required_text(row, "activity_type"),
                        url=canonical_public_github_activity_url(raw_url) or raw_url,
                        title=self._required_text(row, "title"),
                        state=self._required_text(row, "state"),
                        author=self._required_text(row, "author"),
                        created_at=self._required_text(row, "created_at"),
                        merged_at=self._optional_text(row, "merged_at"),
                        is_private=bool(self._required_boolean(row, "is_private")),
                        state_field=self._optional_text(row, "state_field"),
                        origin_type=self._required_text(row, "origin_type"),
                        origin_visibility=self._required_text(row, "origin_visibility"),
                        is_current=bool(self._required_boolean(row, "is_current")),
                    )
                )
            return activities
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def list_public_evidence_records(
        self, *, include_unsafe: bool = False
    ) -> list[PublicEvidenceRecord]:
        safety_clause = "" if include_unsafe else """
                WHERE projects.public_safe = 1
                  AND career_claims.public_safe = 1
                  AND evidence_items.public_safe = 1
        """
        with self._read_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    projects.id AS project_id,
                    projects.name AS project_name,
                    career_claims.id AS claim_id,
                    career_claims.text AS claim_text,
                    evidence_items.id AS evidence_id,
                    evidence_items.stable_id AS evidence_stable_id,
                    evidence_items.locator AS evidence_locator,
                    evidence_items.origin_type AS evidence_origin_type,
                    evidence_items.origin_visibility AS evidence_origin_visibility,
                    sources.id AS source_id,
                    sources.type AS source_type,
                    sources.uri AS source_uri,
                    sources.display_name AS source_display_name,
                    sources.status AS source_status,
                    sources.origin_type AS source_origin_type,
                    sources.origin_visibility AS source_origin_visibility,
                    github_activities.id AS activity_id,
                    github_activities.repo AS activity_repo,
                    github_activities.activity_type AS activity_type,
                    github_activities.url AS activity_url,
                    github_activities.title AS activity_title,
                    github_activities.state AS activity_state,
                    github_activities.state_field AS activity_state_field,
                    github_activities.author AS activity_author,
                    github_activities.created_at AS activity_created_at,
                    github_activities.is_private AS activity_is_private,
                    github_activities.is_current AS activity_is_current,
                    github_activities.origin_type AS activity_origin_type,
                    github_activities.origin_visibility AS activity_origin_visibility
                FROM projects
                JOIN career_claims
                  ON career_claims.project_id = projects.id
                JOIN claim_evidence
                  ON claim_evidence.claim_id = career_claims.id
                JOIN evidence_items
                  ON evidence_items.id = claim_evidence.evidence_id
                LEFT JOIN github_activities
                  ON github_activities.id = evidence_items.github_activity_id
                LEFT JOIN sources
                  ON sources.id = COALESCE(
                      evidence_items.source_id,
                      github_activities.source_id
                  )
                {safety_clause}
                ORDER BY projects.id, career_claims.id, evidence_items.id
                """
            ).fetchall()
        try:
            records: list[PublicEvidenceRecord] = []
            for row in rows:
                activity_id = self._optional_int(row, "activity_id")
                activity_is_private = (
                    None
                    if activity_id is None
                    else self._required_boolean(row, "activity_is_private")
                )
                activity_is_current = (
                    None
                    if activity_id is None
                    else bool(self._required_boolean(row, "activity_is_current"))
                )
                records.append(
                    PublicEvidenceRecord(
                        project_id=self._required_int(row, "project_id"),
                        project_name=self._required_text(row, "project_name"),
                        claim_id=self._required_int(row, "claim_id"),
                        claim_text=self._required_text(row, "claim_text"),
                        evidence_id=self._required_int(row, "evidence_id"),
                        evidence_stable_id=self._required_text(row, "evidence_stable_id"),
                        evidence_locator=self._required_text(row, "evidence_locator"),
                        source_id=self._optional_int(row, "source_id"),
                        source_type=self._optional_text(row, "source_type"),
                        source_uri=self._optional_text(row, "source_uri"),
                        source_display_name=self._optional_text(row, "source_display_name"),
                        source_status=self._optional_text(row, "source_status"),
                        activity_id=activity_id,
                        activity_repo=self._optional_text(row, "activity_repo"),
                        activity_type=self._optional_text(row, "activity_type"),
                        activity_url=self._optional_text(row, "activity_url"),
                        activity_title=self._optional_text(row, "activity_title"),
                        activity_state=self._optional_text(row, "activity_state"),
                        activity_state_field=self._optional_text(row, "activity_state_field"),
                        activity_author=self._optional_text(row, "activity_author"),
                        activity_created_at=self._optional_text(row, "activity_created_at"),
                        activity_is_private=activity_is_private,
                        activity_is_current=activity_is_current,
                        evidence_origin_type=self._required_text(row, "evidence_origin_type"),
                        evidence_origin_visibility=self._required_text(
                            row, "evidence_origin_visibility"
                        ),
                        source_origin_type=self._optional_text(row, "source_origin_type"),
                        source_origin_visibility=self._optional_text(
                            row, "source_origin_visibility"
                        ),
                        activity_origin_type=self._optional_text(row, "activity_origin_type"),
                        activity_origin_visibility=self._optional_text(
                            row, "activity_origin_visibility"
                        ),
                    )
                )
            return records
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def list_evidence_selection_records(self) -> list[PublicEvidenceRecord]:
        return self.list_public_evidence_records(include_unsafe=True)

    def replace_portfolio_projects(
        self,
        projects: tuple[dict[str, object], ...],
        approval_sha256: str,
        review_input_sha256: str,
    ) -> None:
        """Replace only the user-approved semantic project graph atomically."""
        with self._connection() as conn:
            conn.execute("DELETE FROM portfolio_project_evidence")
            conn.execute("DELETE FROM portfolio_projects")
            for project in projects:
                project_id = project.get("id")
                title = project.get("title")
                overview = project.get("overview")
                evidence_ids = project.get("evidence_ids")
                if not all(isinstance(value, str) for value in (project_id, title, overview)):
                    raise RepositoryError("semantic project values are invalid")
                if not isinstance(evidence_ids, (tuple, list)):
                    raise RepositoryError("semantic project evidence is invalid")
                conn.execute(
                    """
                    INSERT INTO portfolio_projects
                        (id, title, overview, status, approval_sha256, review_input_sha256)
                    VALUES (?, ?, ?, 'approved', ?, ?)
                    """,
                    (project_id, title, overview, approval_sha256, review_input_sha256),
                )
                for evidence_id in evidence_ids:
                    if not isinstance(evidence_id, int) or isinstance(evidence_id, bool):
                        raise RepositoryError("semantic project evidence is invalid")
                    conn.execute(
                        """
                        INSERT INTO portfolio_project_evidence
                            (project_id, evidence_id, support_level)
                        VALUES (?, ?, 'direct')
                        """,
                        (project_id, evidence_id),
                    )

    def list_portfolio_projects(self) -> list[dict[str, object]]:
        with self._read_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, title, overview, status, approval_sha256,
                       review_input_sha256, created_at, updated_at
                FROM portfolio_projects
                ORDER BY id
                """
            ).fetchall()
            links = conn.execute(
                """
                SELECT project_id, evidence_id, support_level
                FROM portfolio_project_evidence
                ORDER BY project_id, evidence_id
                """
            ).fetchall()
        grouped: dict[str, list[dict[str, object]]] = {}
        for link in links:
            project_id = self._required_text(link, "project_id")
            grouped.setdefault(project_id, []).append(
                {
                    "evidence_id": self._required_int(link, "evidence_id"),
                    "support_level": self._required_text(link, "support_level"),
                }
            )
        try:
            return [
                {
                    "id": self._required_text(row, "id"),
                    "title": self._required_text(row, "title"),
                    "overview": self._required_text(row, "overview"),
                    "status": self._required_text(row, "status"),
                    "approval_sha256": self._required_text(row, "approval_sha256"),
                    "review_input_sha256": self._required_text(row, "review_input_sha256"),
                    "evidence": grouped.get(self._required_text(row, "id"), []),
                }
                for row in rows
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def upsert_evidence_item(
        self,
        *,
        source_id: int | None,
        snapshot_id: int | None,
        github_activity_id: int | None,
        locator: str,
        stable_id: str,
        content_hash: str | None,
        public_safe: bool,
        origin_type: str | None = None,
        origin_visibility: str | None = None,
    ) -> int:
        with self._connection() as conn:
            columns = self._table_columns(conn, "evidence_items")
            if github_activity_id is not None and (
                origin_type is None or origin_visibility is None
            ):
                activity_row = conn.execute(
                    "SELECT is_private FROM github_activities WHERE id = ?",
                    (github_activity_id,),
                ).fetchone()
                is_private = activity_row is not None and activity_row["is_private"] == 1
                origin_type = origin_type or (
                    "private_github" if is_private else "public_github"
                )
                origin_visibility = origin_visibility or (
                    "private" if is_private else "public"
                )
            origin_type = origin_type or "local"
            origin_visibility = origin_visibility or "private"
            base_values = (
                source_id,
                snapshot_id,
                github_activity_id,
                locator,
                stable_id,
                content_hash,
                int(public_safe),
            )
            if {"origin_type", "origin_visibility"} <= columns:
                values = (*base_values, origin_type, origin_visibility)
            else:
                values = base_values
            if {"kind", "summary", "confidence", "origin_type", "origin_visibility"} <= columns:
                conn.execute(
                    """
                    INSERT INTO evidence_items
                        (source_id, snapshot_id, github_activity_id, locator, stable_id,
                         content_hash, public_safe, origin_type, origin_visibility,
                         kind, summary, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'evidence', ?, 'medium')
                    ON CONFLICT(stable_id) DO UPDATE SET
                        source_id = excluded.source_id,
                        snapshot_id = excluded.snapshot_id,
                        github_activity_id = excluded.github_activity_id,
                        locator = excluded.locator,
                        content_hash = excluded.content_hash,
                        public_safe = excluded.public_safe,
                        origin_type = excluded.origin_type,
                        origin_visibility = excluded.origin_visibility
                    """,
                    (*values, locator),
                )
            elif {"kind", "summary", "confidence"} <= columns:
                conn.execute(
                    """
                    INSERT INTO evidence_items
                        (source_id, snapshot_id, github_activity_id, locator, stable_id,
                         content_hash, public_safe, kind, summary, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'evidence', ?, 'medium')
                    ON CONFLICT(stable_id) DO UPDATE SET
                        source_id = excluded.source_id,
                        snapshot_id = excluded.snapshot_id,
                        github_activity_id = excluded.github_activity_id,
                        locator = excluded.locator,
                        content_hash = excluded.content_hash,
                        public_safe = excluded.public_safe
                    """,
                    (*base_values[:5], base_values[5], base_values[6], locator),
                )
            elif {"origin_type", "origin_visibility"} <= columns:
                conn.execute(
                    """
                    INSERT INTO evidence_items
                        (source_id, snapshot_id, github_activity_id, locator, stable_id,
                         content_hash, public_safe, origin_type, origin_visibility)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stable_id) DO UPDATE SET
                        source_id = excluded.source_id,
                        snapshot_id = excluded.snapshot_id,
                        github_activity_id = excluded.github_activity_id,
                        locator = excluded.locator,
                        content_hash = excluded.content_hash,
                        public_safe = excluded.public_safe,
                        origin_type = excluded.origin_type,
                        origin_visibility = excluded.origin_visibility
                    """,
                    values,
                )
            else:
                conn.execute(
                    """
                    INSERT INTO evidence_items
                        (source_id, snapshot_id, github_activity_id, locator, stable_id, content_hash, public_safe)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(stable_id) DO UPDATE SET
                        source_id = excluded.source_id,
                        snapshot_id = excluded.snapshot_id,
                        github_activity_id = excluded.github_activity_id,
                        locator = excluded.locator,
                        content_hash = excluded.content_hash,
                        public_safe = excluded.public_safe
                    """,
                    base_values,
                )
            row = conn.execute(
                "SELECT id FROM evidence_items WHERE stable_id = ?", (stable_id,)
            ).fetchone()
        return self._required_int(row, "id")

    def upsert_project(self, name: str, public_safe: bool) -> int:
        with self._connection() as conn:
            columns = self._table_columns(conn, "projects")
            row = conn.execute(
                "SELECT id FROM projects WHERE name = ? ORDER BY id LIMIT 1", (name,)
            ).fetchone()
            if row is not None:
                project_id = self._required_int(row, "id")
                conn.execute(
                    "UPDATE projects SET public_safe = ? WHERE id = ?",
                    (int(public_safe), project_id),
                )
                return project_id
            if {"summary", "status", "visibility"} <= columns:
                cursor = conn.execute(
                    """
                    INSERT INTO projects
                        (name, summary, status, visibility, public_safe)
                    VALUES (?, '', 'draft', 'public', ?)
                    """,
                    (name, int(public_safe)),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO projects (name, public_safe) VALUES (?, ?)",
                    (name, int(public_safe)),
                )
            return int(cursor.lastrowid)

    def upsert_career_claim(self, project_id: int, text: str, public_safe: bool) -> int:
        with self._connection() as conn:
            columns = self._table_columns(conn, "career_claims")
            row = conn.execute(
                """
                SELECT id FROM career_claims
                WHERE project_id = ? AND text = ? AND public_safe = ?
                ORDER BY id LIMIT 1
                """,
                (project_id, text, int(public_safe)),
            ).fetchone()
            if row is not None:
                return self._required_int(row, "id")
            if {"claim_type", "confidence"} <= columns:
                cursor = conn.execute(
                    """
                    INSERT INTO career_claims
                        (project_id, claim_type, text, confidence, public_safe)
                    VALUES (?, 'project_evidence', ?, 'medium', ?)
                    """,
                    (project_id, text, int(public_safe)),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO career_claims (project_id, text, public_safe) VALUES (?, ?, ?)",
                    (project_id, text, int(public_safe)),
                )
        return int(cursor.lastrowid)

    def reconcile_github_artifact_safety(self) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE projects SET public_safe = 0 "
                "WHERE public_safe = 1 AND name LIKE 'github:%'"
            )
            conn.execute(
                "UPDATE evidence_items SET public_safe = 0 "
                "WHERE github_activity_id IS NOT NULL"
            )
            conn.execute(
                """
                UPDATE career_claims SET public_safe = 0
                WHERE public_safe = 1 AND id IN (
                    SELECT claim_evidence.claim_id
                    FROM claim_evidence
                    JOIN evidence_items
                      ON evidence_items.id = claim_evidence.evidence_id
                    WHERE evidence_items.github_activity_id IS NOT NULL
                )
                """
            )

    def upsert_github_activity_claim(
        self,
        evidence_id: int,
        project_id: int,
        text: str,
    ) -> int:
        with self._connection() as conn:
            columns = self._table_columns(conn, "career_claims")
            row = conn.execute(
                """
                SELECT career_claims.id
                FROM career_claims
                JOIN claim_evidence ON claim_evidence.claim_id = career_claims.id
                WHERE claim_evidence.evidence_id = ?
                ORDER BY career_claims.id
                LIMIT 1
                """,
                (evidence_id,),
            ).fetchone()
            if row is None:
                if {"claim_type", "confidence"} <= columns:
                    cursor = conn.execute(
                        """
                        INSERT INTO career_claims
                            (project_id, claim_type, text, confidence, public_safe)
                        VALUES (?, 'approved_github_activity', ?, 'high', 1)
                        """,
                        (project_id, text),
                    )
                else:
                    cursor = conn.execute(
                        "INSERT INTO career_claims (project_id, text, public_safe) VALUES (?, ?, 1)",
                        (project_id, text),
                    )
                claim_id = int(cursor.lastrowid)
                conn.execute(
                    """
                    INSERT INTO claim_evidence (claim_id, evidence_id, support_level)
                    VALUES (?, ?, 'direct')
                    """,
                    (claim_id, evidence_id),
                )
                return claim_id
            claim_id = self._required_int(row, "id")
            conn.execute(
                """
                UPDATE career_claims
                SET project_id = ?, text = ?, public_safe = 1
                WHERE id = ?
                """,
                (project_id, text, claim_id),
            )
        return claim_id

    def link_claim_evidence(self, claim_id: int, evidence_id: int, support_level: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO claim_evidence (claim_id, evidence_id, support_level)
                VALUES (?, ?, ?)
                """,
                (claim_id, evidence_id, support_level),
            )

    def record_artifact(self, kind: str, version: int, input_manifest: str) -> None:
        with self._connection() as conn:
            columns = self._table_columns(conn, "artifacts")
            if {"type", "path", "source_profile_version"} <= columns:
                conn.execute(
                    """
                    INSERT INTO artifacts
                        (type, path, source_profile_version, kind, version, input_manifest)
                    VALUES (?, '', ?, ?, ?, ?)
                    """,
                    (kind, str(version), kind, version, input_manifest),
                )
            else:
                conn.execute(
                    "INSERT INTO artifacts (kind, version, input_manifest) VALUES (?, ?, ?)",
                    (kind, version, input_manifest),
                )

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
        with self._read_connection() as conn:
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
                    self._required_int(row, "id"),
                    Path(self._required_text(row, "snapshot_path")),
                    self._required_text(row, "content_hash"),
                    self._required_text(row, "extractor"),
                )
                for row in rows
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def latest_snapshot_metadata_by_source_id(self) -> dict[int, tuple[int, Path, str, str]]:
        with self._read_connection() as conn:
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
                self._required_int(row, "source_id"): (
                    self._required_int(row, "id"),
                    Path(self._required_text(row, "snapshot_path")),
                    self._required_text(row, "content_hash"),
                    self._required_text(row, "extractor"),
                )
                for row in rows
            }
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    def list_sources(self, status: SourceStatus | None = None) -> list[Source]:
        with self._read_connection() as conn:
            columns = self._table_columns(conn, "sources")
            origin_type = "origin_type" if "origin_type" in columns else "'local'"
            origin_visibility = (
                "origin_visibility" if "origin_visibility" in columns else "'unknown'"
            )
            sql = (
                "SELECT id, type, uri, display_name, owner, status, "
                f"{origin_type} AS origin_type, {origin_visibility} AS origin_visibility "
                "FROM sources"
            )
            params: tuple[str, ...] = ()
            if status is not None:
                sql += " WHERE status = ?"
                params = (status.value,)
            sql += " ORDER BY id"
            rows = conn.execute(sql, params).fetchall()

        try:
            return [
                Source(
                    id=self._required_int(row, "id"),
                    type=SourceType(self._required_text(row, "type")),
                    uri=self._required_text(row, "uri"),
                    display_name=self._required_text(row, "display_name"),
                    owner=self._optional_text(row, "owner"),
                    status=SourceStatus(self._required_text(row, "status")),
                    origin_type=self._required_text(row, "origin_type"),
                    origin_visibility=self._required_text(row, "origin_visibility"),
                )
                for row in rows
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise self._semantic_error() from error

    @staticmethod
    def _required_int(row: sqlite3.Row, name: str) -> int:
        value = row[name]
        if type(value) is not int:
            raise TypeError(f"{name} must be an integer")
        return value

    @staticmethod
    def _required_text(row: sqlite3.Row, name: str) -> str:
        value = row[name]
        if not isinstance(value, str):
            raise TypeError(f"{name} must be text")
        return value

    @staticmethod
    def _optional_int(row: sqlite3.Row, name: str) -> int | None:
        value = row[name]
        if value is not None and type(value) is not int:
            raise TypeError(f"{name} must be an integer or null")
        return value

    @staticmethod
    def _required_boolean(row: sqlite3.Row, name: str) -> bool:
        value = row[name]
        if value not in (0, 1) or type(value) is not int:
            raise TypeError(f"{name} must be a boolean integer")
        return bool(value)

    @staticmethod
    def _optional_text(row: sqlite3.Row, name: str) -> str | None:
        value = row[name]
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{name} must be text or null")
        return value

    def update_source_status(self, source_id: int, status: SourceStatus) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE sources SET status = ? WHERE id = ?",
                (status.value, source_id),
            )
