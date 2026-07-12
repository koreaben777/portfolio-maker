from __future__ import annotations

import json

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.models import BuildProfileRequest, BuildProfileResult
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.artifacts import write_json, write_markdown
from portfolio_maker.infrastructure.extractors import extract_approved_text
from portfolio_maker.infrastructure.policy import FilePolicy, SourcePathPolicyError, mask_public_value
from portfolio_maker.infrastructure.managed_files import remove_managed_file
from portfolio_maker.infrastructure.presentation import markdown_text, normalize_label
from portfolio_maker.infrastructure.github_connector import (
    canonical_repository_name,
    is_valid_github_timestamp,
    public_github_activity_identity,
)
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import load_valid_local_snapshot
from portfolio_maker.workspace import WorkspacePaths


def build_profile(request: BuildProfileRequest) -> BuildProfileResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    approved_uris = set(approval.approved_source_uris)
    policy = FilePolicy(
        forbidden_paths=approval.forbidden_paths,
        excluded_file_patterns=approval.excluded_file_patterns,
    )
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    snapshots = repository.latest_snapshot_metadata_by_source_id()
    sources: list[Source] = []
    claims: list[dict[str, object]] = []
    evidence_ids: list[int] = []
    claim_ids: list[int] = []
    source_by_id = {source.id: source for source in repository.list_sources() if source.id is not None}

    for source in repository.list_sources(status=SourceStatus.INGESTED):
        if source.type != SourceType.LOCAL_FILE or source.uri not in approved_uris or source.id is None:
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
        display_name = normalize_label(source.display_name)
        evidence = _snapshot_evidence(display_name, str(snapshot["text"]))
        if evidence is None:
            continue
        evidence = normalize_label(evidence)
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
        evidence_ids.append(evidence_id)
        claim_ids.append(claim_id)

        sources.append(source)
        claims.append(
            {
                "claim_type": "project_evidence",
                "text": claim_text,
                "confidence": "medium",
                "public_safe": False,
                "evidence_uri": source.uri,
                "evidence_snapshot": str(snapshot_path),
            }
        )

    approved_activity_urls = set(approval.approved_github_activity_urls)
    allowed_repositories = set(approval.allowed_repositories)
    excluded_repositories = set(approval.excluded_repositories)
    seen_activities: set[tuple[str, str, str]] = set()
    for activity in repository.list_github_activities():
        try:
            repository_name = canonical_repository_name(activity.repo)
        except ValueError:
            continue
        if (
            activity.id is None
            or activity.source_id is None
            or activity.is_private
            or activity.url not in approved_activity_urls
            or not activity.state.strip()
            or not is_valid_github_timestamp(activity.created_at)
            or public_github_activity_identity(activity.url)
            != (repository_name, activity.activity_type)
        ):
            continue
        source = source_by_id.get(activity.source_id)
        if (
            source is None
            or source.type != SourceType.GITHUB_REPOSITORY
            or source.status in {
                SourceStatus.SKIPPED_POLICY,
                SourceStatus.EXTRACT_FAILED,
                SourceStatus.STALE_SOURCE,
            }
            or repository_name in excluded_repositories
            or (allowed_repositories and repository_name not in allowed_repositories)
        ):
            continue
        activity_key = (repository_name, activity.activity_type, activity.url)
        if activity_key in seen_activities:
            continue
        seen_activities.add(activity_key)
        if source not in sources:
            sources.append(source)
        title = normalize_label(mask_public_value(activity.title))
        author = normalize_label(mask_public_value(activity.author))
        state = normalize_label(activity.state)
        claim_text = f"{repository_name}: {title}"
        project_id = repository.upsert_project(f"github:{repository_name}", public_safe=True)
        evidence_id = repository.upsert_evidence_item(
            source_id=source.id,
            snapshot_id=None,
            github_activity_id=activity.id,
            locator=activity.url,
            stable_id=f"github-activity:{activity.id}",
            content_hash=None,
            public_safe=True,
        )
        claim_id = repository.upsert_career_claim(project_id, claim_text, public_safe=True)
        repository.link_claim_evidence(claim_id, evidence_id, "direct")
        evidence_ids.append(evidence_id)
        claim_ids.append(claim_id)
        claims.append(
            {
                "claim_type": "approved_github_activity",
                "text": claim_text,
                "confidence": "high",
                "public_safe": True,
                "evidence_uri": activity.url,
                "evidence_snapshot": None,
                "activity_type": activity.activity_type,
                "title": title,
                "author": author,
                "created_at": activity.created_at,
                "state": state,
            }
        )

    payload = {
        "version": 1,
        "sources": [
            {
                "id": source.id,
                "type": source.type.value,
                "uri": source.uri,
                "display_name": normalize_label(source.display_name),
                "owner": source.owner,
                "status": source.status.value,
            }
            for source in sources
        ],
        "claims": claims,
    }

    write_json(paths.master_profile_json_path, payload)
    source_lines = [
        f"- {markdown_text(source.display_name)} ({markdown_text(source.type.value)})"
        for source in sources
    ]
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
            {"claim_ids": claim_ids, "evidence_ids": evidence_ids},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
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
    return lines[0] if lines else None
