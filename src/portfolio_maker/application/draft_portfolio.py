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
from portfolio_maker.application.project_composition import (
    build_project_projections,
    project_provenance_manifest,
)
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DraftPortfolioRequest,
    DraftPortfolioResult,
)
from portfolio_maker.domain.models import PublicEvidenceRecord
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
    selection_backed = (
        selection is not None
        and artifact_policy is not None
        and not artifact_policy.legacy_compatibility
    )
    if selection_backed:
        projects = build_project_projections(
            repository.list_portfolio_projects(),
            selection.records,
            set(selection.included_evidence_ids),
        )
        content = _project_draft_content(projects)
        write_markdown(paths.portfolio_draft_path, content)
        manifest = _project_draft_manifest(selection, projects)
        repository.record_artifact(
            "portfolio_draft",
            1,
            json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        )
        return DraftPortfolioResult(
            markdown_path=paths.portfolio_draft_path,
            project_count=len(projects),
        )

    selected_claim_ids = None if selection is None else set(selection.included_claim_ids)
    if selection_backed:
        sources = _local_source_payloads(selection.records)
        evidence_lines = _github_evidence_lines_from_records(selection.records)
    else:
        selected_source_ids = (
            None if selection is None else set(selection.included_source_ids)
        )
        sources = [
            source
            for source in profile["sources"]
            if source.get("type", "local_file") == "local_file"
            and (selected_source_ids is None or source.get("id") in selected_source_ids)
        ]
        evidence_lines = _github_evidence_lines(
            profile.get("claims", []), selected_claim_ids
        )
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


def _project_draft_content(projects: list[dict[str, object]]) -> str:
    sections: list[str] = []
    for project in projects:
        lines = [
            f"## {markdown_text(str(project['title']))}",
            "",
            markdown_text(str(project["overview"])),
            "",
            "Evidence:",
        ]
        claims = project.get("claims", [])
        if isinstance(claims, list):
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                lines.append(f"- {markdown_text(str(claim.get('text', 'Evidence review required')))}")
                evidence = claim.get("evidence", [])
                if isinstance(evidence, list):
                    for item in evidence:
                        if not isinstance(item, dict):
                            continue
                        origin = item.get("origin")
                        if origin == "private_github":
                            lines.append("  - Private GitHub activity (URL withheld)")
                        elif origin == "public_github" and item.get("url"):
                            lines.append(f"  - {item['url']}")
                        else:
                            lines.append(f"  - {markdown_text(str(item.get('label', 'Approved local evidence')))}")
        sections.append("\n".join(lines) + "\n")
    if not sections:
        return "# Portfolio Draft\n\nNo approved portfolio projects are available yet. Evidence remains available for review.\n"
    return "# Portfolio Draft\n\n" + "\n".join(sections)


def _project_draft_manifest(
    selection: EvidenceSelectionResult,
    projects: list[dict[str, object]],
) -> dict[str, object]:
    manifest = selection.input_manifest("portfolio_draft")
    claim_ids = sorted(
        {
            int(claim["id"])
            for project in projects
            for claim in project.get("claims", [])
            if isinstance(claim, dict) and isinstance(claim.get("id"), int)
        }
    )
    evidence_ids = sorted(
        {
            int(evidence_id)
            for project in projects
            for evidence_id in project.get("evidence_ids", [])
            if isinstance(evidence_id, int)
        }
    )
    records = {record.evidence_id: record for record in selection.records}
    source_ids = sorted(
        {
            records[evidence_id].source_id
            for evidence_id in evidence_ids
            if evidence_id in records and records[evidence_id].source_id is not None
        }
    )
    manifest.update(
        {
            "included_source_ids": source_ids,
            "included_evidence_ids": evidence_ids,
            "included_claim_ids": claim_ids,
            "source_ids": source_ids,
            "evidence_ids": evidence_ids,
            "claim_ids": claim_ids,
            "portfolio_project_ids": [
                project["id"] for project in projects if isinstance(project.get("id"), str)
            ],
            "portfolio_project_evidence_ids": evidence_ids,
            **project_provenance_manifest(projects),
        }
    )
    return manifest


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


def _local_source_payloads(
    records: tuple[PublicEvidenceRecord, ...],
) -> list[dict[str, object]]:
    sources: list[dict[str, object]] = []
    seen_source_ids: set[int] = set()
    for record in records:
        if record.activity_id is not None or record.source_id is None:
            continue
        if record.source_id in seen_source_ids:
            continue
        display_name = safe_local_public_label(
            mask_public_value(record.source_display_name or "")
        )
        if not display_name:
            continue
        seen_source_ids.add(record.source_id)
        sources.append({"id": record.source_id, "display_name": display_name})
    return sources


def _github_evidence_lines_from_records(
    records: tuple[PublicEvidenceRecord, ...],
) -> list[str]:
    lines: list[str] = []
    for record in records:
        if record.activity_id is None:
            continue
        title = normalize_label(mask_public_value(record.activity_title or ""))
        activity_type = normalize_label(record.activity_type or "")
        if not title or not activity_type:
            continue
        if record.activity_is_private:
            lines.extend(
                [
                    f"- `private GitHub activity`: {markdown_text(title)}",
                    "  Evidence: approved private activity (URL withheld)",
                ]
            )
            continue
        url = record.activity_url
        if not isinstance(url, str) or not is_public_github_activity_url(url):
            continue
        lines.extend(
            [
                f"- `{markdown_text(activity_type)}`: {markdown_text(title)}",
                f"  Evidence: {url}",
            ]
        )
    return lines


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
