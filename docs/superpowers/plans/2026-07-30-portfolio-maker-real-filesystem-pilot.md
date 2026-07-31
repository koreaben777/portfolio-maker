# Portfolio Maker Real Filesystem Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Portfolio Maker 0.2.2 exact candidate를 사용자의 실제 `$HOME` 전체와 공개·명시 승인 비공개 GitHub에 정상 사용자 흐름으로 적용하고, 안전한 포트폴리오 산출물과 실사용 검증 보고서를 만든다.

**Architecture:** 개발 checkout 및 기존 `.portfolio-maker/` 상태와 분리된 detached pilot worktree를 만든다. `$HOME`과 GitHub는 읽기 입력으로만 사용하고, 승인·snapshot·SQLite·semantic index·project review·artifact·test evidence는 pilot worktree의 비공개 `.portfolio-maker/`에 저장한다. 각 승인 경계에서 사용자 응답을 기다리며, `restricted`와 `open_public`은 별도로 생성·검증한 뒤 최종 전달 상태를 `restricted`로 복원한다.

**Tech Stack:** macOS, Codex app, Python 3.11+, Portfolio Maker CLI 0.2.2, SQLite, Git, GitHub CLI, Node.js LTS, npm, Vite static renderer, Codex plugin child skills.

> [!CAUTION]
> 이 historical 계획의 protocol-v1 full-home 의미 분석은 재개하지 않는다.
> 2026-07-31 실행은 의미 품질 검증에서 중단되었으며 project curation과 artifact
> 단계로 진행하지 않았다. 새 pilot은
> [교정 설계](../specs/2026-07-31-portfolio-maker-resumable-codex-semantic-index-design.md),
> [재개 구현 계획](2026-07-31-portfolio-maker-resumable-codex-semantic-index.md),
> [합성 검증 기록](../../reviews/2026-07-31-resumable-codex-semantic-index-verification.md)을
> 기준으로 fresh candidate와 별도 사용자 승인을 받아야 한다.

## Global Constraints

- Exact candidate는 `0e156bfb289119ece65e67a9bc9e8b000bf6bf2c`이며 다른 HEAD로 대체하지 않는다.
- Pilot checkout은 `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2`다.
- History backup directory는 `/Users/june_kim/Documents/portfolio-maker-real-pilot-backup-0.2.2`다.
- 실행 전 `superpowers:using-git-worktrees`를 읽고 detached worktree를 준비한다.
- 실제 workflow는 exact candidate의 `$portfolio-maker` router가 여섯 child
  skill을 순서대로 호출하는 새 Codex task에서만 시작한다.
- router 또는 child skill이 노출되지 않으면 repository 파일을 임의로 대신 읽어
  진행하지 않고 Task 1에서 `BLOCKED`로 중단한다.
- 기존 root checkout의 tracked·untracked 사용자 변경을 stage, commit, reset, clean, move, delete하지 않는다.
- 기존 어느 checkout의 `.portfolio-maker/`도 복사하거나 재사용하지 않는다.
- `$HOME`과 GitHub는 읽기 전용 입력이며 원본 파일과 원격 상태를 변경하지 않는다.
- `.env`, private key, credential store, browser profile, password-manager export의 내용을 읽지 않는다.
- 실제 credential, token, secret 값을 화면·로그·보고서에 출력하지 않는다.
- Source approval 전에는 ingest를 실행하지 않는다.
- Project candidate는 database truth가 아니며 사용자 결정 전 활성 project가 아니다.
- `restricted`와 `open_public`은 별도 artifact policy와 별도 검증을 사용한다.
- Vision은 사용자가 승인한 실제 local file 하나씩, 별도 명시 확인 후에만 실행한다.
- History apply는 pilot-owned managed history만 대상으로 하며 matching receipt와 exact confirmation 전에는 실행하지 않는다.
- 실제 중단을 만들기 위해 process를 강제 종료하지 않는다.
- Sites 배포, push, merge, tag, release, package publication, GitHub Issue mutation을 수행하지 않는다.
- `.portfolio-maker/`, `.venv/`, `node_modules/`, `dist/`, 실제 사용자 식별정보를 Git에 stage 또는 commit하지 않는다.
- 긴 discovery 또는 ingest 대기 중에는 진행 상태를 한 번만 알리고 완료까지 반복 출력하지 않는다.
- 각 사용자 승인 gate가 해결되지 않으면 해당 task를 `BLOCKED`로 기록하고 후속 task를 실행하지 않는다.
- SQLite와 managed JSON에 대한 read-only 수량 검사는 파일럿 audit
  instrumentation으로만 수행한다. 그 결과는 router나 child skill의 입력으로
  전달하지 않으며, 모든 상태 변경은 기존 CLI 경계만 사용한다.

---

## File and State Map

### Tracked repository inputs

- Read from management checkout:
  `/Users/june_kim/Documents/portfolio-maker/docs/superpowers/specs/2026-07-30-portfolio-maker-real-filesystem-pilot-design.md`
- Read from management checkout:
  `/Users/june_kim/Documents/portfolio-maker/docs/superpowers/plans/2026-07-30-portfolio-maker-real-filesystem-pilot.md`
- Read from exact pilot checkout:
  `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/README.md`
- Read from exact pilot checkout:
  `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.codex-plugin/plugin.json`
- Read from exact pilot checkout:
  `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/skills/*/SKILL.md`
- The design and execution plan are newer management documents and are not
  copied into or committed onto the detached exact-candidate checkout.
- No production source, test, tracked documentation, or metadata modification is planned.

### Pilot-private files

- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/source-approval.json`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/artifact-approval.json`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/discovery-report.md`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/semantic-index/`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/project-review-input-v2.json`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/project-candidates.json`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/project-candidates.md`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/real-filesystem-pilot/preflight.md`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/real-filesystem-pilot/source-baseline.md`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/real-filesystem-pilot/project-decisions.md`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/real-filesystem-pilot/restricted/`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/real-filesystem-pilot/open_public/`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/real-filesystem-pilot/restricted-artifact-approval.json`
- Conditional create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/real-filesystem-pilot/restricted-after-vision/`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/reviews/real-filesystem-pilot/report.md`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/raw/snapshots/`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/derived/vision/`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/artifacts/`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.portfolio-maker/portfolio.db`
- Conditional create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-backup-0.2.2`

