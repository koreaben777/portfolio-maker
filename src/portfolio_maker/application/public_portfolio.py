from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.artifact_approval import load_artifact_policy
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.evidence_selection import (
    EvidenceSelectionRequest,
    EvidenceSelectionResult,
    EvidenceSelectionService,
)
from portfolio_maker.application.project_composition import (
    build_project_projections,
    project_provenance_manifest,
)
from portfolio_maker.application.models import (
    ArtifactKind,
    BuildProfileRequest,
    PublicPortfolioRequest,
    PublicPortfolioResult,
)
from portfolio_maker.infrastructure.artifacts import write_json
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


class PublicPortfolioError(ValueError):
    pass


@dataclass(frozen=True)
class PublicPortfolioBuild:
    payload: dict[str, object]
    selection: EvidenceSelectionResult
    result: PublicPortfolioResult
    selection_manifest: dict[str, object]


def build_public_portfolio(request: PublicPortfolioRequest) -> PublicPortfolioResult:
    paths = WorkspacePaths.from_root(request.workspace)
    build = build_public_portfolio_payload(request, "portfolio_public_manifest")
    selection = build.selection
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    write_json(paths.portfolio_public_json_path, build.payload)
    repository.record_artifact(
        "portfolio_public",
        1,
        json.dumps(
            {
                **build.selection_manifest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return PublicPortfolioResult(
        manifest_path=paths.portfolio_public_json_path,
        project_count=build.result.project_count,
        claim_count=build.result.claim_count,
        evidence_count=build.result.evidence_count,
        claim_ids=build.result.claim_ids,
        evidence_ids=build.result.evidence_ids,
    )


def build_public_portfolio_payload(
    request: PublicPortfolioRequest,
    artifact_kind: ArtifactKind,
) -> PublicPortfolioBuild:
    if artifact_kind not in {"portfolio_public_manifest", "portfolio_html"}:
        raise PublicPortfolioError("unsupported public portfolio artifact kind")
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
    selection = EvidenceSelectionService().select(
        repository,
        EvidenceSelectionRequest(
            artifact_kind=artifact_kind,
            policy=load_artifact_policy(paths),
            current_approval=load_approval(paths),
        ),
    )
    projects = build_project_projections(
        repository.list_portfolio_projects(),
        selection.records,
        set(selection.included_evidence_ids),
    )
    selection_manifest = _project_selection_manifest(selection, projects, artifact_kind)
    payload, claim_ids, evidence_ids = _assemble_payload(
        selection,
        artifact_kind,
        projects,
        selection_manifest,
    )
    return PublicPortfolioBuild(
        payload=payload,
        selection=selection,
        result=PublicPortfolioResult(
            manifest_path=paths.portfolio_public_json_path,
            project_count=len(payload["projects"]),
            claim_count=len(set(claim_ids)),
            evidence_count=len(set(evidence_ids)),
            claim_ids=tuple(sorted(set(claim_ids))),
            evidence_ids=tuple(sorted(set(evidence_ids))),
        ),
        selection_manifest=selection_manifest,
    )


def _assemble_payload(
    selection: EvidenceSelectionResult,
    artifact_kind: ArtifactKind,
    projects: list[dict[str, object]],
    selection_manifest: dict[str, object],
) -> tuple[dict[str, object], list[int], list[int]]:
    claim_ids: list[int] = []
    evidence_ids: list[int] = []
    for project in projects:
        claims = project.get("claims")
        project_evidence_ids = project.get("evidence_ids")
        if isinstance(claims, list):
            claim_ids.extend(
                int(claim["id"])
                for claim in claims
                if isinstance(claim, dict) and isinstance(claim.get("id"), int)
            )
            for claim in claims:
                if isinstance(claim, dict) and isinstance(claim.get("evidence"), list):
                    evidence_ids.extend(
                        int(evidence["id"])
                        for evidence in claim["evidence"]
                        if isinstance(evidence, dict) and isinstance(evidence.get("id"), int)
                    )
        if isinstance(project_evidence_ids, list):
            evidence_ids.extend(
                int(evidence_id)
                for evidence_id in project_evidence_ids
                if isinstance(evidence_id, int)
            )

    payload = {
        "version": 1,
        "delivery_scope": selection.delivery_scope,
        "policy_hash": selection.policy_hash,
        "selection": selection_manifest,
        "generated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "profile": {},
        "projects": projects,
        "skills": [],
        "links": [],
    }
    return payload, claim_ids, evidence_ids


def _project_selection_manifest(
    selection: EvidenceSelectionResult,
    projects: list[dict[str, object]],
    artifact_kind: ArtifactKind,
) -> dict[str, object]:
    manifest = selection.input_manifest(artifact_kind)
    evidence_ids = sorted(
        {
            int(evidence_id)
            for project in projects
            for evidence_id in project.get("evidence_ids", [])
            if isinstance(evidence_id, int)
        }
    )
    claim_ids = sorted(
        {
            int(claim["id"])
            for project in projects
            for claim in project.get("claims", [])
            if isinstance(claim, dict) and isinstance(claim.get("id"), int)
        }
    )
    records_by_id = {record.evidence_id: record for record in selection.records}
    source_ids = sorted(
        {
            records_by_id[evidence_id].source_id
            for evidence_id in evidence_ids
            if evidence_id in records_by_id and records_by_id[evidence_id].source_id is not None
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
