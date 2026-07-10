# Portfolio Maker Architecture Design

Date: 2026-07-09
Status: Approved historical architecture; implemented 0.1.0 MVP uses GitHub discovery only

This document records the approved architecture design and its implemented 0.1.0 MVP boundary.

## Planning Summary

This project will create a program that explores files owned by the user, such as local computer files and GitHub activity, and generates portfolio or job-preparation materials from that evidence.

The initial product form was narrowed from a general local app into a Codex-native local workflow:

- The initial architecture is **local app + CLI/engine**, but the first usable MVP will run primarily through the installed **Codex app on macOS**.
- The MVP starts with **Codex Skill + CLI Engine**.
- The architecture must keep enough separation to later support a deeper **Codex app-server companion** without rewriting the core engine.
- The priority order is:
  1. Build a career knowledge base from approved local files, with GitHub metadata available for discovery review.
  2. Generate evidence-based master profile and portfolio drafts.
  3. Add company-specific strategy and tailored job materials later.
- Initial data sources are **local files and GitHub**. Google Drive is explicitly deferred.
- GitHub scope includes repositories, commits, pull requests, issues, reviews, and Actions activity as discovery metadata; it does not enter snapshots, profiles, or portfolio drafts in the 0.1.0 MVP.
- Local discovery may scan the home directory for candidates, but users must be able to mark forbidden folders.
- Ingestion cannot proceed until the user reviews and approves discovered sources.
- Storage is **SQLite-centered**, with a minimal file-based raw snapshot store for extracted text and metadata that should not be normalized into the database.
- Raw original files are not copied. Only extracted text, metadata, hashes, source URIs, locators, and masking results are stored.
- Initial artifacts are:
  - evidence-based master profile
  - public portfolio draft
- Resume, cover letter, company-specific strategy, and interview-preparation artifacts are deferred.
- Distribution is private and small-scale: code is hosted in the user's GitHub repository, and only approved people pull and use it.

## Product Boundary

The MVP is a **Codex app driven local career knowledge workspace**, not a standalone consumer application.

The user's primary flow is:

```text
Codex app
  -> repo-scoped portfolio-maker skill
  -> portfolio-maker CLI
  -> reusable application use cases
  -> local SQLite, snapshots, and generated artifacts
```

The Codex app provides the interactive orchestration layer: user prompts, local execution, permission review, web search when needed, Git workflow support, and artifact inspection.

The repository provides:

- a repo-scoped Codex skill that defines the workflow
- a CLI adapter for local execution
- a reusable application engine
- local connectors for file system and GitHub sources
- SQLite and snapshot storage
- Markdown and JSON artifact writers
- tests and setup documentation for approved small-group users

The repository does not initially provide:

- standalone GUI
- hosted backend
- multi-user account system
- Codex app-server integration
- MCP server

## Goals

### MVP Goals

1. Guide the user through the career-data discovery workflow from Codex app.
2. Discover local file and GitHub source candidates.
3. Let the user exclude forbidden folders and repositories; source-class exclusion is deferred until company-specific generation.
4. Block body ingestion until source approval is explicit.
5. Ingest approved local files into SQLite and minimal snapshots; retain GitHub repositories and activities only as discovery metadata.
6. Create an evidence-based master profile in JSON and Markdown.
7. Create a public portfolio draft in Markdown.
8. Keep public artifacts free of secrets, tokens, and private raw paths.
9. Provide automated tests for core policy and storage behavior.
10. Provide setup and usage docs for approved small-group users.

### Deferred Goals

- Google Drive connector
- company and job-description research
- company-specific strategy generation
- resume and cover-letter drafting
- interview-preparation artifacts
- OCR/image analysis
- vector database and semantic search
- MCP server
- Codex app-server companion
- standalone GUI
- hosted product, account system, or large-scale distribution

## Architecture Overview

The system is split into adapters, application use cases, domain models, and infrastructure.

```text
.agents/
  skills/
    portfolio-maker/
      SKILL.md

src/portfolio_maker/
  adapters/
    cli/
    codex_skill_support/
    future_app_server/
  application/
    discover_sources
    approve_sources
    ingest_sources
    build_profile
    draft_portfolio
  domain/
    source
    evidence
    project
    skill
    work_item
    career_claim
    artifact
  infrastructure/
    local_fs_connector
    github_connector
    extractors
    sqlite_repository
    raw_snapshot_store
    secret_filter
    audit_log
```

The exact file layout may change during implementation, but these boundaries should remain stable.

## Layer Responsibilities

### Codex Skill

The repo-scoped Codex skill is a workflow guide, not the core intelligence layer.

Responsibilities:

- confirm the user's target artifact
- collect forbidden folders and excluded repositories
- call CLI commands in the required order
- stop after discovery so the user can review source candidates
- inspect generated artifacts for missing evidence or unsafe disclosure
- suggest re-running targeted commands when needed

The skill must not bypass the approval checkpoint.

### CLI Adapter

The CLI is intentionally thin.

