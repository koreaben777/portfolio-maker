from __future__ import annotations

import json
from pathlib import Path

from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DraftPortfolioRequest,
    DraftPortfolioResult,
)
from portfolio_maker.infrastructure.artifacts import write_markdown
from portfolio_maker.infrastructure.policy import mask_public_value
from portfolio_maker.infrastructure.presentation import markdown_text
from portfolio_maker.workspace import WorkspacePaths


class ProfileFormatError(ValueError):
    pass


def draft_portfolio(request: DraftPortfolioRequest) -> DraftPortfolioResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    build_profile(BuildProfileRequest(workspace=request.workspace))
    profile = _load_profile(paths.master_profile_json_path)
    sources = profile["sources"]
    sections = []
    for source in sources:
        masked_name = mask_public_value(str(source["display_name"]))
        display_name = masked_name if masked_name == "[REDACTED]" else markdown_text(masked_name)
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


def _load_profile(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProfileFormatError("master profile must be an object")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ProfileFormatError("master profile sources must be a list")
    if any(
        not isinstance(source, dict)
        or not isinstance(source.get("display_name"), str)
        for source in sources
    ):
        raise ProfileFormatError("master profile sources must contain display names")
    return payload
