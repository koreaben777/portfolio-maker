---
name: portfolio-semantic-index
description: Use when building, refreshing, diagnosing, or applying Portfolio Maker hierarchical semantic index revisions.
---

# Portfolio Semantic Index

Use this skill only for the `codex-semantic-v2` managed semantic-index
workflow. Semantic analysis is not evidence approval, artifact inclusion,
project approval, delivery, or deployment approval. Each managed work unit is
bounded to 262,144 bytes, with no global count cap.

## Workflow

1. Prepare the current approved source scope:

   ```text
   portfolio-maker prepare-semantic-index --workspace . --root <approved-root>
   ```

   Report the preparation estimate exactly as safe counts: `Work units` and
   `Managed input bytes`. Do not expose source or managed locators.

2. Inspect the managed lifecycle state:

   ```text
   portfolio-maker semantic-index-status --workspace . --format json
   ```

   Report ready, receipted, invalid, and complete state together with the
   preparation estimate.

3. **WAIT FOR EXPLICIT USER APPROVAL** before requesting, reading, or writing
   any work unit. The approval does not grant source-policy approval, artifact
   inclusion, project approval, delivery, publish, deploy, Sites, Git, or
   release approval. It authorizes only this managed semantic work loop.

4. After approval, request exactly one returned ready unit at a time:

   ```text
   portfolio-maker next-semantic-work --workspace .
   ```

5. Read only the returned managed input. Preserve its revision, unit,
   dependency, assignment, signal, and child-result bindings. Write only its
   matching managed output and use the existing closed managed schema.
   **Do not read raw files** or inspect arbitrary source content.

6. Produce genuine Codex semantic judgment grounded in managed signal IDs and
   child-output hashes. Analyze semantic summaries, roles, topics, facts,
   purpose, component relationships, and independence or triviality.
   **Do not use token extraction as final meaning** and do not infer meaning
   from filenames. Never synthesize generic, template, placeholder, or
   token-only output merely to satisfy validation.

7. Validate the exact returned unit and require its receipt:

   ```text
   portfolio-maker validate-semantic-work --workspace . --unit-id <returned-unit-id>
   ```

   Validate every unit and require its receipt before requesting another.

8. Rerun status after every receipt and after interruption:

   ```text
   portfolio-maker semantic-index-status --workspace . --format json
   ```

   Resume from the next ready unit. Preserve valid receipts and never restart
   completed work blindly. Repeat steps 4-8 until status is complete.

9. Finalize only after status reports complete:

   ```text
   portfolio-maker finalize-semantic-index --workspace .
   ```

10. Apply only after finalization succeeds:

   ```text
   portfolio-maker apply-semantic-index --workspace .
   ```

11. Prepare the chunked safe review projection:

    ```text
    portfolio-maker prepare-project-review --workspace . --version v3
    ```

## Stop Conditions

Stop without bypass on missing or stale policy, invalid or missing receipt,
dependency invalidation, generic or ungrounded output, DB safety error,
no ready unit while status is incomplete or invalid, or user cancellation.
Report only safe status and counts. Never expose managed payload content,
locators, private URLs, credentials, or raw signal text.

Also stop on aggregate directory timeout budget exhaustion. Do not retry or
infer an exclusion; return to `$portfolio-source-governance` so it can request
explicit user approval for any proposed common-ancestor exclusion.

Never edit SQLite or managed receipts/manifests directly. Do not delete valid
receipts, invent a second output schema, skip validation, force completion, or
convert semantic completion into any later approval.
