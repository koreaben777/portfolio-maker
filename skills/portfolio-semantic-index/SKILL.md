---
name: portfolio-semantic-index
description: Use when building, refreshing, diagnosing, or applying Portfolio Maker hierarchical semantic index revisions.
---

# Portfolio Semantic Index

Use this skill only for the semantic-index revision workflow. Semantic index inclusion is not evidence approval, artifact approval, or deployment approval.

## Workflow

1. Start from the approved source scope. Run the existing CLI command with the approved root:

   ```text
   portfolio-maker prepare-semantic-index --workspace . --root <approved-root>
   ```

   This creates the managed input manifest at `.portfolio-maker/reviews/semantic-index/input-manifest.json`, the matching `input/chunk-####.json` files, and the semantic-index report. Treat these generated manifest/chunks as the only semantic input.

2. Read only the managed manifest and input chunks. Process every node bottom-up: descendants before their parents. Process every node exactly once. The manifest `node_count` and all chunk contents define the complete set. Never impose a global cap, truncate the first N files, skip later chunks, or infer unread nodes from paths, names, or neighboring content.

3. Write one output chunk for each input chunk at the matching managed path `output/chunk-####.json`. Each output must contain `version`, `revision_id`, `input_sha256`, `nodes`, and `output_sha256`. Each node must contain only `node_id`, `semantic_summary`, `semantic_roles`, `topics`, `analysis_status`, and `child_node_ids`; preserve the input status and child IDs exactly.

4. For `unreadable`, `unsupported`, `partial`, or `failed` nodes, preserve the status. Do not invent a summary for an unreadable or unsupported node; an empty summary is valid for those statuses. Keep summaries, roles, and topics free of absolute or relative paths, file URLs, URLs, database names, credentials, and other raw locators.

5. Before applying, validate matching chunk names, SHA-256 values, revision/input hashes, unique IDs, exact node coverage, structural references, status preservation, and safe output text. Only then run:

   ```text
   portfolio-maker apply-semantic-index --workspace .
   ```

   This is the sole application boundary. Never edit SQLite, `portfolio.db`, or any database directly, and never use arbitrary raw-file exploration to fill gaps. If validation fails, leave the managed partial output for diagnosis and do not apply it.
