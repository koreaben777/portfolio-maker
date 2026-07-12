---
name: team-based-review-loop
description: Run a one-cycle team-based implementation review, feedback delivery, fixback wait, and re-review workflow for Codex threads. Use when the user asks to review an implementation from another Codex thread with a reviewer team, send findings back to an implementation thread, require @codex-fable5-style fixes, preserve the same @ponytail and agency-router review roles, document both initial and follow-up reviews, or save the review loop as a reusable repo workflow.
---

# Team Based Review Loop

## Overview

Use this skill to coordinate an evidence-led review loop across Codex threads:
initial review, one consolidated implementation fixback, re-review, and final
documentation. One cycle is the default. Continued cycles need an explicit user
request and must converge by root cause rather than by serial edge-case patches.

## Workflow

1. Identify the target implementation thread and worktree.
   - Use Codex thread tools to locate the thread by title or id.
   - Record thread id, worktree path, branch, and HEAD.
   - Do not push unless the user explicitly asks.

2. Establish the review boundary and a finding ledger before spawning reviewers.
   - Read the authoritative contract, current diff/HEAD, prior review reports,
     and the previous implementation response.
   - Create a ledger entry for each known issue with: root-cause key, contract
     boundary, affected data path, pre-fix reproduction, severity, and status.
   - Collapse findings that differ only by input spelling, casing, Unicode form,
     parser entry point, storage representation, or test fixture into one
     root-cause family unless they cross a genuinely different trust boundary.
   - For a family with multiple representations, write an acceptance matrix
     before requesting implementation. The matrix must cover the whole known
     family, not only the first reproducer.

3. Run the initial review with the same four lanes.
   - `@ponytail`: over-implementation and deletion candidates.
   - `agency-router` / `codebase-onboarding`: logical flow review from input -> use case -> storage -> artifact.
   - `agency-router` / `technical-writer`: plan, README, skill, and artifact contract mismatch.
   - `agency-router` / `reality-checker`: validation status, evidence gaps, edge bugs, CLI exits, privacy boundaries, persistence, parsing, and idempotency.
   - Use subagents only when the user explicitly asks for them or has already authorized this loop.
   - Each lane must classify its result as one of: `novel finding`, `absorbed
     variant`, `documented residual`, or `PASS`. A lane must not turn a
     speculative edge case into a fix request.

4. Admit findings before reporting them.
   - Run the project's relevant test command.
   - Reproduce high-risk findings directly when cheap.
   - Keep secret values masked in output.
   - Separate "tests pass" from "ready"; passing tests do not close untested product or privacy gaps.
   - A finding is actionable only when all are true:
     - it fails on the current HEAD;
     - it is in the agreed review scope and contradicts a contract or user path;
     - it is not already covered by an open ledger family or its acceptance matrix;
     - it has a minimal pre-fix reproduction or a direct, observable impact.
   - Mark out-of-scope corruption, unsupported data formats, intentionally
     documented behavior, and unproven hardening ideas as residuals or
     non-findings. Do not send them as implementation requests.

5. Document the initial review.
   - Save a Markdown report under `docs/reviews/`.
   - Include target thread, worktree, branch/HEAD, role lanes, evidence checked, findings by severity, closed/non-issues, and next re-review checks.
   - Use absolute file paths in findings.

6. Send one consolidated fixback request to the implementation thread.
   - Tell the thread to read the review document.
   - Require `@codex-fable5` style execution: inspect first, add focused failing checks, make minimal fixes, verify with evidence, and self-review before claiming completion.
   - Send at most one request per root-cause family. Include the ledger key,
     acceptance matrix, and exact non-goals so the implementation does not
     overfit the initial reproducer or widen scope.
   - Do not mix P3 cleanup with P1/P2 safety or correctness work unless the
     cleanup is necessary for the same minimal fix. Record independent P3 work
     as follow-up debt by default.
   - Require the final implementation response to start with a stable marker such as `[TEAM_REVIEW_FIX_DONE]`.
   - Ask for changed files, commit/HEAD, verification commands, and residual risk.

7. Wait for completion, then re-review.
   - Poll the implementation thread until the marker appears or the thread blocks.
   - Re-run the same four review lanes with the same scope.
   - Re-run local verification and the previous reproductions.
   - Re-review the acceptance matrix first. A newly reported variant in an
     already-open family is `absorbed` unless it demonstrates that the matrix
     or the stated root cause was incomplete.
   - Do not silently expand into a second fixback cycle unless the user
     requested another loop.

8. Apply convergence rules before any additional fixback.
   - The default outcome after the re-review is `PASS`, `NEEDS WORK`, or
     `BLOCKED`.
   - P3 cleanup does not block `PASS` unless the user explicitly requires zero
     open feedback. Record it as debt with a concrete reason instead of
     repeatedly reopening implementation.
   - If a valid P1/P2 variant appears after an initial fix, issue one
     **family-closure request**: update the ledger root cause and acceptance
     matrix, then ask for one design-level minimal fix that covers all known
     representations.
   - Do not issue serial point fixes for the same family. If a family-closure
     fix still misses a new representation, require a written root-cause
     analysis and send one redesign request, not another narrow patch.
   - A claim of a new finding must name why it is not equivalent to an existing
     ledger family. Without that distinction, classify it as absorbed or a
     residual.

9. Document the re-review.
   - Save a second Markdown report under `docs/reviews/`.
   - Split findings into: closed, still open, newly found, and low-risk cleanup.
   - State whether the loop outcome is PASS, NEEDS WORK, or BLOCKED.

## Severity Rules

- P1: privacy/security leak, data loss, core MVP flow impossible, or direct contradiction of the MVP promise.
- P2: expected user path fails, plan contract drift, non-idempotent persistence, unsafe CLI behavior, or external failure breaking unrelated local work.
- P3: stale docs, over-specified tests, dead future scaffolding, small cleanup, or explicit MVP limitation that is documented but still worth tracking.

## Verification Budget

- The implementation thread runs focused regressions and one full suite for
  each fix commit.
- The review coordinator independently runs one focused suite and one full
  suite after each substantive fix commit. Review lanes reuse that evidence
  unless they need a distinct direct reproduction.
- Do not run a full suite separately in every reviewer lane, after report-only
  edits, or again solely for staging and publication.
- Keep a compact command/result ledger in the report so later cycles do not
  repeat equivalent checks without a changed contract boundary.

## Report Template

Use this compact structure:

```markdown
# Team Based Review Loop N - Initial/Re-Review Findings

Date:
Target thread:
Target worktree:
Branch / HEAD:
Status: PASS | NEEDS WORK | BLOCKED

## Evidence Checked
## Closed Findings
## Findings
## Ponytail Cleanup
## Next Minimal Checks
```

## Guardrails

- Keep reports in the user's language.
- Keep code identifiers and commands exactly as they appear.
- Never print secrets, tokens, private key material, or raw credential values.
- Never remote-push from the review loop unless explicitly requested.
- If the implementation thread changes files while review docs are being written, do not revert its changes; read the current state and continue.
- Preserve a root-cause ledger in every continued loop report. State which
  proposed variants were absorbed, deferred, or rejected and why.
