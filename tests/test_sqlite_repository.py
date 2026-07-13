import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from stat import S_IMODE

import pytest

from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
import portfolio_maker.infrastructure.sqlite_repository as sqlite_repository_module
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError, SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def column_names(repository, table_name):
    with repository._connection() as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def test_workspace_paths_create_expected_directories(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()

    assert paths.root == workspace / ".portfolio-maker"
    assert paths.db_path == workspace / ".portfolio-maker" / "portfolio.db"
    assert paths.reviews_dir.is_dir()
    assert paths.artifacts_dir.is_dir()
    assert paths.local_snapshots_dir.is_dir()
    assert not paths.db_path.exists()
    assert not paths.master_profile_json_path.exists()
    assert not paths.master_profile_md_path.exists()
    assert not paths.portfolio_draft_path.exists()


def test_sqlite_repository_initialize_creates_schema_tables(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)

    repository.initialize()

    assert {
        "sources",
        "source_snapshots",
        "github_activities",
    } <= repository.table_names()


def test_sqlite_repository_keeps_schema_creation_inside_guarded_transaction(
    workspace,
    monkeypatch,
):
    paths = WorkspacePaths.from_root(workspace)
    traces: list[str] = []
    original_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(traces.append)
        return connection

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        traced_connect,
    )

    SQLiteRepository(paths.db_path).initialize()

    first_schema_statement = next(
        index for index, statement in enumerate(traces) if "CREATE TABLE" in statement
    )
    first_commit = traces.index("COMMIT")
    assert first_schema_statement < first_commit


