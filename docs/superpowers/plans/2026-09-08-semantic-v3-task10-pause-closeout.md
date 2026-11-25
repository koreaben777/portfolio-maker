<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Semantic v3 Task 10 Pause Authority Closeout Implementation Plan

> 2026-09-24 운영 연결: [목적 중심 후속 계획](2026-09-24-portfolio-outcome-recovery.md)을 먼저 확인한다. 이 문서의 기능 계약과 과거 검증·종료 판정은 보존한다. 후속 계획은 receipt 교정, 조기 자원 검증, 작은 포트폴리오 수직 검증을 추가하며 구현·Git·release 승인이 아니다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for implementation and a separate independent reviewer for verification.
>
> Status: CYCLE_CLOSED_NEEDS_WORK, 2026-09-08. Pause race CLOSED; new receipt replay integrity finding T10-I1 OPEN. No commit or release authorization.

**Goal:** Close v3 Task 10's late-receipt pause race and independently verify the entire cumulative Task 10 candidate.

**Architecture:** Keep the existing lifecycle/SQLite/result-pack separation. Persist pause authority with the run; the receipt transaction, not a polling thread, decides whether an active lease may still commit. Preserve the last committed pack prefix, exact replay and additive v2 compatibility.

**Tech Stack:** Python 3.11+, SQLite, pytest, existing deterministic clock and synthetic workspace fixtures.

**Spec:** Development branch `docs/superpowers/specs/2026-08-02-portfolio-maker-home-scale-adaptive-semantic-frontier-design.md`, sections 10/14, plus the normative clarification below. The full original Task 10 acceptance matrix remains required.

## Global Constraints

Cycle outcome: the Developer and independent Review Team each obtained full 1699 passed.
The original pause race is closed, but changing only a stored receipt_sha256 still allows
successful application replay. Independent verdict is NEEDS_WORK (Important 1).
The Planner separately reproduced both outcomes. No second implementation fixback was started.
Checked steps below record completed cycle procedures, not Task 10 acceptance or a PASS verdict.
See ../../DEVELOPMENT_STATUS.md and the worktree's ignored task-10-2026-09-08-cycle-outcome.md.

- Existing worktree: `.worktrees/portfolio-maker-0.2.2-cycle-01`; branch `codex/portfolio-maker-0.2.2-cycle-01`; base HEAD `15ca55093cf7bcaed1182bc08af0d1e38d2a6f0e`.
- Preserve the cumulative seven-file WIP and all three existing stashes; no reset, stash application, cleanup or unrelated refactor.
- Planner owns documentation. Developer owns the code/test fix. Review Team owns independent read-only review.
- Exactly one focused fixback followed by independent cumulative re-review. A remaining/new material finding ends this cycle as NEEDS_WORK; do not silently start another fixback.
- Task 11–16, pilot/source exploration, dependency installation, version bump, commit/staging, push/merge/rebase/tag/release and deployment are excluded.
- Historical plan commit steps do not grant Git-write authority in this cycle.

## Normative pause contract

1. The ten-second receipt grace window starts when the active user-pause request is durably accepted, not after an unbounded worker finishes. At `now >= deadline`, an uncommitted result is no longer eligible for receipt.
2. Store the request deadline and sufficient clock-validity authority durably. Repeated pause requests must not extend the earliest accepted deadline.
3. After obtaining the transaction lock, receipt commit must check current run/lease/pause authority using current valid time. A check only before append, fsync, polling or lock acquisition is insufficient.
4. An expired worker must not create a receipt or advance unit/input/result counters. Lease expiry, ready restoration, slot release and checkpoint state use one transactional decision. Preserve the committed result prefix; recover only uncommitted tail.
5. A valid receipt inside the window may finish and publish the pause checkpoint. Exact replay of an already committed receipt remains idempotent and must not charge twice.
6. Losing the pause caller/process must not erase the deadline. Resume/recovery and repeated requests must honor existing authority. Invalid or incomparable clock state must fail closed and recover safely; never silently grant a fresh grace period.
7. Separate user-pause authority from the existing 300-second unit and phase/total budgets. Keep bounded accounting and avoid double charge on forced pause or restart.
8. Physical DB contention may delay a caller's response; it must never extend result eligibility. Test eligibility at lock acquisition separately from elapsed response/checkpoint latency and report any bounded-latency limitation.

