from __future__ import annotations

import json
from pathlib import Path

from portfolio_maker.application.approval import approval_forbidden_paths, load_approval
from portfolio_maker.application.models import BuildProfileRequest, BuildProfileResult
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.artifacts import write_json, write_markdown
from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.policy import (
    FilePolicy,
    SourcePathPolicyError,
    approved_regular_file_path,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def build_profile(request: BuildProfileRequest) -> BuildProfileResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    approved_uris = set(approval.approved_source_uris)
    policy = FilePolicy(forbidden_paths=approval_forbidden_paths(paths, approval))
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    snapshots = repository.latest_snapshots_by_source_id()
    snapshot_hashes = repository.latest_snapshot_hashes_by_source_id()
    sources: list[Source] = []
    claims: list[dict[str, object]] = []

    for source in repository.list_sources(status=SourceStatus.INGESTED):
        if source.type != SourceType.LOCAL_FILE or source.uri not in approved_uris or source.id is None:
            continue
        try:
            source_path = approved_regular_file_path(source.uri, policy)
            extracted = extract_text(source_path)
        except FileNotFoundError:
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            continue
        except SourcePathPolicyError:
            repository.update_source_status(source.id, SourceStatus.SKIPPED_POLICY)
            continue
        except OSError:
            repository.update_source_status(source.id, SourceStatus.EXTRACT_FAILED)
            continue

        snapshot_path = snapshots.get(source.id)
        evidence = _snapshot_evidence(
            source,
            snapshot_path,
            extracted.content_hash,
            snapshot_hashes.get(source.id),
        )
        if evidence is None:
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            continue

        sources.append(source)
        claims.append(
            {
                "claim_type": "project_evidence",
                "text": f"{source.display_name}: {evidence}",
                "confidence": "medium",
                "public_safe": False,
                "evidence_uri": source.uri,
                "evidence_snapshot": str(snapshot_path),
            }
        )

    payload = {
        "version": 1,
        "sources": [
            {
                "id": source.id,
                "type": source.type.value,
                "uri": source.uri,
                "display_name": source.display_name,
                "owner": source.owner,
                "status": source.status.value,
            }
            for source in sources
        ],
        "claims": claims,
    }

    write_json(paths.master_profile_json_path, payload)
    source_lines = [f"- {source.display_name} ({source.type.value})" for source in sources]
    claim_lines = [
        f"- {claim['text']} ({claim['confidence']})\n  Evidence: {claim['evidence_uri']}"
        for claim in claims
    ]
    write_markdown(
        paths.master_profile_md_path,
        "\n\n".join(
            [
                "# Master Profile",
                "## Sources\n" + "\n".join(source_lines),
                "## Claims\n" + "\n".join(claim_lines),
            ]
        )
        + "\n",
    )
    return BuildProfileResult(
        json_path=paths.master_profile_json_path,
        markdown_path=paths.master_profile_md_path,
        claim_count=len(claims),
    )


def _snapshot_evidence(
    source: Source,
    snapshot_path: Path | None,
    content_hash: str,
    snapshot_hash: str | None,
) -> str | None:
    if snapshot_path is None or not snapshot_path.exists() or snapshot_hash != content_hash:
        return None
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("source_id") != source.id
        or payload.get("source_uri") != source.uri
        or payload.get("content_hash") != content_hash
        or not isinstance(payload.get("text"), str)
    ):
        return None
    lines = [
        line.strip().lstrip("#").strip()
        for line in payload["text"].splitlines()
        if line.strip()
    ]
    for line in lines:
        if line != source.display_name:
            return line
    return lines[0] if lines else "Approved evidence captured."
