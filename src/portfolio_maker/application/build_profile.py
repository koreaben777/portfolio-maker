from __future__ import annotations

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.models import BuildProfileRequest, BuildProfileResult
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.artifacts import write_json, write_markdown
from portfolio_maker.infrastructure.extractors import extract_approved_text
from portfolio_maker.infrastructure.policy import FilePolicy, SourcePathPolicyError
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import load_valid_local_snapshot
from portfolio_maker.workspace import WorkspacePaths


def build_profile(request: BuildProfileRequest) -> BuildProfileResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    approved_uris = set(approval.approved_source_uris)
    policy = FilePolicy(forbidden_paths=approval.forbidden_paths)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    snapshots = repository.latest_snapshot_metadata_by_source_id()
    sources: list[Source] = []
    claims: list[dict[str, object]] = []

    for source in repository.list_sources(status=SourceStatus.INGESTED):
        if source.type != SourceType.LOCAL_FILE or source.uri not in approved_uris or source.id is None:
            continue
        try:
            source_path, extracted = extract_approved_text(source.uri, policy)
        except FileNotFoundError:
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            continue
        except SourcePathPolicyError:
            repository.update_source_status(source.id, SourceStatus.SKIPPED_POLICY)
            continue
        except OSError:
            repository.update_source_status(source.id, SourceStatus.EXTRACT_FAILED)
            continue

        snapshot_metadata = snapshots.get(source.id)
        if (
            snapshot_metadata is None
            or snapshot_metadata[2] != extracted.content_hash
            or snapshot_metadata[3] != extracted.extractor
        ):
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            continue
        snapshot_path = snapshot_metadata[1]
        snapshot = load_valid_local_snapshot(
            snapshot_path,
            source.id,
            source.uri,
            source_path.name,
            extracted,
        )
        if snapshot is None:
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            continue
        evidence = _snapshot_evidence(source.display_name, str(snapshot["text"]))
        if evidence is None:
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


def _snapshot_evidence(display_name: str, text: str) -> str | None:
    lines = [
        line.strip().lstrip("#").strip()
        for line in text.splitlines()
        if line.strip()
    ]
    for line in lines:
        if line != display_name:
            return line
    return lines[0] if lines else None
