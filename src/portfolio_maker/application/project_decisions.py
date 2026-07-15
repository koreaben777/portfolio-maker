from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from portfolio_maker.application.project_boundary import ProjectCandidateV2


class ProjectDecisionError(ValueError):
    pass


ProjectDecisionMode = Literal["review", "automatic"]
ResolvedProjectStatus = Literal[
    "manually_approved", "auto_included_high", "auto_included_medium"
]


@dataclass(frozen=True)
class ProjectDecisionSet:
    mode: ProjectDecisionMode = "review"
    manual_include_ids: tuple[str, ...] = ()
    manual_exclude_ids: tuple[str, ...] = ()
    manual_review_ids: tuple[str, ...] = ()
    stale_candidate_ids: tuple[str, ...] = ()
    excluded_source_candidate_ids: tuple[str, ...] = ()
    evidence_conflict_candidate_ids: tuple[str, ...] = ()
    boundary_critical_failure_candidate_ids: tuple[str, ...] = ()
    broad_root_candidate_ids: tuple[str, ...] = ()
    generated_or_cache_only_candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedProject:
    candidate: ProjectCandidateV2
    status: ResolvedProjectStatus
    review_recommended: bool = False

    @property
    def project_id(self) -> str:
        return self.candidate.project_id


@dataclass(frozen=True)
class ResolvedProjectSet:
    projects: tuple[ResolvedProject, ...]
    review_required_ids: tuple[str, ...]
    excluded_project_ids: tuple[str, ...]
    review_recommended_ids: tuple[str, ...]


ACTIVE_PROJECT_STATES = frozenset(
    {"manually_approved", "auto_included_high", "auto_included_medium"}
)


def resolve_project_decisions(
    candidates: tuple[ProjectCandidateV2, ...],
    decisions: ProjectDecisionSet,
) -> ResolvedProjectSet:
    if decisions.mode not in {"review", "automatic"}:
        raise ProjectDecisionError("project decision mode is invalid")

    manual_include_ids = set(decisions.manual_include_ids)
    manual_exclude_ids = set(decisions.manual_exclude_ids)
    manual_review_ids = set(decisions.manual_review_ids)
    blocked_ids = _blocked_candidate_ids(decisions)

    projects: list[ResolvedProject] = []
    review_required_ids: list[str] = []
    excluded_project_ids: list[str] = []
    review_recommended_ids: list[str] = []

    for candidate in sorted(candidates, key=lambda item: item.project_id):
        project_id = candidate.project_id
        if project_id in manual_exclude_ids:
            excluded_project_ids.append(project_id)
            continue
        if project_id in manual_include_ids:
            projects.append(
                ResolvedProject(candidate=candidate, status="manually_approved")
            )
            continue
        if project_id in manual_review_ids:
            review_required_ids.append(project_id)
            continue
        if decisions.mode == "review":
            review_required_ids.append(project_id)
            continue
        if _requires_review(candidate, blocked_ids):
            review_required_ids.append(project_id)
            continue
        if candidate.confidence == "high":
            projects.append(
                ResolvedProject(candidate=candidate, status="auto_included_high")
            )
            continue
        if candidate.confidence == "medium":
            review_recommended = bool(candidate.counter_signals)
            projects.append(
                ResolvedProject(
                    candidate=candidate,
                    status="auto_included_medium",
                    review_recommended=review_recommended,
                )
            )
            if review_recommended:
                review_recommended_ids.append(project_id)
            continue
        review_required_ids.append(project_id)

    return ResolvedProjectSet(
        projects=tuple(projects),
        review_required_ids=tuple(review_required_ids),
        excluded_project_ids=tuple(excluded_project_ids),
        review_recommended_ids=tuple(review_recommended_ids),
    )


def _blocked_candidate_ids(decisions: ProjectDecisionSet) -> set[str]:
    return set().union(
        decisions.stale_candidate_ids,
        decisions.excluded_source_candidate_ids,
        decisions.evidence_conflict_candidate_ids,
        decisions.boundary_critical_failure_candidate_ids,
        decisions.broad_root_candidate_ids,
        decisions.generated_or_cache_only_candidate_ids,
    )


def _requires_review(candidate: ProjectCandidateV2, blocked_ids: set[str]) -> bool:
    return (
        not candidate.evidence_ids
        or candidate.id in blocked_ids
        or candidate.project_id in blocked_ids
        or candidate.confidence == "low"
    )