### Interfaces between tasks

- Task 1 produces the exact installed pilot checkout consumed by all later tasks.
- Task 2 produces approved discovery scope and GitHub repository policy consumed by Task 3.
- Task 3 produces reviewable local/GitHub candidates consumed by Task 4.
- Task 4 produces exact source and artifact approvals consumed by Tasks 5–11.
- Task 5 produces ingested sources and active semantic index consumed by Task 6.
- Task 6 produces safe project candidates consumed by Task 7.
- Task 7 produces active project decisions consumed by Tasks 8–10.
- Task 8 produces the restricted artifact baseline consumed by Task 9.
- Task 9 produces scope-isolation evidence and restores restricted final state.
- Task 10 conditionally produces included vision-derived evidence and regenerated restricted artifacts.
- Task 11 conditionally prunes only pilot-managed history after backup and exact user confirmation.
- Task 12 reconciles every previous result into the final private report.

---

## Reusable Artifact Revalidation Gate

Run this gate immediately before every artifact build after source, artifact,
project, or vision state has changed:

```bash
.venv/bin/portfolio-maker ingest --workspace .
.venv/bin/portfolio-maker prepare-project-review --workspace . --version v2
```

Then invoke `$portfolio-project-curation` on only the newly generated safe v2
bundle, validate the candidate files, and run:

```bash
.venv/bin/portfolio-maker compose-projects --workspace . --mode review
.venv/bin/portfolio-maker list-projects --workspace . --format table
```

Compare the result with `project-decisions.md`.

- Reapply an existing manual decision only when project identity and boundary
  fingerprint are unchanged.
- If a project is new, removed, merged, split, or has a changed boundary,
  pause for a fresh user decision and update `project-decisions.md`.
- Do not use automatic mode in this gate.
- Do not build while any intended final project remains `review_required`.

This is evidence/policy revalidation, not permission to change delivery scope.

---

### Task 1: Prepare the Exact Isolated Pilot Checkout

**Files:**
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/.venv/`
- Create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/web/portfolio/node_modules/`
- Generated/ignored: `/Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2/web/portfolio/dist/`

**Interfaces:**
- Consumes: exact commit `0e156bfb289119ece65e67a9bc9e8b000bf6bf2c`
- Produces: clean installed CLI and renderer at the fixed pilot path

- [ ] **Step 1: Verify the pilot and backup paths do not already exist**

Run from `/Users/june_kim/Documents/portfolio-maker`:

```bash
test ! -e /Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2
test ! -e /Users/june_kim/Documents/portfolio-maker-real-pilot-backup-0.2.2
git worktree list --porcelain
```

Expected: both `test` commands exit 0 and no worktree uses the pilot path. If either path exists, stop as `BLOCKED`; do not delete or reuse it.

- [ ] **Step 2: Create a detached worktree at the exact candidate**

```bash
git worktree add --detach /Users/june_kim/Documents/portfolio-maker-real-pilot-0.2.2 0e156bfb289119ece65e67a9bc9e8b000bf6bf2c
```

Expected: worktree creation succeeds at the exact commit.

- [ ] **Step 3: Verify exact identity and initial cleanliness**

Run from the pilot checkout:

```bash
git rev-parse HEAD
git status --porcelain=v1 --untracked-files=all
test ! -e .portfolio-maker
```

Expected: HEAD is the exact 40-character candidate, status output is empty, and `.portfolio-maker` is absent.

- [ ] **Step 4: Install the Python package in a local virtual environment**

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Expected: install exits 0 without printing credential values.

- [ ] **Step 5: Install the locked renderer dependencies**

Run from `web/portfolio`:

```bash
npm ci
npm run build
```

Expected: Vite production build exits 0. Record npm advisories as maintenance residuals; do not run `npm audit fix` or update the lockfile.

- [ ] **Step 6: Verify installed version and command surface**

Run from the pilot checkout:

```bash
.venv/bin/python --version
node --version
npm --version
gh --version | sed -n '1p'
.venv/bin/python -c "import portfolio_maker; print(portfolio_maker.__version__)"
.venv/bin/portfolio-maker --help
```

Expected: runtime versions are recorded safely, package version is `0.2.2`,
and help includes discovery, semantic project, history, vision, and artifact
commands.

- [ ] **Step 7: Verify the plugin router and six child skills**

```bash
.venv/bin/python -c "import json; from pathlib import Path; payload = json.loads(Path('.codex-plugin/plugin.json').read_text()); assert payload['name'] == 'portfolio-maker' and payload['version'] == '0.2.2' and payload['skills'] == './skills/'"
find skills -mindepth 2 -maxdepth 2 -name SKILL.md -print | sort
codex plugin list
```

Expected: the exact candidate declares one router plus six child skill files,
and the fresh Codex task exposes all seven names from this candidate:

- `$portfolio-maker`;
- `$portfolio-source-governance`;
- `$portfolio-vision-analysis`;
- `$portfolio-semantic-index`;
- `$portfolio-project-curation`;
- `$portfolio-project-review`;
- `$portfolio-artifacts`.

If `codex plugin list` or the current task catalog does not establish that
binding, stop and ask the user to authorize exact-candidate plugin setup and a
new task. Do not silently use a different installed version or the
repository-compatibility entrypoint.

- [ ] **Step 8: Verify no tracked candidate file changed**

```bash
git status --porcelain=v1 --untracked-files=all
```

Expected: no tracked or untracked non-ignored source change. Do not commit ignored installation or build outputs.

---

### Task 2: Initialize Approval and Fix the Discovery Scope

**Files:**
- Create: `.portfolio-maker/reviews/source-approval.json`
- Create: `.portfolio-maker/reviews/real-filesystem-pilot/preflight.md`
- Modify after user gate: `.portfolio-maker/reviews/source-approval.json`

