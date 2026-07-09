from __future__ import annotations

import json

from portfolio_maker.application.models import DraftPortfolioRequest, DraftPortfolioResult
from portfolio_maker.infrastructure.artifacts import write_markdown
from portfolio_maker.workspace import WorkspacePaths


def draft_portfolio(request: DraftPortfolioRequest) -> DraftPortfolioResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    profile = json.loads(paths.master_profile_json_path.read_text(encoding="utf-8"))
    sources = profile["sources"]
    sections = [
        f"## {source['display_name']}\n\nEvidence: {source['display_name']}\n"
        for source in sources
    ]

    write_markdown(paths.portfolio_draft_path, "# Portfolio Draft\n\n" + "\n".join(sections))
    return DraftPortfolioResult(
        markdown_path=paths.portfolio_draft_path,
        project_count=len(sources),
    )
