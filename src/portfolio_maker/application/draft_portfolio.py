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
    sections = []
    for source in sources:
        display_name = source["display_name"]
        sections.append(
            "\n".join(
                [
                    f"## {display_name}",
                    "",
                    "This project is included because approved evidence was found.",
                    "",
                    "- Role: Evidence review required",
                    "- Technical approach: Evidence review required",
                    "- Outcome: Evidence review required",
                    "",
                    f"Internal evidence reference: `{display_name}`",
                    "",
                ]
            )
        )

    write_markdown(paths.portfolio_draft_path, "# Portfolio Draft\n\n" + "\n".join(sections))
    return DraftPortfolioResult(
        markdown_path=paths.portfolio_draft_path,
        project_count=len(sources),
    )
