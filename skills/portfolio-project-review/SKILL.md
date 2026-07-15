---
name: portfolio-project-review
description: Use when approving, automatically including, excluding, re-including, merging, splitting, or reassigning Portfolio Maker projects.
---

# Portfolio Project Review

Use this skill for project decision state only. Work from the approved project
review input and current persisted decision state. Project decisions do not
authorize source discovery, evidence approval, artifact inclusion, delivery,
or deployment.

## Mode Gate

- Require an explicit `automatic` mode before applying automatic decisions.
  Treat an omitted mode as `review`; do not infer automatic mode from words
  such as "all", "confirm", or "include".
- The CLI entry point is:

  ```text
  portfolio-maker compose-projects --mode automatic
  ```

- In `review` mode, leave non-manual candidates as `review_required`.

## Decision States

Apply the current decision engine and preserve its exact states:

| Condition | State or result |
| --- | --- |
| Explicit manual include | `manually_approved` |
| Allowed automatic high-confidence inclusion | `auto_included_high` |
| Allowed automatic medium-confidence inclusion | `auto_included_medium` |
| Low confidence, missing evidence, or a blocked/stale/conflicting candidate | `review_required` |
| Explicit manual exclusion | `excluded` |

In automatic mode, high and medium candidates are included only when the
engine permits inclusion. Medium inclusion remains review-recommended when
counter-signals are present. Low-confidence candidates remain
`review_required`; never confirm them automatically. A persisted `inactive`
row is not an active approval and must not be treated as included.

## Manual Precedence and Reversibility

- An explicit manual include, exclude, or review decision takes precedence
  over later automatic composition when the boundary and persisted identity
  are unchanged.
- Exclusion is a reversible project-state change, not deletion. Use the
  existing state command:

  ```text
  portfolio-maker set-project-state --project-id <project-id> --state excluded
  portfolio-maker set-project-state --project-id <project-id> --state included
  ```

- Use `--state excluded` for exclusion and `--state included` for explicit
  re-inclusion. Do not edit SQLite, JSON, active-index files, or source files
  directly.
- An excluded project may reappear only after an explicit re-inclusion or
  another explicit project-state change. Automatic re-analysis must not
  silently resurrect it. A changed boundary requires review rather than
  automatic propagation of the old exclusion.
- Verify the persisted result with `list-projects`, optionally filtering by
  `--decision-status` and using `--format ids`.

## Identity Changes

Merging, splitting, and reassigning change project identity or evidence
ownership. They require a review decision and persistent state/lineage before
materialization. Do not auto-merge duplicates, auto-split distinct outputs,
or silently reassign evidence based only on names, paths, or output shape.
When an identity change invalidates the prior boundary, leave the result for
review and do not carry an old exclusion across the new identity without an
explicit decision.

## Preservation and Authority Boundaries

- Exclusion must never delete source, evidence, or the semantic index. Do not
  delete or reindex those records as a side effect of a project decision, and
  do not delete derived metadata to hide a project.
- Automatic inclusion does not approve evidence, artifact contents, or
  deployment. Keep evidence approval and artifact approval as separate
  decisions.
- Keep delivery scope separate from project state. `restricted` and
  `open_public` are delivery choices, not consequences of automatic
  inclusion. Never grant deployment or public permission automatically;
  public delivery needs its own explicit approval.

## Final Check

Before reporting completion, confirm:

1. The mode was explicitly `automatic` if automatic inclusion was requested.
2. The current engine result uses only the states above, with low confidence
   and blocked candidates still in `review_required`.
3. Manual decisions remain authoritative and exclusions are persisted through
   `set-project-state`.
4. Merge/split/reassign decisions have review and persistent identity state.
5. Source, evidence, index, and derived metadata were preserved.
6. Evidence, artifacts, delivery scope, deployment, and public permission
   were not granted as a side effect.
