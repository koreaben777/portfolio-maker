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
from portfolio_maker.domain.models import (
    GitHubActivity,
    PublicEvidenceRecord,
    Source,
    SourceType,
)
from portfolio_maker.infrastructure.github_connector import (
    canonical_public_github_activity_url,
    canonical_repository_name,
    contains_unicode_control,
    is_valid_github_activity_state,
    is_valid_github_timestamp,
    public_github_activity_identity,
    public_github_repository_name,
)
from portfolio_maker.infrastructure.policy import contains_hidden_secret_shaped_public_value
from portfolio_maker.infrastructure.presentation import normalize_label
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

    def input_manifest(self, artifact_kind: ArtifactKind) -> dict[str, object]:
        origin_counts: dict[str, int] = {}
        for record in self.records:
            origin = _record_origin(record)
            origin_counts[origin] = origin_counts.get(origin, 0) + 1
        return {
            "artifact_kind": artifact_kind,
            "delivery_scope": self.delivery_scope,
            "policy_hash": self.policy_hash,
            "included_source_ids": list(self.included_source_ids),
            "included_evidence_ids": list(self.included_evidence_ids),
            "included_claim_ids": list(self.included_claim_ids),
            "source_ids": list(self.included_source_ids),
            "evidence_ids": list(self.included_evidence_ids),
            "claim_ids": list(self.included_claim_ids),
            "excluded_decisions": list(self.excluded_decisions),
            "origin_counts": origin_counts,
        }


class EvidenceSelectionService:
    def decision_for_source(
        self,
        source: Source,
        request: EvidenceSelectionRequest,
    ) -> str | None:
        visibility = source.origin_visibility or "private"
        record = PublicEvidenceRecord(
            project_id=0,
            project_name="",
            claim_id=0,
            claim_text="",
            evidence_id=0,
            evidence_stable_id="",
            evidence_locator=source.uri,
            source_id=source.id,
            source_type=source.type.value,
            source_uri=source.uri,
            source_display_name=source.display_name,
            source_status=source.status.value,
            activity_id=None,
            activity_repo=None,
            activity_type=None,
            activity_url=None,
            activity_title=None,
            activity_state=None,
            activity_state_field=None,
            activity_author=None,
            activity_created_at=None,
            activity_is_private=None,
            evidence_origin_type=source.origin_type or "local",
            evidence_origin_visibility=visibility,
            source_origin_type=source.origin_type or "local",
            source_origin_visibility=visibility,
        )
        return self._exclusion_reason(
            record,
            "local",
            request.policy.for_kind(request.artifact_kind),
            request.current_approval,
            request.policy.legacy_compatibility
            and request.artifact_kind in {"portfolio_public_manifest", "portfolio_html"},
        )

    def decision_for_activity(
        self,
        activity: GitHubActivity,
        source: Source,
        request: EvidenceSelectionRequest,
    ) -> str | None:
        origin = "private_github" if activity.is_private else "public_github"
        visibility = activity.origin_visibility or (
            "private" if activity.is_private else "public"
        )
        record = PublicEvidenceRecord(
            project_id=0,
            project_name="",
            claim_id=0,
            claim_text="",
            evidence_id=0,
            evidence_stable_id="",
            evidence_locator=activity.url,
            source_id=source.id,
            source_type=source.type.value,
            source_uri=source.uri,
            source_display_name=source.display_name,
            source_status=source.status.value,
            activity_id=activity.id,
            activity_repo=activity.repo,
            activity_type=activity.activity_type,
            activity_url=activity.url,
            activity_title=activity.title,
            activity_state=activity.state,
            activity_state_field=activity.state_field,
            activity_author=activity.author,
            activity_created_at=activity.created_at,
            activity_is_private=activity.is_private,
            evidence_origin_type=activity.origin_type or origin,
            evidence_origin_visibility=visibility,
            source_origin_type=source.origin_type or origin,
            source_origin_visibility=source.origin_visibility or visibility,
            activity_origin_type=activity.origin_type or origin,
            activity_origin_visibility=visibility,
        )
        return self._exclusion_reason(
            record,
            origin,
            request.policy.for_kind(request.artifact_kind),
            request.current_approval,
            request.policy.legacy_compatibility
            and request.artifact_kind in {"portfolio_public_manifest", "portfolio_html"},
        )

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
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                record.activity_title,
                record.activity_author,
                record.activity_state,
                record.activity_created_at,
            )
        ):
            return "invalid_activity_metadata"
        if (
            contains_unicode_control(record.activity_title)
            or contains_unicode_control(record.activity_author)
            or contains_unicode_control(record.activity_state)
            or contains_hidden_secret_shaped_public_value(record.activity_title)
            or contains_hidden_secret_shaped_public_value(record.activity_author)
            or contains_hidden_secret_shaped_public_value(record.activity_state)
            or not is_valid_github_activity_state(
                record.activity_type or "",
                record.activity_state,
                record.activity_state_field,
            )
            or not is_valid_github_timestamp(record.activity_created_at)
        ):
            return "invalid_activity_metadata"
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
        if (
            public_github_activity_identity(activity_url)
            != (repository_name, record.activity_type)
        ):
            return "invalid_activity_url"
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
            "excluded_directories": sorted(
                str(path) for path in request.current_approval.excluded_directories
            ),
            "excluded_file_patterns": sorted(
                request.current_approval.excluded_file_patterns
            ),
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
