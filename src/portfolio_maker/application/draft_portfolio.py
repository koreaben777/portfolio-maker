from __future__ import annotations

import json
from pathlib import Path

from portfolio_maker.application.approval import ApprovalMissingError, load_approval
from portfolio_maker.application.artifact_approval import load_artifact_policy
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.evidence_selection import (
    EvidenceSelectionRequest,
    EvidenceSelectionService,
)
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DraftPortfolioRequest,
    DraftPortfolioResult,
)
from portfolio_maker.infrastructure.artifacts import write_markdown
from portfolio_maker.infrastructure.policy import mask_public_value
from portfolio_maker.infrastructure.presentation import (
    markdown_text,
    normalize_label,
    safe_local_public_label,
)
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
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    artifact_policy = None
    selection = None
    try:
        artifact_policy = load_artifact_policy(paths)
        selection = EvidenceSelectionService().select(
            repository,
            EvidenceSelectionRequest(
                artifact_kind="portfolio_draft",
                policy=artifact_policy,
                current_approval=load_approval(paths),
            ),
        )
    except ApprovalMissingError:
        pass
    selected_source_ids = (
        None if selection is None else set(selection.included_source_ids)
    )
    selected_claim_ids = None if selection is None else set(selection.included_claim_ids)
    sources = [
        source
        for source in profile["sources"]
        if source.get("type", "local_file") == "local_file"
        and (selected_source_ids is None or source.get("id") in selected_source_ids)
    ]
    sections = []
    for source in sources:
        masked_name = mask_public_value(str(source["display_name"]))
        safe_name = safe_local_public_label(masked_name)
        display_name = (
            safe_name if safe_name == "[REDACTED]" else markdown_text(safe_name)
        )
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

    evidence_lines = _github_evidence_lines(
        profile.get("claims", []), selected_claim_ids
    )
    content = "# Portfolio Draft\n\n" + "\n".join(sections)
    if evidence_lines:
        content += "## GitHub Activity Evidence\n\n" + "\n".join(evidence_lines) + "\n"
    write_markdown(paths.portfolio_draft_path, content)
    claim_ids = profile_result.claim_ids
    evidence_ids = profile_result.evidence_ids
    repository.record_artifact(
        "portfolio_draft",
        1,
        json.dumps(
            (
                {"claim_ids": claim_ids, "evidence_ids": evidence_ids}
                if selection is None or artifact_policy.legacy_compatibility
                else selection.input_manifest("portfolio_draft")
            ),
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


def _github_evidence_lines(
    claims: list[object], selected_claim_ids: set[int] | None
) -> list[str]:
    lines: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict) or claim.get("claim_type") != "approved_github_activity":
            continue
        if (
            selected_claim_ids is not None
            and claim.get("claim_id") is not None
            and claim.get("claim_id") not in selected_claim_ids
        ):
            continue
        if claim.get("public_safe") is not True:
            continue
        title = claim.get("title")
        activity_type = claim.get("activity_type")
        url = claim.get("evidence_uri")
        if not all(isinstance(value, str) for value in (title, activity_type, url)):
            continue
        if claim.get("origin_type") == "private_github":
            lines.extend(
                [
                    f"- `private GitHub activity`: {markdown_text(mask_public_value(title))}",
                    "  Evidence: approved private activity (URL withheld)",
                ]
            )
            continue
        if not is_public_github_activity_url(url):
            continue
        lines.extend(
            [
                f"- `{markdown_text(normalize_label(activity_type))}`: {markdown_text(mask_public_value(title))}",
                f"  Evidence: {url}",
            ]
        )
    return lines
