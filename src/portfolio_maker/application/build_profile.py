from __future__ import annotations

from dataclasses import replace
import json

from portfolio_maker.application.approval import SourceApproval, load_approval
from portfolio_maker.application.artifact_approval import (
    ArtifactPolicySet,
    load_artifact_policy,
)
from portfolio_maker.application.evidence_selection import (
    EvidenceSelectionRequest,
    EvidenceSelectionService,
)
from portfolio_maker.application.models import BuildProfileRequest, BuildProfileResult
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.artifacts import write_json, write_markdown
from portfolio_maker.infrastructure.extractors import extract_approved_text
from portfolio_maker.infrastructure.managed_files import remove_managed_file
from portfolio_maker.infrastructure.presentation import (
    markdown_text,
    normalize_label,
    safe_local_public_label,
)
from portfolio_maker.infrastructure.policy import (
    FilePolicy,
    SourcePathPolicyError,
    mask_public_value,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import load_valid_local_snapshot
from portfolio_maker.workspace import WorkspacePaths


def build_profile(request: BuildProfileRequest) -> BuildProfileResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    artifact_policy = load_artifact_policy(paths)
    selection_service = EvidenceSelectionService()
    selection_request = EvidenceSelectionRequest(
        artifact_kind="master_profile",
        policy=artifact_policy,
        current_approval=approval,
    )
    pool_request = _evidence_pool_request(artifact_policy, approval)
    policy = FilePolicy(
        forbidden_paths=approval.excluded_directories,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.reconcile_github_artifact_safety()
    snapshots = repository.latest_snapshot_metadata_by_source_id()
    sources: list[Source] = []
    claims: list[dict[str, object]] = []
    evidence_ids: list[int] = []
    claim_ids: list[int] = []
    github_source_repositories: dict[int, str] = {}
    source_by_id = {source.id: source for source in repository.list_sources() if source.id is not None}

    for source in repository.list_sources(status=SourceStatus.INGESTED):
        if (
            source.type != SourceType.LOCAL_FILE
            or source.id is None
            or selection_service.decision_for_source(source, pool_request) is not None
        ):
            continue
        try:
            source_path, extracted = extract_approved_text(source.uri, policy)
        except FileNotFoundError:
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            continue
        except SourcePathPolicyError:
            repository.update_source_status(source.id, SourceStatus.SKIPPED_POLICY)
            continue
        except OSError:
            repository.update_source_status(source.id, SourceStatus.EXTRACT_FAILED)
            continue

        snapshot_metadata = snapshots.get(source.id)
        if (
            snapshot_metadata is None
            or snapshot_metadata[2] != extracted.content_hash
            or snapshot_metadata[3] != extracted.extractor
        ):
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            continue
        snapshot_path = snapshot_metadata[1]
        snapshot = load_valid_local_snapshot(
            snapshot_path,
            source.id,
            source.uri,
            source_path.name,
            extracted,
        )
        if snapshot is None:
            repository.update_source_status(source.id, SourceStatus.STALE_SOURCE)
            continue
        raw_display_name = normalize_label(source.display_name)
        display_name = safe_local_public_label(mask_public_value(raw_display_name))
        evidence = _snapshot_evidence(raw_display_name, str(snapshot["text"]))
        if evidence is None:
            continue
        evidence = safe_local_public_label(mask_public_value(evidence))
        claim_text = f"{display_name}: {evidence}"
        project_id = repository.upsert_project(f"local:{source.id}", public_safe=False)
        evidence_id = repository.upsert_evidence_item(
            source_id=source.id,
            snapshot_id=snapshot_metadata[0],
            github_activity_id=None,
            locator=source.uri,
            stable_id=f"source-snapshot:{source.id}:{snapshot_metadata[2]}",
            content_hash=snapshot_metadata[2],
            public_safe=False,
        )
        claim_id = repository.upsert_career_claim(project_id, claim_text, public_safe=False)
        repository.link_claim_evidence(claim_id, evidence_id, "direct")
        if selection_service.decision_for_source(source, selection_request) is not None:
            continue

        sources.append(source)
        evidence_ids.append(evidence_id)
        claim_ids.append(claim_id)
        claims.append(
            {
                "claim_type": "project_evidence",
                "text": claim_text,
                "confidence": "medium",
                "public_safe": False,
                "claim_id": claim_id,
                "evidence_id": evidence_id,
                "evidence_uri": f"local-evidence:{evidence_id}",
                "evidence_snapshot": f"local-snapshot:{snapshot_metadata[0]}",
            }
        )

    seen_activities: set[tuple[str, str, str]] = set()
    for activity in repository.list_github_activities():
        if activity.id is None or activity.source_id is None:
            continue
        source = source_by_id.get(activity.source_id)
        if source is None:
            continue
        if selection_service.decision_for_activity(
            activity, source, pool_request
        ) is not None:
            continue
        canonical_activity_url = activity.url
        repository_name = activity.repo.casefold()
        title = normalize_label(mask_public_value(activity.title))
        if not title:
            continue
        activity_key = (
            repository_name,
            activity.activity_type,
            canonical_activity_url,
        )
        if activity_key in seen_activities:
            continue
        seen_activities.add(activity_key)
        author = normalize_label(mask_public_value(activity.author))
        state = normalize_label(mask_public_value(activity.state))
        is_private = activity.is_private
        evidence_uri = (
            f"private-github-activity:{activity.id}"
            if is_private
            else canonical_activity_url
        )
        claim_text = (
            f"Private GitHub activity: {title}"
            if is_private
            else f"{repository_name}: {title}"
        )
        project_id = repository.upsert_project(f"github:{repository_name}", public_safe=True)
        evidence_id = repository.upsert_evidence_item(
            source_id=source.id,
            snapshot_id=None,
            github_activity_id=activity.id,
            locator=canonical_activity_url,
            stable_id=f"github-activity:{activity.id}",
            content_hash=None,
            public_safe=True,
        )
        claim_id = repository.upsert_github_activity_claim(
            evidence_id,
            project_id,
            claim_text,
        )
        if selection_service.decision_for_activity(
            activity, source, selection_request
        ) is not None:
            continue

        if source not in sources:
            sources.append(source)
        github_source_repositories[source.id] = repository_name
        evidence_ids.append(evidence_id)
        claim_ids.append(claim_id)
        claims.append(
            {
                "claim_type": "approved_github_activity",
                "text": claim_text,
                "confidence": "high",
                "public_safe": True,
                "claim_id": claim_id,
                "evidence_id": evidence_id,
                "evidence_uri": evidence_uri,
                "evidence_snapshot": None,
                "activity_type": activity.activity_type,
                "title": title,
                "author": author,
                "created_at": activity.created_at,
                "state": state,
                "origin_type": "private_github" if is_private else "public_github",
            }
        )

    selection = selection_service.select(repository, selection_request)

    if artifact_policy.legacy_compatibility:
        for claim in claims:
            claim.pop("claim_id", None)
            claim.pop("evidence_id", None)
            claim.pop("origin_type", None)

    payload = {
        "version": 1,
        "sources": [
            _public_source_payload(source, github_source_repositories)
            for source in sources
        ],
        "claims": claims,
    }
    if not artifact_policy.legacy_compatibility:
        payload["delivery_scope"] = selection.delivery_scope
        payload["policy_hash"] = selection.policy_hash

    write_json(paths.master_profile_json_path, payload)
    source_lines = []
    for source in sources:
        source_payload = _public_source_payload(source, github_source_repositories)
        source_lines.append(
            f"- {markdown_text(str(source_payload['display_name']))} "
            f"({markdown_text(str(source_payload['type']))})"
        )
    claim_lines = [
        f"- {markdown_text(str(claim['text']))} ({markdown_text(str(claim['confidence']))})"
        f"\n  Evidence: {markdown_text(str(claim['evidence_uri']))}"
        for claim in claims
    ]
    write_markdown(
        paths.master_profile_md_path,
        "\n\n".join(
            [
                "# Master Profile",
                "## Sources\n" + "\n".join(source_lines),
                "## Claims\n" + "\n".join(claim_lines),
            ]
        )
        + "\n",
    )
    repository.record_artifact(
        "master_profile",
        1,
        json.dumps(
            (
                {"claim_ids": claim_ids, "evidence_ids": evidence_ids}
                if artifact_policy.legacy_compatibility
                else selection.input_manifest("master_profile")
            ),
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    if request.invalidate_portfolio_draft:
        remove_managed_file(paths.portfolio_draft_path, missing_ok=True)
    return BuildProfileResult(
        json_path=paths.master_profile_json_path,
        markdown_path=paths.master_profile_md_path,
        claim_count=len(claims),
        claim_ids=tuple(claim_ids),
        evidence_ids=tuple(evidence_ids),
    )


def _snapshot_evidence(display_name: str, text: str) -> str | None:
    lines = [
        line.strip().lstrip("#").strip()
        for line in text.splitlines()
        if line.strip()
    ]
    for line in lines:
        if line != display_name:
            return line
    return None


def _evidence_pool_request(
    artifact_policy: ArtifactPolicySet,
    approval: SourceApproval,
) -> EvidenceSelectionRequest:
    pool_policies = tuple(
        replace(
            policy,
            delivery_scope="restricted",
            include_local=True,
            include_public_github=True,
            include_private_github=True,
            excluded_source_uris=(),
            excluded_repositories=(),
            excluded_activity_urls=(),
        )
        for policy in artifact_policy.policies
    )
    return EvidenceSelectionRequest(
        artifact_kind="master_profile",
        policy=ArtifactPolicySet(pool_policies, explicit=artifact_policy.explicit),
        current_approval=approval,
    )


def _public_source_payload(
    source: Source, github_source_repositories: dict[int, str]
) -> dict[str, object]:
    repository_name = (
        github_source_repositories.get(source.id)
        if source.type == SourceType.GITHUB_REPOSITORY
        else None
    )
    if source.type == SourceType.GITHUB_REPOSITORY and source.origin_visibility == "private":
        return {
            "id": source.id,
            "type": "private_github",
            "uri": f"private-github-source:{source.id}",
            "display_name": "Private GitHub repository",
            "owner": None,
            "status": source.status.value,
        }
    if repository_name is not None:
        owner = repository_name.split("/", 1)[0]
        display_name = repository_name
    elif source.type == SourceType.LOCAL_FILE:
        return {
            "id": source.id,
            "type": source.type.value,
            "uri": f"local-source:{source.id}",
            "display_name": safe_local_public_label(
                mask_public_value(source.display_name)
            ),
            "owner": None,
            "status": source.status.value,
        }
    else:
        owner = source.owner
        display_name = normalize_label(source.display_name)
    return {
        "id": source.id,
        "type": source.type.value,
        "uri": source.uri,
        "display_name": display_name,
        "owner": owner,
        "status": source.status.value,
    }
