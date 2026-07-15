from __future__ import annotations

import json
import sqlite3

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.adapters.cli import main
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def approve_semantic_root(workspace, root) -> None:
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [root.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")


def test_cli_discover_command_creates_report(workspace, tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "README.md").write_text("# Demo\n", encoding="utf-8")

    exit_code = main(
        [
            "discover",
            "--workspace",
            str(workspace),
            "--home",
            str(tmp_path),
            "--no-github",
        ]
    )

    assert exit_code == 0
    assert (workspace / ".portfolio-maker" / "reviews" / "discovery-report.md").exists()


def test_cli_discover_exclude_directory_persists_policy_and_excludes_files(workspace, tmp_path):
    home = tmp_path / "home"
    excluded = home / "excluded"
    excluded.mkdir(parents=True)
    secret = excluded / "README.md"
    secret.write_text("# excluded\n", encoding="utf-8")

    exit_code = main(
        [
            "discover",
            "--workspace",
            str(workspace),
            "--home",
            str(home),
            "--no-github",
            "--exclude-directory",
            str(excluded),
        ]
    )

    approval = json.loads(
        (workspace / ".portfolio-maker" / "reviews" / "source-approval.json").read_text(
            encoding="utf-8"
        )
    )
    report = (workspace / ".portfolio-maker" / "reviews" / "discovery-report.md").read_text(
        encoding="utf-8"
    )
    assert exit_code == 0
    assert str(excluded.resolve()) in approval["excluded_directories"]
    assert "excluded_directory: [redacted]" in report
    assert secret.name not in report


def test_cli_approve_write_sample(workspace):
    exit_code = main(["approve", "--workspace", str(workspace), "--write-sample"])

    assert exit_code == 0
    assert (workspace / ".portfolio-maker" / "reviews" / "source-approval.json").exists()


def test_cli_prepare_semantic_index_reports_chunks_without_locator_values(
    workspace, tmp_path, capsys
):
    root = tmp_path / "approved-root"
    root.mkdir()
    for index in range(501):
        (root / f"item-{index:03}.md").write_text("evidence", encoding="utf-8")
    approve_semantic_root(workspace, root)

    exit_code = main(
        [
            "prepare-semantic-index",
            "--workspace",
            str(workspace),
            "--root",
            str(root),
        ]
    )

    captured = capsys.readouterr()
    report = (
        workspace / ".portfolio-maker" / "reviews" / "semantic-index-report.md"
    )
    assert exit_code == 0
    assert "Semantic index input" in captured.out
    assert "Chunks: 6" in captured.out
    assert str(workspace) not in captured.out
    assert str(root) not in captured.out
    assert report.exists()
    report_text = report.read_text(encoding="utf-8")
    assert "Files: 501" in report_text
    assert "Directories:" in report_text
    assert "Complete:" in report_text
    assert "Partial:" in report_text
    assert "Unsupported:" in report_text
    assert "Unreadable:" in report_text
    assert "Failed:" in report_text
    assert "Active revision: none" in report_text


def test_cli_apply_semantic_index_is_controlled_when_output_missing(
    workspace, tmp_path, capsys
):
    root = tmp_path / "approved-root"
    root.mkdir()
    (root / "README.md").write_text("evidence", encoding="utf-8")
    approve_semantic_root(workspace, root)
    assert main(
        [
            "prepare-semantic-index",
            "--workspace",
            str(workspace),
            "--root",
            str(root),
        ]
    ) == 0
    capsys.readouterr()

    exit_code = main(["apply-semantic-index", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "semantic index output is missing" in captured.err
    assert "Traceback" not in captured.err
    assert str(workspace) not in captured.err


def test_cli_prepare_semantic_index_invalid_root_is_controlled_without_locator(
    workspace, tmp_path, capsys
):
    write_sample_approval(WorkspacePaths.from_root(workspace))
    missing_root = tmp_path / "missing-root"

    exit_code = main(
        [
            "prepare-semantic-index",
            "--workspace",
            str(workspace),
            "--root",
            str(missing_root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "semantic index root is invalid" in captured.err
    assert "Traceback" not in captured.err
    assert str(workspace) not in captured.err
    assert str(missing_root) not in captured.err


def test_cli_prepare_semantic_index_rejects_unapproved_root_without_writing_index_or_report(
    workspace, tmp_path, capsys
):
    approved_root = tmp_path / "approved-root"
    approved_root.mkdir()
    root = tmp_path / "unapproved-root"
    root.mkdir()
    (root / "README.md").write_text("evidence", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [approved_root.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    exit_code = main(
        [
            "prepare-semantic-index",
            "--workspace",
            str(workspace),
            "--root",
            str(root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "semantic index root is not approved" in captured.err
    assert "Traceback" not in captured.err
    assert str(workspace) not in captured.err
    assert str(root) not in captured.err
    assert not paths.semantic_index_manifest_path.exists()
    assert not paths.semantic_index_report_path.exists()


def test_cli_prepare_semantic_index_missing_approval_is_controlled_without_locator(
    workspace, tmp_path, capsys
):
    root = tmp_path / "approved-root"
    root.mkdir()

    exit_code = main(
        [
            "prepare-semantic-index",
            "--workspace",
            str(workspace),
            "--root",
            str(root),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "semantic index approval is missing" in captured.err
    assert "Traceback" not in captured.err
    assert str(workspace) not in captured.err
    assert str(root) not in captured.err


def test_cli_approve_sample_preserves_existing_approval_unless_forced(workspace, capsys):
    approval_path = workspace / ".portfolio-maker" / "reviews" / "source-approval.json"
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text(
        '{"approved_source_uris": ["file:///approved.txt"]}',
        encoding="utf-8",
    )

    rejected_exit_code = main(["approve", "--workspace", str(workspace), "--write-sample"])

    rejected = capsys.readouterr()
    assert rejected_exit_code == 1
    assert "already exists" in rejected.err
    assert "file:///approved.txt" in approval_path.read_text(encoding="utf-8")

    forced_exit_code = main(
        ["approve", "--workspace", str(workspace), "--write-sample", "--force"]
    )

    assert forced_exit_code == 0
    assert "file:///approved.txt" not in approval_path.read_text(encoding="utf-8")


def test_cli_approve_write_sample_artifact_policy(workspace):
    exit_code = main(
        [
            "approve",
            "--workspace",
            str(workspace),
            "--write-sample-artifact-policy",
        ]
    )

    policy_path = (
        workspace / ".portfolio-maker" / "reviews" / "artifact-approval.json"
    )
    assert exit_code == 0
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    assert payload["artifacts"]["portfolio_html"]["delivery_scope"] == "restricted"


def test_cli_prepare_project_review_and_sample_approval(workspace, capsys):
    main(["approve", "--workspace", str(workspace), "--write-sample"])
    main(["approve", "--workspace", str(workspace), "--write-sample-artifact-policy"])

    review_exit = main(["prepare-project-review", "--workspace", str(workspace)])
    review_output = capsys.readouterr().out
    assert review_exit == 0
    assert "Project review input:" in review_output
    review_path = workspace / ".portfolio-maker" / "reviews" / "project-review-input.json"
    assert review_path.exists()

    approval_exit = main(
        ["approve", "--workspace", str(workspace), "--write-sample-project-approval"]
    )
    assert approval_exit == 0
    approval_path = workspace / ".portfolio-maker" / "reviews" / "project-approval.json"
    assert approval_path.exists()

    rejected_exit = main(
        ["approve", "--workspace", str(workspace), "--write-sample-project-approval"]
    )
    captured = capsys.readouterr()
    assert rejected_exit == 1
    assert "already exists" in captured.err


def test_cli_compose_projects_missing_approval_is_controlled(workspace, capsys):
    exit_code = main(["compose-projects", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "project review input is missing" in captured.err
    assert "Traceback" not in captured.err


def test_cli_ingest_missing_approval_exits_without_traceback(workspace, capsys):
    exit_code = main(["ingest", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Approval file missing" in captured.err
    assert "Traceback" not in captured.err


def test_cli_render_html_missing_sites_exits_without_traceback(workspace, capsys):
    exit_code = main(["render-html", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Sites project missing" in captured.err
    assert "Traceback" not in captured.err


def test_cli_ingest_malformed_approval_exits_without_traceback(workspace, capsys):
    approval_path = workspace / ".portfolio-maker" / "reviews" / "source-approval.json"
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text('{"approved_source_uris": "not-a-list"}', encoding="utf-8")

    exit_code = main(["ingest", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "approved_source_uris must be a list" in captured.err
    assert "Traceback" not in captured.err


def test_cli_profile_rejects_github_approval_url_with_query_without_traceback(workspace, capsys):
    approval_path = workspace / ".portfolio-maker" / "reviews" / "source-approval.json"
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text(
        '{"approved_github_activity_urls": ["https://github.com/octo/demo/pull/1?token=synthetic"]}',
        encoding="utf-8",
    )

    exit_code = main(["build-profile", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "approved_github_activity_urls" in captured.err
    assert "Traceback" not in captured.err


def test_cli_ingest_non_object_approval_exits_without_traceback(workspace, capsys):
    approval_path = workspace / ".portfolio-maker" / "reviews" / "source-approval.json"
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text("[]", encoding="utf-8")

    exit_code = main(["ingest", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "approval payload must be an object" in captured.err
    assert "Traceback" not in captured.err


def test_cli_ingest_invalid_utf8_approval_exits_cleanly_and_preserves_state(workspace, capsys):
    approval_path = workspace / ".portfolio-maker" / "reviews" / "source-approval.json"
    approval_path.parent.mkdir(parents=True)
    damaged = b"\xff\xfe"
    approval_path.write_bytes(damaged)

    exit_code = main(["ingest", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "invalid UTF-8" in captured.err
    assert "repair or replace" in captured.err.casefold()
    assert "Traceback" not in captured.err
    assert approval_path.read_bytes() == damaged


def test_cli_ingest_invalid_json_approval_exits_cleanly_and_preserves_state(workspace, capsys):
    approval_path = workspace / ".portfolio-maker" / "reviews" / "source-approval.json"
    approval_path.parent.mkdir(parents=True)
    damaged = b"{invalid-json"
    approval_path.write_bytes(damaged)

    exit_code = main(["ingest", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "invalid JSON" in captured.err
    assert "repair or replace" in captured.err.casefold()
    assert "Traceback" not in captured.err
    assert approval_path.read_bytes() == damaged


def test_cli_discover_invalid_sqlite_exits_cleanly_and_preserves_state(
    workspace,
    tmp_path,
    capsys,
):
    database_path = workspace / ".portfolio-maker" / "portfolio.db"
    database_path.parent.mkdir(parents=True)
    damaged = b"not-a-sqlite-database"
    database_path.write_bytes(damaged)
    home = tmp_path / "home"
    home.mkdir()

    exit_code = main(
        [
            "discover",
            "--workspace",
            str(workspace),
            "--home",
            str(home),
            "--no-github",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "database" in captured.err.casefold()
    assert "repair or replace" in captured.err.casefold()
    assert "Traceback" not in captured.err
    assert database_path.read_bytes() == damaged


def test_cli_discover_busy_database_exits_with_retryable_contention_error(
    workspace,
    tmp_path,
    capsys,
):
    paths = WorkspacePaths.from_root(workspace)
    SQLiteRepository(paths.db_path).initialize()
    home = tmp_path / "home"
    home.mkdir()
    writer = sqlite3.connect(paths.db_path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        exit_code = main(
            [
                "discover",
                "--workspace",
                str(workspace),
                "--home",
                str(home),
                "--no-github",
            ]
        )
    finally:
        writer.rollback()
        writer.close()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "try again" in captured.err.casefold()
    assert "repair or replace" not in captured.err.casefold()
    assert "Traceback" not in captured.err


def test_cli_discover_unsafe_database_path_preserves_state_without_corruption_advice(
    workspace,
    tmp_path,
    capsys,
):
    database_path = workspace / ".portfolio-maker" / "portfolio.db"
    database_path.parent.mkdir(parents=True)
    external = tmp_path / "external.db"
    external.write_bytes(b"external marker")
    database_path.symlink_to(external)
    home = tmp_path / "home"
    home.mkdir()

    exit_code = main(
        [
            "discover",
            "--workspace",
            str(workspace),
            "--home",
            str(home),
            "--no-github",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Unsafe managed database path: portfolio.db" in captured.err
    assert "preserve or back up" in captured.err.casefold()
    assert "repair or replace" not in captured.err.casefold()
    assert "Traceback" not in captured.err
    assert external.read_bytes() == b"external marker"


def test_cli_ingest_invalid_enum_row_exits_without_traceback(workspace, capsys):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text("{}", encoding="utf-8")
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

    exit_code = main(["ingest", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "stored data is invalid" in captured.err
    assert "Traceback" not in captured.err


def test_cli_discover_skips_self_referential_symlink_and_keeps_valid_candidate(
    workspace,
    tmp_path,
    capsys,
):
    home = tmp_path / "home"
    home.mkdir()
    (home / "valid.md").write_text("valid evidence", encoding="utf-8")
    (home / "loop.md").symlink_to("loop.md")

    exit_code = main(
        [
            "discover",
            "--workspace",
            str(workspace),
            "--home",
            str(home),
            "--no-github",
        ]
    )

    captured = capsys.readouterr()
    report = workspace / ".portfolio-maker" / "reviews" / "discovery-report.md"
    assert exit_code == 0
    assert "Traceback" not in captured.err
    assert "valid.md" in report.read_text(encoding="utf-8")
    assert "skipped_unresolvable" in report.read_text(encoding="utf-8")


def test_cli_discover_missing_root_exits_without_traceback(workspace, tmp_path, capsys):
    exit_code = main(
        [
            "discover",
            "--workspace",
            str(workspace),
            "--home",
            str(tmp_path / "missing"),
            "--no-github",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Discovery root does not exist" in captured.err
    assert "Traceback" not in captured.err


def test_cli_draft_malformed_profile_recovers_by_rebuilding(workspace, capsys):
    profile_path = workspace / ".portfolio-maker" / "artifacts" / "master-profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text('["unexpected"]', encoding="utf-8")
    approval_path = workspace / ".portfolio-maker" / "reviews" / "source-approval.json"
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text("{}", encoding="utf-8")

    exit_code = main(["draft-portfolio", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Portfolio draft:" in captured.out
    assert "Traceback" not in captured.err


def test_cli_discover_anchors_relative_forbidden_path_to_workspace(workspace, monkeypatch):
    private_path = workspace / "private" / "notes.md"
    private_path.parent.mkdir()
    private_path.write_text("private evidence", encoding="utf-8")
    monkeypatch.chdir(workspace.parent)

    exit_code = main(
        [
            "discover",
            "--workspace",
            str(workspace),
            "--home",
            str(workspace),
            "--forbidden-path",
            "private",
            "--no-github",
        ]
    )

    report = workspace / ".portfolio-maker" / "reviews" / "discovery-report.md"
    assert exit_code == 0
    assert private_path.name not in report.read_text(encoding="utf-8")


def test_cli_ingest_invalid_tilde_forbidden_path_exits_without_traceback(workspace, capsys):
    approval_path = workspace / ".portfolio-maker" / "reviews" / "source-approval.json"
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text(
        '{"forbidden_paths": ["~portfolio_maker_missing_user/private"]}',
        encoding="utf-8",
    )

    exit_code = main(["ingest", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "invalid forbidden path" in captured.err
    assert "Traceback" not in captured.err
