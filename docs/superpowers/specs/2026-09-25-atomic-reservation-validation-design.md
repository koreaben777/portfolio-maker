<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Atomic reservation and validation — authenticated bounded-read amendment

Status: written specification approved by user on 2026-09-25 20:07 (+09:00). Direction and scope expansion were approved earlier the same day. Implementation-plan review is the next gate; no product changes made for this amendment.

## Goal and scope

Preserve claim-first exclusion while making failed pre-publication `next` authority validation leave no committed reservation/accounting changes. Applies to first issue, resumed materialization and issued-input replay. Keep authenticated proof verification, legacy authority checks, canonical R1 receipt accounting, and existing schema. This is an amendment to the authenticated bounded-read prerequisite, not Task11 caching or Task12 finalize/apply.

Evidence: `acceptance-consumed-root.log` contains four real corrupted-root tests: validation and receipt replay preserve state; first next and issued replay reject corruption but change DB state. Existing `reserve_next_semantic_v3_unit` commits before application validation. `_connection` already provides BEGIN IMMEDIATE, rollback-before-commit and explicit ambiguous-commit handling.

## Alternatives and decision

1. Validate before reservation: shortest change, but a losing concurrent run can read managed/source data. Rejected.
2. Commit then compensate on error: another process can observe the reservation; blindly restoring time/aggregate counters can erase legitimate concurrent work. Rejected.
3. Hold the existing SQLite write transaction through read-only validation, then commit: recommended. Losers cannot pass reservation authority before the transaction resolves; failed validation rolls back all provisional changes. Cost: a longer workspace writer lock, including source verification and legacy full reads. No claim of bounded end-to-end lock duration.

## Transaction boundary

Add a narrowly scoped repository reservation context manager, using the existing `_connection` implementation. Extract existing reservation SQL into a connection-taking private helper rather than duplicating its authority, budget, lease or accounting logic. Keep the current public `reserve_next_semantic_v3_unit` as a compatibility wrapper with its original immediate-commit behavior for existing callers. The lifecycle uses the new context manager.

Ordered flow:

1. Existing read-only execution-authority lookup remains. It is not permission to consume managed/source/exchange data.
2. Enter BEGIN IMMEDIATE. Revalidate canonical DB authority, singleton occupancy, unit, lease and budget using the existing reservation logic. All reservation and active-time changes are provisional.
3. Only after successful reservation, perform current external authority checks. For a new/materializing lease, perform both current bounded materializations and intervening authority recheck, and compare canonical results. For an issued lease, verify and read the existing input via read-only replay. No exchange publish, cleanup, result write, receipt write or separate repository write is allowed inside this stage.
4. Exit the context normally only after all required read-only validation succeeds. Commit provisional reservation/accounting changes. Do not return a success to the caller from inside the transaction context; first let context exit verify the commit outcome.
5. After a confirmed commit, preserve existing publication-expiry check, exchange publication, input issuance and budget cleanup protocol. This amendment does not claim atomicity between SQLite and filesystem publication.

Any pre-commit validation exception rolls back provisional DB changes and leaves exchange files unchanged. Rollback is transaction rollback, not counter compensation. A crash before commit relies on SQLite recovery; a crash after commit retains the existing recoverable materializing/issued lease protocol.

Dependencies needed for materialization must be validated/read using the same transaction connection and passed explicitly to the application materializer. Do not make the materializer open another writer, rely on uncommitted data through another connection, or introduce ambient/global/thread-local transaction reuse. The implementation plan must enumerate other repository calls on this path and remove nested write opportunities before running concurrency tests.

## Budget, time and commit semantics

If reservation returns the existing explicit budget-stop outcome, its existing pause/repair transaction is committed normally without external data reads; cleanup stays after confirmed commit. That outcome is not an authority-validation failure and is excluded from the unchanged-state assertion.

On authority failure, persisted active-time values are unchanged. Do not reset the old monotonic timestamp separately: a subsequent successful operation may charge elapsed time under the existing accounting rules. There is no new free-time exemption, receipt recalculation or schema change.

Do not swallow `CommitOutcomeAmbiguousError`, retry publication automatically, or treat post-commit path-identity errors as successful rollback. Retain existing fail-closed ambiguous-outcome behavior and preserve files for recovery. The unchanged-state guarantee applies to validation failures before commit, not ambiguous commit failures or mutations by other actors.

## Concurrency and compatibility

BEGIN IMMEDIATE is the serialization mechanism; no second mutex or persistent transaction framework is added. A waiting run may proceed after a failed contender rolls back; it has not lost ownership to a committed reservation. After a successful contender commits, another run must observe occupancy and fail before external reads. Existing SQLite busy/timeout error behavior remains, with no new infinite retry.

Legacy verification stays full and inside the same reservation validation boundary; it may hold the writer lock longer than the authenticated path. This cost is measured and reported, not removed by weakening legacy integrity.

The old source-drift assertion that a first failed materialization must leave a lock is superseded only for failure within the new pre-publication validation boundary: first-next drift now rolls back to prepared/ready. An existing issued/materializing lease is restored to its prior state, not deleted. Update that test with explicit pre/post state and file assertions; do not merely remove the lock assertion. Later publication-stage failure behavior remains unchanged.

R1 receipt authority, receipt schema, cumulative byte checks, history replay and validate commit path are unchanged. `sqlite_repository.py` may now receive the narrow transaction-boundary refactor explicitly authorized by this amendment; inherited R1 changes must remain intact.

## Acceptance

- Keep all four consumed-root tests; first next and issued replay must turn GREEN without weakening snapshots.
- Corrupt an accessed non-root record so successful root/summary checks cannot bypass later rollback coverage.
- First-next source drift, second-materialization failure and issued-input replay corruption: logical DB snapshot and managed/exchange files unchanged.
- Two real concurrent contenders: after winner success loser performs no external reads; after rollback a waiting valid contender may succeed. Use synchronization events, not sleep-only ordering assumptions.
- Existing lease/accounting values restored on failure; successful replay still uses the established active-time rules; budget-stop persists its intended pause transition.
- Synthetic subprocess termination before transaction commit followed by restart shows no partial reservation. Test after commit separately for recoverable lease behavior, without requiring filesystem/DB atomicity.
- Commit ambiguity retains existing error handling and never publishes success or deletes exchange evidence.
- No nested writer/deadlock: transaction-scoped dependency validation and materialization integration tests.
- Whole regression, dedicated R1/concurrency/pause-resume tests, expanded 1k/10k E. Record writer-lock occupancy and timeout consequences separately from per-tree proof bounds. Existing representative/audit/history costs remain visible.
- Diff checks, candidate hashes, both checkout HEAD/stash/root-file preservation; no Git writes/install/real-data/home access.

## Process and remaining limits

Use the existing isolated development worktree, sequential implementation and RED/GREEN evidence. Preserve all prior logs and failing tests. Written-spec review precedes an implementation-plan amendment; implementation begins only after that plan and execution method are approved. No automatic rereview loop.

This amendment resolves one identified ordering conflict. True legacy fixtures, full-tree/resource cases, cross-projection joins, audit closure parity, build memory evidence and overall E disposition remain separate open prerequisites. Current status remains NEEDS_WORK.
