from __future__ import annotations

import json
import sqlite3
import hashlib
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.artifact_approval import write_sample_artifact_policy
from portfolio_maker.application.history import HistoryError
import portfolio_maker.application.render_html as render_html_module
from portfolio_maker.application.vision_analysis import VisionAnalysisError
from portfolio_maker.adapters import cli
from portfolio_maker.adapters.cli import main
from portfolio_maker.domain.history_models import CleanupExecutionState
from portfolio_maker.domain.vision_models import VisionRequestStatus, VisionReviewStatus
from portfolio_maker.domain.semantic_models import boundary_fingerprint
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import RepositoryError, SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def approve_semantic_root(workspace, root) -> None:
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [root.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")


def seed_v2_candidate(workspace, *, confidence="medium") -> tuple[WorkspacePaths, str]:
    paths = WorkspacePaths.from_root(workspace)
    evidence_path = workspace / "approved-evidence.md"
    evidence_path.write_text("Approved evidence", encoding="utf-8")
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [evidence_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    write_sample_artifact_policy(paths)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            None,
            SourceType.LOCAL_FILE,
            evidence_path.resolve().as_uri(),
            "Approved evidence",
            None,
            SourceStatus.INGESTED,
        )
    )
    evidence_id = repository.upsert_evidence_item(
        source_id=source_id,
        snapshot_id=None,
        github_activity_id=None,
        locator=evidence_path.resolve().as_uri(),
        stable_id="source-snapshot:v2:approved",
        content_hash="approved",
        public_safe=False,
    )
    technical_project_id = repository.upsert_project("technical:v2", public_safe=False)
    claim_id = repository.upsert_career_claim(
        technical_project_id, "Approved evidence", public_safe=False
    )
    repository.link_claim_evidence(claim_id, evidence_id, "direct")
    review_input = {
        "version": 2,
        "artifact_kind": "master_profile",
        "delivery_scope": "restricted",
        "policy_hash": "a" * 64,
        "index_revision": "revision-v2",
        "nodes": [
            {
                "node_id": "node-approved",
                "parent_node_id": None,
                "relative_hierarchy": ".",
                "topics": ["portfolio"],
                "evidence_ids": [evidence_id],
            }
        ],
        "github_evidence": [],
    }
    review_input["input_sha256"] = hashlib.sha256(
        json.dumps(
            review_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    paths.ensure()
    paths.project_review_input_v2_path.write_text(
        json.dumps(review_input), encoding="utf-8"
    )
    candidates = {
        "version": 2,
        "review_input_sha256": review_input["input_sha256"],
        "candidates": [
            {
                "id": "candidate-insurance-rag",
                "project_id": "insurance-rag",
                "title": "Insurance RAG",
                "overview": "Grounded project evidence.",
                "boundary_type": "directory_root",
                "boundary_node_ids": ["node-approved"],
                "boundary_fingerprint": boundary_fingerprint(
                    "directory_root", ("node-approved",)
                ),
                "evidence_ids": [evidence_id],
                "grouping_rationale": ["One approved work unit."],
                "counter_signals": [],
                "review_reasons": [],
                "confidence": confidence,
            }
        ],
    }
    paths.project_candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    return paths, "insurance-rag"


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


def test_cli_compose_projects_automatic_materializes_medium_candidate(workspace, capsys):
    paths, project_id = seed_v2_candidate(workspace)

    exit_code = main(
        [
            "compose-projects",
            "--workspace",
            str(workspace),
            "--mode",
            "automatic",
        ]
    )

    assert exit_code == 0
    assert "Composed projects: 1" in capsys.readouterr().out
    assert [project["id"] for project in SQLiteRepository(paths.db_path).list_portfolio_projects()] == [
        project_id
    ]


def test_cli_review_mode_keeps_candidate_inactive_until_manual_include(workspace, capsys):
    paths, project_id = seed_v2_candidate(workspace)

    assert main(["compose-projects", "--workspace", str(workspace), "--mode", "review"]) == 0
    assert SQLiteRepository(paths.db_path).list_portfolio_projects() == []

    assert main(
        [
            "set-project-state",
            "--workspace",
            str(workspace),
            "--project-id",
            project_id,
            "--state",
            "included",
        ]
    ) == 0
    assert main(
        [
            "list-projects",
            "--workspace",
            str(workspace),
            "--decision-status",
            "manually_approved",
            "--format",
            "ids",
        ]
    ) == 0
    assert capsys.readouterr().out.splitlines()[-1] == project_id


def test_cli_set_project_state_reports_unknown_project_without_traceback(workspace, capsys):
    exit_code = main(
        [
            "set-project-state",
            "--workspace",
            str(workspace),
            "--project-id",
            "missing-project",
            "--state",
            "excluded",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unknown" in captured.err
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


def test_cli_history_policy_writes_sample_without_path_and_handles_duplicate(
    workspace, capsys
):
    command = ["history-policy", "--workspace", str(workspace), "--write-sample"]

    assert main(command) == 0
    created = capsys.readouterr()
    assert created.out == "History policy sample created.\n"
    assert str(workspace) not in created.out
    assert created.err == ""

    assert main(command) == 1
    duplicate = capsys.readouterr()
    assert duplicate.out == ""
    assert "history policy" in duplicate.err.casefold()
    assert str(workspace) not in duplicate.err
    assert "Traceback" not in duplicate.err


def test_cli_history_plan_reports_only_safe_summary(workspace, monkeypatch, capsys):
    received = []

    def plan(request):
        received.append(request)
        return SimpleNamespace(
            plan_id="plan-safe",
            candidate_count=2,
            total_bytes=321,
            report_path=workspace / ".portfolio-maker" / "reviews" / "private-report.md",
        )

    monkeypatch.setattr(cli, "plan_history_cleanup", plan)

    assert main(["history-plan", "--workspace", str(workspace)]) == 0

    captured = capsys.readouterr()
    assert captured.out == "History plan: plan-safe\nCandidates: 2\nBytes: 321\n"
    assert captured.err == ""
    assert received[0].workspace == workspace
    assert str(workspace) not in captured.out
    assert ".portfolio-maker" not in captured.out
    assert "private-report" not in captured.out


def test_cli_history_export_reports_receipt_without_archive_or_backup_path(
    workspace, tmp_path, monkeypatch, capsys
):
    backup_dir = tmp_path / "outside-backup"
    received = []

    def export(request):
        received.append(request)
        return SimpleNamespace(
            plan_id="plan-safe",
            receipt_hash="a" * 64,
            archive_path=backup_dir / "private-history-export.tar",
        )

    monkeypatch.setattr(cli, "export_history_plan", export)

    assert main(
        [
            "history-export",
            "--workspace",
            str(workspace),
            "--plan-id",
            "plan-safe",
            "--backup-dir",
            str(backup_dir),
        ]
    ) == 0

    captured = capsys.readouterr()
    assert captured.out == f"History plan: plan-safe\nExport receipt: {'a' * 64}\n"
    assert captured.err == ""
    assert received[0].workspace == workspace
    assert received[0].backup_dir == backup_dir
    assert str(workspace) not in captured.out
    assert str(backup_dir) not in captured.out
    assert "private-history-export" not in captured.out


def test_cli_history_apply_modes_report_only_execution_summary(
    workspace, monkeypatch, capsys
):
    calls = []

    def apply(request):
        calls.append(("apply", request))
        return SimpleNamespace(
            execution_id="execution-new",
            state=CleanupExecutionState.PURGED,
            affected_count=3,
        )

    def resume(request):
        calls.append(("resume", request))
        return SimpleNamespace(
            execution_id="execution-resume",
            state=CleanupExecutionState.PURGED,
            affected_count=2,
        )

    def recover(request):
        calls.append(("recover", request))
        return SimpleNamespace(
            execution_id="execution-recover",
            state=CleanupExecutionState.QUARANTINED,
            affected_count=1,
        )

    monkeypatch.setattr(cli, "apply_history_plan", apply)
    monkeypatch.setattr(cli, "resume_history_cleanup", resume)
    monkeypatch.setattr(cli, "recover_history_cleanup", recover)

    commands = (
        (
            [
                "history-apply",
                "--workspace",
                str(workspace),
                "--plan-id",
                "plan-safe",
                "--export-receipt",
                "b" * 64,
                "--confirm",
                "synthetic confirmation phrase",
            ],
            "Execution: execution-new\nState: purged\nAffected: 3\n",
        ),
        (
            [
                "history-apply",
                "--workspace",
                str(workspace),
                "--resume",
                "execution-resume",
            ],
            "Execution: execution-resume\nState: purged\nAffected: 2\n",
        ),
        (
            [
                "history-apply",
                "--workspace",
                str(workspace),
                "--recover",
                "execution-recover",
            ],
            "Execution: execution-recover\nState: quarantined\nAffected: 1\n",
        ),
    )
    for command, expected_output in commands:
        assert main(command) == 0
        captured = capsys.readouterr()
        assert captured.out == expected_output
        assert captured.err == ""
        assert str(workspace) not in captured.out
        assert "synthetic confirmation phrase" not in captured.out

    assert [kind for kind, _ in calls] == ["apply", "resume", "recover"]


def test_cli_history_apply_rejects_missing_partial_and_mixed_modes_before_service(
    workspace, tmp_path, monkeypatch, capsys
):
    invoked = []

    def unexpected(*args):
        invoked.append(args)
        raise AssertionError("history service must not be invoked")

    monkeypatch.setattr(cli, "apply_history_plan", unexpected)
    monkeypatch.setattr(cli, "resume_history_cleanup", unexpected)
    monkeypatch.setattr(cli, "recover_history_cleanup", unexpected)

    private_path = tmp_path / "private-workspace"
    receipt = "c" * 64
    confirmation = "synthetic confirmation must not be echoed"
    invalid_commands = (
        ["history-apply", "--workspace", str(private_path)],
        [
            "history-apply",
            "--workspace",
            str(private_path),
            "--resume",
            "",
        ],
        [
            "history-apply",
            "--workspace",
            str(private_path),
            "--recover",
            "",
        ],
        [
            "history-apply",
            "--workspace",
            str(private_path),
            "--plan-id",
            "plan-safe",
        ],
        [
            "history-apply",
            "--workspace",
            str(private_path),
            "--resume",
            "execution-safe",
            "--confirm",
            confirmation,
        ],
        [
            "history-apply",
            "--workspace",
            str(private_path),
            "--plan-id",
            "plan-safe",
            "--export-receipt",
            receipt,
            "--confirm",
            confirmation,
            "--resume",
            "",
        ],
        [
            "history-apply",
            "--workspace",
            str(private_path),
            "--plan-id",
            "plan-safe",
            "--export-receipt",
            receipt,
            "--confirm",
            confirmation,
            "--recover",
            "",
        ],
        [
            "history-apply",
            "--workspace",
            str(private_path),
            "--recover",
            "execution-safe",
            "--export-receipt",
            receipt,
        ],
    )
    for command in invalid_commands:
        assert main(command) == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "history apply mode is invalid" in captured.err
        assert str(private_path) not in captured.err
        assert receipt not in captured.err
        assert confirmation not in captured.err
        assert "Traceback" not in captured.err

    assert invoked == []


def _approved_vision_source(workspace: Path) -> tuple[WorkspacePaths, int, Path]:
    source_path = workspace / "synthetic-approved.bin"
    source_path.write_bytes(b"synthetic binary input")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            None,
            SourceType.LOCAL_FILE,
            source_path.resolve().as_uri(),
            source_path.name,
            None,
            SourceStatus.APPROVED,
        )
    )
    return paths, source_id, source_path


def _fake_vision_sites_build(*args, **kwargs):
    temporary_site = Path(kwargs["cwd"])
    dist = temporary_site / "dist"
    (dist / "assets").mkdir(parents=True)
    generated = temporary_site / "src" / "generated" / "portfolio-data.ts"
    (dist / "index.html").write_text(
        '<script type="module" src="./assets/main.js"></script>\n'
        + generated.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (dist / "assets" / "main.js").write_text(
        "document.body.dataset.ready = 'true';",
        encoding="utf-8",
    )
    return subprocess.CompletedProcess(args[0], 0)


def test_cli_vision_parser_exposes_exact_commands_and_arguments() -> None:
    parser = cli.build_parser()
    help_text = parser.format_help()

    for command in (
        "prepare-vision-analysis",
        "apply-vision-analysis",
        "list-vision-analyses",
        "set-vision-analysis-state",
    ):
        assert command in help_text

    prepare = parser.parse_args(
        [
            "prepare-vision-analysis",
            "--workspace",
            "synthetic-workspace",
            "--source-id",
            "7",
            "--confirm-codex-share",
        ]
    )
    assert prepare.workspace == Path("synthetic-workspace")
    assert prepare.source_id == 7
    assert prepare.confirm_codex_share is True

    apply = parser.parse_args(
        [
            "apply-vision-analysis",
            "--workspace",
            "synthetic-workspace",
            "--result",
            "synthetic-result.json",
        ]
    )
    assert apply.workspace == Path("synthetic-workspace")
    assert apply.result == Path("synthetic-result.json")

    listed = parser.parse_args(
        ["list-vision-analyses", "--workspace", "synthetic-workspace"]
    )
    assert listed.workspace == Path("synthetic-workspace")

    state = parser.parse_args(
        [
            "set-vision-analysis-state",
            "--workspace",
            "synthetic-workspace",
            "--analysis-id",
            "analysis-safe",
            "--state",
            "included",
        ]
    )
    assert state.analysis_id == "analysis-safe"
    assert state.state == "included"


def test_cli_vision_prepare_requires_confirmation_before_any_persistence(
    workspace, capsys
):
    paths, source_id, source_path = _approved_vision_source(workspace)

    assert main(
        [
            "prepare-vision-analysis",
            "--workspace",
            str(workspace),
            "--source-id",
            str(source_id),
        ]
    ) == 1

    captured = capsys.readouterr()
    repository = SQLiteRepository(paths.db_path)
    with repository._read_connection() as connection:
        request_count = connection.execute(
            "SELECT COUNT(*) FROM vision_analysis_requests"
        ).fetchone()[0]
    assert request_count == 0
    assert list(paths.vision_requests_dir.iterdir()) == []
    assert captured.out == ""
    assert "confirmation" in captured.err.casefold()
    assert str(workspace) not in captured.err
    assert source_path.resolve().as_uri() not in captured.err
    assert "Traceback" not in captured.err


def test_cli_vision_prepare_routes_typed_request_and_safe_output(
    workspace, monkeypatch, capsys
):
    received = []

    def prepare(request):
        received.append(request)
        return SimpleNamespace(id="request-safe", status=VisionRequestStatus.PENDING)

    monkeypatch.setattr(cli, "prepare_vision_analysis", prepare)

    assert main(
        [
            "prepare-vision-analysis",
            "--workspace",
            str(workspace),
            "--source-id",
            "23",
            "--confirm-codex-share",
        ]
    ) == 0

    captured = capsys.readouterr()
    assert captured.out == "Vision request: request-safe\nStatus: pending\n"
    assert captured.err == ""
    assert received[0].workspace == workspace
    assert received[0].source_id == 23
    assert received[0].confirm_codex_share is True
    assert str(workspace) not in captured.out
    assert "23" not in captured.out


def test_cli_vision_apply_completed_and_status_only_outputs_are_safe(
    workspace, tmp_path, monkeypatch, capsys
):
    received = []
    rows = {
        "analysis-safe": {
            "confidence": "medium",
            "source_id": 91,
            "result_hash": "f" * 64,
            "relative_path": "derived/vision/private.json",
            "adapter": "codex-app-vision-v1",
        }
    }

    class FakeRepository:
        def __init__(self, db_path):
            self.db_path = db_path

        def initialize(self):
            return None

        def get_vision_analysis(self, analysis_id):
            return rows[analysis_id]

    def apply(request):
        received.append(request)
        if request.result_path.name == "status-only.json":
            return None
        return SimpleNamespace(
            id="analysis-safe",
            review_status=VisionReviewStatus.PENDING,
            result_hash="f" * 64,
            analysis_path=workspace / "private-analysis.json",
        )

    monkeypatch.setattr(cli, "apply_vision_analysis", apply)
    monkeypatch.setattr(cli, "SQLiteRepository", FakeRepository)

    completed_path = tmp_path / "completed.json"
    assert main(
        [
            "apply-vision-analysis",
            "--workspace",
            str(workspace),
            "--result",
            str(completed_path),
        ]
    ) == 0
    completed = capsys.readouterr()
    assert completed.out == (
        "Vision analysis: analysis-safe\n"
        "Review state: pending\n"
        "Confidence: medium\n"
    )
    assert completed.err == ""

    status_path = tmp_path / "status-only.json"
    assert main(
        [
            "apply-vision-analysis",
            "--workspace",
            str(workspace),
            "--result",
            str(status_path),
        ]
    ) == 0
    status_only = capsys.readouterr()
    assert status_only.out == "Vision result recorded.\n"
    assert status_only.err == ""

    assert [request.result_path for request in received] == [
        completed_path,
        status_path,
    ]
    serialized = completed.out + status_only.out
    for forbidden in (
        str(workspace),
        str(completed_path),
        str(status_path),
        "f" * 64,
        "codex-app-vision-v1",
        "derived/vision",
        "91",
    ):
        assert forbidden not in serialized


def test_cli_vision_list_is_deterministic_and_allowlist_only(
    workspace, monkeypatch, capsys
):
    rows = [
        {
            "id": "analysis-a",
            "request_id": "request-private-a",
            "source_id": 41,
            "source_content_hash": "a" * 64,
            "result_hash": "b" * 64,
            "relative_path": "derived/vision/private-a.json",
            "adapter": "codex-app-vision-v1",
            "status": "completed",
            "confidence": "medium",
            "review_status": "pending",
        },
        {
            "id": "analysis-b",
            "request_id": "request-private-b",
            "source_id": 42,
            "source_content_hash": "c" * 64,
            "result_hash": "d" * 64,
            "relative_path": "derived/vision/private-b.json",
            "adapter": "codex-app-vision-v1",
            "status": "completed",
            "confidence": "high",
            "review_status": "included",
        },
    ]

    class FakeRepository:
        def __init__(self, db_path):
            self.db_path = db_path

        def initialize(self):
            return None

        def list_vision_analyses(self):
            return rows

    monkeypatch.setattr(cli, "SQLiteRepository", FakeRepository)

    assert main(["list-vision-analyses", "--workspace", str(workspace)]) == 0

    captured = capsys.readouterr()
    assert captured.out == (
        "Vision analyses: 2\n"
        "analysis-a\tcompleted\tpending\tmedium\n"
        "analysis-b\tcompleted\tincluded\thigh\n"
    )
    assert captured.err == ""
    assert captured.out.index("analysis-a") < captured.out.index("analysis-b")
    for forbidden in (
        str(workspace),
        "request-private",
        "derived/vision",
        "codex-app-vision-v1",
        "a" * 64,
        "b" * 64,
        "41",
        "42",
    ):
        assert forbidden not in captured.out


def test_cli_vision_state_routes_include_and_exclude(
    workspace, monkeypatch, capsys
):
    received = []

    def set_state(request):
        received.append(request)
        return SimpleNamespace(analysis_id=request.analysis_id, state=request.state)

    monkeypatch.setattr(cli, "set_vision_analysis_state", set_state)

    for state in ("included", "excluded"):
        assert main(
            [
                "set-vision-analysis-state",
                "--workspace",
                str(workspace),
                "--analysis-id",
                "analysis-safe",
                "--state",
                state,
            ]
        ) == 0
        captured = capsys.readouterr()
        assert captured.out == (
            f"Vision analysis: analysis-safe\nReview state: {state}\n"
        )
        assert captured.err == ""

    assert [request.state for request in received] == ["included", "excluded"]


def test_cli_vision_controlled_failure_redacts_private_details(
    workspace, monkeypatch, capsys
):
    private_locator = str(workspace / "private-result.json")
    private_hash = "e" * 64

    def fail(_request):
        raise VisionAnalysisError(
            f"failed at {private_locator} {private_hash} codex-app-vision-v1 observation"
        )

    monkeypatch.setattr(cli, "apply_vision_analysis", fail)

    assert main(
        [
            "apply-vision-analysis",
            "--workspace",
            str(workspace),
            "--result",
            private_locator,
        ]
    ) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "vision command failed safely\n"
    for forbidden in (
        private_locator,
        private_hash,
        "codex-app-vision-v1",
        "observation",
        "Traceback",
    ):
        assert forbidden not in captured.err


def test_cli_vision_repository_failure_redacts_private_details(
    workspace, monkeypatch, capsys
):
    private_locator = str(workspace / ".portfolio-maker" / "portfolio.db")

    class FailingRepository:
        def __init__(self, db_path):
            self.db_path = db_path

        def initialize(self):
            raise RepositoryError(f"database failed at {private_locator}")

    monkeypatch.setattr(cli, "SQLiteRepository", FailingRepository)

    assert main(["list-vision-analyses", "--workspace", str(workspace)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "vision command failed safely\n"
    assert private_locator not in captured.err
    assert "Traceback" not in captured.err


def test_cli_vision_capability_unavailable_smoke_keeps_artifacts_empty_and_safe(
    workspace, monkeypatch, capsys
):
    paths, source_id, source_path = _approved_vision_source(workspace)
    repository_root = Path(__file__).resolve().parents[1]
    shutil.copytree(
        repository_root / "web" / "portfolio",
        workspace / "web" / "portfolio",
        ignore=shutil.ignore_patterns("node_modules", "dist"),
    )
    monkeypatch.setattr(
        render_html_module.subprocess,
        "run",
        _fake_vision_sites_build,
    )

    assert main(
        [
            "prepare-vision-analysis",
            "--workspace",
            str(workspace),
            "--source-id",
            str(source_id),
            "--confirm-codex-share",
        ]
    ) == 0
    prepared_output = capsys.readouterr()
    request_id = prepared_output.out.splitlines()[0].split(": ", 1)[1]
    repository = SQLiteRepository(paths.db_path)
    request = repository.get_vision_request(request_id)

    result_path = workspace / "synthetic-status-only.json"
    result_path.write_text(
        json.dumps(
            {
                "schema_version": "vision-analysis-v1",
                "request_id": request_id,
                "source_content_hash": request["source_content_hash"],
                "adapter": "codex-app-vision-v1",
                "status": "unavailable",
                "reason_code": "capability_unavailable",
            }
        ),
        encoding="utf-8",
    )
    assert main(
        [
            "apply-vision-analysis",
            "--workspace",
            str(workspace),
            "--result",
            str(result_path),
        ]
    ) == 0
    assert capsys.readouterr().out == "Vision result recorded.\n"

    for command in ("build-profile", "draft-portfolio", "render-html"):
        assert main([command, "--workspace", str(workspace)]) == 0
        capsys.readouterr()

    repository = SQLiteRepository(paths.db_path)
    assert repository.list_vision_analyses() == []
    with repository._read_connection() as connection:
        vision_evidence = connection.execute(
            "SELECT COUNT(*) FROM evidence_items WHERE stable_id LIKE 'vision-analysis:%'"
        ).fetchone()[0]
        vision_claims = connection.execute(
            "SELECT COUNT(*) FROM career_claims"
        ).fetchone()[0]
    assert vision_evidence == 0
    assert vision_claims == 0
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            paths.master_profile_json_path,
            paths.master_profile_md_path,
            paths.portfolio_draft_path,
            paths.portfolio_public_json_path,
            paths.portfolio_html_path,
        )
    )
    for forbidden in (
        source_path.resolve().as_uri(),
        str(source_path.resolve()),
        str(workspace.resolve()),
        request_id,
        request["source_content_hash"],
        "codex-app-vision-v1",
        "capability_unavailable",
    ):
        assert forbidden not in serialized


def test_cli_history_error_is_controlled_and_never_leaks_locator(
    workspace, monkeypatch, capsys
):
    unsafe_locator = f"file://{workspace}/private-history-plan.json"

    def fail(_request):
        raise HistoryError(f"history failure: {unsafe_locator}")

    monkeypatch.setattr(cli, "plan_history_cleanup", fail)

    assert main(["history-plan", "--workspace", str(workspace)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "managed history command failed safely" in captured.err
    assert unsafe_locator not in captured.err
    assert str(workspace) not in captured.err
    assert "Traceback" not in captured.err
