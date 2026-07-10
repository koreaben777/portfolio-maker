from __future__ import annotations

from pathlib import Path

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.models import IngestSourcesRequest, IngestSourcesResult
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_approved_text
from portfolio_maker.infrastructure.policy import FilePolicy, SourcePathPolicyError
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import (
    has_valid_migrated_snapshot,
    load_valid_local_snapshot,
    migrate_verified_managed_legacy_snapshot,
    write_local_snapshot,
)
from portfolio_maker.workspace import WorkspacePaths


def ingest_sources(request: IngestSourcesRequest) -> IngestSourcesResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    approved_uris = set(approval.approved_source_uris)
    policy = FilePolicy(forbidden_paths=approval.forbidden_paths)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    ingested_count = 0
    skipped_count = 0
    snapshot_paths: list[Path] = []

    for source in repository.list_sources():
        if source.type == SourceType.LOCAL_FILE and source.id is not None:
            _cleanup_legacy_snapshot_state(paths, repository, source)

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

        snapshot_rows = repository.snapshot_metadata_for_source(source.id)
        latest = snapshot_rows[-1] if snapshot_rows else None
        latest_id = latest[0] if latest else None
        latest_snapshot = latest[1] if latest else None
        latest_hash = latest[2] if latest else None
        latest_extractor = latest[3] if latest else None
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
                repository.update_source_snapshot(
                    latest_id,
                    latest_snapshot,
                    extracted.content_hash,
                    extracted.extractor,
                )
            repository.update_source_status(source.id, SourceStatus.INGESTED)
            skipped_count += 1
            continue
        snapshot_path = write_local_snapshot(
            paths,
            source.id,
            source_path,
            extracted,
            source_uri=source.uri,
        )
        if latest_snapshot == snapshot_path and latest_hash == extracted.content_hash:
            repository.update_source_snapshot(
                latest_id,
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


def _cleanup_legacy_snapshot_state(
    paths: WorkspacePaths,
    repository: SQLiteRepository,
    source: Source,
) -> None:
    if source.id is None:
        return
    rows = repository.snapshot_metadata_for_source(source.id)
    legacy_path = paths.local_snapshots_dir / f"source-{source.id}.json"
    legacy_rows = [
        row
        for row in rows
        if row[1] == legacy_path and row[3] == "text-v1"
    ]
    migration = migrate_verified_managed_legacy_snapshot(paths, source.id, source.uri)
    if migration is None:
        if legacy_path.exists() or legacy_path.is_symlink():
            return
        for snapshot_id, _, content_hash, _ in legacy_rows:
            if has_valid_migrated_snapshot(paths, source.id, source.uri, content_hash):
                migrated_path = (
                    paths.local_snapshots_dir / f"source-{source.id}-{content_hash}.json"
                )
                repository.update_source_snapshot(
                    snapshot_id,
                    migrated_path,
                    content_hash,
                    "text-v2",
                )
            else:
                repository.delete_source_snapshot(snapshot_id)
        return

    migrated_path, extracted = migration
    content_hash = extracted.content_hash
    matching_v2_rows = [
        row
        for row in rows
        if row[1] == migrated_path
        and row[2] == content_hash
        and row[3] == extracted.extractor
    ]
    if legacy_rows:
        for snapshot_id, _, _, _ in legacy_rows:
            if matching_v2_rows:
                repository.delete_source_snapshot(snapshot_id)
            else:
                repository.update_source_snapshot(
                    snapshot_id,
                    migrated_path,
                    content_hash,
                    extracted.extractor,
                )
                matching_v2_rows.append((snapshot_id, migrated_path, content_hash, extracted.extractor))
    elif not matching_v2_rows:
        repository.insert_source_snapshot(
            source.id,
            migrated_path,
            content_hash,
            extracted.extractor,
        )