**Interfaces:**
- Consumes: installed CLI from Task 1
- Produces: exact excluded directories, excluded repositories, private opt-in, and private repository allowlist

The `$portfolio-maker` router must hand this stage to
`$portfolio-source-governance`; Tasks 2–4 remain within that child skill until
all source and artifact gates are current.

- [ ] **Step 1: Create the normal source-approval sample**

Run from the pilot checkout:

```bash
.venv/bin/portfolio-maker approve --workspace . --write-sample
```

Expected: `.portfolio-maker/reviews/source-approval.json` exists and every approved URI/activity list is empty.

- [ ] **Step 2: Record safe preflight metadata**

Use `apply_patch` to create `preflight.md` containing only:

```markdown
# Real Filesystem Pilot Preflight

- candidate: 0e156bfb289119ece65e67a9bc9e8b000bf6bf2c
- version: 0.2.2
- scan root: user home directory
- source approval initialized: yes
- prior workspace reused: no
- push, deploy, issue mutation authorized: no
```

Do not record tokens, source paths, private repository URLs, or hashes in this public-safe summary.

- [ ] **Step 3: Check GitHub authentication without exposing a token**

```bash
gh auth status
```

Expected: authenticated account and host are reported; no token-value command is used. If not authenticated, stop and ask the user to complete `gh auth login`.

- [ ] **Step 4: List repository names for the private allowlist gate**

```bash
gh repo list --limit 1000 --json nameWithOwner,isPrivate --jq '.[] | select(.isPrivate == true) | .nameWithOwner'
```

Expected: canonical private `owner/repo` names only. Do not fetch repository contents or activity endpoints in this step.

- [ ] **Step 5: Ask the user for the exact discovery policy**

Pause and request one response containing:

- additional excluded local directories, or confirmation that there are none;
- excluded GitHub repositories, or confirmation that there are none;
- private repositories selected from Step 4 for `allowed_repositories`;
- excluded filename globs, or confirmation that there are none.

Do not infer selections from repository names.

- [ ] **Step 6: Apply the user-approved policy**

Use `apply_patch` on `source-approval.json` with these deterministic rules:

- keep `version` at `1`;
- keep `approved_source_uris`, `approved_github_activity_urls`, and
  `approved_private_github_activity_urls` empty;
- keep the legacy `forbidden_paths` array empty;
- write the user's approved local exclusions to `excluded_directories`;
- write the user's approved repository exclusions to `excluded_repositories`;
- write the user's approved private repositories to `allowed_repositories`;
- set `private_sources_allowed` to `true` only when `allowed_repositories` is
  non-empty, otherwise keep it `false`;
- write the user's approved filename globs to `excluded_file_patterns`.

Do not infer, abbreviate, or normalize values by hand. The loader validation in
the next step is the authority for canonical repository and path handling.

- [ ] **Step 7: Validate the policy without discovery**

```bash
.venv/bin/python -c "from pathlib import Path; from portfolio_maker.application.approval import load_approval; from portfolio_maker.workspace import WorkspacePaths; load_approval(WorkspacePaths.from_root(Path('.')))"
```

Expected: exit 0. Re-open the JSON and verify approved URI/activity arrays remain empty.

- [ ] **Step 8: Record the gate**

Update `preflight.md` with four safe decimal counts: additional excluded
directories, excluded repositories, private repositories allowlisted, and
excluded filename patterns. Do not record private names or local paths in
`preflight.md`.

---

### Task 3: Run Full-Home and GitHub Discovery

**Files:**
- Create/modify: `.portfolio-maker/reviews/discovery-report.md`
- Modify: `.portfolio-maker/reviews/source-approval.json` only through CLI persistence of the pilot checkout exclusion

**Interfaces:**
- Consumes: Task 2 policy
- Produces: reviewable local source candidates and public/private GitHub activity candidates

- [ ] **Step 1: Capture approval-file hash before discovery**

```bash
shasum -a 256 .portfolio-maker/reviews/source-approval.json
```

Expected: one hash for later comparison. Keep the value private.

- [ ] **Step 2: Run the exact normal discovery flow**

```bash
.venv/bin/portfolio-maker discover --workspace . --home "$HOME" --exclude-directory "$PWD"
```

Expected: discovery completes or returns a controlled failure. During a long scan, issue one progress update and then wait. Do not add `--no-github`.

- [ ] **Step 3: Verify discovery outputs and persisted exclusion**

```bash
test -f .portfolio-maker/reviews/discovery-report.md
test -f .portfolio-maker/portfolio.db
rg -n '^## ' .portfolio-maker/reviews/discovery-report.md
```

Expected: report and DB exist; report has local and GitHub sections. The exact pilot checkout is present in `excluded_directories`.

- [ ] **Step 4: Verify approval did not silently gain evidence approvals**

Inspect `source-approval.json`.

Expected:

```text
approved_source_uris: empty
approved_github_activity_urls: empty
approved_private_github_activity_urls: empty
```

- [ ] **Step 5: Check discovery safety without printing matching values**

```bash
rg -l -i 'BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|password|token|secret|credential)[[:space:]]*[:=]' .portfolio-maker/reviews/discovery-report.md
```

Expected: no output. A hit is a stop condition; do not print the matching line.

- [ ] **Step 6: Summarize candidate counts privately**

Inspect the report and database read-only. Record counts for:

- local source candidates;
- public repositories and activities;
- private repositories and activities;
- skipped or failed entries.

Use `apply_patch` to add only counts and controlled error summaries to `preflight.md`.

- [ ] **Step 7: Ask the user to review exact candidates**

Pause and provide clickable local links to:

```text
.portfolio-maker/reviews/discovery-report.md
.portfolio-maker/reviews/source-approval.json
```

Request exact selections for:

- confirmation that the canonical `$HOME` URI may be used as the semantic-index
  root approval;
- approved local `file://` URIs;
- approved public GitHub activity URLs;
- approved private GitHub activity URLs;
- any newly discovered directory or repository exclusions.

Do not proceed to ingest until the user approves.

---

### Task 4: Apply Source and Artifact Approvals

