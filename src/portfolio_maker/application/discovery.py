from __future__ import annotations

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

    candidates, skipped = discover_local_candidates(request.home, request.forbidden_paths)
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
    github_error: str | None = None
    if request.include_github:
        try:
            github_repos, github_activities = discover_github_candidates()
        except (GitHubDiscoveryError, FileNotFoundError) as error:
            github_error = str(error) or "GitHub discovery failed"

        excluded_repositories: set[str] = set()
        private_sources_allowed = False
        if paths.approval_path.exists():
            approval = load_approval(paths)
            excluded_repositories = set(approval.excluded_repositories)
            private_sources_allowed = approval.private_sources_allowed

        github_repos = [
            repo
            for repo in github_repos
            if repo.name_with_owner not in excluded_repositories
            and (private_sources_allowed or not repo.is_private)
        ]
        allowed_repos = {repo.name_with_owner for repo in github_repos}
        github_activities = [
            activity for activity in github_activities if activity.repo in allowed_repos
        ]
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
        _render_report(candidates, skipped, github_repos, github_activities, github_error),
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
    github_error: str | None = None,
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
    if github_error:
        lines.extend(["", "## GitHub Status", f"- GitHub discovery failed: {github_error}"])
    lines.extend(["", "## Skipped"])
    for item in skipped:
        path = "[redacted]" if item.reason in {"forbidden", "skipped_policy"} else item.path
        lines.append(f"- {item.reason}: {path}")
    lines.append("")
    return "\n".join(lines)
