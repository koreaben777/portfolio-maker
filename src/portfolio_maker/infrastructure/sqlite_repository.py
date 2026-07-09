from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
from pathlib import Path

from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    uri TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    owner TEXT,
    status TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS evidence_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    snapshot_id INTEGER REFERENCES source_snapshots(id),
    kind TEXT NOT NULL,
    locator TEXT NOT NULL,
    quote_hash TEXT,
    summary TEXT NOT NULL,
    confidence TEXT NOT NULL
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
    merged_at TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    visibility TEXT NOT NULL,
    primary_source_id INTEGER REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS career_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_type TEXT NOT NULL,
    text TEXT NOT NULL,
    confidence TEXT NOT NULL,
    public_safe INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS claim_evidence (
    claim_id INTEGER NOT NULL REFERENCES career_claims(id),
    evidence_id INTEGER NOT NULL REFERENCES evidence_items(id),
    support_level TEXT NOT NULL,
    PRIMARY KEY (claim_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    path TEXT NOT NULL,
    source_profile_version TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class SQLiteRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

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
                    status = excluded.status
                """,
                (
                    source.type.value,
                    source.uri,
                    source.display_name,
                    source.owner,
                    source.status.value,
                ),
            )
            row = conn.execute(
                "SELECT id FROM sources WHERE uri = ?",
                (source.uri,),
            ).fetchone()
        return int(row["id"])

    def insert_github_activity(self, activity: GitHubActivity) -> int:
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO github_activities
                    (source_id, repo, activity_type, url, title, state, author, created_at, merged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        return int(cursor.lastrowid)

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

    def list_sources(self, status: SourceStatus | None = None) -> list[Source]:
        sql = "SELECT id, type, uri, display_name, owner, status FROM sources"
        params: tuple[str, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status.value,)
        sql += " ORDER BY id"

        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()

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

    def update_source_status(self, source_id: int, status: SourceStatus) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE sources SET status = ? WHERE id = ?",
                (status.value, source_id),
            )
