<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Atomic Reservation Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Preserve the user's selected sequential goose execution; no automatic rereview loop.

**Goal:** Reject corrupt pre-publication authority without committing reservation or active-accounting changes, while retaining claim-first exclusion.

**Architecture:** Extract existing reservation/dependency SQL into connection-taking helpers. A narrow repository context manager holds the existing BEGIN IMMEDIATE transaction while the application performs read-only verification. Commit before exchange publication; preserve the existing publication/recovery and receipt protocols.

**Tech Stack:** Python >=3.11, stdlib SQLite/contextlib, existing pytest environment; no installation.

**Spec:** `docs/superpowers/specs/2026-09-25-atomic-reservation-validation-design.md` — user approved 2026-09-25 20:07. Plan review still pending.

## Global Constraints

- Work only in `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-0.2.2-cycle-01`; expected HEAD `15ca55093cf7bcaed1182bc08af0d1e38d2a6f0e`.
- No Git writes, staging, commits, resets, stashes, merges, pushes, installs, real-data access, home pilot or deployment. Keep existing WIP and evidence; override skill defaults to commit/cleanup.
- Keep canonical R1 receipt accounting and schema. Permit only the approved narrow repository transaction/dependency extraction and lifecycle changes.
- No ambient/global/thread-local transaction reuse, compensation writes, new mutex or new persistent transaction framework.
- Partial verification must not regain per-call full generation scans. Legacy retains its existing full verification.
- Budget-stop commits its intended pause/repair; validation exceptions before commit roll back. Commit ambiguity is not rollback success.
- No exchange publish/cleanup/result/receipt writes inside pre-publication validation. SQLite/filesystem publication is not one atomic transaction.
- Failed first-next source validation now preserves prepared/ready rather than leaving a new lock. Existing leases are restored, not deleted. Preserve post-publication behavior.
- This amendment is not prerequisite completion or Task11/12 implementation. E remains NEEDS_WORK until all original acceptance gaps are resolved.

## Review Focus

1. Transaction body raises after active-time charge: counters, aggregate and original monotonic timestamp must roll back (Tasks1–2).
2. Budget outcome is None: must commit pause before cleanup, not raise inside the context and accidentally undo it (Task2).
3. Second contender begins while first is verifying: no external reads before it owns a valid reservation; rollback permits subsequent progress (Task3).
4. Validation path accidentally opens a nested writer or reads dependencies through another connection: use explicit same-transaction data and integration guards (Tasks1–2).
5. Process death or post-commit ambiguity: never report success or compensate; preserve a recoverable state and managed files (Task3).

## File ownership / commands

Modify `src/portfolio_maker/infrastructure/sqlite_repository.py` (reservation transaction only), `src/portfolio_maker/application/semantic_lifecycle_v3.py` (pre-publication control flow/dependencies), `tests/test_semantic_lifecycle_v3.py`, and `tests/test_semantic_scale_v3.py` (lock duration observations). Create `tests/test_semantic_atomic_reservation.py` for isolated transaction/concurrency/restart cases. Update the existing prerequisite progress ledger and review report; do not rewrite old logs.

All test commands run from the development worktree:
```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests ../../.venv/bin/python3 -m pytest -q -p no:cacheprovider tests/test_semantic_atomic_reservation.py
```
Log directory: `.superpowers/sdd/2026-09-25-authenticated-bounded-read/`; prefix new logs `atomic-`. Before edits write an additive `atomic-baseline.json` and `atomic-baseline.patch`, recording current src/test hashes, both HEAD/stash lists and both checkout existing-file hashes. Do not replace prerequisite baselines or followup candidates.

### Task 1: Transaction-scoped reservation and dependencies

**Files:** repository module; new atomic test module.

**Interfaces (exact new names):**
```python
# Methods on SQLiteRepository; the public immediate-commit method stays available.
def _reserve_semantic_v3_unit_in_connection(
    self, conn: sqlite3.Connection, run_id: str, lease_nonce: str,
    *, expected_authority_root: str,
) -> SemanticV3LeaseReservation | None: ...

def _semantic_v3_unit_dependencies_in_connection(
    self, conn: sqlite3.Connection, run_id: str, unit_id: str, expected_root: str,
) -> tuple[tuple[str, str], ...]: ...

@contextmanager
def semantic_v3_reservation_transaction(
    self, run_id: str, lease_nonce: str, *, expected_authority_root: str,
) -> Iterator[tuple[SemanticV3LeaseReservation | None, tuple[tuple[str, str], ...]]]: ...
```
Ellipses here denote interface signatures only. The body is specified by extraction below, not a stub.