## Files and responsibilities

The full candidate inventory is seven files:

- `src/portfolio_maker/adapters/cli.py`: existing v2/v3 route and safe output; preserve.
- `src/portfolio_maker/application/models.py`: existing lifecycle contracts; change only if the pause fix requires it.
- `src/portfolio_maker/application/semantic_lifecycle_v3.py`: validate/pause/resume orchestration and pack-tail cleanup.
- `src/portfolio_maker/infrastructure/semantic_exchange.py`: existing exchange cleanup; preserve or minimally adapt.
- `src/portfolio_maker/infrastructure/sqlite_repository.py`: additive schema, pause authority, receipt transaction, expiry/checkpoint and restart.
- `tests/test_cli.py`: privacy/compatibility regressions where affected.
- `tests/test_semantic_lifecycle_v3.py`: permanent clock/concurrency/recovery regressions using existing fixtures.

No production/test path outside this inventory may change without a new scope decision. Planner documentation changes are a separate inventory, not unexpected code scope drift.

## Task 10 closeout steps

- [x] **1. Capture identity and RED.** Confirm base/branch/diff hashes, no active duplicate owner, seven-file scope and stash identities. In `tests/test_semantic_lifecycle_v3.py`, use `_prepared_frontier_run`, `_write_valid_v3_exchange_output` and patched `SQLiteRepository._semantic_v3_monotonic_seconds` to recreate the previously observed sequence:
  `pause_requested_at=100; deadline=110; worker receipt lock/transaction entry=111`.
  Assert no new receipt and no used_units/input/result increment. Verify failure is the late receipt, not fixture setup.
- [x] **2. Add adversarial cases.** Permanently cover before/equal/after deadline, worker wins the transaction race, pause caller interruption/repository reopening, repeated pause without extension, legacy v3 rows without pause authority, invalid clock basis, exact replay, forced tail recovery and once-only accounting. Keep synthetic inputs private and isolated.
- [x] **3. Minimal GREEN.** Extend the existing additive schema and transaction checks to satisfy the normative contract. Reuse the existing lease expiry/checkpoint and pack-prefix recovery paths. Do not redesign frontier selection, cache or finalization.
- [x] **4. Verify the whole candidate.** Run the commands below, permanent adversarial tests, in-memory production compilation, CLI privacy, `git diff --check`, exact scope and stash checks. Record exact exit status/count and cumulative diff SHA-256.
- [x] **5. Developer report.** Write `.superpowers/sdd/2026-08-02-portfolio-maker-home-scale-adaptive-semantic-frontier/task-10-2026-09-08-developer-report.md` with RED/GREEN, contracts, commands, hashes and residual risks; stop edits and return READY_FOR_REVIEW.
- [x] **6. Independent Review Team.** Review the frozen cumulative seven-file diff against base, not only this fix. Reproduce the pause race and all original Task 10 axes. Record complete file coverage and fresh focused/affected/full verification. PASS requires no Critical/Important and no unresolved acceptance failure; otherwise NEEDS_WORK or BLOCKED.
- [x] **7. Planner closeout.** Compare hashes, actual artifacts and independent verdict. Update current status/progress without rewriting historical evidence. Task 10 can be accepted as a verified uncommitted candidate; Git integration remains separate.

## Source-pinned commands

Run from the development worktree using the existing root interpreter; do not install a worktree venv.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_semantic_lifecycle_v3.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_semantic_lifecycle_v3.py tests/test_semantic_work_lifecycle.py tests/test_semantic_index.py tests/test_sqlite_repository.py tests/test_cli.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/python -m pytest -q -p no:cacheprovider
git diff --check
```

The affected command is a minimum, not permission to omit other affected semantic/catalog/prepare/frontier/quality suites. The Developer and Review Team must report their complete affected inventory. No production user workspace or full-home pilot data may be used.
