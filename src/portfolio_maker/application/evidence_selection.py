from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from portfolio_maker.application.approval import SourceApproval
from portfolio_maker.application.artifact_approval import (
    ArtifactPolicy,
    ArtifactPolicySet,
)
from portfolio_maker.application.models import ArtifactKind
from portfolio_maker.domain.models import PublicEvidenceRecord
from portfolio_maker.infrastructure.github_connector import (
    canonical_public_github_activity_url,
    canonical_repository_name,
    public_github_repository_name,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository


class EvidenceSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceSelectionRequest:
    artifact_kind: ArtifactKind
    policy: ArtifactPolicySet
    current_approval: SourceApproval


@dataclass(frozen=True)
class EvidenceSelectionResult:
    delivery_scope: str
    included_source_ids: tuple[int, ...]
    included_evidence_ids: tuple[int, ...]
    included_claim_ids: tuple[int, ...]
    excluded_decisions: tuple[dict[str, object], ...]
    policy_hash: str
    records: tuple[PublicEvidenceRecord, ...] = ()


class EvidenceSelectionService:
    def select(
        self,
        repository: SQLiteRepository,
        request: EvidenceSelectionRequest,
    ) -> EvidenceSelectionResult:
        policy = request.policy.for_kind(request.artifact_kind)
        if policy.delivery_scope == "open_public" and (
            policy.include_local or policy.include_private_github
        ):
            raise EvidenceSelectionError(
                "open_public cannot include local or private evidence"
            )

        included_sources: set[int] = set()
        included_evidence: set[int] = set()
        included_claims: set[int] = set()
        selected_records: list[PublicEvidenceRecord] = []
        excluded: list[dict[str, object]] = []
        legacy_public_compat = request.policy.legacy_compatibility and request.artifact_kind in {
            "portfolio_public_manifest",
            "portfolio_html",
        }

        for record in repository.list_evidence_selection_records():
            origin = _record_origin(record)
            reason = self._exclusion_reason(
                record,
                origin,
                policy,
                request.current_approval,
                legacy_public_compat,
            )
            if reason is not None:
                excluded.append({"evidence_id": record.evidence_id, "reason": reason})
                continue
            selected_records.append(record)
            if record.source_id is not None:
                included_sources.add(record.source_id)
            included_evidence.add(record.evidence_id)
            included_claims.add(record.claim_id)

        return EvidenceSelectionResult(
            delivery_scope=policy.delivery_scope,
            included_source_ids=tuple(sorted(included_sources)),
            included_evidence_ids=tuple(sorted(included_evidence)),
            included_claim_ids=tuple(sorted(included_claims)),
            excluded_decisions=tuple(excluded),
            policy_hash=_policy_hash(request),
            records=tuple(selected_records),
        )

    def _exclusion_reason(
        self,
        record: PublicEvidenceRecord,
        origin: str,
        policy: ArtifactPolicy,
        approval: SourceApproval,
        legacy_public_compat: bool,
    ) -> str | None:
        if record.evidence_origin_visibility in {None, "unknown"}:
            return "unknown_origin"
        if record.source_status in {"skipped_policy", "extract_failed", "stale_source"}:
            return "stale_or_revoked"
        if record.source_id is None or record.source_uri is None:
            return "missing_source"
        if record.source_type == "github_repository" and record.activity_id is None:
            return "missing_activity"
        if record.source_type == "local_file" and record.activity_id is not None:
            return "invalid_origin"

        if origin == "local":
            if legacy_public_compat:
                return "legacy_public_compatibility"
            if policy.delivery_scope == "open_public":
                return "open_public_origin"
            if not policy.include_local:
                return "excluded_origin"
            if record.source_uri not in set(approval.approved_source_uris):
                return "unapproved_source"
            if record.source_status != "ingested":
                return "source_not_ingested"
            if record.source_uri in set(policy.excluded_source_uris):
                return "excluded_source"
            return None

        if record.activity_repo is None or record.activity_url is None:
            return "missing_activity"
        try:
            repository_name = canonical_repository_name(record.activity_repo)
        except ValueError:
            return "invalid_repository"
        activity_url = canonical_public_github_activity_url(record.activity_url)
        if activity_url is None:
            return "invalid_activity_url"
        if repository_name in set(policy.excluded_repositories):
            return "excluded_repository"
        if repository_name in set(approval.excluded_repositories):
            return "excluded_repository"
        if approval.allowed_repositories and repository_name not in set(
            approval.allowed_repositories
        ):
            return "repository_not_allowed"
        if origin == "public_github":
            if not policy.include_public_github:
                return "excluded_origin"
            if activity_url not in set(approval.approved_github_activity_urls):
                return "unapproved_activity"
        elif origin == "private_github":
            if legacy_public_compat or policy.delivery_scope == "open_public":
                return "open_public_origin"
            if not policy.include_private_github:
                return "excluded_origin"
            if not approval.private_sources_allowed:
                return "private_sources_not_allowed"
            if not approval.allowed_repositories:
                return "private_repository_not_allowed"
            if activity_url not in set(approval.approved_private_github_activity_urls):
                return "unapproved_private_activity"
        else:
            return "unknown_origin"
        if record.source_uri in set(policy.excluded_source_uris):
            return "excluded_source"
        if activity_url in set(policy.excluded_activity_urls):
            return "excluded_activity"
        if public_github_repository_name(record.source_uri) != repository_name:
            return "source_repository_mismatch"
        return None


def select_evidence_for_artifact(
    repository: SQLiteRepository,
    request: EvidenceSelectionRequest,
) -> EvidenceSelectionResult:
    return EvidenceSelectionService().select(repository, request)


def _record_origin(record: PublicEvidenceRecord) -> str:
    if record.activity_id is None:
        return "local"
    if record.activity_is_private:
        return "private_github"
    return "public_github"


def _policy_hash(request: EvidenceSelectionRequest) -> str:
    policy = request.policy.for_kind(request.artifact_kind)
    payload = {
        "artifact_kind": request.artifact_kind,
        "explicit": request.policy.explicit,
        "policy": {
            "delivery_scope": policy.delivery_scope,
            "include_local": policy.include_local,
            "include_public_github": policy.include_public_github,
            "include_private_github": policy.include_private_github,
            "excluded_source_uris": sorted(policy.excluded_source_uris),
            "excluded_repositories": sorted(policy.excluded_repositories),
            "excluded_activity_urls": sorted(policy.excluded_activity_urls),
        },
        "approval": {
            "approved_source_uris": sorted(request.current_approval.approved_source_uris),
            "excluded_repositories": sorted(request.current_approval.excluded_repositories),
            "allowed_repositories": sorted(request.current_approval.allowed_repositories),
            "private_sources_allowed": request.current_approval.private_sources_allowed,
            "approved_github_activity_urls": sorted(
                request.current_approval.approved_github_activity_urls
            ),
            "approved_private_github_activity_urls": sorted(
                request.current_approval.approved_private_github_activity_urls
            ),
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
