from __future__ import annotations

from datetime import datetime, timezone
import json

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.artifact_approval import load_artifact_policy
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.evidence_selection import (
    EvidenceSelectionRequest,
    EvidenceSelectionService,
)
from portfolio_maker.application.models import (
    BuildProfileRequest,
    PublicPortfolioRequest,
    PublicPortfolioResult,
)
from portfolio_maker.domain.models import PublicEvidenceRecord
from portfolio_maker.infrastructure.artifacts import write_json
from portfolio_maker.infrastructure.policy import mask_public_value
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
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    approval = load_approval(paths)
    selection_service = EvidenceSelectionService()
    selection = selection_service.select(
        repository,
        EvidenceSelectionRequest(
            artifact_kind="portfolio_public_manifest",
            policy=load_artifact_policy(paths),
            current_approval=approval,
        ),
    )

    projects: dict[str, dict[str, object]] = {}
    claim_ids: list[int] = []
    evidence_ids: list[int] = []
    for record in selection.records:
        item = _public_record(record)
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
        "delivery_scope": selection.delivery_scope,
        "policy_hash": selection.policy_hash,
        "selection": selection.input_manifest("portfolio_public_manifest"),
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
                **selection.input_manifest("portfolio_public_manifest"),
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


def _public_record(record: PublicEvidenceRecord) -> dict[str, object] | None:
    if record.activity_id is None:
        label = normalize_label(mask_public_value(record.source_display_name or ""))
        if not label:
            return None
        return {
            "project_key": f"local:{record.source_id}",
            "project_name": label,
            "repository": None,
            "claim_id": record.claim_id,
            "claim_text": f"Approved local evidence: {label}",
            "evidence": {
                "id": record.evidence_id,
                "kind": "local_evidence",
                "public_safe": True,
                "source_label": label,
                "provenance": "Approved local evidence",
            },
            "timeline": {
                "evidence_id": record.evidence_id,
                "activity_type": "local_evidence",
                "title": label,
                "created_at": "",
                "provenance": "Approved local evidence",
            },
        }

    title = normalize_label(mask_public_value(record.activity_title or ""))
    author = normalize_label(mask_public_value(record.activity_author or ""))
    state = normalize_label(mask_public_value(record.activity_state or ""))
    if not title or not author or not state:
        return None
    if record.activity_is_private:
        project_key = "github:private"
        project_name = "Private GitHub evidence"
        claim_text = f"Private GitHub activity: {title}"
        safe_url = "Private GitHub activity (URL withheld)"
    else:
        project_key = f"github:{record.activity_repo}"
        project_name = str(record.activity_repo)
        claim_text = f"{record.activity_repo}: {title}"
        safe_url = record.activity_url
    evidence = {
        "id": record.evidence_id,
        "kind": "github_activity",
        "public_safe": True,
        "activity_type": record.activity_type,
        "title": title,
        "author": author,
        "state": state,
        "created_at": record.activity_created_at,
        "url": safe_url,
    }
    return {
        "project_key": project_key,
        "project_name": project_name,
        "repository": None if record.activity_is_private else record.activity_repo,
        "claim_id": record.claim_id,
        "claim_text": claim_text,
        "evidence": evidence,
        "timeline": {
            "evidence_id": record.evidence_id,
            "activity_type": record.activity_type,
            "title": title,
            "created_at": record.activity_created_at,
            "url": safe_url,
        },
    }
