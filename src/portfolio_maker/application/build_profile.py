from __future__ import annotations

from portfolio_maker.application.models import BuildProfileRequest, BuildProfileResult
from portfolio_maker.domain.models import SourceStatus
from portfolio_maker.infrastructure.artifacts import write_json, write_markdown
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def build_profile(request: BuildProfileRequest) -> BuildProfileResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    sources = repository.list_sources(status=SourceStatus.INGESTED)
    claims = [
        {
            "text": f"Worked on {source.display_name}.",
            "confidence": "low",
            "public_safe": False,
            "evidence_uri": source.uri,
        }
        for source in sources
    ]
    payload = {
        "version": 1,
        "sources": [
            {
                "id": source.id,
                "type": source.type.value,
                "display_name": source.display_name,
                "owner": source.owner,
            }
            for source in sources
        ],
        "claims": claims,
    }

    write_json(paths.master_profile_json_path, payload)
    write_markdown(
        paths.master_profile_md_path,
        "# Master Profile\n\n"
        + "\n".join(f"- {claim['text']} ({claim['confidence']})" for claim in claims)
        + ("\n" if claims else ""),
    )
    return BuildProfileResult(
        json_path=paths.master_profile_json_path,
        markdown_path=paths.master_profile_md_path,
        claim_count=len(claims),
    )
