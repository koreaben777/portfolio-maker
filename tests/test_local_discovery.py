from __future__ import annotations

from pathlib import Path

from portfolio_maker.application.discovery import discover_sources
from portfolio_maker.application.models import DiscoverSourcesRequest
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.github_connector import (
    GitHubActivityCandidate,
    GitHubRepositoryCandidate,
)
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


def test_discover_local_candidates_blocks_forbidden_root_without_listing_children(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    secret = home / "secret-child.md"
    secret.write_text("# Secret\n", encoding="utf-8")

    candidates, skipped = discover_local_candidates(home, forbidden_paths=(home,))

    assert candidates == []
    assert [(item.path, item.reason) for item in skipped] == [(home.resolve(), "forbidden")]
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


def test_discover_sources_redacts_policy_skipped_paths_in_report(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    (home / ".env").write_text("API_KEY=secret\n", encoding="utf-8")

    result = discover_sources(DiscoverSourcesRequest(workspace=workspace, home=home, include_github=False))

    report = result.report_path.read_text(encoding="utf-8")
    assert "skipped_policy" in report
    assert ".env" not in report


def test_discover_sources_includes_github_candidates(workspace, tmp_path, monkeypatch):
    def fake_discover_github_candidates(**kwargs):
        assert kwargs == {
            "excluded_repositories": (),
            "private_sources_allowed": False,
        }
        return (
            [
                GitHubRepositoryCandidate(
                    name_with_owner="octo/demo",
                    url="https://github.com/octo/demo",
                    is_private=False,
                    description="Demo portfolio project",
                    primary_language="Python",
                )
            ],
            [
                GitHubActivityCandidate(
                    repo="octo/demo",
                    activity_type="pull_request",
                    url="https://github.com/octo/demo/pull/1",
                    title="Add RAG ingestion",
                    state="MERGED",
                    author="octo",
                    created_at="2026-01-01T00:00:00Z",
                    merged_at="2026-01-02T00:00:00Z",
                )
            ],
            [],
        )

    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        fake_discover_github_candidates,
    )

    result = discover_sources(
        DiscoverSourcesRequest(
            workspace=workspace,
            home=tmp_path,
            include_github=True,
            forbidden_paths=(),
        )
    )

    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    sources = repository.list_sources()
    report = result.report_path.read_text(encoding="utf-8")
    repo_source = next(source for source in sources if source.type == SourceType.GITHUB_REPOSITORY)
    with repository.connect() as conn:
        activity = conn.execute(
            "SELECT source_id FROM github_activities WHERE repo = ?",
            ("octo/demo",),
        ).fetchone()

    assert result.discovered_count == 2
    assert activity["source_id"] == repo_source.id
    assert "octo/demo" in report
    assert "Add RAG ingestion" in report


def test_discover_sources_keeps_local_report_when_github_fails(workspace, tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    readme.write_text("# Demo\n", encoding="utf-8")

    def fail_github(**kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        fail_github,
    )

    result = discover_sources(
        DiscoverSourcesRequest(
            workspace=workspace,
            home=tmp_path,
            include_github=True,
            forbidden_paths=(),
        )
    )

    report = result.report_path.read_text(encoding="utf-8")
    assert result.discovered_count == 1
    assert "README.md" in report
    assert "GitHub discovery failed" in report


def test_discover_sources_uses_approval_forbidden_paths_on_rerun(workspace, tmp_path):
    home = tmp_path / "home"
    private = home / "private"
    private.mkdir(parents=True)
    secret = private / "README.md"
    secret.write_text("# Secret\n", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        f"""
{{
  "approved_source_uris": [],
  "forbidden_paths": ["{private}"],
  "excluded_repositories": [],
  "private_sources_allowed": false
}}
""".strip(),
        encoding="utf-8",
    )

    result = discover_sources(
        DiscoverSourcesRequest(
            workspace=workspace,
            home=home,
            include_github=False,
            forbidden_paths=(),
        )
    )

    report = result.report_path.read_text(encoding="utf-8")
    assert result.discovered_count == 0
    assert "forbidden: [redacted]" in report
    assert secret.name not in report


def test_discover_sources_filters_private_and_excluded_github_repos(workspace, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    paths.approval_path.write_text(
        """
{
  "approved_source_uris": [],
  "forbidden_paths": [],
  "excluded_repositories": ["octo/excluded"],
  "private_sources_allowed": false
}
""".strip(),
        encoding="utf-8",
    )

    def fake_discover_github_candidates(**kwargs):
        assert kwargs == {
            "excluded_repositories": ("octo/excluded",),
            "private_sources_allowed": False,
        }
        return (
            [
                GitHubRepositoryCandidate(
                    name_with_owner="octo/public",
                    url="https://github.com/octo/public",
                    is_private=False,
                    description="Public",
                    primary_language="Python",
                ),
                GitHubRepositoryCandidate(
                    name_with_owner="octo/private",
                    url="https://github.com/octo/private",
                    is_private=True,
                    description="Private",
                    primary_language="Python",
                ),
                GitHubRepositoryCandidate(
                    name_with_owner="octo/excluded",
                    url="https://github.com/octo/excluded",
                    is_private=False,
                    description="Excluded",
                    primary_language="Python",
                ),
            ],
            [
                GitHubActivityCandidate(
                    repo="octo/public",
                    activity_type="issue",
                    url="https://github.com/octo/public/issues/1",
                    title="Keep",
                    state="OPEN",
                    author="octo",
                    created_at="2026-01-01T00:00:00Z",
                    merged_at=None,
                ),
                GitHubActivityCandidate(
                    repo="octo/private",
                    activity_type="issue",
                    url="https://github.com/octo/private/issues/1",
                    title="Drop private",
                    state="OPEN",
                    author="octo",
                    created_at="2026-01-01T00:00:00Z",
                    merged_at=None,
                ),
                GitHubActivityCandidate(
                    repo="octo/excluded",
                    activity_type="issue",
                    url="https://github.com/octo/excluded/issues/1",
                    title="Drop excluded",
                    state="OPEN",
                    author="octo",
                    created_at="2026-01-01T00:00:00Z",
                    merged_at=None,
                ),
            ],
            [],
        )

    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        fake_discover_github_candidates,
    )

    result = discover_sources(
            DiscoverSourcesRequest(
                workspace=workspace,
                home=home,
                include_github=True,
                forbidden_paths=(),
            )
    )

    report = result.report_path.read_text(encoding="utf-8")
    assert result.discovered_count == 2
    assert "octo/public" in report
    assert "octo/private" not in report
    assert "octo/excluded" not in report