Responsibilities:

- parse command arguments
- resolve config and workspace paths
- call application use cases
- print safe progress output
- return meaningful exit codes

The CLI must not contain business logic that would make future app-server or MCP adapters reimplement behavior.

Initial commands:

```bash
portfolio-maker discover
portfolio-maker approve
portfolio-maker ingest
portfolio-maker build-profile
portfolio-maker draft-portfolio
portfolio-maker run-mvp
```

`run-mvp` may orchestrate the full pipeline, but it must still stop before ingestion when approval is missing.

### Application Use Cases

Application use cases are the reusable engine boundary.

Initial use cases:

- `discover_sources`
- `approve_sources`
- `ingest_sources`
- `build_profile`
- `draft_portfolio`

Rules:

- Use cases do not print directly to the terminal.
- Use cases do not depend on Codex thread state.
- Use cases accept explicit request models and return explicit result models.
- Long-running use cases expose progress events through callbacks or returned event records.
- Use cases are directly testable without Codex app.

These rules are what keep the system open to a future Codex app-server companion.

### Domain Layer

The implemented 0.1.0 domain keeps only the concepts used by the MVP runtime:

- `Source`: an approved local file or discovered GitHub repository record
- `GitHubActivity`: repository/activity discovery metadata

Company-specific generation may add normalized evidence, project, claim, and artifact models when a runtime reader and writer require them.

### Infrastructure Layer

Infrastructure implements side effects.

Initial infrastructure:

- local file system discovery
- local document text extraction
- GitHub API or GitHub CLI based collection
- SQLite repository
- raw snapshot file store
- secret masking and policy filters

Infrastructure modules must return structured errors rather than leaking raw command output or secret values into logs.

## Data Flow

### 1. Discovery

Discovery searches for source candidates.

Local discovery:

- scans the home directory for candidate folders and files
- applies default exclusions
- applies user forbidden-folder rules
- records candidate metadata without unnecessary body reads

GitHub discovery:

- lists repositories and activity candidates
- includes commits, pull requests, issues, reviews, and Actions activity
- marks public and private repository visibility
- defers organization-resource marking and direct-user-activity prioritization until a later discovery upgrade
- stores repositories and activities as discovery metadata only; it does not ingest GitHub bodies or use GitHub activity in current artifacts

Output:

- `.portfolio-maker/reviews/discovery-report.md`
- initial source records in SQLite

### 2. Approval

The user reviews discovery output and confirms what may be ingested.

Approval state is stored in:

```text
.portfolio-maker/reviews/source-approval.json
```

The 0.1.0 approval fields are:

- `version`: optional approval-format version; omitted values default to `1`, and only version `1` is supported
- `approved_source_uris`: approved local source URIs
- `forbidden_paths`: local paths that must not be read or used in artifacts
- `excluded_repositories`: GitHub repositories excluded from discovery
- `private_sources_allowed`: whether private GitHub repositories may appear in discovery

Repository allowlists and excluded file patterns are deferred and not implemented in 0.1.0.

Ingestion must fail closed when approval is missing.

In 0.1.0, GitHub approval settings control discovery visibility only. They do not authorize GitHub artifact input.

### 3. Ingestion

Ingestion reads only approved local file sources. Immediately before reading, it rejects symbolic links, non-regular files, non-canonical file URIs, policy-blocked paths, and files over the MVP size limit.

It stores:

- extracted text snapshot
- source URI
- content hash
- metadata
- locator information
- extractor version
- masking result

It does not copy original files into the project store.

### 4. Synthesis

The 0.1.0 synthesis stage builds a master profile from the latest approved local snapshots whose original source still exists and has the same current hash. Missing, changed, policy-blocked, or unavailable snapshots are excluded until re-ingestion. It lists one `project_evidence` claim per remaining source. GitHub activity is not artifact input until the later company-specific generation phase.

Detailed project summaries, skill inventories, role analyses, and confidence-scored claims are deferred with company-specific generation.

### 5. Portfolio Drafting

The portfolio draft is generated from current local-snapshot-based master-profile content.

In 0.1.0 it is a review-required portfolio skeleton: approved sources are listed, while role, technical approach, and outcome remain placeholders. Evidence-rendered portfolio writing is deferred to a later generation phase.

The 0.1.0 skeleton includes only:

- approved source title
- generic inclusion notice
- placeholder role, technical approach, and outcome fields
- internal evidence reference for review

Project summaries, problem/context, implementation details, evidence-backed outcomes, and public-safe technology stacks are deferred until evidence-rendered portfolio generation is implemented.

The public draft must not expose secrets, tokens, or private raw paths.

## Local Storage Layout

The MVP stores project-local state under:

```text
.portfolio-maker/
  portfolio.db
  raw/
    snapshots/
      local/
  artifacts/
    master-profile.json
    master-profile.md
    portfolio-draft.md
  reviews/
    discovery-report.md
    source-approval.json
```