def test_sqlite_repository_creates_phase_one_evidence_tables(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    assert {
        "evidence_items",
        "projects",
        "career_claims",
        "claim_evidence",
        "artifacts",
    } <= repository.table_names()


def test_sqlite_repository_evidence_relationships_enforce_foreign_keys(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    with pytest.raises(RepositoryError):
        with repository._connection() as conn:
            conn.execute(
                "INSERT INTO claim_evidence (claim_id, evidence_id, support_level) VALUES (1, 1, 'direct')"
            )


def test_sqlite_repository_enforces_foreign_keys(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    with pytest.raises(RepositoryError):
        with repository._connection() as conn:
            conn.execute(
                """
                INSERT INTO source_snapshots (
                    source_id,
                    snapshot_path,
                    content_hash,
                    extractor
                )
                VALUES (?, ?, ?, ?)
                """,
                (999, "/missing", "hash", "test"),
            )


def test_sqlite_repository_initialize_creates_required_schema_columns(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    assert {"discovered_at", "approved_at"} <= column_names(repository, "sources")
    assert {"extracted_at"} <= column_names(repository, "source_snapshots")
    assert {"repo", "activity_type", "url", "state_field"} <= column_names(
        repository, "github_activities"
    )


def test_sqlite_repository_migrates_legacy_workflow_rows_without_provenance(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    with sqlite3.connect(paths.db_path) as connection:
        connection.execute(
            """
            CREATE TABLE github_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                repo TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                state TEXT NOT NULL,
                author TEXT NOT NULL,
                created_at TEXT NOT NULL,
                merged_at TEXT,
                is_private INTEGER NOT NULL DEFAULT 0,
                UNIQUE(repo, activity_type, url)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO github_activities
                (repo, activity_type, url, title, state, author, created_at)
            VALUES ('octo/demo', 'workflow_run',
                    'https://github.com/octo/demo/actions/runs/1',
                    'CI', 'queued', 'octo', '2026-01-01T00:00:00Z')
            """
        )

    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    assert "state_field" in column_names(repository, "github_activities")
    assert repository.list_github_activities()[0].state_field is None


def test_sqlite_repository_upsert_source_lists_inserted_source(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="/workspace/project",
            display_name="project",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )

    sources = repository.list_sources()

    assert source_id == 1
    assert len(sources) == 1
    assert sources[0].id == 1
    assert sources[0].uri == "/workspace/project"


def test_sqlite_repository_upsert_source_updates_existing_uri(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    first_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="/workspace/project",
            display_name="project",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )
    second_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.GITHUB_REPOSITORY,
            uri="/workspace/project",
            display_name="updated project",
            owner="june",
            status=SourceStatus.APPROVED,
        )
    )

    sources = repository.list_sources()

    assert second_id == first_id
    assert len(sources) == 1
    assert sources[0] == Source(
        id=first_id,
        type=SourceType.GITHUB_REPOSITORY,
        uri="/workspace/project",
        display_name="updated project",
        owner="june",
        status=SourceStatus.APPROVED,
    )


def test_sqlite_repository_list_sources_filters_by_status(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="/workspace/project",
            display_name="project",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )
    approved_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.GITHUB_REPOSITORY,
            uri="https://github.com/example/project",
            display_name="github project",
            owner="example",
            status=SourceStatus.APPROVED,
        )
    )

    sources = repository.list_sources(status=SourceStatus.APPROVED)

    assert len(sources) == 1
    assert sources[0].id == approved_id
    assert sources[0].status == SourceStatus.APPROVED


def test_sqlite_repository_update_source_status_is_observable(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="/workspace/project",
            display_name="project",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )

    repository.update_source_status(source_id, SourceStatus.APPROVED)

    sources = repository.list_sources(status=SourceStatus.APPROVED)
    assert [source.id for source in sources] == [source_id]


def test_sqlite_repository_upserts_github_activity_by_repo_type_and_url(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    activity = GitHubActivity(
        id=None,
        source_id=None,
        repo="octo/demo",
        activity_type="issue",
        url="https://github.com/octo/demo/issues/1",
        title="First title",
        state="OPEN",
        author="octo",
        created_at="2026-01-01T00:00:00Z",
        merged_at=None,
    )

    first_id = repository.insert_github_activity(activity)
    second_id = repository.insert_github_activity(
        GitHubActivity(
            id=None,
            source_id=None,
            repo=activity.repo,
            activity_type=activity.activity_type,
            url=activity.url,
            title="Updated title",
            state=activity.state,
            author=activity.author,
            created_at=activity.created_at,
            merged_at=activity.merged_at,
        )
    )

    with repository._connection() as conn:
        rows = conn.execute("SELECT id, title FROM github_activities").fetchall()
    assert second_id == first_id
    assert [(row["id"], row["title"]) for row in rows] == [(first_id, "Updated title")]


def test_sqlite_repository_legacy_alias_lookup_does_not_scan_unrelated_rows(
    workspace, monkeypatch
):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    with repository._connection() as conn:
        for activity_number in range(10, 110):
            conn.execute(
                """
                INSERT INTO github_activities
                    (repo, activity_type, url, title, state, author, created_at, is_private)
                VALUES ('octo/demo', 'issue', ?, 'Old title', 'OPEN', 'octo', ?, 0)
                """,
                (
                    f"https://github.com/octo/demo/issues/{activity_number}",
                    "2026-01-01T00:00:00Z",
                ),
            )
        conn.execute(
            """
            INSERT INTO github_activities
                (repo, activity_type, url, title, state, author, created_at, is_private)
            VALUES ('octo/demo', 'issue', ?, 'Legacy alias', 'OPEN', 'octo', ?, 0)
            """,
            ("https://github.com/Octo/Demo/issues/1", "2026-01-01T00:00:00Z"),
        )

    original_canonicalizer = sqlite_repository_module.canonical_public_github_activity_url
    canonicalizer_calls = []

    def tracked_canonicalizer(value):
        canonicalizer_calls.append(value)
        return original_canonicalizer(value)

    monkeypatch.setattr(
        sqlite_repository_module,
        "canonical_public_github_activity_url",
        tracked_canonicalizer,
    )
    repository.insert_github_activity(
        GitHubActivity(
            None,
            None,
            "octo/demo",
            "issue",
            "https://github.com/octo/demo/issues/1",
            "Current title",
            "OPEN",
            "octo",
            "2026-01-02T00:00:00Z",
            None,
        )
    )

    assert len(canonicalizer_calls) <= 2


def test_sqlite_repository_requires_workflow_state_provenance(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    with pytest.raises(RepositoryError, match="state or provenance is invalid"):
        repository.insert_github_activity(
            GitHubActivity(
                id=None,
                source_id=None,
                repo="octo/demo",
                activity_type="workflow_run",
                url="https://github.com/octo/demo/actions/runs/1",
                title="CI",
                state="queued",
                author="octo",
                created_at="2026-01-01T00:00:00Z",
                merged_at=None,
            )
        )


def test_sqlite_repository_creates_private_unlinked_database_and_upgrades_permissions(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.root.mkdir(parents=True)
    paths.root.chmod(0o755)
    repository = SQLiteRepository(paths.db_path)

    repository.initialize()

    assert S_IMODE(paths.root.stat().st_mode) == 0o700
    assert S_IMODE(paths.db_path.stat().st_mode) == 0o600
    assert paths.db_path.stat().st_nlink == 1


@pytest.mark.parametrize("alias_kind", ("symlink", "hardlink", "directory", "fifo"))
def test_sqlite_repository_rejects_unsafe_main_database_entry(
    workspace,
    tmp_path,
    alias_kind,
):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    external = tmp_path / "external.db"
    sqlite3.connect(external).close()
    if alias_kind == "symlink":
        paths.db_path.symlink_to(external)
    elif alias_kind == "hardlink":
        os.link(external, paths.db_path)
    elif alias_kind == "directory":
        paths.db_path.mkdir()
    else:
        os.mkfifo(paths.db_path)

    with pytest.raises(RepositoryError, match="Unsafe managed database path: portfolio.db"):
        SQLiteRepository(paths.db_path).initialize()

    assert external.stat().st_size == 0


@pytest.mark.parametrize(
    ("suffix", "alias_kind"),
    [
        (suffix, alias_kind)
        for suffix in ("-journal", "-wal", "-shm")
        for alias_kind in ("symlink", "hardlink", "directory", "fifo")
    ],
)
def test_sqlite_repository_rejects_unsafe_database_sidecars_before_write(
    workspace,
    tmp_path,
    suffix,
    alias_kind,
):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    sidecar = paths.db_path.with_name(paths.db_path.name + suffix)
    sidecar.unlink()
    external = tmp_path / f"external{suffix}"
    external.write_bytes(b"external sidecar marker")
    if alias_kind == "symlink":
        sidecar.symlink_to(external)
    elif alias_kind == "hardlink":
        os.link(external, sidecar)
    elif alias_kind == "directory":
        sidecar.mkdir()
    else:
        os.mkfifo(sidecar)

    with pytest.raises(RepositoryError, match=f"Unsafe managed database path: portfolio.db{suffix}"):
        repository.upsert_source(
            Source(
                id=None,
                type=SourceType.LOCAL_FILE,
                uri="file:///safe.md",
                display_name="safe.md",
                owner=None,
                status=SourceStatus.DISCOVERED,
            )
        )

    assert external.read_bytes() == b"external sidecar marker"


def test_sqlite_repository_accepts_normal_persisted_wal_and_shm_sidecars(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    writer = sqlite3.connect(paths.db_path)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE wal_marker (value TEXT)")
        writer.commit()
        assert paths.db_path.with_name("portfolio.db-wal").exists()
        assert paths.db_path.with_name("portfolio.db-shm").exists()

        assert "sources" in repository.table_names()
    finally:
        writer.close()


def test_sqlite_repository_migrates_legacy_mvp_schema(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    with sqlite3.connect(paths.db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                summary TEXT NOT NULL,
                status TEXT NOT NULL,
                visibility TEXT NOT NULL,
                primary_source_id INTEGER
            );
            CREATE TABLE career_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_type TEXT NOT NULL,
                text TEXT NOT NULL,
                confidence TEXT NOT NULL,
                public_safe INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE evidence_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                snapshot_id INTEGER,
                kind TEXT NOT NULL,
                locator TEXT NOT NULL,
                quote_hash TEXT,
                summary TEXT NOT NULL,
                confidence TEXT NOT NULL
            );
            CREATE TABLE artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                path TEXT NOT NULL,
                source_profile_version TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    with repository._read_connection() as connection:
        columns = {
            table: {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            for table in ("projects", "career_claims", "evidence_items", "artifacts")
        }
    assert "public_safe" in columns["projects"]
    assert "project_id" in columns["career_claims"]
    assert {
        "github_activity_id",
        "stable_id",
        "content_hash",
        "public_safe",
    } <= columns["evidence_items"]
    assert {"kind", "version", "input_manifest"} <= columns["artifacts"]

    source_id = repository.upsert_source(
        Source(
            None,
            SourceType.LOCAL_FILE,
            "file:///legacy.md",
            "legacy.md",
            None,
            SourceStatus.DISCOVERED,
        )
    )
    project_id = repository.upsert_project("local:legacy", public_safe=False)
    evidence_id = repository.upsert_evidence_item(
        source_id=source_id,
        snapshot_id=None,
        github_activity_id=None,
        locator="file:///legacy.md",
        stable_id="legacy-source:1",
        content_hash=None,
        public_safe=False,
    )
    claim_id = repository.upsert_career_claim(
        project_id,
        "Legacy evidence",
        public_safe=False,
    )
    repository.link_claim_evidence(claim_id, evidence_id, "direct")
    repository.record_artifact("legacy-check", 1, "{}")


def test_sqlite_repository_rejects_main_replacement_before_connect(workspace, tmp_path, monkeypatch):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    replacement = tmp_path / "replacement.db"
    sqlite3.connect(replacement).close()
    monkeypatch.setattr(repository, "_lock_database_directory", lambda directory_descriptor: None)
    original_connect = sqlite3.connect
    replaced = False

    def replace_before_connect(path, *args, **kwargs):
        nonlocal replaced
        if not replaced:
            replaced = True
            os.replace(replacement, paths.db_path)
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr("portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect", replace_before_connect)

    with pytest.raises(RepositoryError, match="database changed"):
        repository.table_names()


def test_sqlite_repository_does_not_create_missing_replacement_target_during_connect(
    workspace,
    tmp_path,
    monkeypatch,
):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    missing_external = tmp_path / "missing-external.db"
    original_connect = sqlite3.connect
    replaced = False

    def replace_with_missing_target(path, *args, **kwargs):
        nonlocal replaced
        if not replaced:
            replaced = True
            paths.db_path.unlink()
            paths.db_path.symlink_to(missing_external)
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        replace_with_missing_target,
    )

    with pytest.raises(RepositoryError):
        repository.table_names()

    assert not missing_external.exists()


def test_sqlite_repository_prevents_empty_late_journal_alias_after_connect(
    workspace,
    tmp_path,
    monkeypatch,
):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    sidecar = paths.db_path.with_name("portfolio.db-journal")
    external = tmp_path / "external-journal"
    external.write_bytes(b"")
    original_validate = repository._validate_database_family
    attempts = 0
    injection_blocked = False
    injected = False

    def inject_after_validation(directory_descriptor, identity, stage):
        nonlocal attempts, injected, injection_blocked
        original_validate(directory_descriptor, identity, stage)
        if stage == "after connect":
            attempts += 1
            try:
                sidecar.unlink()
            except FileNotFoundError:
                pass
            except PermissionError:
                injection_blocked = True
                return
            try:
                os.link(external, sidecar)
            except PermissionError:
                injection_blocked = True
            else:
                injected = True

    monkeypatch.setattr(repository, "_validate_database_family", inject_after_validation)

    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="file:///late-journal.md",
            display_name="late-journal.md",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )

    assert attempts == 1
    assert injection_blocked is True
    assert injected is False
    assert external.stat().st_size == 0


def test_sqlite_repository_prevents_journal_alias_after_overlapping_read(
    workspace,
    tmp_path,
    monkeypatch,
):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    sidecar = paths.db_path.with_name("portfolio.db-journal")
    external = tmp_path / "external-journal"
    external.write_bytes(b"")
    original_validate = repository._validate_database_family
    overlapping_read_completed = False
    injected = False
    injection_blocked = False

    def inject_after_write_validation(directory_descriptor, identity, stage):
        nonlocal overlapping_read_completed, injected, injection_blocked
        original_validate(directory_descriptor, identity, stage)
        if stage != "after write transaction":
            return
        assert "sources" in SQLiteRepository(paths.db_path).table_names()
        overlapping_read_completed = True
        try:
            sidecar.unlink()
            os.link(external, sidecar)
        except PermissionError:
            injection_blocked = True
        else:
            injected = True

    monkeypatch.setattr(repository, "_validate_database_family", inject_after_write_validation)

    error: RepositoryError | None = None
    try:
        repository.upsert_source(
            Source(
                id=None,
                type=SourceType.LOCAL_FILE,
                uri="file:///overlapping-read.md",
                display_name="overlapping-read.md",
                owner=None,
                status=SourceStatus.DISCOVERED,
            )
        )
    except RepositoryError as caught:
        error = caught

    assert external.stat().st_size == 0
    assert error is None
    assert overlapping_read_completed is True
    assert injection_blocked is True
    assert injected is False
    assert S_IMODE(paths.root.stat().st_mode) == 0o700


def test_sqlite_repository_prevents_journal_alias_after_child_process_read(
    workspace,
    tmp_path,
    monkeypatch,
):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    sidecar = paths.db_path.with_name("portfolio.db-journal")
    external = tmp_path / "external-journal"
    external.write_bytes(b"")
    original_validate = repository._validate_database_family
    child: subprocess.Popen[str] | None = None
    child_completed_before_injection = False
    injected = False
    injection_blocked = False
    child_script = (
        "from pathlib import Path\n"
        "import sys\n"
        "from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository\n"
        "print('started', flush=True)\n"
        "print('sources' in SQLiteRepository(Path(sys.argv[1])).table_names(), flush=True)\n"
    )
    environment = os.environ | {
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")
    }

    def inject_after_write_validation(directory_descriptor, identity, stage):
        nonlocal child, child_completed_before_injection, injected, injection_blocked
        original_validate(directory_descriptor, identity, stage)
        if stage != "after write transaction":
            return
        child = subprocess.Popen(
            [sys.executable, "-c", child_script, str(paths.db_path)],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "started"
        deadline = time.monotonic() + 1
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        child_completed_before_injection = child.poll() is not None
        try:
            sidecar.unlink()
            os.link(external, sidecar)
        except PermissionError:
            injection_blocked = True
        else:
            injected = True

    monkeypatch.setattr(repository, "_validate_database_family", inject_after_write_validation)

    error: RepositoryError | None = None
    try:
        repository.upsert_source(
            Source(
                id=None,
                type=SourceType.LOCAL_FILE,
                uri="file:///child-read.md",
                display_name="child-read.md",
                owner=None,
                status=SourceStatus.DISCOVERED,
            )
        )
    except RepositoryError as caught:
        error = caught

    assert child is not None
    stdout, stderr = child.communicate(timeout=5)
    assert external.stat().st_size == 0
    assert error is None
    assert child_completed_before_injection is False
    assert child.returncode == 0
    assert stdout.strip() == "True"
    assert stderr == ""
    assert injection_blocked is True
    assert injected is False
    assert S_IMODE(paths.root.stat().st_mode) == 0o700


@pytest.mark.parametrize("operation", ("read", "write"))
def test_sqlite_repository_preserves_nonzero_user_version(workspace, operation):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    with sqlite3.connect(paths.db_path) as connection:
        connection.execute("PRAGMA user_version = 37")

    if operation == "read":
        assert "sources" in repository.table_names()
    else:
        repository.upsert_source(
            Source(
                id=None,
                type=SourceType.LOCAL_FILE,
                uri="file:///user-version.md",
                display_name="user-version.md",
                owner=None,
                status=SourceStatus.DISCOVERED,
            )
        )

    with sqlite3.connect(paths.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 37


def test_sqlite_repository_read_does_not_block_on_healthy_writer(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    writer = sqlite3.connect(paths.db_path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        assert "sources" in repository.table_names()
    finally:
        writer.rollback()
        writer.close()


def test_sqlite_repository_rejects_commit_replacement_without_visible_persistence(
    workspace,
    tmp_path,
    monkeypatch,
):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="file:///commit-replacement.md",
            display_name="commit-replacement.md",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )
    replacement = tmp_path / "replacement.db"
    with sqlite3.connect(paths.db_path) as visible:
        with sqlite3.connect(replacement) as replacement_connection:
            visible.backup(replacement_connection)
    detached = sqlite3.connect(paths.db_path)
    monkeypatch.setattr(repository, "_lock_database_directory", lambda directory_descriptor: None)
    original_connect = sqlite3.connect
    replaced = False

    def replace_inside_commit(path, *args, **kwargs):
        connection = original_connect(path, *args, **kwargs)

        def replace_on_commit(statement):
            nonlocal replaced
            if statement == "COMMIT" and not replaced:
                replaced = True
                os.replace(replacement, paths.db_path)

        connection.set_trace_callback(replace_on_commit)
        return connection

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        replace_inside_commit,
    )

    try:
        with pytest.raises(RepositoryError, match="database changed"):
            repository.update_source_status(source_id, SourceStatus.APPROVED)

        with sqlite3.connect(paths.db_path) as visible:
            visible_status = visible.execute(
                "SELECT status FROM sources WHERE id = ?", (source_id,)
            ).fetchone()[0]
        detached_status = detached.execute(
            "SELECT status FROM sources WHERE id = ?", (source_id,)
        ).fetchone()[0]
    finally:
        detached.close()

    assert replaced is True
    assert visible_status == SourceStatus.DISCOVERED
    assert detached_status == SourceStatus.APPROVED


def test_sqlite_repository_rolls_back_when_database_replaced_before_commit(
    workspace,
    tmp_path,
    monkeypatch,
):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="file:///before-replacement.md",
            display_name="before-replacement.md",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )
    replacement = tmp_path / "replacement.db"
    sqlite3.connect(replacement).close()
    original_connect = sqlite3.connect
    original_stat = os.stat
    replaced = False

    def connect_with_replacement_trace(path, *args, **kwargs):
        connection = original_connect(path, *args, **kwargs)

        def replace_on_insert(statement):
            nonlocal replaced
            if "UPDATE sources" in statement and not replaced:
                replaced = True

        connection.set_trace_callback(replace_on_insert)
        return connection

    def report_replacement_after_update(path, *args, **kwargs):
        if replaced and path == paths.db_path.name and "dir_fd" in kwargs:
            return original_stat(replacement)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.sqlite3.connect",
        connect_with_replacement_trace,
    )
    monkeypatch.setattr(
        "portfolio_maker.infrastructure.sqlite_repository.os.stat",
        report_replacement_after_update,
    )

    with pytest.raises(RepositoryError, match="database changed"):
        repository.update_source_status(source_id, SourceStatus.APPROVED)

    replaced = False
    assert repository.list_sources()[0].status == SourceStatus.DISCOVERED


def test_sqlite_repository_maps_invalid_existing_enum_row_to_controlled_error(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    with sqlite3.connect(paths.db_path) as connection:
        connection.execute(
            """
            CREATE TABLE sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                uri TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                owner TEXT,
                status TEXT NOT NULL,
                discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
                approved_at TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO sources (type, uri, display_name, owner, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("local_file", "file:///invalid.md", "invalid.md", None, "invalid-status"),
        )

    with pytest.raises(RepositoryError, match="stored data is invalid"):
        SQLiteRepository(paths.db_path).list_sources()


@pytest.mark.parametrize("column", ("uri", "owner"))
def test_sqlite_repository_maps_blob_source_text_to_controlled_error(workspace, column):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    values = {
        "type": "local_file",
        "uri": "file:///blob.md",
        "display_name": "blob.md",
        "owner": "owner",
        "status": "discovered",
    }
    values[column] = sqlite3.Binary(b"blob")
    with sqlite3.connect(paths.db_path) as connection:
        connection.execute(
            """
            CREATE TABLE sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                uri TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                owner TEXT,
                status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO sources (type, uri, display_name, owner, status)
            VALUES (:type, :uri, :display_name, :owner, :status)
            """,
            values,
        )

    with pytest.raises(RepositoryError, match="stored data is invalid"):
        SQLiteRepository(paths.db_path).list_sources()


@pytest.mark.parametrize("column", ("source_id", "snapshot_path", "content_hash", "extractor"))
def test_sqlite_repository_maps_blob_snapshot_text_to_controlled_error(workspace, column):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    values = {
        "source_id": 1,
        "snapshot_path": "/safe/snapshot.json",
        "content_hash": "hash",
        "extractor": "text-v2",
    }
    values[column] = sqlite3.Binary(b"blob")
    with sqlite3.connect(paths.db_path) as connection:
        connection.execute(
            """
            CREATE TABLE source_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                snapshot_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                extractor TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO source_snapshots (source_id, snapshot_path, content_hash, extractor)
            VALUES (:source_id, :snapshot_path, :content_hash, :extractor)
            """,
            values,
        )

    with pytest.raises(RepositoryError, match="stored data is invalid"):
        repository = SQLiteRepository(paths.db_path)
        if column == "source_id":
            repository.latest_snapshot_metadata_by_source_id()
        else:
            repository.snapshot_metadata_for_source(1)


def test_sqlite_repository_does_not_expose_raw_connection_bypass():
    assert not hasattr(SQLiteRepository, "connect")


def test_sqlite_repository_new_schema_rejects_invalid_enum_values(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    with sqlite3.connect(paths.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO sources (type, uri, display_name, owner, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("invalid-type", "file:///invalid.md", "invalid.md", None, "invalid-status"),
            )


def test_sqlite_repository_repeated_initialize_and_discovery_write_remain_supported(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)

    repository.initialize()
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="file:///repeat.md",
            display_name="repeat.md",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )

    assert repository.list_sources()[0].id == source_id
