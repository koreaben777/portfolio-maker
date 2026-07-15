from __future__ import annotations

import pytest

from portfolio_maker.application.project_boundary import ProjectCandidateV2
from portfolio_maker.application.project_decisions import (
    ProjectDecisionSet,
    resolve_project_decisions,
)


def candidate_v2(
    project_id: str,
    confidence: str,
    *,
    evidence_ids: tuple[int, ...] = (101,),
    counter_signals: tuple[str, ...] = (),
) -> ProjectCandidateV2:
    return ProjectCandidateV2(
        id=f"candidate-{project_id}",
        project_id=project_id,
        title=f"Project {project_id}",
        overview="A grounded project candidate.",
        boundary_type="directory_root",
        boundary_node_ids=(f"node-{project_id}",),
        boundary_fingerprint=f"sha256:{project_id}",
        evidence_ids=evidence_ids,
        grouping_rationale=("The evidence has one coherent purpose.",),
        counter_signals=counter_signals,
        review_reasons=(),
        confidence=confidence,  # type: ignore[arg-type]
    )


def automatic_policy(**changes: object) -> ProjectDecisionSet:
    values: dict[str, object] = {"mode": "automatic"}
    values.update(changes)
    return ProjectDecisionSet(**values)  # type: ignore[arg-type]


def test_automatic_mode_includes_high_and_medium_and_keeps_low_in_review() -> None:
    resolved = resolve_project_decisions(
        (
            candidate_v2("high", "high"),
            candidate_v2("medium", "medium"),
            candidate_v2("low", "low"),
        ),
        automatic_policy(),
    )

    assert [item.status for item in resolved.projects] == [
        "auto_included_high",
        "auto_included_medium",
    ]
    assert resolved.review_required_ids == ("low",)


def test_review_mode_includes_only_manually_approved_candidates() -> None:
    resolved = resolve_project_decisions(
        (candidate_v2("high", "high"), candidate_v2("medium", "medium")),
        ProjectDecisionSet(mode="review", manual_include_ids=("medium",)),
    )

    assert [(item.project_id, item.status) for item in resolved.projects] == [
        ("medium", "manually_approved"),
    ]
    assert resolved.review_required_ids == ("high",)


def test_manual_exclude_takes_precedence_over_manual_include_and_automatic_mode() -> None:
    resolved = resolve_project_decisions(
        (candidate_v2("high", "high"),),
        automatic_policy(
            manual_include_ids=("high",), manual_exclude_ids=("high",)
        ),
    )

    assert resolved.projects == ()
    assert resolved.excluded_project_ids == ("high",)
    assert resolved.review_required_ids == ()


def test_manual_review_prevents_automatic_materialization() -> None:
    resolved = resolve_project_decisions(
        (candidate_v2("high", "high"),),
        automatic_policy(manual_review_ids=("high",)),
    )

    assert resolved.projects == ()
    assert resolved.review_required_ids == ("high",)


@pytest.mark.parametrize(
    "decision_field",
    (
        "stale_candidate_ids",
        "excluded_source_candidate_ids",
        "evidence_conflict_candidate_ids",
        "boundary_critical_failure_candidate_ids",
        "broad_root_candidate_ids",
        "generated_or_cache_only_candidate_ids",
    ),
)
def test_automatic_mode_keeps_policy_blocked_candidate_in_review(
    decision_field: str,
) -> None:
    resolved = resolve_project_decisions(
        (candidate_v2("blocked", "high"),),
        automatic_policy(**{decision_field: ("blocked",)}),
    )

    assert resolved.projects == ()
    assert resolved.review_required_ids == ("blocked",)


def test_missing_approved_evidence_blocks_automatic_materialization() -> None:
    resolved = resolve_project_decisions(
        (candidate_v2("missing-evidence", "high", evidence_ids=()),),
        automatic_policy(),
    )

    assert resolved.projects == ()
    assert resolved.review_required_ids == ("missing-evidence",)


def test_blockers_accept_the_candidate_identifier() -> None:
    candidate = candidate_v2("high", "high")
    resolved = resolve_project_decisions(
        (candidate,),
        automatic_policy(stale_candidate_ids=(candidate.id,)),
    )

    assert resolved.projects == ()
    assert resolved.review_required_ids == ("high",)


def test_counter_signals_do_not_block_medium_but_mark_it_review_recommended() -> None:
    resolved = resolve_project_decisions(
        (
            candidate_v2(
                "medium", "medium", counter_signals=("Check the parent boundary.",)
            ),
        ),
        automatic_policy(),
    )

    assert [(item.project_id, item.status) for item in resolved.projects] == [
        ("medium", "auto_included_medium"),
    ]
    assert resolved.review_recommended_ids == ("medium",)


def test_resolution_never_creates_projects_without_candidates() -> None:
    resolved = resolve_project_decisions((), automatic_policy())

    assert resolved.projects == ()
    assert resolved.review_required_ids == ()
    assert resolved.excluded_project_ids == ()
