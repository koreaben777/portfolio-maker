from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.models import BuildProfileRequest, BuildProfileResult
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.artifacts import write_json, write_markdown
from portfolio_maker.infrastructure.policy import FilePolicy
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def build_profile(request: BuildProfileRequest) -> BuildProfileResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    approved_uris = set(approval.approved_source_uris)
    policy = FilePolicy(
        forbidden_paths=tuple(Path(path) for path in approval.forbidden_paths)
    )
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    sources = [
        source
        for source in repository.list_sources(status=SourceStatus.INGESTED)
        if source.type == SourceType.LOCAL_FILE
        and source.uri in approved_uris
        and policy.classify_path(_path_from_file_uri(source.uri)) == "candidate"
    ]
    snapshots = repository.latest_snapshots_by_source_id()
    claims = [
        {
            "claim_type": "project_evidence",
            "text": f"{source.display_name}: {_snapshot_evidence(source.display_name, snapshots.get(source.id or -1))}",
            "confidence": "medium",
            "public_safe": False,
            "evidence_uri": source.uri,
            "evidence_snapshot": str(snapshots[source.id]) if source.id in snapshots else None,
        }
        for source in sources
    ]
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


def _snapshot_evidence(display_name: str, snapshot_path: Path | None) -> str:
    if snapshot_path is None or not snapshot_path.exists():
        return "Approved evidence captured."
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    lines = [
        line.strip().lstrip("#").strip()
        for line in str(payload.get("text") or "").splitlines()
        if line.strip()
    ]
    for line in lines:
        if line != display_name:
            return line
    return lines[0] if lines else "Approved evidence captured."


def _path_from_file_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("Only file URIs are supported")
    return Path(unquote(parsed.path))
