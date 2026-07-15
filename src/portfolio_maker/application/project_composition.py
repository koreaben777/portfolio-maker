from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from pathlib import Path

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.artifact_approval import load_artifact_policy
from portfolio_maker.application.evidence_selection import (
    EvidenceSelectionRequest,
    EvidenceSelectionService,
)
from portfolio_maker.application.models import (
    BuildProfileRequest,
    ComposeProjectsRequest,
    ComposeProjectsResult,
    PrepareProjectReviewRequest,
    PrepareProjectReviewResult,
)
from portfolio_maker.infrastructure.policy import (
    contains_hidden_secret_shaped_public_value,
    mask_public_value,
)
from portfolio_maker.infrastructure.presentation import normalize_label, safe_local_public_label
from portfolio_maker.infrastructure.github_connector import (
    contains_unicode_control,
    is_public_github_activity_url,
)
from portfolio_maker.domain.models import PublicEvidenceRecord
from portfolio_maker.infrastructure.artifacts import write_json
from portfolio_maker.infrastructure.managed_files import (
    read_managed_bytes,
    remove_managed_file,
    write_managed_text,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


class ProjectCompositionError(ValueError):
    pass


@dataclass(frozen=True)
class ApprovedPortfolioProject:
    id: str
    title: str
    overview: str
    evidence_ids: tuple[int, ...]
    status: str = "approved"


@dataclass(frozen=True)
class ProjectApproval:
    review_input_sha256: str
    projects: tuple[ApprovedPortfolioProject, ...]
    rejected_candidate_ids: tuple[str, ...]
    unassigned_evidence_ids: tuple[int, ...]


_PROJECT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CANDIDATE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def build_review_input_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProjectCompositionError("project review input must be an object")
    if payload.get("version") != 1:
        raise ProjectCompositionError("project review input version must be 1")
    if payload.get("artifact_kind") != "master_profile":
        raise ProjectCompositionError("project review input must use master_profile")
    if payload.get("delivery_scope") not in {"restricted", "open_public"}:
        raise ProjectCompositionError("project review input delivery scope is invalid")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        raise ProjectCompositionError("project review evidence must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise ProjectCompositionError("project review evidence entries must be objects")
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, int) or isinstance(evidence_id, bool) or evidence_id <= 0:
            raise ProjectCompositionError("project review evidence IDs are invalid")
        if evidence_id in seen:
            raise ProjectCompositionError("project review evidence IDs must be unique")
        seen.add(evidence_id)
        origin = item.get("origin")
        if origin not in {"local", "public_github", "private_github"}:
            raise ProjectCompositionError("project review evidence origin is invalid")
        normalized_item = {
            "evidence_id": evidence_id,
            "stable_id": _safe_text(item.get("stable_id"), "stable_id"),
            "origin": origin,
            "source_label": _safe_public_label(item.get("source_label"), origin),
        }
        for key in ("excerpt", "activity_type", "title", "created_at"):
            if key in item and item[key] is not None:
                normalized_item[key] = _safe_text(item[key], key)
        if origin == "private_github":
            normalized_item["source_label"] = "Private GitHub activity"
        normalized.append(normalized_item)
    canonical = {
        "version": 1,
        "artifact_kind": "master_profile",
        "delivery_scope": payload["delivery_scope"],
        "policy_hash": _safe_hash(payload.get("policy_hash"), "policy_hash"),
        "evidence": normalized,
    }
    input_bytes = _canonical_bytes(canonical)
    return {**canonical, "input_sha256": hashlib.sha256(input_bytes).hexdigest()}


def parse_project_approval(payload: Any) -> ProjectApproval:
    if not isinstance(payload, dict):
        raise ProjectCompositionError("project approval must be an object")
    if payload.get("version") != 1:
        raise ProjectCompositionError("project approval version must be 1")
    review_hash = _safe_hash(payload.get("review_input_sha256"), "review_input_sha256")
    raw_projects = payload.get("projects", [])
    if not isinstance(raw_projects, list):
        raise ProjectCompositionError("project approval projects must be a list")
    projects: list[ApprovedPortfolioProject] = []
    seen_project_ids: set[str] = set()
    seen_evidence_ids: set[int] = set()
    for raw in raw_projects:
        if not isinstance(raw, dict):
            raise ProjectCompositionError("project entries must be objects")
        project_id = raw.get("id")
        if not isinstance(project_id, str) or _PROJECT_ID.fullmatch(project_id) is None:
            raise ProjectCompositionError("project IDs must be ASCII kebab-case")
        if project_id in seen_project_ids:
            raise ProjectCompositionError("project IDs must be unique")
        seen_project_ids.add(project_id)
        title = _safe_text(raw.get("title"), "project title")
        overview = _safe_text(raw.get("overview"), "project overview")
        evidence_ids = raw.get("evidence_ids")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in evidence_ids)
            or len(set(evidence_ids)) != len(evidence_ids)
        ):
            raise ProjectCompositionError("approved projects require unique evidence IDs")
        duplicate_ids = seen_evidence_ids.intersection(evidence_ids)
        if duplicate_ids:
            raise ProjectCompositionError("evidence cannot belong to multiple approved projects")
        seen_evidence_ids.update(evidence_ids)
        if raw.get("status") != "approved":
            raise ProjectCompositionError("only approved projects may be materialized")
        projects.append(
            ApprovedPortfolioProject(
                id=project_id,
                title=title,
                overview=overview,
                evidence_ids=tuple(evidence_ids),
            )
        )
    rejected = _string_ids(payload.get("rejected_candidate_ids", []), "rejected_candidate_ids")
    unassigned = _integer_ids(payload.get("unassigned_evidence_ids", []), "unassigned_evidence_ids")
    if seen_evidence_ids.intersection(unassigned):
        raise ProjectCompositionError("approved and unassigned evidence cannot overlap")
    return ProjectApproval(review_hash, tuple(projects), rejected, unassigned)


