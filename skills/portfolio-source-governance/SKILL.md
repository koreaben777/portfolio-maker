---
name: portfolio-source-governance
description: Use when configuring Portfolio Maker scan roots, excluded folders, GitHub source permissions, or artifact evidence policy.
---

# Portfolio Source Governance

Use this skill before Portfolio Maker discovery or semantic indexing. It establishes the user-approved scan scope and policy inputs; it does not decide project meaning or grant artifact or deployment permission.

## Confirm scope before reading

Stop and ask for explicit confirmation of all of the following before listing, reading, crawling, or indexing source files:

- the exact scan root(s), including whether a home or parent directory is in scope;
- excluded local directories and excluded filename patterns;
- excluded GitHub repositories and, if used, the canonical allowed repositories;
- whether the user explicitly opts in to private GitHub discovery.

Treat "read first and exclude later," "analyze my home," and "use private GitHub as much as possible" as insufficient approval. Do not broaden a workspace root to a home or parent root. Do not inspect `.env` files, credential stores, private keys, browser profiles, password exports, or credential values.

The Portfolio Maker-managed `.portfolio-maker/` directory and unsafe filesystem entries remain hard boundaries. If the root or exclusions change, stop, reconfirm them, and regenerate the policy inputs and hash before continuing.

## Configure approved policy

After scope confirmation, use the existing approval and discovery flow from the repository root:

1. Create the source-policy template when needed:

   ```bash
   portfolio-maker approve --workspace . --write-sample
   ```

2. Ensure `.portfolio-maker/reviews/source-approval.json` records the confirmed `excluded_directories`, `forbidden_paths`, `excluded_file_patterns`, `excluded_repositories`, `allowed_repositories`, and `private_sources_allowed` values. Keep activity approval lists empty until discovery presents exact URLs.

3. Run discovery only within the confirmed scope:

   ```bash
   portfolio-maker discover --workspace .
   ```

   Review `.portfolio-maker/reviews/discovery-report.md` before selecting evidence. Approve only exact, current activity URLs in the matching public or private approval list.

4. Keep `private_sources_allowed` false unless the user explicitly opts in. Private discovery also requires the approved repository allowlist and the tool's own authentication check. Never infer private permission from an authenticated session, repository visibility, or a vague request. Do not inspect credentials to satisfy this gate.

5. Create and review the artifact policy separately:

   ```bash
   portfolio-maker approve --workspace . --write-sample-artifact-policy
   ```

   Review `.portfolio-maker/reviews/artifact-approval.json` and its per-artifact exclusions and delivery scope. Do not treat the sample policy as approval of every source.

## Authority boundaries

- **Index authority:** confirmed root, exclusions, current source policy, and its policy hash authorize what `prepare-semantic-index` may analyze. Index inclusion is analysis input only.
- **Evidence authority:** source approval plus exact approved GitHub activity URLs determine which discovered items can become evidence. A semantic-index node is not evidence approval.
- **Artifact authority:** `artifact-approval.json` determines per-artifact inclusion and exclusions. Evidence approval is not artifact inclusion.
- **Deployment authority:** `restricted` is the default delivery scope. `open_public` and any public hosting require separate explicit approval and validation. Artifact filenames containing `public` do not grant public deployment permission.

Only after the root, exclusions, source policy, and current policy hash are confirmed may the next workflow invoke the existing `prepare-semantic-index` command. Never use that command to bypass source approval or to infer evidence, artifact, or deployment permission.