`.portfolio-maker/` is local working data. The implementation plan should decide which subpaths are ignored by Git and whether example templates are committed separately.

## SQLite Model

Initial tables:

```text
sources
  id
  type
  uri
  display_name
  owner
  status
  discovered_at
  approved_at

source_snapshots
  id
  source_id
  snapshot_path
  content_hash
  extractor
  extracted_at

github_activities
  id
  source_id
  repo
  activity_type
  url
  title
  state
  author
  created_at
  merged_at

```

`evidence_items`, `projects`, `career_claims`, `claim_evidence`, and `artifacts` are intentionally deferred until company-specific generation has runtime readers and writers. A vector database is not part of the MVP.

## Evidence Rules

1. Current profile claims are derived from approved local snapshots.
2. GitHub URLs and activities remain discovery-report metadata in the 0.1.0 MVP.
3. Public artifacts must not expose private raw paths or sensitive content.
4. If an ingested local source disappears or its hash changes, mark it stale and require re-ingestion before generating artifacts.

## Security and Privacy

### Local File Policy

Default discovery may consider the home directory, but these classes should be excluded or handled conservatively:

- `.Trash`
- `Library`
- `Applications`
- browser profiles
- password-manager exports
- private keys
- `.env` and credential files
- package caches
- virtual environments
- `node_modules`
- `.git/objects`
- large binary media

Users can add forbidden folders. Forbidden-folder descendants should not be ingested and should not appear in reports with unnecessarily revealing names.

### Secret Handling

The system must:

- mask detected secrets in extracted snapshots
- avoid printing secrets to terminal output
- avoid logging raw tokens or credentials
- treat token-like values as unsafe even in generated drafts
- keep GitHub tokens outside the repository

### GitHub Policy

The MVP may use GitHub CLI auth or a fine-grained token.

Rules:

- prefer read-only access
- never store or print token values
- distinguish public and private repositories; organization classification is deferred
- show private repositories only when explicitly allowed
- report GitHub rate-limit and per-repository failures without discarding unrelated discovery results
- do not use GitHub repositories or activities as profile or portfolio input in the 0.1.0 MVP

### Approval Gate

`discover` may produce candidate reports. `ingest` may not read source bodies until `source-approval.json` exists and approves the target sources.

This gate also applies when `run-mvp` is used.

`build-profile` rechecks current approval, forbidden-path policy, original-file hash, and latest snapshot integrity before using an existing snapshot.

## Error Handling

The pipeline should be resumable and fail partially rather than destroying prior state.

Expected local-source states: `skipped_policy`, `extract_failed`, `stale_source`, `approved`, and `ingested`.

Failure handling:

- File extraction failures are recorded without raw content.
- Missing local evidence prevents current artifact claims.
- Public-risk findings are excluded from public artifacts.

## Testing Strategy

### Unit Tests

Cover:

- path exclusion rules
- forbidden-folder matching
- secret masking
- source and GitHub discovery model creation
- artifact writer structure
- approval gate behavior

### Integration Tests

Cover:

- fixture home-directory discovery
- fixture GitHub API response discovery and metadata storage
- SQLite persistence and reload
- master-profile generation from fixtures
- portfolio draft generation from a fixture profile
- blocked ingestion when approval is missing

### Manual Verification

Run the Codex-guided sequence:

```text
discover -> approve -> ingest -> build-profile -> draft-portfolio
```

Then verify:

- discovery report is understandable
- forbidden paths are respected
- master profile claims derive from approved local snapshots
- portfolio draft omits private raw paths and secrets
- generated artifacts are saved in the documented paths

## Distribution Model

The repository is distributed through the user's GitHub account to a very small approved group.

The MVP should include:

- README setup instructions
- required tools and supported macOS version assumptions
- GitHub authentication setup notes
- Codex app usage notes
- safety warnings about local file scanning
- troubleshooting for permission, GitHub auth, and rate-limit failures

It should not include:

- hosted license checks
- user accounts
- auto-updater
- telemetry by default
- remote storage

## Future App-Server Extension Boundary

The MVP should be ready for a future companion application that uses Codex app-server.

To preserve that path:

- core use cases must be independent of CLI and Codex thread state
- progress should be represented as structured events
- request and result models should be serializable
- approval state must be persisted outside the current thread
- storage paths and schema must remain stable
- long-running jobs must be resumable

Future shape:

```text
Desktop or web companion
  -> Codex app-server client
  -> application use cases
  -> same SQLite and snapshot storage
  -> same generated artifacts
```

MCP can be added before app-server if needed, exposing the same use cases as tools such as:

- `discover_sources`
- `get_discovery_report`
- `ingest_approved_sources`
- `build_profile`
- `draft_portfolio`

MCP is explicitly not part of the MVP.

## References

- Codex app features: https://developers.openai.com/codex/app/features
- Codex skills: https://developers.openai.com/codex/skills
- Codex app-server: https://developers.openai.com/codex/app-server
- Codex MCP: https://developers.openai.com/codex/mcp
