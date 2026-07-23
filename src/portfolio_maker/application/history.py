from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from portfolio_maker.application.models import (
    HistoryPlanRequest,
    HistoryPlanResult,
)
from portfolio_maker.domain.history_models import (
    HistoryPlan,
    HistoryPolicy,
    HistoryUnit,
)
from portfolio_maker.infrastructure.history_store import HistoryStore
from portfolio_maker.infrastructure.managed_files import (
    read_managed_relative_file,
    write_managed_text,
)
from portfolio_maker.infrastructure.sqlite_repository import (
    RepositoryError,
    SQLiteRepository,
)
from portfolio_maker.workspace import WorkspacePaths


_POLICY_FIELDS = frozenset(
    {
        "version",
        "minimum_age_days",
        "keep_latest_text_snapshots_per_source",
        "retain_included_vision_analyses",
        "retain_pending_review_vision_analyses",
        "retain_incomplete_vision_requests",
    }
)
_MAX_MINIMUM_AGE_DAYS = timedelta.max.days
_MAX_SQLITE_INTEGER = (1 << 63) - 1


class HistoryError(RuntimeError):
    pass


class _DuplicatePolicyFieldError(ValueError):
    pass


def write_sample_history_policy(
    paths: WorkspacePaths,
    *,
    force: bool = False,
) -> Path:
    payload = {
        "version": 1,
        **_policy_payload(HistoryPolicy.default()),
    }
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        paths.ensure()
        return write_managed_text(
            paths.history_policy_path,
            content,
            overwrite=force,
        )
    except FileExistsError as error:
        raise HistoryError("history policy already exists") from error
    except OSError as error:
        raise HistoryError("history policy path is unsafe") from error


def load_history_policy(paths: WorkspacePaths) -> HistoryPolicy | None:
    try:
        snapshot = read_managed_relative_file(
            paths.reviews_dir,
            Path(paths.history_policy_path.name),
        )
    except FileNotFoundError:
        return None
    except OSError as error:
        raise HistoryError("history policy path is unsafe") from error
    try:
        payload = json.loads(
            snapshot.content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_fields,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicatePolicyFieldError,
    ) as error:
        raise HistoryError("history policy is malformed") from error
    return _parse_history_policy(payload)


def plan_history_cleanup(request: HistoryPlanRequest) -> HistoryPlanResult:
    paths = WorkspacePaths.from_root(request.workspace)
    try:
        paths.ensure()
    except OSError as error:
        raise HistoryError("managed history workspace is unsafe") from error
    policy = load_history_policy(paths)
    if policy is None:
        return _write_summary(
            paths,
            plan_id=None,
            units=(),
            fallback_reason="history_policy_missing_retain_all",
        )

    repository = SQLiteRepository(paths.db_path)
    store = HistoryStore(paths)
    try:
        with repository.exclusive_workspace_operation():
            repository.initialize()
            units = _select_history_candidates(repository, policy)
            if not units:
                return _write_summary(
                    paths,
                    plan_id=None,
                    units=(),
                    fallback_reason="no_eligible_candidates",
                )
            plan = HistoryPlan.create(policy, list(units))
            plan_path = store.write_plan(plan)
            try:
                repository.create_history_plan(
                    plan,
                    plan_path.relative_to(paths.root),
                )
            except RepositoryError as error:
                try:
                    store.remove_plan(plan_path)
                except OSError as cleanup_error:
                    raise HistoryError(
                        "history cleanup plan binding failed and cleanup was incomplete"
                    ) from cleanup_error
                raise HistoryError("history cleanup planning failed safely") from error
            try:
                return _write_summary(
                    paths,
                    plan_id=plan.id,
                    units=plan.units,
                )
            except OSError as error:
                raise HistoryError(
                    "history cleanup report publication failed safely"
                ) from error
    except RepositoryError as error:
        raise HistoryError("history cleanup planning failed safely") from error
    except (OSError, TypeError, ValueError) as error:
        raise HistoryError("managed history candidate or plan is unsafe") from error


