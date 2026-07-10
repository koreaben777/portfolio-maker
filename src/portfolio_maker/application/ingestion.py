from __future__ import annotations

from pathlib import Path

from portfolio_maker.application.approval import approval_forbidden_paths, load_approval
from portfolio_maker.application.models import IngestSourcesRequest, IngestSourcesResult
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_approved_text
from portfolio_maker.infrastructure.policy import FilePolicy, SourcePathPolicyError
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import (
    is_verified_managed_legacy_snapshot,
    load_valid_local_snapshot,
    write_local_snapshot,
)
from portfolio_maker.workspace import WorkspacePaths


def ingest_sources(request: IngestSourcesRequest) -> IngestSourcesResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    approved_uris = set(approval.approved_source_uris)
    policy = FilePolicy(forbidden_paths=approval_forbidden_paths(paths, approval))
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    latest_metadata = repository.latest_snapshot_metadata_by_source_id()
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
            source_path, extracted = extract_approved_text(source.uri, policy)
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

        latest = latest_metadata.get(source.id)
        latest_snapshot = latest[0] if latest else None
        latest_hash = latest[1] if latest else None
        latest_extractor = latest[2] if latest else None
        if (
            latest_snapshot is not None
            and latest_hash == extracted.content_hash
            and load_valid_local_snapshot(
                latest_snapshot,
                source.id,
                source.uri,
                source_path.name,
                extracted,
            ) is not None
        ):
            if latest_extractor != extracted.extractor:
                repository.update_latest_source_snapshot(
                    source.id,
                    latest_snapshot,
                    extracted.content_hash,
                    extracted.extractor,
                )
            repository.update_source_status(source.id, SourceStatus.INGESTED)
            skipped_count += 1
            continue
        legacy_snapshot = (
            latest_snapshot
            if latest_snapshot is not None
            and latest_hash == extracted.content_hash
            and is_verified_managed_legacy_snapshot(
                paths,
                latest_snapshot,
                source.id,
                source.uri,
                extracted.content_hash,
            )
            else None
        )
        snapshot_path = write_local_snapshot(
            paths,
            source.id,
            source_path,
            extracted,
            source_uri=source.uri,
        )
        if legacy_snapshot is not None:
            repository.update_latest_source_snapshot(
                source.id,
                snapshot_path,
                extracted.content_hash,
                extracted.extractor,
            )
            legacy_snapshot.unlink()
        elif latest_snapshot == snapshot_path and latest_hash == extracted.content_hash:
            repository.update_latest_source_snapshot(
                source.id,
                snapshot_path,
                extracted.content_hash,
                extracted.extractor,
            )
        else:
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