def validate_project_approval(approval: ProjectApproval, review_input: dict[str, Any]) -> None:
    normalized = build_review_input_payload(review_input)
    if approval.review_input_sha256 != normalized["input_sha256"]:
        raise ProjectCompositionError("project approval does not match current review input")
    available = {
        item["evidence_id"] for item in normalized["evidence"] if isinstance(item, dict)
    }
    linked = {evidence_id for project in approval.projects for evidence_id in project.evidence_ids}
    if not linked.issubset(available):
        raise ProjectCompositionError("project approval references unknown evidence")
    if not set(approval.unassigned_evidence_ids).issubset(available):
        raise ProjectCompositionError("unassigned evidence is not in the review input")


def parse_candidate_payload(payload: Any, review_input: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ProjectCompositionError("project candidates version is invalid")
    if payload.get("review_input_sha256") != review_input.get("input_sha256"):
        raise ProjectCompositionError("project candidates do not match current review input")
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ProjectCompositionError("project candidates must be a list")
    available = {
        item["evidence_id"] for item in review_input["evidence"] if isinstance(item, dict)
    }
    result: list[dict[str, Any]] = []
    seen_candidate_ids: set[str] = set()
    for candidate in raw_candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str):
            raise ProjectCompositionError("candidate entries are invalid")
        if _CANDIDATE_ID.fullmatch(candidate["id"]) is None:
            raise ProjectCompositionError("candidate IDs are invalid")
        if candidate["id"] in seen_candidate_ids:
            raise ProjectCompositionError("candidate IDs must be unique")
        seen_candidate_ids.add(candidate["id"])
        if candidate.get("status") not in {"candidate", "rejected"}:
            raise ProjectCompositionError("candidate status is invalid")
        if candidate.get("confidence") not in {"low", "medium", "high"}:
            raise ProjectCompositionError("candidate confidence is invalid")
        if candidate.get("review_required") is not True:
            raise ProjectCompositionError("candidate review_required must be true")
        evidence_ids = candidate.get("evidence_ids")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or len(set(evidence_ids)) != len(evidence_ids)
            or any(not isinstance(value, int) or isinstance(value, bool) for value in evidence_ids)
            or not set(evidence_ids).issubset(available)
        ):
            raise ProjectCompositionError("candidate evidence IDs are invalid")
        for field in ("title", "overview", "grouping_rationale", "confidence"):
            _safe_text(candidate.get(field), field)
        result.append(candidate)
    return tuple(result)


def prepare_project_review(request: PrepareProjectReviewRequest) -> PrepareProjectReviewResult:
    from portfolio_maker.application.project_boundary import (
        ActiveSemanticRevisionMissing,
        prepare_project_review_v2,
    )

    try:
        return prepare_project_review_v2(request)
    except ActiveSemanticRevisionMissing:
        pass
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    current = _collect_review_input(paths)
    write_json(paths.project_review_input_path, current)
    return PrepareProjectReviewResult(
        input_path=paths.project_review_input_path,
        evidence_count=len(current["evidence"]),
    )


