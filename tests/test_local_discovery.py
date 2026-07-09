from __future__ import annotations

from pathlib import Path

from portfolio_maker.application.discovery import discover_sources
from portfolio_maker.application.models import DiscoverSourcesRequest
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.local_discovery import discover_local_candidates
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def test_discover_local_candidates_finds_readme_and_skips_forbidden_and_policy_paths(tmp_path):
    home = tmp_path / "home"
    project = home / "project"
    forbidden = home / "private"
    node_modules = home / "project" / "node_modules"
    project.mkdir(parents=True)
    forbidden.mkdir(parents=True)
    node_modules.mkdir(parents=True)
    readme = project / "README.md"
    readme.write_text("# Portfolio\n", encoding="utf-8")
    (forbidden / "README.md").write_text("# Secret\n", encoding="utf-8")
    (node_modules / "package.json").write_text("{}", encoding="utf-8")

    candidates, skipped = discover_local_candidates(home, forbidden_paths=(forbidden,))

    assert [(candidate.path, candidate.display_name) for candidate in candidates] == [
        (readme.resolve(), "README.md")
    ]
    assert candidates[0].uri == readme.resolve().as_uri()
    assert (forbidden.resolve(), "forbidden") in [(item.path, item.reason) for item in skipped]
    assert (node_modules.resolve(), "skipped_policy") in [(item.path, item.reason) for item in skipped]


def test_discover_local_candidates_prunes_forbidden_children(tmp_path):
    home = tmp_path / "home"
    forbidden = home / "private"
    forbidden.mkdir(parents=True)
    secret = forbidden / "secret-child.md"
    secret.write_text("# Secret\n", encoding="utf-8")

    candidates, skipped = discover_local_candidates(home, forbidden_paths=(forbidden,))

    assert candidates == []
    assert (forbidden.resolve(), "forbidden") in [(item.path, item.reason) for item in skipped]
    reported_paths = [str(item.path) for item in skipped] + [str(candidate.path) for candidate in candidates]
    assert secret.name not in "\n".join(reported_paths)


def test_discover_local_candidates_respects_zero_max_candidates(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "README.md").write_text("# Portfolio\n", encoding="utf-8")

    candidates, skipped = discover_local_candidates(home, max_candidates=0)

    assert candidates == []
    assert skipped == []


def test_discover_local_candidates_uses_contract_skip_reasons_for_oversize_and_oserror(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    oversized = home / "oversized.md"
    unreadable = home / "unreadable.md"
    oversized.write_bytes(b"x" * 2_000_001)
    unreadable.write_text("# blocked\n", encoding="utf-8")
    unreadable_resolved = unreadable.resolve()
    original_stat = Path.stat

    def fail_unreadable_stat(path, *args, **kwargs):
        if path == unreadable_resolved:
            raise OSError("blocked")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_unreadable_stat)

    candidates, skipped = discover_local_candidates(home)

    assert candidates == []
    assert (oversized.resolve(), "skipped_policy") in [(item.path, item.reason) for item in skipped]
    assert (unreadable_resolved, "skipped_permission_denied") in [(item.path, item.reason) for item in skipped]


def test_discover_sources_writes_report_and_persists_local_files(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    project = home / "project"
    project.mkdir(parents=True)
    readme = project / "README.md"
    readme.write_text("# Portfolio\n", encoding="utf-8")

    result = discover_sources(DiscoverSourcesRequest(workspace=workspace, home=home, include_github=False))

    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    sources = repository.list_sources()

    assert result.report_path == paths.discovery_report_path
    assert result.discovered_count == 1
    assert result.skipped_count == 0
    assert len(result.events) == 1
    assert result.events[0].stage == "discovery"
    assert result.events[0].message == "local discovery complete"
    assert paths.discovery_report_path.exists()
    report = paths.discovery_report_path.read_text(encoding="utf-8")
    assert "README.md" in report
    assert readme.resolve().as_uri() in report
    assert sources[0].type == SourceType.LOCAL_FILE
    assert sources[0].status == SourceStatus.DISCOVERED
    assert sources[0].uri == readme.resolve().as_uri()
