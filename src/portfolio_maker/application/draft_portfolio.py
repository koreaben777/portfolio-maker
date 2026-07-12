from __future__ import annotations

import json
from pathlib import Path

from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.models import (
    BuildProfileRequest,
    BuildProfileResult,
    DraftPortfolioRequest,
    DraftPortfolioResult,
)
from portfolio_maker.infrastructure.artifacts import write_markdown
from portfolio_maker.infrastructure.policy import mask_public_value
from portfolio_maker.infrastructure.presentation import markdown_text, normalize_label
from portfolio_maker.infrastructure.github_connector import is_public_github_activity_url
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


class ProfileFormatError(ValueError):
    pass


def draft_portfolio(request: DraftPortfolioRequest) -> DraftPortfolioResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    profile_result = build_profile(BuildProfileRequest(workspace=request.workspace))
    profile = _load_profile(paths.master_profile_json_path)
    sources = [
        source for source in profile["sources"] if source.get("type", "local_file") == "local_file"
    ]
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

    evidence_lines = _github_evidence_lines(profile.get("claims", []))
    content = "# Portfolio Draft\n\n" + "\n".join(sections)
    if evidence_lines:
        content += "## GitHub Activity Evidence\n\n" + "\n".join(evidence_lines) + "\n"
    write_markdown(paths.portfolio_draft_path, content)
    claim_ids = profile_result.claim_ids if isinstance(profile_result, BuildProfileResult) else ()
    evidence_ids = profile_result.evidence_ids if isinstance(profile_result, BuildProfileResult) else ()
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.record_artifact(
        "portfolio_draft",
        1,
        json.dumps(
            {"claim_ids": claim_ids, "evidence_ids": evidence_ids},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
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
    claims = payload.get("claims", [])
    if not isinstance(claims, list) or any(not isinstance(claim, dict) for claim in claims):
        raise ProfileFormatError("master profile claims must be a list")
    return payload


def _github_evidence_lines(claims: list[object]) -> list[str]:
    lines: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict) or claim.get("claim_type") != "approved_github_activity":
            continue
        if claim.get("public_safe") is not True:
            continue
        title = claim.get("title")
        activity_type = claim.get("activity_type")
        url = claim.get("evidence_uri")
        if not all(isinstance(value, str) for value in (title, activity_type, url)):
            continue
        if not is_public_github_activity_url(url):
            continue
        lines.extend(
            [
                f"- `{markdown_text(normalize_label(activity_type))}`: {markdown_text(mask_public_value(title))}",
                f"  Evidence: {markdown_text(url)}",
            ]
        )
    return lines