def write_sample_project_approval(paths: WorkspacePaths, *, force: bool = False) -> Path:
    paths.ensure()
    try:
        review_input = json.loads(read_managed_bytes(paths.project_review_input_path).decode("utf-8"))
    except FileNotFoundError as error:
        raise ProjectCompositionError(
            "project review input is missing; run prepare-project-review first"
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectCompositionError("project review input is invalid") from error
    normalized = build_review_input_payload(review_input)
    payload = {
        "version": 1,
        "review_input_sha256": normalized["input_sha256"],
        "projects": [],
        "rejected_candidate_ids": [],
        "unassigned_evidence_ids": [
            item["evidence_id"] for item in normalized["evidence"] if isinstance(item, dict)
        ],
    }
    try:
        return write_managed_text(
            paths.project_approval_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            overwrite=force,
        )
    except FileExistsError as error:
        raise ProjectCompositionError(
            f"Project approval already exists: {paths.project_approval_path}. Use --force to reset it"
        ) from error


def compose_projects(request: ComposeProjectsRequest) -> ComposeProjectsResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    stored_input = _read_json(paths.project_review_input_path, "project review input")
    approval_payload = _read_json(paths.project_approval_path, "project approval")
    stored_input = build_review_input_payload(stored_input)
    approval = parse_project_approval(approval_payload)
    current_input = _collect_review_input(paths)
    validate_project_approval(approval, current_input)
    if approval.review_input_sha256 != stored_input["input_sha256"]:
        raise ProjectCompositionError("project approval does not match saved review input")

    if paths.project_candidates_path.exists():
        candidates = parse_candidate_payload(
            _read_json(paths.project_candidates_path, "project candidates"),
            current_input,
        )
        candidate_ids = {candidate["id"] for candidate in candidates}
        if not set(approval.rejected_candidate_ids).issubset(candidate_ids):
            raise ProjectCompositionError("rejected candidate is unknown")
    elif approval.rejected_candidate_ids:
        raise ProjectCompositionError("rejected candidate is unknown")
    _validate_candidate_markdown(paths)

    approval_hash = _hash_approval(approval)
    available_evidence_ids = {
        item["evidence_id"]
        for item in current_input["evidence"]
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), int)
    }
    linked_evidence_ids = {
        evidence_id
        for project in approval.projects
        for evidence_id in project.evidence_ids
    }
    computed_unassigned = sorted(
        set(approval.unassigned_evidence_ids)
        | (available_evidence_ids - linked_evidence_ids)
    )
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.replace_portfolio_projects(
        tuple(
            {
                "id": project.id,
                "title": project.title,
                "overview": project.overview,
                "evidence_ids": project.evidence_ids,
            }
            for project in approval.projects
        ),
        approval_hash,
        approval.review_input_sha256,
    )
    repository.record_artifact(
        "project_composition",
        1,
        json.dumps(
            {
                "project_ids": [project.id for project in approval.projects],
                "linked_evidence_ids": sorted(
                    evidence_id
                    for project in approval.projects
                    for evidence_id in project.evidence_ids
                ),
                "unassigned_evidence_ids": computed_unassigned,
                "approval_sha256": approval_hash,
                "review_input_sha256": approval.review_input_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    for path in (
        paths.master_profile_json_path,
        paths.master_profile_md_path,
        paths.portfolio_draft_path,
        paths.portfolio_public_json_path,
        paths.portfolio_html_path,
    ):
        remove_managed_file(path, missing_ok=True)
    return ComposeProjectsResult(
        project_count=len(approval.projects),
        unassigned_evidence_count=len(computed_unassigned),
    )


def _collect_review_input(paths: WorkspacePaths) -> dict[str, Any]:
    from portfolio_maker.application.build_profile import build_profile

    build_profile(
        BuildProfileRequest(
            workspace=paths.workspace,
            invalidate_portfolio_draft=False,
            write_artifacts=False,
        )
    )
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    approval = load_approval(paths)
    artifact_policy = load_artifact_policy(paths)
    selection = EvidenceSelectionService().select(
        repository,
        EvidenceSelectionRequest(
            artifact_kind="master_profile",
            policy=artifact_policy,
            current_approval=approval,
        ),
    )
    evidence: list[dict[str, Any]] = []
    for record in selection.records:
        item: dict[str, Any] = {
            "evidence_id": record.evidence_id,
            "stable_id": record.evidence_stable_id,
            "origin": _record_origin(record),
            "source_label": record.source_display_name or "Approved evidence",
        }
        if record.activity_id is None:
            item["excerpt"] = safe_local_public_label(
                mask_public_value(record.claim_text or "Approved local evidence")
            )
        else:
            item["activity_type"] = record.activity_type
            item["title"] = record.activity_title
            item["created_at"] = record.activity_created_at
        evidence.append(item)
    return build_review_input_payload(
        {
            "version": 1,
            "artifact_kind": "master_profile",
            "delivery_scope": selection.delivery_scope,
            "policy_hash": selection.policy_hash,
            "evidence": evidence,
        }
    )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(read_managed_bytes(path).decode("utf-8"))
    except FileNotFoundError as error:
        raise ProjectCompositionError(f"{label} is missing: {path}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectCompositionError(f"{label} is invalid") from error
    if not isinstance(payload, dict):
        raise ProjectCompositionError(f"{label} must be an object")
    return payload


def _validate_candidate_markdown(paths: WorkspacePaths) -> None:
    try:
        raw = read_managed_bytes(paths.project_candidates_markdown_path)
    except FileNotFoundError:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectCompositionError("project candidate markdown is invalid UTF-8") from error
    if (
        "file://" in text.casefold()
        or ".portfolio-maker" in text.casefold()
        or "portfolio.db" in text.casefold()
        or "https://github.com/" in text.casefold()
        or contains_hidden_secret_shaped_public_value(text)
    ):
        raise ProjectCompositionError("project candidate markdown contains unsafe data")


def _hash_approval(approval: ProjectApproval) -> str:
    payload = {
        "version": 1,
        "review_input_sha256": approval.review_input_sha256,
        "projects": [
            {
                "id": project.id,
                "title": project.title,
                "overview": project.overview,
                "evidence_ids": list(project.evidence_ids),
                "status": project.status,
            }
            for project in approval.projects
        ],
        "rejected_candidate_ids": list(approval.rejected_candidate_ids),
        "unassigned_evidence_ids": list(approval.unassigned_evidence_ids),
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _record_origin(record: Any) -> str:
    if record.activity_id is None:
        return "local"
    return "private_github" if record.activity_is_private else "public_github"


def build_project_projections(
    projects: list[dict[str, object]],
    records: tuple[PublicEvidenceRecord, ...],
    selected_evidence_ids: set[int],
) -> list[dict[str, object]]:
    records_by_id = {record.evidence_id: record for record in records}
    projections: list[dict[str, object]] = []
    for project in projects:
        project_id = project.get("id")
        title = project.get("title")
        overview = project.get("overview")
        links = project.get("evidence")
        if not all(isinstance(value, str) for value in (project_id, title, overview)):
            continue
        if not isinstance(links, list):
            continue
        effective_ids = [
            link["evidence_id"]
            for link in links
            if isinstance(link, dict)
            and isinstance(link.get("evidence_id"), int)
            and link["evidence_id"] in selected_evidence_ids
            and link["evidence_id"] in records_by_id
        ]
        if not effective_ids:
            continue
        evidence_payloads: list[dict[str, object]] = []
        claim_payloads: dict[int, dict[str, object]] = {}
        timeline: list[dict[str, object]] = []
        for evidence_id in sorted(set(effective_ids)):
            record_payload = _safe_record_payload(records_by_id[evidence_id])
            if record_payload is None:
                continue
            record = records_by_id[evidence_id]
            evidence_payloads.append(record_payload["evidence"])
            claim_payload = claim_payloads.setdefault(
                record.claim_id,
                {
                    "id": record.claim_id,
                    "text": record_payload["claim_text"],
                    "public_safe": True,
                    "evidence": [],
                },
            )
            claim_evidence = claim_payload["evidence"]
            if isinstance(claim_evidence, list):
                claim_evidence.append(record_payload["evidence"])
            timeline.append(record_payload["timeline"])
        if not evidence_payloads:
            continue
        project_title = normalize_label(mask_public_value(title))
        project_overview = normalize_label(mask_public_value(overview))
        if not project_title or not project_overview:
            continue
        approval_sha256 = project.get("approval_sha256")
        review_input_sha256 = project.get("review_input_sha256")
        if not _is_sha256(approval_sha256) or not _is_sha256(review_input_sha256):
            continue
        claims = sorted(claim_payloads.values(), key=lambda item: int(item["id"]))
        timeline.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        projections.append(
            {
                "id": project_id,
                "name": project_title,
                "title": project_title,
                "overview": project_overview,
                "public_safe": True,
                "evidence_ids": [int(item["id"]) for item in evidence_payloads],
                "claims": claims,
                "timeline": timeline,
                "project_approval_sha256": approval_sha256,
                "project_review_input_sha256": review_input_sha256,
            }
        )
    return projections


def project_provenance_manifest(projects: list[dict[str, object]]) -> dict[str, str]:
    """Return the shared approval provenance for the projects in one artifact."""
    approval_hashes = {
        project["project_approval_sha256"]
        for project in projects
        if isinstance(project.get("project_approval_sha256"), str)
    }
    review_hashes = {
        project["project_review_input_sha256"]
        for project in projects
        if isinstance(project.get("project_review_input_sha256"), str)
    }
    if len(approval_hashes) != 1 or len(review_hashes) != 1:
        return {}
    return {
        "project_approval_sha256": next(iter(approval_hashes)),
        "project_review_input_sha256": next(iter(review_hashes)),
    }


def _safe_record_payload(record: PublicEvidenceRecord) -> dict[str, object] | None:
    if record.activity_id is None:
        label = safe_local_public_label(mask_public_value(record.source_display_name or ""))
        if not label:
            return None
        evidence = {
            "id": record.evidence_id,
            "kind": "local_evidence",
            "origin": "local",
            "public_safe": True,
            "label": label,
            "provenance": "Approved local evidence",
        }
        return {
            "claim_text": f"Approved local evidence: {label}",
            "evidence": evidence,
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
        origin = "private_github"
        claim_text = f"Private GitHub activity: {title}"
        safe_url: str | None = None
        provenance = "Approved private GitHub activity (URL withheld)"
    else:
        if not isinstance(record.activity_url, str) or not is_public_github_activity_url(record.activity_url):
            return None
        origin = "public_github"
        claim_text = f"{record.activity_repo}: {title}"
        safe_url = record.activity_url
        provenance = "Approved public GitHub activity"
    evidence = {
        "id": record.evidence_id,
        "kind": "github_activity",
        "origin": origin,
        "public_safe": True,
        "activity_type": record.activity_type,
        "title": title,
        "author": author,
        "state": state,
        "created_at": record.activity_created_at,
        "url": safe_url,
        "provenance": provenance,
    }
    timeline = {
        "evidence_id": record.evidence_id,
        "activity_type": record.activity_type,
        "title": title,
        "created_at": record.activity_created_at,
        "url": safe_url,
    }
    return {"claim_text": claim_text, "evidence": evidence, "timeline": timeline}


def _safe_public_label(value: Any, origin: str) -> str:
    if origin == "local":
        if not isinstance(value, str) or contains_unicode_control(value):
            raise ProjectCompositionError("source label contains unsafe text")
        if contains_hidden_secret_shaped_public_value(value):
            return "Approved local evidence"
        safe_label = safe_local_public_label(mask_public_value(normalize_label(value)))
        return safe_label or "Approved local evidence"
    return _safe_text(value, "source label")


def _safe_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ProjectCompositionError(f"{field} must be text")
    if contains_unicode_control(value):
        raise ProjectCompositionError(f"{field} contains unsafe text")
    normalized = normalize_label(mask_public_value(value))
    if not normalized or contains_hidden_secret_shaped_public_value(value):
        raise ProjectCompositionError(f"{field} contains unsafe text")
    if field in {"project title", "project overview", "grouping_rationale", "excerpt"} and normalized == "[REDACTED]":
        raise ProjectCompositionError(f"{field} contains unsafe text")
    if _contains_unsafe_locator(normalized):
        raise ProjectCompositionError(f"{field} contains unsafe locator")
    return normalized


def _safe_hash(value: Any, field: str) -> str:
    text = _safe_text(value, field)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text.casefold()):
        raise ProjectCompositionError(f"{field} is invalid")
    return text


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _contains_unsafe_locator(value: str) -> bool:
    folded = value.casefold()
    return bool(
        "file://" in folded
        or ".portfolio-maker" in folded
        or "portfolio.db" in folded
        or re.search(r"(?i)https?://", value)
        or re.match(r"^(?:/|\\\\|[a-z]:[\\\\/])", value)
        or re.search(r"(?i)(?:^|[/\\\\])(?:raw|snapshots?)(?:[/\\\\]|$)", value)
    )


def _string_ids(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ProjectCompositionError(f"{field} must be a list of strings")
    if len(set(value)) != len(value) or any(_CANDIDATE_ID.fullmatch(item) is None for item in value):
        raise ProjectCompositionError(f"{field} contains invalid IDs")
    return tuple(value)


def _integer_ids(value: Any, field: str) -> tuple[int, ...]:
    if not isinstance(value, list) or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in value):
        raise ProjectCompositionError(f"{field} must be a list of positive integers")
    if len(set(value)) != len(value):
        raise ProjectCompositionError(f"{field} contains duplicate IDs")
    return tuple(value)


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
