from __future__ import annotations

from datetime import datetime, timezone
import json

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.models import (
    BuildProfileRequest,
    PublicPortfolioRequest,
    PublicPortfolioResult,
)
from portfolio_maker.domain.models import PublicEvidenceRecord
from portfolio_maker.infrastructure.artifacts import write_json
from portfolio_maker.infrastructure.github_connector import (
    canonical_public_github_activity_url,
    canonical_repository_name,
    contains_unicode_control,
    is_valid_github_activity_state,
    is_valid_github_timestamp,
    public_github_activity_identity,
    public_github_repository_name,
)
from portfolio_maker.infrastructure.policy import (
    contains_hidden_secret_shaped_public_value,
    mask_public_value,
)
from portfolio_maker.infrastructure.presentation import normalize_label
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


class PublicPortfolioError(ValueError):
    pass


def build_public_portfolio(request: PublicPortfolioRequest) -> PublicPortfolioResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    build_profile(
        BuildProfileRequest(
            workspace=request.workspace,
            invalidate_portfolio_draft=False,
        )
    )
    approval = load_approval(paths)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    projects: dict[str, dict[str, object]] = {}
    claim_ids: list[int] = []
    evidence_ids: list[int] = []
    for record in repository.list_public_evidence_records():
        item = _public_record(record, approval)
        if item is None:
            continue
        project_key = str(item["project_key"])
        project = projects.setdefault(
            project_key,
            {
                "id": project_key,
                "name": item["project_name"],
                "repository": item.get("repository"),
                "public_safe": True,
                "claims": [],
                "timeline": [],
            },
        )
        claims = project["claims"]
        if not isinstance(claims, list):
            raise PublicPortfolioError("public manifest claim collection is invalid")
        claim = next(
            (candidate for candidate in claims if candidate["id"] == item["claim_id"]),
            None,
        )
        if claim is None:
            claim = {
                "id": item["claim_id"],
                "text": item["claim_text"],
                "public_safe": True,
                "evidence": [],
            }
            claims.append(claim)
            claim_ids.append(int(item["claim_id"]))
        evidence = item["evidence"]
        claim_evidence = claim["evidence"]
        if not isinstance(claim_evidence, list) or not isinstance(evidence, dict):
            raise PublicPortfolioError("public manifest evidence collection is invalid")
        if not any(candidate["id"] == evidence["id"] for candidate in claim_evidence):
            claim_evidence.append(evidence)
            evidence_ids.append(int(evidence["id"]))
            timeline = project["timeline"]
            if not isinstance(timeline, list):
                raise PublicPortfolioError("public manifest timeline is invalid")
            timeline.append(item["timeline"])

    for project in projects.values():
        claims = project["claims"]
        timeline = project["timeline"]
        if isinstance(claims, list):
            claims.sort(key=lambda claim: int(claim["id"]))
        if isinstance(timeline, list):
            timeline.sort(key=lambda entry: str(entry["created_at"]), reverse=True)

    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "profile": {},
        "projects": list(projects.values()),
        "skills": [],
        "links": [],
    }
    write_json(paths.portfolio_public_json_path, payload)
    repository.record_artifact(
        "portfolio_public",
        1,
        json.dumps(
            {
                "claim_ids": sorted(set(claim_ids)),
                "evidence_ids": sorted(set(evidence_ids)),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return PublicPortfolioResult(
        manifest_path=paths.portfolio_public_json_path,
        project_count=len(projects),
        claim_count=len(set(claim_ids)),
        evidence_count=len(set(evidence_ids)),
        claim_ids=tuple(sorted(set(claim_ids))),
        evidence_ids=tuple(sorted(set(evidence_ids))),
    )


def _public_record(
    record: PublicEvidenceRecord,
    approval,
) -> dict[str, object] | None:
    if record.activity_id is not None:
        return _public_github_record(record, approval)
    return None


def _public_github_record(record: PublicEvidenceRecord, approval) -> dict[str, object] | None:
    required = (
        record.activity_id,
        record.source_id,
        record.source_type,
        record.source_uri,
        record.source_status,
        record.activity_repo,
        record.activity_type,
        record.activity_url,
        record.activity_title,
        record.activity_state,
        record.activity_author,
        record.activity_created_at,
        record.activity_is_private,
    )
    if any(value is None for value in required):
        return None
    if record.source_type != "github_repository" or record.activity_is_private:
        return None
    if record.source_status in {"skipped_policy", "extract_failed", "stale_source"}:
        return None
    try:
        repository_name = canonical_repository_name(record.activity_repo)
    except ValueError:
        return None
    activity_url = canonical_public_github_activity_url(record.activity_url)
    if activity_url is None:
        return None
    if (
        activity_url not in approval.approved_github_activity_urls
        or repository_name in set(approval.excluded_repositories)
        or (
            approval.allowed_repositories
            and repository_name not in set(approval.allowed_repositories)
        )
        or public_github_repository_name(record.source_uri) != repository_name
        or public_github_activity_identity(activity_url)
        != (repository_name, record.activity_type)
    ):
        return None
    if (
        contains_unicode_control(record.activity_title)
        or contains_unicode_control(record.activity_author)
        or contains_unicode_control(record.activity_state)
        or contains_hidden_secret_shaped_public_value(record.activity_title)
        or contains_hidden_secret_shaped_public_value(record.activity_author)
        or contains_hidden_secret_shaped_public_value(record.activity_state)
        or not is_valid_github_activity_state(
            record.activity_type,
            record.activity_state,
            record.activity_state_field,
        )
        or not is_valid_github_timestamp(record.activity_created_at)
    ):
        return None
    title = normalize_label(mask_public_value(record.activity_title))
    author = normalize_label(mask_public_value(record.activity_author))
    state = normalize_label(mask_public_value(record.activity_state))
    if not title or not author or not state:
        return None
    claim_text = f"{repository_name}: {title}"
    evidence = {
        "id": record.evidence_id,
        "kind": "github_activity",
        "public_safe": True,
        "activity_type": record.activity_type,
        "title": title,
        "author": author,
        "state": state,
        "created_at": record.activity_created_at,
        "url": activity_url,
    }
    return {
        "project_key": f"github:{repository_name}",
        "project_name": repository_name,
        "repository": repository_name,
        "claim_id": record.claim_id,
        "claim_text": claim_text,
        "evidence": evidence,
        "timeline": {
            "evidence_id": record.evidence_id,
            "activity_type": record.activity_type,
            "title": title,
            "created_at": record.activity_created_at,
            "url": activity_url,
        },
    }
