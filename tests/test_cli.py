from __future__ import annotations

from portfolio_maker.adapters.cli import main


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


def test_cli_approve_write_sample(workspace):
    exit_code = main(["approve", "--workspace", str(workspace), "--write-sample"])

    assert exit_code == 0
    assert (workspace / ".portfolio-maker" / "reviews" / "source-approval.json").exists()


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


def test_cli_ingest_missing_approval_exits_without_traceback(workspace, capsys):
    exit_code = main(["ingest", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Approval file missing" in captured.err
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


def test_cli_ingest_non_object_approval_exits_without_traceback(workspace, capsys):
    approval_path = workspace / ".portfolio-maker" / "reviews" / "source-approval.json"
    approval_path.parent.mkdir(parents=True)
    approval_path.write_text("[]", encoding="utf-8")

    exit_code = main(["ingest", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "approval payload must be an object" in captured.err
    assert "Traceback" not in captured.err


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