**Files:**
- Modify: `.portfolio-maker/reviews/source-approval.json`
- Create: `.portfolio-maker/reviews/artifact-approval.json`
- Create: `.portfolio-maker/reviews/real-filesystem-pilot/source-baseline.md`

**Interfaces:**
- Consumes: exact user selections from Task 3
- Produces: current source approval, restricted artifact policy, and representative source baseline

- [ ] **Step 1: Apply exact source and activity approvals**

Use `apply_patch` to place only the exact Task 3 selections into:

```text
approved_source_uris
approved_github_activity_urls
approved_private_github_activity_urls
```

Resolve the confirmed semantic root once:

```bash
.venv/bin/python -c "from pathlib import Path; print(Path.home().resolve(strict=True).as_uri())"
```

Add that exact URI to `approved_source_uris` together with the user-selected
local source URIs. This approves `$HOME` as semantic analysis scope; it does
not automatically approve every discovered file as evidence. Also apply any
newly approved exclusions. Preserve Task 2 private opt-in and allowlist.

- [ ] **Step 2: Validate source approval**

```bash
.venv/bin/python -c "from pathlib import Path; from portfolio_maker.application.approval import load_approval; from portfolio_maker.workspace import WorkspacePaths; load_approval(WorkspacePaths.from_root(Path('.')))"
```

Expected: exit 0 with no policy-format error.

- [ ] **Step 3: Create the artifact-policy sample**

```bash
.venv/bin/portfolio-maker approve --workspace . --write-sample-artifact-policy
```

Expected: all four artifact kinds exist with `delivery_scope: restricted`, local/public/private inclusion enabled, and empty exclusion lists.

- [ ] **Step 4: Ask the user for per-artifact exclusions**

Pause and ask whether any approved source URI, repository, or activity must be excluded from:

- master profile;
- portfolio draft;
- portfolio public manifest;
- portfolio HTML.

If none, record the explicit response and keep the restricted defaults.

- [ ] **Step 5: Apply per-artifact exclusions**

Use `apply_patch` to update only the corresponding `excluded_source_uris`, `excluded_repositories`, and `excluded_activity_urls` arrays. Keep all four policies `restricted`.

Validate the complete policy through its production loader:

```bash
.venv/bin/python -c "from pathlib import Path; from portfolio_maker.application.artifact_approval import load_artifact_policy; from portfolio_maker.workspace import WorkspacePaths; policy = load_artifact_policy(WorkspacePaths.from_root(Path('.'))); assert policy.explicit and len(policy.policies) == 4"
```

Expected: exit 0 with four explicit, restricted artifact policies.

- [ ] **Step 6: Select representative source baselines**

From the approved local sources, ask the user to select one regular file associated with each available anchor project:

- 보험 RAG;
- PlayMCP;
- Portfolio Maker.

For each selected file, run read-only `stat` and `shasum -a 256`. Use `apply_patch` to create `source-baseline.md` with private path, size, modification time, and hash. This file stays under ignored `.portfolio-maker/reviews/`.

- [ ] **Step 7: Verify no artifact exists before ingest**

```bash
test ! -e .portfolio-maker/artifacts/master-profile.json
test ! -e .portfolio-maker/artifacts/portfolio.html
```

Expected: both tests exit 0.

---

### Task 5: Ingest Approved Sources and Build the Semantic Index

**Files:**
- Modify: `.portfolio-maker/portfolio.db`
- Create: `.portfolio-maker/raw/snapshots/local/`
- Create: `.portfolio-maker/reviews/semantic-index/input/`
- Create via semantic skill: managed semantic-index output chunks
- Create/modify: `.portfolio-maker/reviews/semantic-index-report.md`
- Create: `.portfolio-maker/reviews/project-review-input-v2.json`

**Interfaces:**
- Consumes: Task 4 source approval
- Produces: ingested source records, managed snapshots, active semantic revision, and v2 safe review bundle

- [ ] **Step 1: Ingest exact approved sources**

```bash
.venv/bin/portfolio-maker ingest --workspace .
```

Expected: approved local sources and eligible GitHub activities are ingested; unapproved sources are not.

- [ ] **Step 2: Verify source states read-only**

```bash
sqlite3 .portfolio-maker/portfolio.db "SELECT status, COUNT(*) FROM sources GROUP BY status ORDER BY status;"
```

Expected: approved usable sources are `ingested`; skipped/stale/failure states are counted and retained for reporting.

- [ ] **Step 3: Prepare the full-home semantic index**

```bash
.venv/bin/portfolio-maker prepare-semantic-index --workspace . --root "$HOME"
```

Expected: managed input manifest and chunks are created without a global file-count truncation.

- [ ] **Step 4: Invoke the semantic-index child skill**

Use `$portfolio-semantic-index` only on the managed input chunks in:

```text
.portfolio-maker/reviews/semantic-index/input/
```

The skill must not re-scan `$HOME`, raw snapshots, private GitHub URLs, or original files outside the managed chunk contract.

- [ ] **Step 5: Apply the semantic index**

```bash
.venv/bin/portfolio-maker apply-semantic-index --workspace .
```

Expected: hash, coverage, schema, and binding checks pass; an active revision is persisted.

- [ ] **Step 6: Prepare the v2 project-review bundle**

```bash
.venv/bin/portfolio-maker prepare-project-review --workspace . --version v2
```

Expected: `project-review-input-v2.json` exists.

- [ ] **Step 7: Scan the v2 bundle for forbidden locators**

```bash
rg -l 'file://|/Users/|github\\.com/.+/.+/(pull|issues|commit)/' .portfolio-maker/reviews/project-review-input-v2.json
```

Expected: no output. A hit stops the pilot before Codex grouping.

---

### Task 6: Generate and Review Project Candidates

**Files:**
- Read only: `.portfolio-maker/reviews/project-review-input-v2.json`
- Create: `.portfolio-maker/reviews/project-candidates.json`
- Create: `.portfolio-maker/reviews/project-candidates.md`

**Interfaces:**
- Consumes: Task 5 safe v2 bundle
- Produces: review-required grouped candidates and unassigned evidence

