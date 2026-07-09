from __future__ import annotations

from portfolio_maker.application.models import DiscoverSourcesRequest, DiscoverSourcesResult, ProgressEvent
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
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

    paths.discovery_report_path.write_text(_render_report(candidates, skipped), encoding="utf-8")

    return DiscoverSourcesResult(
        report_path=paths.discovery_report_path,
        discovered_count=len(candidates),
        skipped_count=len(skipped),
        events=(
            ProgressEvent(
                stage="discover",
                message="Local discovery complete",
                count=len(candidates),
            ),
        ),
    )


def _render_report(candidates: list[LocalCandidate], skipped: list[SkippedPath]) -> str:
    lines = ["# Discovery Report", "", "## Local candidates"]
    for candidate in candidates:
        lines.append(f"- {candidate.display_name}: {candidate.uri}")
    lines.extend(["", "## Skipped"])
    for item in skipped:
        lines.append(f"- {item.reason}: {item.path}")
    lines.append("")
    return "\n".join(lines)
