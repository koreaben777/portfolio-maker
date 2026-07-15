---
name: portfolio-maker
description: Use when starting, resuming, diagnosing, or completing an end-to-end Portfolio Maker workflow across sources, semantic indexing, project review, and artifacts.
---

# Portfolio Maker Router

Use `$portfolio-maker` as the routing-only entrypoint for a complete Portfolio
Maker workflow. The router must inspect workspace state and invoke the
specialized child skills by name; do not perform their work in this router.

## Routing Contract

Before each handoff, inspect only the current managed workspace state needed to
decide whether the next gate is satisfied. Do not read raw files, source
content, snapshots, databases, credentials, private URLs, or unapproved
review material from this router. Do not duplicate a child skill's schema,
policy, or validation rules; load and follow the named child skill instead.

Always route these stages in this order:

1. `$portfolio-source-governance` first. Confirm the exact approved scope,
   exclusions, source policy, and current policy state before discovery or
   indexing.
2. `$portfolio-semantic-index` next. Invoke it only when governance has
   produced current approved inputs for the semantic-index workflow.
3. `$portfolio-project-curation` next. Invoke it only with the current safe
   semantic review input, never with raw sources or arbitrary evidence.
4. `$portfolio-project-review` next. Preserve the explicit review mode. Pass
   `automatic` only when the user explicitly requested automatic decisions;
   otherwise use review/manual handling.
5. `$portfolio-artifacts` last. Invoke it only after current policy, evidence,
   project-review, and artifact-approval gates are satisfied.

If any required policy, managed input, approval, or hash is missing or stale,
conflicting, or rejected, stop at that stage and report the exact missing gate.
Do not infer approval from filenames, prior runs, authentication, a request for
"automatic" output, or the presence of a generated file. A zero-project or
all-unassigned state is valid: do not invent a project, claim populated output,
or silently promote review-required material.

## Modes And Commands

Preserve the user's explicit target (`master profile`, `portfolio draft`, or
`both`) and explicit command (`start`, `resume`, `diagnose`, or `complete`).
When a child skill defines the CLI command or payload contract, use that child
skill's command verbatim. The router does not create substitute schemas,
rewrite candidate or approval payloads, or edit SQLite, JSON, snapshots, or
source files directly.

Diagnosis is a gated status check, not permission to continue. A resume starts
with the current governance state and must revalidate every later input before
reuse. A complete request still ends after local artifact validation unless
the user separately authorizes delivery.

## Delivery Boundary

Generation, validation, delivery, and hosting are separate decisions. never auto-host, never auto-publish, never auto-commit, and never auto-push. Do not
return a synthetic deployment URL. Keep restricted output restricted and ask
for a separate explicit delivery choice after the child artifact workflow has
validated the local result.

The five child skills are the authorities for their own stage:

- `$portfolio-source-governance`
- `$portfolio-semantic-index`
- `$portfolio-project-curation`
- `$portfolio-project-review`
- `$portfolio-artifacts`

This router only orders and gates those invocations.
