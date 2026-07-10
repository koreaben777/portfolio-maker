from __future__ import annotations

from portfolio_maker.application.approval import approval_forbidden_paths, load_approval
from portfolio_maker.application.models import IngestSourcesRequest, IngestSourcesResult
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.policy import (
    FilePolicy,
    SourcePathPolicyError,
    approved_regular_file_path,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import write_local_snapshot
from portfolio_maker.workspace import WorkspacePaths


def ingest_sources(request: IngestSourcesRequest) -> IngestSourcesResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    approved_uris = set(approval.approved_source_uris)
    policy = FilePolicy(forbidden_paths=approval_forbidden_paths(paths, approval))
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    latest_hashes = repository.latest_snapshot_hashes_by_source_id()
    ingested_count = 0
    skipped_count = 0
    snapshot_paths: list[Path] = []

    for source in repository.list_sources():
        if source.uri not in approved_uris or source.type != SourceType.LOCAL_FILE:
            skipped_count += 1
            continue

        if source.id is None:
            skipped_count += 1
            continue

        try:
            source_path = approved_regular_file_path(source.uri, policy)
        except FileNotFoundError:
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            skipped_count += 1
            continue
        except SourcePathPolicyError:
            repository.update_source_status(source.id, SourceStatus.SKIPPED_POLICY)
            skipped_count += 1
            continue
        except OSError:
            repository.update_source_status(source.id, SourceStatus.EXTRACT_FAILED)
            skipped_count += 1
            continue

        try:
            extracted = extract_text(source_path)
        except FileNotFoundError:
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            skipped_count += 1
            continue
        except OSError:
            repository.update_source_status(source.id, SourceStatus.EXTRACT_FAILED)
            skipped_count += 1
            continue
        if (
            source.status == SourceStatus.INGESTED
            and latest_hashes.get(source.id) == extracted.content_hash
        ):
            skipped_count += 1
            continue
        snapshot_path = write_local_snapshot(
            paths,
            source.id,
            source_path,
            extracted,
            source_uri=source.uri,
        )
        repository.insert_source_snapshot(
            source.id,
            snapshot_path,
            extracted.content_hash,
            extracted.extractor,
        )
        repository.update_source_status(source.id, SourceStatus.INGESTED)
        snapshot_paths.append(snapshot_path)
        ingested_count += 1

    return IngestSourcesResult(
        ingested_count=ingested_count,
        skipped_count=skipped_count,
        snapshot_paths=tuple(snapshot_paths),
    )