- [ ] **Step 1: Invoke the project-review child skill with an exact fence**

The router must hand this stage to `$portfolio-project-curation`. That skill
may invoke `$portfolio-project-review` only with the safe v2 bundle.

Request:

```text
Read only .portfolio-maker/reviews/project-review-input-v2.json.
Create .portfolio-maker/reviews/project-candidates.json and
.portfolio-maker/reviews/project-candidates.md.
Group a coherent parent and its children as one project unless a child is an
independent product, contest entry, or deployment. Keep trivial or uncertain
evidence unassigned. Do not inspect original files or infer private locators.
```

- [ ] **Step 2: Validate candidate files**

```bash
test -f .portfolio-maker/reviews/project-candidates.json
test -f .portfolio-maker/reviews/project-candidates.md
```

Expected: both files exist and pass the current candidate schema when `compose-projects` is invoked.

- [ ] **Step 3: Check mandatory anchor recall**

Review candidate titles and summaries for:

- 보험 RAG;
- PlayMCP;
- Portfolio Maker.

Expected: each non-excluded anchor is present or has a concrete evidence/policy reason for absence.

- [ ] **Step 4: Audit over-fragmentation and trivial candidates**

Review every candidate and record:

- parent/child groups that should be one project;
- independent child products correctly split;
- trivial files/folders incorrectly proposed;
- evidence intentionally left unassigned.

Do not delete candidates to improve the count.

- [ ] **Step 5: Ask for the user's project-boundary review**

Pause with clickable links to both candidate files. Ask the user to confirm:

- which boundaries are correct;
- which candidates must merge or split;
- which candidates should remain unassigned;
- whether the candidate set is suitable for decision materialization.

If regrouping is required, regenerate candidates from the same v2 bundle and repeat this task before continuing.

---

### Task 7: Exercise Review and Automatic Project Decisions

**Files:**
- Modify: `.portfolio-maker/portfolio.db`
- Read: `.portfolio-maker/reviews/project-candidates.json`
- Create: `.portfolio-maker/reviews/real-filesystem-pilot/project-decisions.md`

**Interfaces:**
- Consumes: user-approved candidate boundaries from Task 6
- Produces: reviewed decision set, automatic comparison, and final active project states

The router must keep this stage under `$portfolio-project-review`; review mode
is the authoritative final decision lane, while automatic mode is only the
approved comparison exercise.

- [ ] **Step 1: Materialize review-required decisions**

```bash
.venv/bin/portfolio-maker compose-projects --workspace . --mode review
.venv/bin/portfolio-maker list-projects --workspace . --format table
```

Expected: every candidate is review-required; none becomes active without a user state change.

- [ ] **Step 2: Ask for included/excluded decisions**

Pause and ask the user to mark every candidate `included` or `excluded`. Use
`apply_patch` to record the complete decision table privately in
`project-decisions.md` with delivery scope, project ID, boundary fingerprint,
safe title, and decision state. Keep separate `restricted` and `open_public`
sections when the latter is exercised.

- [ ] **Step 3: Run automatic mode for comparison before persisting manual decisions**

```bash
.venv/bin/portfolio-maker compose-projects --workspace . --mode automatic
.venv/bin/portfolio-maker list-projects --workspace . --format table
```

Expected: high and medium candidates are auto-included; low/review-only
evidence is not. Because Step 2 decisions have not yet been persisted, manual
precedence cannot mask the automatic result.

- [ ] **Step 4: Exercise exclude and re-include on one auto-included medium project**

Choose one exact medium project ID reported by automatic mode. Invoke
`set-project-state` first with that ID and `excluded`, then with the same ID and
`included`. Before and after, record read-only counts for source, evidence,
semantic-node, and candidate rows.

Expected: state changes succeed without changing those counts. If no medium
project exists, record `not exercised: no eligible medium candidate`.

- [ ] **Step 5: Apply the user's intended final decisions**

Build a private decision table from the exact Task 7 Step 2 response. For
every row, invoke `set-project-state` once with the row's exact ID and either
`included` or `excluded`. Reject any row whose ID is absent from the candidate
set, and verify that the CLI echoes the same ID and state.

- [ ] **Step 6: Capture the review-mode authoritative final state**

```bash
.venv/bin/portfolio-maker list-projects --workspace . --format table
```

Expected: every project reflects the explicit user decision, overriding the
temporary automatic comparison.

---

### Task 8: Generate and Validate Restricted Artifacts

**Files:**
- Create: `.portfolio-maker/artifacts/master-profile.json`
- Create: `.portfolio-maker/artifacts/master-profile.md`
- Create: `.portfolio-maker/artifacts/portfolio-draft.md`
- Create: `.portfolio-maker/artifacts/portfolio-public.json`
- Create: `.portfolio-maker/artifacts/portfolio.html`
- Create: `.portfolio-maker/reviews/real-filesystem-pilot/restricted/`

**Interfaces:**
- Consumes: Task 7 final project decisions and restricted artifact policy
- Produces: richest local/restricted portfolio set and preserved restricted baseline

The router must hand Tasks 8–9 to `$portfolio-artifacts`. The child skill must
revalidate current source, project, and artifact policy before each scope
generation.

- [ ] **Step 1: Revalidate current policy and project decisions**

Run the reusable Artifact Revalidation Gate. Expected: current restricted
policy produces the user-approved final project state with no unresolved
boundary changes.

- [ ] **Step 2: Generate the restricted artifact set**

```bash
.venv/bin/portfolio-maker build-profile --workspace .
.venv/bin/portfolio-maker draft-portfolio --workspace .
.venv/bin/portfolio-maker render-html --workspace .
```

Expected: all five artifacts exist.

- [ ] **Step 3: Verify artifact structure**

```bash
test -s .portfolio-maker/artifacts/master-profile.json
test -s .portfolio-maker/artifacts/master-profile.md
test -s .portfolio-maker/artifacts/portfolio-draft.md
test -s .portfolio-maker/artifacts/portfolio-public.json
test -s .portfolio-maker/artifacts/portfolio.html
```

