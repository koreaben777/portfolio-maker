from __future__ import annotations

import json
from pathlib import Path

import pytest

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.discovery import discover_sources
from portfolio_maker.application.draft_portfolio import draft_portfolio
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DiscoverSourcesRequest,
    DraftPortfolioRequest,
)
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.github_connector import (
    GitHubActivityCandidate,
    GitHubDiscoveryResult,
    GitHubEndpointOutcome,
    GitHubRepositoryCandidate,
)
from portfolio_maker.infrastructure.local_discovery import discover_local_candidates
from portfolio_maker.infrastructure.local_discovery import DiscoveryRootError
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


def test_discover_local_candidates_skips_filename_pattern_before_candidate(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    excluded = home / "PRIVATE-notes.md"
    excluded.write_text("private", encoding="utf-8")

    candidates, skipped = discover_local_candidates(
        home,
        excluded_file_patterns=("private*",),
    )

    assert candidates == []
    assert (excluded.resolve(), "skipped_policy") in [(item.path, item.reason) for item in skipped]


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


def test_discover_local_candidates_maps_unresolvable_root_to_controlled_error(tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to("loop")

    with pytest.raises(DiscoveryRootError, match="cannot be resolved"):
        discover_local_candidates(loop)


def test_discover_local_candidates_deduplicates_canonical_uri_before_cap(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    original = home / "a-original.md"
    alias = home / "b-alias.md"
    distinct = home / "c-distinct.md"
    original.write_text("original", encoding="utf-8")
    alias.symlink_to(original)
    distinct.write_text("distinct", encoding="utf-8")

    candidates, _ = discover_local_candidates(home, max_candidates=2)

    assert [candidate.uri for candidate in candidates] == [
        original.resolve().as_uri(),
        distinct.resolve().as_uri(),
    ]


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
    assert "may be incomplete" in report


def test_discover_sources_rejects_report_symlink_and_preserves_external_file(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    external = tmp_path / "external-report.md"
    external.write_text("external marker", encoding="utf-8")
    paths.discovery_report_path.symlink_to(external)

    with pytest.raises(OSError, match="regular file"):
        discover_sources(
            DiscoverSourcesRequest(workspace=workspace, home=home, include_github=False)
        )

    assert external.read_text(encoding="utf-8") == "external marker"


def test_discovery_normalizes_control_characters_and_markdown_in_local_label(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    source = home / "safe\n## Forged.md"
    source.write_text("evidence", encoding="utf-8")

    result = discover_sources(
        DiscoverSourcesRequest(workspace=workspace, home=home, include_github=False)
    )

    report = result.report_path.read_text(encoding="utf-8")
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    assert "\n## Forged" not in report
    assert "\\#\\# Forged" in report
    assert "\n" not in repository.list_sources()[0].display_name


def test_discover_sources_redacts_policy_skipped_paths_in_report(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    (home / ".env").write_text("API_KEY=secret\n", encoding="utf-8")

    result = discover_sources(DiscoverSourcesRequest(workspace=workspace, home=home, include_github=False))

    report = result.report_path.read_text(encoding="utf-8")
    assert "skipped_policy" in report
    assert ".env" not in report


def test_discover_sources_skips_timestamped_password_export(tmp_path):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    (home / "bitwarden_export_20260710.json").write_text("{}", encoding="utf-8")

    result = discover_sources(
        DiscoverSourcesRequest(workspace=workspace, home=home, include_github=False)
    )

    report = result.report_path.read_text(encoding="utf-8")
    assert result.discovered_count == 0
    assert "bitwarden_export_20260710.json" not in report


def test_discover_sources_includes_github_candidates(workspace, tmp_path, monkeypatch):
    def fake_discover_github_candidates(**kwargs):
        assert kwargs == {
            "excluded_repositories": (),
            "allowed_repositories": (),
            "private_sources_allowed": False,
        }
        return (
            [
                GitHubRepositoryCandidate(
                    name_with_owner="octo/demo",
                    url="https://github.com/octo/demo",
                    is_private=False,
                )
            ],
            [
                GitHubActivityCandidate(
                    repo="octo/demo",
                    activity_type="pull_request",
                    url="https://github.com/octo/demo/pull/1",
                    title="Add RAG\n## Forged",
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
    with repository._connection() as conn:
        activity = conn.execute(
            "SELECT source_id FROM github_activities WHERE repo = ?",
            ("octo/demo",),
        ).fetchone()

    assert result.discovered_count == 2
    assert activity["source_id"] == repo_source.id
    assert "octo/demo" in report
    assert "\n## Forged" not in report
    assert "Add RAG \\#\\# Forged" in report


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


def test_successful_github_rediscovery_invalidates_disappeared_activities(workspace, tmp_path, monkeypatch):
    activity = GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="pull_request",
        url="https://github.com/octo/demo/pull/1",
        title="Observed once",
        state="MERGED",
        author="octo",
        created_at="2026-01-01T00:00:00Z",
        merged_at="2026-01-02T00:00:00Z",
    )
    repo = GitHubRepositoryCandidate("octo/demo", "https://github.com/octo/demo", False)
    responses = [([repo], [activity], []), ([repo], [], [])]

    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        lambda **kwargs: responses.pop(0),
    )

    request = DiscoverSourcesRequest(workspace=workspace, home=tmp_path, include_github=True)
    discover_sources(request)
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    assert repository.list_github_activities()[0].is_private is False
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity.url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 1

    discover_sources(request)

    assert repository.list_github_activities()[0].is_private is True
    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 0


def test_failed_github_rediscovery_preserves_existing_activity_visibility(workspace, tmp_path, monkeypatch):
    activity = GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="pull_request",
        url="https://github.com/octo/demo/pull/1",
        title="Observed once",
        state="MERGED",
        author="octo",
        created_at="2026-01-01T00:00:00Z",
        merged_at="2026-01-02T00:00:00Z",
    )
    repo = GitHubRepositoryCandidate("octo/demo", "https://github.com/octo/demo", False)
    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        lambda **kwargs: ([repo], [activity], []),
    )
    request = DiscoverSourcesRequest(workspace=workspace, home=tmp_path, include_github=True)
    discover_sources(request)
    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        lambda **kwargs: (_ for _ in ()).throw(FileNotFoundError("gh")),
    )

    discover_sources(request)

    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    assert repository.list_github_activities()[0].is_private is False


def test_incomplete_github_rediscovery_preserves_existing_activity_visibility(workspace, tmp_path, monkeypatch):
    activity = GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="pull_request",
        url="https://github.com/octo/demo/pull/1",
        title="Observed once",
        state="MERGED",
        author="octo",
        created_at="2026-01-01T00:00:00Z",
        merged_at="2026-01-02T00:00:00Z",
    )
    repo = GitHubRepositoryCandidate("octo/demo", "https://github.com/octo/demo", False)
    responses = [([repo], [activity], []), ([repo], [], ["GitHub workflow runs discovery failed"])]
    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        lambda **kwargs: responses.pop(0),
    )
    request = DiscoverSourcesRequest(workspace=workspace, home=tmp_path, include_github=True)
    discover_sources(request)

    discover_sources(request)

    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    assert repository.list_github_activities()[0].is_private is False


def test_partial_rediscovery_hides_activity_from_repository_that_is_no_longer_public(
    workspace,
    tmp_path,
    monkeypatch,
):
    activity = GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="pull_request",
        url="https://github.com/octo/demo/pull/1",
        title="Previously public",
        state="MERGED",
        author="octo",
        created_at="2026-01-01T00:00:00Z",
        merged_at="2026-01-02T00:00:00Z",
    )
    public_repo = GitHubRepositoryCandidate("octo/demo", "https://github.com/octo/demo", False)
    private_repo = GitHubRepositoryCandidate("octo/demo", "https://github.com/octo/demo", True)
    responses = [
        GitHubDiscoveryResult([public_repo], [activity], [], ()),
        GitHubDiscoveryResult(
            [private_repo],
            [],
            ["GitHub workflow runs discovery failed for octo/other"],
            (),
        ),
    ]
    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        lambda **kwargs: responses.pop(0),
    )
    request = DiscoverSourcesRequest(workspace=workspace, home=tmp_path, include_github=True)
    discover_sources(request)
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity.url]
    approval["private_sources_allowed"] = True
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 1
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    assert activity.url in paths.portfolio_draft_path.read_text(encoding="utf-8")

    discover_sources(request)

    repository = SQLiteRepository(paths.db_path)
    assert repository.list_github_activities()[0].is_private is True
    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 0
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    assert activity.url not in paths.portfolio_draft_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("failure_repository", ("octo/other", "octo/demo"))
def test_partial_rediscovery_revokes_successfully_empty_pr_endpoint(
    workspace,
    tmp_path,
    monkeypatch,
    failure_repository,
):
    activity = GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="pull_request",
        url="https://github.com/octo/demo/pull/1",
        title="Previously public",
        state="MERGED",
        author="octo",
        created_at="2026-01-01T00:00:00Z",
        merged_at="2026-01-02T00:00:00Z",
    )
    demo = GitHubRepositoryCandidate("octo/demo", "https://github.com/octo/demo", False)
    other = GitHubRepositoryCandidate("octo/other", "https://github.com/octo/other", False)
    responses = [
        GitHubDiscoveryResult([demo], [activity], [], ()),
        GitHubDiscoveryResult(
            [demo, other],
            [],
            [f"GitHub workflow runs discovery failed for {failure_repository}"],
            (
                GitHubEndpointOutcome("octo/demo", "pull_request", True),
                GitHubEndpointOutcome(failure_repository, "workflow_run", False),
            ),
        ),
    ]
    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        lambda **kwargs: responses.pop(0),
    )
    request = DiscoverSourcesRequest(workspace=workspace, home=tmp_path, include_github=True)
    discover_sources(request)
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [activity.url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 1

    discover_sources(request)

    assert build_profile(BuildProfileRequest(workspace=workspace)).claim_count == 0
    draft_portfolio(DraftPortfolioRequest(workspace=workspace))
    assert activity.url not in paths.portfolio_draft_path.read_text(encoding="utf-8")


def test_discovery_review_comment_can_be_approved_and_rendered_as_evidence(workspace, tmp_path, monkeypatch):
    url = "https://github.com/octo/demo/pull/1#discussion_r1"
    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        lambda **kwargs: (
            [GitHubRepositoryCandidate("octo/demo", "https://github.com/octo/demo", False)],
            [
                GitHubActivityCandidate(
                    repo="octo/demo",
                    activity_type="review_comment",
                    url=url,
                    title="Review comment: Tighten checks",
                    state="commented",
                    author="octo",
                    created_at="2026-01-01T00:00:00Z",
                    merged_at=None,
                )
            ],
            [],
        ),
    )

    discover_sources(DiscoverSourcesRequest(workspace=workspace, home=tmp_path, include_github=True))
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_github_activity_urls"] = [url]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")

    draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    draft = paths.portfolio_draft_path.read_text(encoding="utf-8")
    assert "review\\_comment" in draft
    assert "Tighten checks" in draft
    assert url in draft


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
            "allowed_repositories": (),
            "private_sources_allowed": False,
        }
        return (
            [
                GitHubRepositoryCandidate(
                    name_with_owner="octo/public",
                    url="https://github.com/octo/public",
                    is_private=False,
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


def test_discover_sources_excludes_its_workspace_store_on_rerun(tmp_path):
    workspace = tmp_path / "workspace"
    readme = workspace / "README.md"
    workspace.mkdir()
    readme.write_text("# Portfolio\n", encoding="utf-8")

    discover_sources(
        DiscoverSourcesRequest(workspace=workspace, home=workspace, include_github=False)
    )
    result = discover_sources(
        DiscoverSourcesRequest(workspace=workspace, home=workspace, include_github=False)
    )

    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    report = result.report_path.read_text(encoding="utf-8")
    assert all(".portfolio-maker" not in source.uri for source in repository.list_sources())
    assert ".portfolio-maker" not in report
