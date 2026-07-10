from __future__ import annotations

from pathlib import Path

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.models import DiscoverSourcesRequest, DiscoverSourcesResult, ProgressEvent
from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.github_connector import (
    GitHubDiscoveryError,
    GitHubActivityCandidate,
    GitHubRepositoryCandidate,
    discover_github_candidates,
)
from portfolio_maker.infrastructure.local_discovery import LocalCandidate, SkippedPath, discover_local_candidates
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def discover_sources(request: DiscoverSourcesRequest) -> DiscoverSourcesResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()

    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    approval = load_approval(paths) if paths.approval_path.exists() else None
    approval_forbidden_paths = (
        tuple(Path(path) for path in approval.forbidden_paths) if approval else ()
    )
    candidates, skipped = discover_local_candidates(
        request.home,
        request.forbidden_paths + approval_forbidden_paths,
    )
    for candidate in candidates:
        repository.upsert_source(
            Source(
                id=None,
                type=SourceType.LOCAL_FILE,
                uri=candidate.uri,
                display_name=candidate.display_name,
                owner=None,
                status=SourceStatus.DISCOVERED,
            )
        )

    github_repos: list[GitHubRepositoryCandidate] = []
    github_activities: list[GitHubActivityCandidate] = []
    github_statuses: list[str] = []
    if request.include_github:
        excluded_repositories = approval.excluded_repositories if approval else ()
        private_sources_allowed = approval.private_sources_allowed if approval else False
        try:
            github_repos, github_activities, github_statuses = discover_github_candidates(
                excluded_repositories=tuple(excluded_repositories),
                private_sources_allowed=private_sources_allowed,
            )
        except (GitHubDiscoveryError, FileNotFoundError) as error:
            github_statuses = [str(error) or "GitHub discovery failed"]

        repo_source_ids: dict[str, int] = {}
        for repo in github_repos:
            repo_source_ids[repo.name_with_owner] = repository.upsert_source(
                Source(
                    id=None,
                    type=SourceType.GITHUB_REPOSITORY,
                    uri=repo.url,
                    display_name=repo.name_with_owner,
                    owner=repo.name_with_owner.split("/", 1)[0],
                    status=SourceStatus.DISCOVERED,
                )
            )
        for activity in github_activities:
            repository.insert_github_activity(
                GitHubActivity(
                    id=None,
                    source_id=repo_source_ids.get(activity.repo),
                    repo=activity.repo,
                    activity_type=activity.activity_type,
                    url=activity.url,
                    title=activity.title,
                    state=activity.state,
                    author=activity.author,
                    created_at=activity.created_at,
                    merged_at=activity.merged_at,
                )
            )

    paths.discovery_report_path.write_text(
        _render_report(candidates, skipped, github_repos, github_activities, github_statuses),
        encoding="utf-8",
    )
    discovered_count = len(candidates) + len(github_repos) + len(github_activities)

    return DiscoverSourcesResult(
        report_path=paths.discovery_report_path,
        discovered_count=discovered_count,
        skipped_count=len(skipped),
        events=(
            ProgressEvent(
                stage="discovery",
                message="local discovery complete",
                count=discovered_count,
            ),
        ),
    )


def _render_report(
    candidates: list[LocalCandidate],
    skipped: list[SkippedPath],
    github_repos: list[GitHubRepositoryCandidate],
    github_activities: list[GitHubActivityCandidate],
    github_statuses: list[str] | None = None,
) -> str:
    lines = ["# Discovery Report", "", "## Local candidates"]
    for candidate in candidates:
        lines.append(f"- {candidate.display_name}: {candidate.uri}")
    lines.extend(["", "## GitHub Repositories"])
    for repo in github_repos:
        visibility = "private" if repo.is_private else "public"
        lines.append(f"- `{repo.name_with_owner}` ({visibility}): {repo.url}")
    lines.extend(["", "## GitHub Activities"])
    for activity in github_activities:
        lines.append(f"- `{activity.activity_type}` `{activity.repo}`: {activity.title} {activity.url}")
    if github_statuses:
        lines.extend(["", "## GitHub Status"])
        for status in github_statuses:
            lines.append(f"- GitHub discovery failed: {status}")
    lines.extend(["", "## Skipped"])
    for item in skipped:
        path = "[redacted]" if item.reason in {"forbidden", "skipped_policy"} else item.path
        lines.append(f"- {item.reason}: {path}")
    lines.append("")
    return "\n".join(lines)
