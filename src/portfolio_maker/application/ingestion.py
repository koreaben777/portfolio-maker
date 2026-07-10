from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.models import IngestSourcesRequest, IngestSourcesResult
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.policy import FilePolicy
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import write_local_snapshot
from portfolio_maker.workspace import WorkspacePaths


def _path_from_file_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("Only file URIs are supported")
    return Path(unquote(parsed.path))


def ingest_sources(request: IngestSourcesRequest) -> IngestSourcesResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    approved_uris = set(approval.approved_source_uris)
    policy = FilePolicy(
        forbidden_paths=tuple(Path(path) for path in approval.forbidden_paths)
    )
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

        source_path = _path_from_file_uri(source.uri)
        classification = policy.classify_path(source_path)
        if classification != "candidate":
            if classification == "skipped_policy":
                repository.update_source_status(source.id, SourceStatus.SKIPPED_POLICY)
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
        snapshot_path = write_local_snapshot(paths, source.id, source_path, extracted)
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