Expected: every command exits 0.

- [ ] **Step 4: Run filename-only privacy scans**

```bash
rg -l -i '/Users/|file://|BEGIN [A-Z ]*PRIVATE KEY|(api[_-]?key|password|token|secret|credential)[[:space:]]*[:=]' .portfolio-maker/artifacts
rg -l 'request[_ -]?id|result[_ -]?id|source[_ -]?hash|result[_ -]?hash' .portfolio-maker/artifacts
```

Expected: no output. Do not print matching lines if a filename is returned.

- [ ] **Step 5: Verify active project/evidence consistency**

Compare `list-projects`, master profile, draft, manifest, and HTML. Expected:

- included project IDs appear consistently;
- excluded, stale, rejected, and unassigned projects do not appear;
- each material claim has an approved evidence link;
- no unsupported role, approach, or outcome is invented.

- [ ] **Step 6: Preserve the restricted baseline mechanically**

```bash
mkdir -p .portfolio-maker/reviews/real-filesystem-pilot/restricted
cp -p .portfolio-maker/artifacts/master-profile.json .portfolio-maker/artifacts/master-profile.md .portfolio-maker/artifacts/portfolio-draft.md .portfolio-maker/artifacts/portfolio-public.json .portfolio-maker/artifacts/portfolio.html .portfolio-maker/reviews/real-filesystem-pilot/restricted/
shasum -a 256 .portfolio-maker/reviews/real-filesystem-pilot/restricted/*
```

Expected: five exact copies and five hashes. The destination must be new and private.

- [ ] **Step 7: Exercise the interactive HTML**

Start a local-only server from `.portfolio-maker/artifacts` in a reusable PTY
session and retain the session ID:

```bash
.venv/bin/python -m http.server 8765 --bind 127.0.0.1
```

Using the in-app browser, open `http://127.0.0.1:8765/portfolio.html` and verify:

- page loads without a remote application backend;
- project filters change visible cards;
- a project detail opens;
- timeline entries render;
- empty/absent fields do not create unsupported claims.

Do not wait on the foreground process in the main execution lane. After browser
validation, send Ctrl-C to the retained PTY session and verify the server
exits.

- [ ] **Step 8: Ask for restricted portfolio qualitative review**

Pause and ask the user to review the HTML for project boundaries, titles, summaries, missing work, overstatement, and overall usefulness. Record feedback before changing scope.

---

### Task 9: Generate Open-Public Artifacts and Prove Isolation

**Files:**
- Modify then restore: `.portfolio-maker/reviews/artifact-approval.json`
- Regenerate: `.portfolio-maker/artifacts/`
- Create: `.portfolio-maker/reviews/real-filesystem-pilot/open_public/`

**Interfaces:**
- Consumes: Task 8 restricted baseline
- Produces: open-public comparison evidence and restored restricted final artifacts

- [ ] **Step 1: Save the exact restricted artifact policy**

```bash
cp -p .portfolio-maker/reviews/artifact-approval.json .portfolio-maker/reviews/real-filesystem-pilot/restricted-artifact-approval.json
shasum -a 256 .portfolio-maker/reviews/artifact-approval.json .portfolio-maker/reviews/real-filesystem-pilot/restricted-artifact-approval.json
```

Expected: both hashes match. Record the hash privately.

- [ ] **Step 2: Set all four policies to open-public**

Use `apply_patch` so each artifact policy has exactly:

```json
{
  "delivery_scope": "open_public",
  "include_local": false,
  "include_public_github": true,
  "include_private_github": false
}
```

Preserve each policy's explicit exclusion arrays.

- [ ] **Step 3: Regenerate the artifact set**

Run the reusable Artifact Revalidation Gate under the new `open_public`
policy. Any public-scope boundary change requires a fresh user decision before
generation. Then run:

```bash
.venv/bin/portfolio-maker build-profile --workspace .
.venv/bin/portfolio-maker draft-portfolio --workspace .
.venv/bin/portfolio-maker render-html --workspace .
```

Expected: generation succeeds even when public evidence is sparse or empty.

- [ ] **Step 4: Preserve open-public outputs**

```bash
mkdir -p .portfolio-maker/reviews/real-filesystem-pilot/open_public
cp -p .portfolio-maker/artifacts/master-profile.json .portfolio-maker/artifacts/master-profile.md .portfolio-maker/artifacts/portfolio-draft.md .portfolio-maker/artifacts/portfolio-public.json .portfolio-maker/artifacts/portfolio.html .portfolio-maker/reviews/real-filesystem-pilot/open_public/
```

- [ ] **Step 5: Prove local/private isolation**

Build the sensitive-value scan set directly from the current source approval,
private source baseline, vision records, and managed-history records. It must
contain every approved local source label and URI, private repository name and
URL, absolute user path, managed locator, request/result identifier, and stored
hash. Scan `open_public/` in fixed-string, filename-only mode for each non-empty
value; never echo either the pattern or matching line.

Also run:

```bash
rg -l '/Users/|file://' .portfolio-maker/reviews/real-filesystem-pilot/open_public
```

Expected: zero hits. Public GitHub activity appears only when it was exactly approved.

- [ ] **Step 6: Compare scope behavior**

Compare restricted and open-public manifest/HTML. Expected:

- delivery scope changes;
- local and private evidence disappear;
- public GitHub evidence remains only when allowed;
- sparse public evidence yields an honest reduced or empty result.

- [ ] **Step 7: Restore restricted policy and artifacts**

```bash
cp -p .portfolio-maker/reviews/real-filesystem-pilot/restricted-artifact-approval.json .portfolio-maker/reviews/artifact-approval.json
shasum -a 256 .portfolio-maker/reviews/artifact-approval.json .portfolio-maker/reviews/real-filesystem-pilot/restricted-artifact-approval.json
```

Expected: restored and saved policy hashes match. Validate the restored policy
through `load_artifact_policy`, run the reusable Artifact Revalidation Gate,
and reapply only identity-stable decisions from `project-decisions.md`. Then
rerun:

```bash
.venv/bin/portfolio-maker build-profile --workspace .
.venv/bin/portfolio-maker draft-portfolio --workspace .
.venv/bin/portfolio-maker render-html --workspace .
```

Expected: current `.portfolio-maker/artifacts/` hashes match the Task 8 restricted baseline unless later approved vision evidence has not yet been added. At this point, before Task 10, hashes should match exactly.

---

### Task 10: Run the Real Vision Capability Pilot

**Files:**
- Create: `.portfolio-maker/reviews/vision-requests/`
- Create via vision skill: closed-schema result files
- Create: `.portfolio-maker/derived/vision/`
- Modify: `.portfolio-maker/portfolio.db`
- Regenerate after approved inclusion: `.portfolio-maker/artifacts/`

**Interfaces:**
- Consumes: Task 4 approved local sources and Task 7 active projects
- Produces: pending/completed/status-only vision records and optional explicitly included derived evidence

- [ ] **Step 1: List eligible ingested local source IDs read-only**

```bash
sqlite3 .portfolio-maker/portfolio.db "SELECT id, display_name, status FROM sources WHERE type = 'local_file' AND status = 'ingested' ORDER BY id;"
```

Expected: safe source IDs and display names for user selection.

- [ ] **Step 2: Ask the user to select two exact sources**

Pause and request:

- one approved ordinary image source ID;
- one approved multi-page PDF or Office source ID.

If either class is unavailable, record the missing class and test only the available class. Do not choose on the user's behalf.

- [ ] **Step 3: Obtain explicit confirmation for the image**

Ask the user to confirm that the selected image may be exposed to the current Codex session for analysis. Without a fresh affirmative response, do not run the prepare command.

- [ ] **Step 4: Prepare and execute the image request**

Invoke `prepare-vision-analysis` with `--workspace .`,
`--confirm-codex-share`, and the exact integer image source ID approved in Step
2. Capture the request path and request ID emitted by that command. Invoke
`$portfolio-vision-analysis` only for that exact pending request. The skill may
read only its bound source and must write a closed-schema result.

- [ ] **Step 5: Apply and review the image result**

Invoke `apply-vision-analysis --workspace . --result` with the exact result
path returned by the child skill, then run:

```bash
.venv/bin/portfolio-maker list-vision-analyses --workspace .
```

Expected: completed medium/high results begin `pending`; status-only outcomes create no evidence.

- [ ] **Step 6: Ask the user to include or exclude the image analysis**

Pause with a safe summary. Resolve the analysis ID from the immediately
preceding list output and verify that it is bound to the approved request. Run
`set-vision-analysis-state` with that exact ID and the user's exact
`included`/`excluded` choice. Never include low-confidence or status-only
output.

- [ ] **Step 7: Repeat confirmation and execution for the multi-page source**

After a separate affirmative user response, prepare the exact source, invoke `$portfolio-vision-analysis`, apply the result, and list analyses.

Expected:

- supported is valid only if the current session actually reviews the required page range;
- otherwise the result is `unsupported`, `unavailable`, `failed`, or `cancelled`;
- no status-only result creates evidence or project claims.

- [ ] **Step 8: Regenerate restricted artifacts after any included result**

If vision state changed, first run the reusable Artifact Revalidation Gate.
Any new evidence assignment or boundary change requires a fresh user decision
and an update to `project-decisions.md`. Then run:

```bash
.venv/bin/portfolio-maker build-profile --workspace .
.venv/bin/portfolio-maker draft-portfolio --workspace .
.venv/bin/portfolio-maker render-html --workspace .
```

Expected: only explicit included medium/high derived evidence appears. Pending, excluded, low-confidence, and status-only results remain absent.

If at least one result is included, preserve this exact post-vision restricted
set and its hashes:

```bash
mkdir -p .portfolio-maker/reviews/real-filesystem-pilot/restricted-after-vision
cp -p .portfolio-maker/artifacts/master-profile.json .portfolio-maker/artifacts/master-profile.md .portfolio-maker/artifacts/portfolio-draft.md .portfolio-maker/artifacts/portfolio-public.json .portfolio-maker/artifacts/portfolio.html .portfolio-maker/reviews/real-filesystem-pilot/restricted-after-vision/
shasum -a 256 .portfolio-maker/reviews/real-filesystem-pilot/restricted-after-vision/*
```

- [ ] **Step 9: Re-run privacy and open-public isolation checks**

Restricted artifacts must omit source URI, absolute path, request/result IDs,
hashes, adapter metadata, and raw observations.

If at least one vision result was included, repeat Task 9 Steps 2–7 against
the post-vision state: regenerate `open_public`, replace the prior comparison
copies mechanically, run the fixed-string scan including every vision-derived
identifier and safe display phrase, restore the restricted policy, and
regenerate restricted artifacts. The final current artifact hashes must match
`restricted-after-vision/`.

If no vision result was included, record that the pre-vision open-public
isolation result remains authoritative and that no local vision evidence was
eligible to project.

---

### Task 11: Exercise Pilot-Managed History Safely

**Files:**
- Create: `.portfolio-maker/reviews/history-policy.json`
- Create: `.portfolio-maker/reviews/history-plans/`
- Conditional create: `.portfolio-maker/reviews/history-journal/`
- Conditional create: `.portfolio-maker/quarantine/`
- Conditional create: `/Users/june_kim/Documents/portfolio-maker-real-pilot-backup-0.2.2`

**Interfaces:**
- Consumes: pilot-owned managed snapshots and SQLite rows from prior tasks
- Produces: dry-run plan, bound backup receipt, negative-binding evidence, and optional normal apply result

- [ ] **Step 1: Verify no-policy preserve-all behavior**

Before creating a history policy:

```bash
.venv/bin/portfolio-maker history-plan --workspace .
```

Expected: retain-all/no-op behavior; no managed file or SQLite row is deleted.

- [ ] **Step 2: Create and review a sample policy**

```bash
.venv/bin/portfolio-maker history-policy --workspace . --write-sample
```

Open `.portfolio-maker/reviews/history-policy.json`. Do not change retention values without a separate user review.

