<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Portfolio Maker Semantic Crawl Watchdog TDD Plan

> Task 12 is limited to the structural-crawl watchdog. It does not run a real
> home pilot or change evidence, semantic, artifact, project, or delivery policy.

## Contract

Implement the companion design addendum with `--directory-timeout-seconds`, a
`30.0` second default, a `300.0` second maximum, a reusable cancellable process
worker, safe timeout aggregates, and exact protocol-v2 manifest binding.

## Step 1: Lock the public and model contract

Add request/result fields and CLI/parser regressions first:

- reject bool, non-number, non-finite, non-positive, and excessive deadlines
  before crawl or managed writes;
- pass the normalized deadline into preparation;
- print and report only the deadline, timeout count, and unreadable-directory
  count alongside existing aggregates.

Run the focused model and CLI tests and retain the failing output before adding
production behavior.

## Step 2: Introduce the cancellable directory worker

Add deterministic crawler regressions using an injected blocking enumerator and
test process context. Prove:

- a blocked child directory returns within a bounded test deadline;
- the child is unreadable, descendants are absent, and a later sibling exists;
- exactly one timeout is counted;
- the timed-out worker is terminated/joined and its replacement is also joined;
- a root timeout raises the root-timeout error;
- crash, EOF, wrong request ID, wrong protocol, and malformed payload fail closed;
- caller interruption and normal success leave no worker alive.

Implement one reusable `spawn` worker. The worker performs only one immediate
directory materialization per request. Keep recursion, policy, ordering, node ID,
rename, symlink, hard-link, and exclusion decisions in the parent.

## Step 3: Integrate preparation and publication

Add application regressions before integration:

- child timeout produces a fresh revision with unreadable/timeout aggregates;
- root timeout produces no semantic revision, manifest, work plan, private
  structural/locator data, or report;
- worker internal failures become controlled `SemanticIndexError` values;
- a later responsive rerun is a normal fresh revision.

Only after these tests fail, pass the deadline into the crawler and populate the
new result aggregates.

## Step 4: Bind protocol-v2 lifecycle validation

Extend the exact input-manifest schema with:

```json
{
  "directory_timeout_seconds": 30.0,
  "timed_out_directory_count": 0,
  "unreadable_directory_count": 0
}
```

Add missing, extra, type, range, and changed-value tamper cases to status,
finalize, apply, and replay-focused tests. Update every shared exact-schema loader
instead of adding a permissive compatibility branch.

## Step 5: Verify packaging and safety

Run crawler, semantic prepare/work/finalize/apply, models, and CLI focused suites;
then the full Python suite. Verify every top-level and semantic help surface,
build sdist and wheel, install the wheel in a fresh temporary environment, and
run installed help.

Run adversarial cleanup probes for success, timeout, crash, malformed response,
and interruption. Audit stdout/report/manifest for locators, source names,
content, credentials, and semantic signals.

The web project is untouched, so retain the already reviewed Task 11 Vite result
and record that no web rebuild is required for this Python/docs-only change.

Finish with `git diff --check`, exact changed-file scope, `git show --check`, a
clean tracked worktree, an absent candidate `.venv`, and one focused child commit.