def _parse_history_policy(payload: Any) -> HistoryPolicy:
    if not isinstance(payload, dict) or set(payload) != _POLICY_FIELDS:
        raise HistoryError("history policy must use the fixed v1 schema")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise HistoryError("history policy must use version 1")
    minimum_age_days = payload["minimum_age_days"]
    keep_latest = payload["keep_latest_text_snapshots_per_source"]
    retained_states = (
        payload["retain_included_vision_analyses"],
        payload["retain_pending_review_vision_analyses"],
        payload["retain_incomplete_vision_requests"],
    )
    if (
        type(minimum_age_days) is not int
        or minimum_age_days < 0
        or minimum_age_days > _MAX_MINIMUM_AGE_DAYS
        or type(keep_latest) is not int
        or keep_latest < 1
        or keep_latest > _MAX_SQLITE_INTEGER
        or any(type(value) is not bool for value in retained_states)
    ):
        raise HistoryError("history policy contains invalid values")
    if not all(retained_states):
        raise HistoryError("history policy must retain mandatory workflow states")
    return HistoryPolicy(
        minimum_age_days=minimum_age_days,
        keep_latest_text_snapshots_per_source=keep_latest,
        retain_included_vision_analyses=retained_states[0],
        retain_pending_review_vision_analyses=retained_states[1],
        retain_incomplete_vision_requests=retained_states[2],
    )


def _select_history_candidates(
    repository: SQLiteRepository,
    policy: HistoryPolicy,
) -> tuple[HistoryUnit, ...]:
    oldest_datetime = datetime.min.replace(tzinfo=timezone.utc)
    maximum_elapsed_days = (datetime.now(timezone.utc) - oldest_datetime).days
    if policy.minimum_age_days > maximum_elapsed_days:
        return ()
    return tuple(repository.history_candidates(policy))


def _write_summary(
    paths: WorkspacePaths,
    *,
    plan_id: str | None,
    units: tuple[HistoryUnit, ...],
    fallback_reason: str | None = None,
) -> HistoryPlanResult:
    kind_counts = Counter(unit.kind for unit in units)
    reason_counts = Counter(unit.reason for unit in units)
    if fallback_reason is not None:
        reason_counts[fallback_reason] += 1
    kinds = tuple(sorted(kind_counts))
    reasons = tuple(sorted(reason_counts))
    candidate_count = len(units)
    total_bytes = sum(unit.size for unit in units)
    lines = [
        "# Managed History Cleanup Plan",
        "",
        f"Plan ID: {plan_id or 'none'}",
        f"Candidates: {candidate_count}",
        f"Bytes: {total_bytes}",
        "Kinds:",
    ]
    lines.extend(
        [f"- {kind}: {kind_counts[kind]}" for kind in kinds]
        or ["- none: 0"]
    )
    lines.append("Reasons:")
    lines.extend(
        [f"- {reason}: {reason_counts[reason]}" for reason in reasons]
        or ["- none: 0"]
    )
    report_name = (
        f"history-plan-{plan_id}-report.md"
        if plan_id is not None
        else f"history-plan-{fallback_reason}-report.md"
    )
    report_path = write_managed_text(
        paths.reviews_dir / report_name,
        "\n".join(lines) + "\n",
        overwrite=plan_id is None,
    )
    return HistoryPlanResult(
        plan_id=plan_id,
        candidate_count=candidate_count,
        total_bytes=total_bytes,
        kinds=kinds,
        reasons=reasons,
        report_path=report_path,
    )


def _policy_payload(policy: HistoryPolicy) -> dict[str, object]:
    return {
        "minimum_age_days": policy.minimum_age_days,
        "keep_latest_text_snapshots_per_source": (
            policy.keep_latest_text_snapshots_per_source
        ),
        "retain_included_vision_analyses": (
            policy.retain_included_vision_analyses
        ),
        "retain_pending_review_vision_analyses": (
            policy.retain_pending_review_vision_analyses
        ),
        "retain_incomplete_vision_requests": (
            policy.retain_incomplete_vision_requests
        ),
    }


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise _DuplicatePolicyFieldError
        payload[key] = value
    return payload