- [ ] **Step 3: Ask the user to approve the exact retention policy**

Pause with a safe policy summary. Continue only after the user approves the policy values.

- [ ] **Step 4: Create a dry-run plan**

```bash
.venv/bin/portfolio-maker history-plan --workspace .
```

Expected: private plan and safe summary. Inspect every candidate and prove it is pilot-managed history, not an original source, active semantic material, approval file, or current/referenced artifact.

- [ ] **Step 5: Handle a no-candidate plan honestly**

If the plan has no executable candidates, record `valid no-op: insufficient expired unreferenced managed history` and skip Steps 6–10. Do not create artificial source changes or force a destructive candidate.

- [ ] **Step 6: Create the exact empty backup directory**

```bash
mkdir /Users/june_kim/Documents/portfolio-maker-real-pilot-backup-0.2.2
test -d /Users/june_kim/Documents/portfolio-maker-real-pilot-backup-0.2.2
test -z "$(find /Users/june_kim/Documents/portfolio-maker-real-pilot-backup-0.2.2 -mindepth 1 -maxdepth 1 -print -quit)"
```

Expected: directory is real, outside the workspace, and empty.

- [ ] **Step 7: Export the exact plan**

Resolve the plan ID from the persisted plan created in Step 4 and verify it
matches the CLI output. Invoke `history-export` with that exact ID and:

```text
/Users/june_kim/Documents/portfolio-maker-real-pilot-backup-0.2.2
```

Expected: bound receipt and archive are produced.

- [ ] **Step 8: Verify negative binding before mutation**

Run `history-apply` once with a deliberately incorrect confirmation phrase while keeping the real plan ID and receipt.

Expected: controlled failure before journal creation, quarantine move, or SQLite mutation. Do not print the real confirmation phrase in commentary.

- [ ] **Step 9: Ask for exact apply confirmation**

Show the user a safe plan summary and backup success. Ask the user to approve applying the plan. The agent reads the private plan's exact `confirmation_phrase` locally only after approval.

- [ ] **Step 10: Apply the exact plan**

Resolve all three values from private managed records: the persisted plan ID,
the export receipt emitted in Step 7, and the plan's confirmation phrase.
Verify plan and receipt binding once more, then invoke `history-apply` with
those exact three values.

Expected: only exact pilot-managed candidates and bound SQLite rows are processed. If interrupted naturally, stop and use the reported execution ID with exactly one of `--resume` or `--recover`; do not create a new cleanup run.

- [ ] **Step 11: Verify source immutability after history**

Re-run `stat` and `shasum -a 256` for every representative source in `source-baseline.md`.

Expected: size, modification time, and content hash all match the baseline.

---

### Task 12: Final Privacy Audit, User Acceptance, and Report

**Files:**
- Create/finalize: `.portfolio-maker/reviews/real-filesystem-pilot/report.md`
- Read: every pilot artifact and private test-evidence directory
- No tracked source or documentation modification

**Interfaces:**
- Consumes: Tasks 1–11 evidence
- Produces: `PASS`, `NEEDS_WORK`, or `BLOCKED` practical-use decision

- [ ] **Step 1: Verify exact pilot identity and Git cleanliness**

```bash
git rev-parse HEAD
git status --porcelain=v1 --untracked-files=all
```

Expected: exact candidate HEAD and no non-ignored source/test/doc change.

- [ ] **Step 2: Re-run representative source immutability checks**

Compare final source measurements with `source-baseline.md`.

Expected: every measured source is unchanged. Any mismatch is a Critical pilot failure.

- [ ] **Step 3: Run final filename-only leakage scans**

Scan:

```text
.portfolio-maker/artifacts/
.portfolio-maker/reviews/real-filesystem-pilot/restricted/
.portfolio-maker/reviews/real-filesystem-pilot/open_public/
```

for actual absolute paths, `file://`, private repository names/URLs, credential patterns, request/result IDs, hashes, and managed locators. Record filenames only. Do not print matched sensitive values.

Expected:

- deliverable artifacts contain no forbidden locator or secret;
- open-public contains no local/private evidence;
- private test evidence may contain baseline paths/hashes only in `source-baseline.md`, which is not a deliverable.

- [ ] **Step 4: Reconcile counts and decisions**

Record:

- discovered local/public/private candidates;
- approved/excluded/skipped/failed sources;
- project candidate, included, excluded, review-required, and unassigned counts;
- anchor recall;
- review versus automatic differences;
- vision outcomes and decisions;
- history no-op or apply outcome.

- [ ] **Step 5: Write the private report**

Use `apply_patch` to create `report.md` with:

```markdown
# Portfolio Maker 0.2.2 Real Filesystem Pilot

## Decision
PENDING_USER_ACCEPTANCE

## Exact Candidate
## Scope and User Approvals
## Discovery Results
## Semantic Index and Project Grouping
## Review and Automatic Decisions
## Restricted Artifacts
## Open-Public Isolation
## Vision Capability Results
## Managed-History Results
## Source Immutability
## Privacy and Locator Audit
## User Qualitative Review
## Skipped Items and Residual Risks
## Commands and Exit Statuses
```

Keep actual secret values out of the report.

- [ ] **Step 6: Ask the user for final qualitative acceptance**

Provide clickable local links to:

- restricted `portfolio.html`;
- restricted `portfolio-draft.md`;
- project-candidates review;
- final private report.

Ask the user to judge project boundaries, omissions, overstatement, readability, and whether the result is useful as a base portfolio.

- [ ] **Step 7: Apply the decision rule**

After the user's response, replace `PENDING_USER_ACCEPTANCE` with exactly one
of the following:

- `PASS` only when every mandatory design criterion passes and the user accepts the result;
- `NEEDS_WORK` when a behavior, grouping, privacy, or usability criterion fails;
- `BLOCKED` only when required environment or user approval prevented verification.

- [ ] **Step 8: Preserve all pilot evidence**

Do not delete the worktree, `.portfolio-maker/`, backup directory, build outputs, or report. Do not push, deploy, or mutate GitHub issues. Hand off exact local paths and remaining risks.
