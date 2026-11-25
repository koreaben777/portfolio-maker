<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Portfolio Maker Semantic Crawl Timeout Budget TDD Plan

> Task 13 bounds cumulative blocked-directory waiting. It does not auto-exclude
> sources or change the successful protocol-v2 semantic lifecycle.

## Step 1: Freeze the operational and private-review contract

Add the timeout-budget request, result, CLI parser, workspace review path, and
documentation contract. Use a `120.0` second default, `3600.0` maximum, and
`0.01` minimum. Verify invalid direct and CLI values fail before workspace
creation. Add exact-schema tests for canonical self-hashed private review data
before implementing any writer.

## Step 2: Prove the crawler budget RED cases

With an injected monotonic clock and blocking synthetic enumerator, add failing
tests for repeated timeouts, successful calls that consume no budget, a final
request shortened to remaining budget, exhaustion without replacement or sibling
traversal, and timeout/interruption cleanup with no active child. Keep existing
per-directory timeout and worker reuse tests unchanged.

## Step 3: Implement the bounded crawler

Thread the budget into `crawl_local_structure`. A timeout consumes only its
measured monotonic wait; a nonfinal timeout reaps then replaces the reusable
worker, while an exhausted budget reaps without replacement and raises a typed,
non-disclosing error carrying private relative hierarchies only.

## Step 4: Fail closed and publish the governance review

Before revision/database/public semantic publication, catch aggregate exhaustion
and atomically write the local timeout review. Assert that this is the only new
managed result and that normal semantic manifests, work plans, reports, chunks,
and database are absent. Reject malformed, symlinked, duplicate-key, stale, and
root-mismatched reviews. A responsive later prepare must atomically write a
resolved record with no reused hierarchy entries. Prepare the resolved text
before publication, write it before the semantic input manifest, and restore
the exact prior review bytes whenever analysis or any publish step fails.

## Step 5: Wire the CLI and governance instructions

Add the CLI override and safe aggregate presentation. Update source governance
and router instructions to read only the private review, propose common-ancestor
exclusions, and wait for explicit approval before policy mutation or rerun.
Update README and development principles with the same operational boundary.

## Step 6: Verify the complete boundary

Run crawler/application/CLI/plugin focused tests and the full Python suite;
then all relevant help surfaces, sdist/wheel plus fresh supported-Python
install, documentation checks, privacy scan, diff checks, and scope/status
audit. The web surface is untouched, so no Vite build is required.