- [ ] **1. Snapshot pre-edit state and read exact reservation/dependency implementations.** Read `_connection` and `_semantic_v3_active_lease_reservation`; retain existing exception translation and ambiguous-commit behavior. Existing `initialized_repository()` fixture already claims running, so do NOT use it for a first-reservation test: use `_prepared_frontier_run` plus actual approval.
- [ ] **2. Write rollback and commit tests.** New module imports `_prepared_frontier_run`, `_lifecycle_snapshot`, approval/next models and functions from `test_semantic_lifecycle_v3`. Test first reservation using the real prepared fixture:
```python
repository, prepared = _prepared_frontier_run(workspace)
approve_semantic_execution(ApproveSemanticExecutionRequest(
    workspace, prepared.revision_id, prepared.proposal_sha256))
authority = repository.load_semantic_v3_execution_authority(prepared.run_id)
before = _lifecycle_snapshot(repository, prepared.run_id, prepared.revision_id)
with pytest.raises(RuntimeError, match="validation failed"):
    with repository.semantic_v3_reservation_transaction(
        prepared.run_id, "atomic-test-nonce",
        expected_authority_root=authority.proposal.authority_root,
    ) as (reservation, dependencies):
        assert reservation is not None and not reservation.issued
        assert isinstance(dependencies, tuple)
        raise RuntimeError("validation failed")
assert _lifecycle_snapshot(repository, prepared.run_id, prepared.revision_id) == before
```
Add normal-exit case: after context exit, execution lock exists and matches yielded run/unit/nonce. Add issued-replay rollback: issue a real input first, advance the existing injectable monotonic clock, snapshot, then raise in context and compare all rows. Never compute expected rollback counters using production accounting helpers.
- [ ] **3. Run new module and retain RED.** Expected missing-context-manager failure. Existing consumed-root RED is separate; do not claim it is this test's failure.
- [ ] **4. Extract, do not redesign, SQL.** Move the existing body of `reserve_next_semantic_v3_unit` currently under `with self._connection()` into `_reserve_semantic_v3_unit_in_connection`, preserving statement order, all None budget returns, active lease helper, canonical authority checks and errors. Public wrapper retains its input validation, `_connection`, and IntegrityError handling. Both entry points invoke the same helper.
- [ ] **5. Extract dependency verification.** Move SELECT LIMIT65, digest validation and dependency-root validation into `_semantic_v3_unit_dependencies_in_connection`. Existing public dependency method delegates inside `_read_connection`. New context uses its own open write connection and returns `(reservation, dependencies)`; for None and issued reservation yield empty dependencies (no materialization consumes them). Do not add dependency requirements to legacy immediate-commit reservation callers.
- [ ] **6. Implement narrow context body.** Validate nonce/root exactly as existing API, enter `_connection`, call the reservation helper, fetch dependencies for a non-issued reservation, then yield the tuple. Let exceptions propagate through `_connection` rollback. Translate SQLite IntegrityError consistently with the existing wrapper; never catch and convert validation exceptions to successful context exit.
- [ ] **7. Run new tests and existing repository/lifecycle reservation tests.** Whole lifecycle still contains the two known consumed-root RED cases until Task2; record those as expected open failures rather than hiding them. Verify the old public wrapper retains its original semantics. Save diff and logs, no commit.

### Task 2: Integrate pre-publication validation inside the transaction

**Files:** lifecycle module; lifecycle and atomic tests.
**Consumes:** Task1 context yields `(reservation, dependencies)`.
**Produces:** next commits only after successful read-only validation; validate path retains existing behavior.

- [ ] **1. Re-run existing four consumed-root tests before editing.**
```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests ../../.venv/bin/python3 -m pytest -q -p no:cacheprovider tests/test_semantic_lifecycle_v3.py -k used_authenticated_root_corruption
```
Expected current 2 RED/2 GREEN. Preserve complete logs.
- [ ] **2. Add failures after the first authority check.** Parameterize first-next materialization failure on call1/call2. Wrap the real `_materialize_bounded_v3_input`, invoke it, and on the selected call raise `SemanticLifecycleV3Error('synthetic validation failure')`. Compare `_lifecycle_snapshot` and pre/post workspace non-DB file maps, including added/deleted files, as in consumed-root tests. Assert the selected call was reached.
- [ ] **3. Add real non-root corruption case.** In a prepared fixture open manifest and authenticated index, use the reader record hook only to observe actual visited record offsets while obtaining summary and the queued target's node entry. Choose an accessed node/value frame not on the summary path, flip one payload byte without changing size. Assert a subsequent summary query still succeeds and node query fails; then `next` must fail with unchanged DB/files. If the small fixture shares a leaf, construct a larger synthetic fixture until summary and target occupy separate leaves; do not skip or replace this case with root corruption.
- [ ] **4. Add issued-input corruption rollback case.** Issue input, modify its bytes, snapshot after the deliberate corruption, call next again, assert rejection and exact DB/files equality. Preserve the corrupted input as evidence; no cleanup.
- [ ] **5. Update only the explicitly superseded source-drift expectation.** Existing `test_next_rejects_current_source_drift_without_publishing_exchange_input` changes its old `lock is not None` assertion to full before/after DB snapshot equality and no new input/output. Add a direct current-file drift case without mutating unit authority so actual source verification is exercised. Leave post-publication/receipt failure tests unchanged.
- [ ] **6. Add explicit dependencies to the materializer.** Signature gains keyword-only `dependencies: tuple[tuple[str,str], ...] | None = None`. When None use the existing public dependency read (validate call at present line426 needs this); when explicitly empty tuple use it as-is. Next must pass the tuple from Task1 on both calls. Do not use truthiness fallback for an empty tuple. Do not change payload schema or dependency-root computation.
- [ ] **7. Move only the read-only stage.** Use this control-flow shape with existing error translations and existing publish code:
```python
binding = None
payload = None
with repository.semantic_v3_reservation_transaction(
    run_id, uuid4().hex, expected_authority_root=authority.proposal.authority_root,
) as (reservation, dependencies):
    if reservation is not None:
        _validate_execution_authority(paths, authority)
        if reservation.issued:
            binding = exchange.replay_input(
                reservation.run_id, reservation.lease_nonce,
                _required_digest(reservation.input_sha256),
                _required_nonnegative_int(reservation.input_bytes))
        else:
            payload = _materialize_bounded_v3_input(
                paths, authority, reservation, dependencies=dependencies)
            _validate_execution_authority(paths, authority)
            checked = _materialize_bounded_v3_input(
                paths, authority, reservation, dependencies=dependencies)
            if semantic_canonical_json_bytes(checked) != semantic_canonical_json_bytes(payload):
                raise SemanticLifecycleV3Error("semantic execution authority is stale")
# Context has committed, or raised. Only here handle None cleanup/error,
# issued return, or the existing revalidate/publication/issue protocol.
```
Retain the existing special source-authority-to-stale translation around each materialization. Do not add a return or budget exception inside the context. `None` remains a committed budget-stop, followed by existing cleanup. Issued result is constructed/returned after confirmed context exit. Errors at commit must escape before either action.
- [ ] **8. Audit all repository calls in that stage.** Pre-context initialize/load authority remain outside. Inside: only Task1 helper/dependency reads on its conn; application authority opens manifest/approval/auth or legacy catalog and structural pack; materializer reads source and given dependencies; replay_input is read-only. Publication-expiry check, abandon-for-budget and issue-input writes all remain outside. Add a guard test monkeypatching public `semantic_v3_unit_dependencies` to raise on a normal next: next should still succeed, demonstrating no fallback dependency connection in either materialization. Existing validate tests must still succeed with the fallback.
- [ ] **9. Run all lifecycle tests, atomic tests and repository suite.** Existing tests hooking `reserve_next_semantic_v3_unit` to inject a reservation-time event must be retargeted to the new context/private helper at the equivalent stage, retaining assertions and effects. Do not delete old assertions merely because they no longer fire. Clock/pause, R1 older receipt replay and corruption cases must remain GREEN. Log intentional source-drift semantic change separately.

### Task 3: Concurrency, crashes, ambiguous commit and resource evidence

**Files:** atomic tests; scale test instrumentation; ledger/report. Production changes only for defects reproduced here within this approved boundary.

- [ ] **1. Add deterministic two-contender tests.** Use two prepared and approved runs, actual SQLite connections, `threading.Event` and `ThreadPoolExecutor`. Wrap first run's `_validate_execution_authority` to signal `entered` and wait on `release` with timeout; keep the real verifier. Wrap `_connection` in the second thread to signal `attempted` before delegating to the real context; no mocked reservation decisions. After both signals, release the first. On success, second must report busy, remain prepared, and have zero calls to external authority/materialization/replay hooks for its run. Parameterize first-run synthetic validation failure: second may then succeed, first DB snapshot is restored, and first has no exchange output. Always release events in finally; finite future timeouts prevent hanging tests.
- [ ] **2. Add writer timeout test.** On a dedicated contender connection use a short test-only SQLite busy timeout, hold the first transaction via events, and assert the contender terminates with a controlled lifecycle/repository error and no external reads. Production timeout/retry configuration remains unchanged. Release the holder and confirm subsequent valid progress; never loop indefinitely on locked database.
- [ ] **3. Add synthetic process-death test before commit.** Parent prepares/approves synthetic workspace and records DB/files. Child launched with `sys.executable -c` and PYTHONPATH uses the real reservation context, writes a flushed READY line to stdout from inside its yielded body, then blocks on stdin. Parent waits for READY with a bounded reader/future timeout, terminates/kills child and waits. Reopen repository in another process and assert no new committed lease/lock/counters and successful next. No source preparation in `-c` (avoids crawler spawn/import errors). Keep control signals on pipes rather than workspace files.
- [ ] **4. Add post-commit process-death comparison.** Child exits the reservation context normally before READY, then blocks. After termination assert a materializing lease exists; next in a fresh process resumes using the same nonce and publishes input. Distinguish this legitimate committed state from Task3 step3 rollback. No compensating writes or false DB/filesystem-atomicity assertion.
- [ ] **5. Test commit ambiguity at the actual boundary.** Wrap `_connection` only for the reservation call: delegate through the real context and allow real commit, then raise `CommitOutcomeAmbiguousError('synthetic post-commit uncertainty')`. Next must raise a controlled error, never return input success, and leave pre-existing exchange bytes unchanged. Assert committed reservation may exist, not rollback. Repeat with issued replay to ensure no return occurs inside the context. Do not induce ambiguity by monkeypatching whole next or reserve to skip SQL. Existing `_connection` identity/commit tests remain authoritative for low-level ambiguity classification.
- [ ] **6. Pin budget semantics.** Run existing `test_corrupt_durable_active_time_pauses_and_releases_without_rollback`, `test_invalid_active_clock_fails_closed_and_releases_the_lease`, `test_issued_lease_deadline_pauses_and_removes_exchange_slots`, total-budget and exchange-byte-budget tests. Add an external-read trap around a known None budget outcome; pause must persist and cleanup happen only after commit. Failure validation must restore original monotonic fields, while subsequent successful replay follows existing clock tests.
- [ ] **7. Measure actual transaction occupancy in expanded E.** Wrap `semantic_v3_reservation_transaction` with a contextmanager calling the real one. Capture outer start-to-exit (includes lock wait), and yield-entry-to-resume/body time separately using `perf_counter`; record per-operation counts/max/sum and failures. Do not label body time as exact lock-held time: it excludes transaction setup/commit. Keep existing proof/node/row/full-scan/result-prefix/audit observations. Run existing exact1k/10k two-lane E in fresh child processes. For a synthetic extension-free legacy fixture use a pre-approval historical build fixture, not deletion from an approved manifest; until available explicitly mark legacy lock timing unmeasured and keep the acceptance open.
- [ ] **8. Run final verification without hiding failures.**
```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests ../../.venv/bin/python3 -m pytest -q -p no:cacheprovider
PM_SEMANTIC_EARLY_SCALE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests ../../.venv/bin/python3 -m pytest -q -s -p no:cacheprovider tests/test_semantic_scale_v3.py
git diff --check
```
Save complete logs and exact exit codes. If a concurrency/pause contract cannot hold with longer writer occupancy, report NEEDS_WORK and evidence rather than increasing deadlines or silently weakening the contract.
- [ ] **9. Preserve and hand off.** Hash current changed/new src/tests and additive diff; verify both checkout HEAD/stash values, root existing files and all pre-existing evidence against atomic baseline. Inspect repository diff specifically for canonical receipt/schema changes: none permitted. Record intentional repository transaction extraction, source-drift semantic change and all remaining gaps in original ledger/report. No automatic rereview loop, no commits. The existing independent review is not approval of this new candidate.

## Traceability and self-review

- Goal/transaction ordering and same-conn dependencies: Tasks1–2.
- Budget/time/ambiguous commit: Tasks1–3; explicit None outcome and post-context return.
- Concurrent first/same lease, crash/restart: Task3; prior lease rollback also Task1.
- Legacy full behavior: unchanged dispatch; timing requires a real pre-approval fixture, otherwise explicitly open.
- Receipt/schema invariants, performance and preservation: Tasks2–3.
- Reviewed interfaces match: context yields tuple, no raw connection escapes; dependency fallback retained for validate, never next. No new domain type/schema needed.
- Current separate prerequisite gaps are not silently closed by this plan. In particular true legacy fixture and audit closure parity remain required for overall acceptance.

## Execution handoff

Written specification approved. This implementation plan awaits review. Preserve already chosen goose sequential implementation in the existing worktree. After plan approval, create the additive baseline, perform Tasks1–3 under TDD, and report evidence with honest remaining NEEDS_WORK items. Do not request the execution-method selection again.
