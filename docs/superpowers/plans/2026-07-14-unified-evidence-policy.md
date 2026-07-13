# 통합 근거 풀과 생성물별 근거 선택 정책 구현 계획

상태: 설계 문서 작성 완료, 구현 전
설계: docs/superpowers/specs/2026-07-14-unified-evidence-policy-design.md
추적 Issue: #12

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로컬 파일, 공개 GitHub, 명시적으로 허용한 private GitHub 근거를 하나의 evidence pool로 통합하고, 생성물별 artifact policy로 포함·제외 범위를 선택할 수 있게 한다.

**Architecture:** discovery는 사용자가 선택한 제외 폴더와 GitHub private opt-in을 적용해 source/activity inventory를 만든다. 기존 SQLite normalized evidence graph는 visibility를 보유하고, 모든 artifact builder는 하나의 EvidenceSelectionService를 통해 artifact visibility와 include/exclude policy를 적용한다. public artifact는 private evidence를 하드 차단하며, private artifact만 별도 private hosting gate 뒤에서 private evidence를 사용할 수 있다.

**Tech Stack:** Python 3.11+, 기존 SQLite repository/migration, GitHub CLI gh, 기존 Portfolio Maker CLI/Codex skill, vanilla TypeScript/Vite static renderer

## Global Constraints

- 일반 로컬 discovery는 사용자가 선택한 scan root에서 사용자가 지정한 제외 폴더만 제외한다.
- `.portfolio-maker` workspace, non-regular file, unsafe symlink는 운영상 하드 제외로 유지한다.
- `forbidden_paths`는 기존 approval 호환 alias로 유지하고 `excluded_directories`와 합쳐 적용한다.
- private GitHub discovery는 `gh auth status`, `private_sources_allowed=true`, repository allowlist, source/activity approval을 모두 요구한다.
- private repository 파일의 clone/raw ingestion은 이번 범위에 포함하지 않는다. 현재 GitHub metadata/activity connector만 확장한다.
- 모든 생성물은 공통 EvidenceSelectionService를 사용한다.
- public artifact는 private evidence를 어떠한 override로도 포함하지 않는다.
- private artifact는 `visibility: private`와 private deployment gate 없이는 생성·배포하지 않는다.
- 기존 source-approval.json, 기존 database, 기존 profile/draft/HTML의 보수적 기본 동작을 유지한다.
- credential, token, private raw path, private repository URL은 public report/artifact/log에 출력하지 않는다.
- 회사/JD 맞춤 포트폴리오(#3), Google Drive, OCR, semantic search, MCP/app-server, external LLM은 구현하지 않는다.

---

## Task 1: Approval schema와 정책 파일 분리

**Files:**
- Modify: `src/portfolio_maker/application/approval.py`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/workspace.py`
- Create: `src/portfolio_maker/application/artifact_approval.py`
- Test: `tests/test_approval.py`
- Create: `tests/test_artifact_approval.py`

**Interfaces:**
- `SourceApproval.excluded_directories: tuple[str, ...]`
- `SourceApproval.approved_private_github_activity_urls: tuple[str, ...]`
- `ArtifactPolicy(artifact_kind, visibility, include_local, include_public_github, include_private_github, excluded_source_uris, excluded_repositories, excluded_activity_urls)`
- `load_artifact_policy(paths: WorkspacePaths) -> ArtifactPolicySet`
- `write_sample_artifact_policy(paths: WorkspacePaths, force: bool = False) -> Path`

- [ ] **Step 1: Write failing schema tests**

```python
def test_existing_approval_defaults_private_activity_to_empty(tmp_path):
    paths = WorkspacePaths.from_root(tmp_path)
    write_sample_approval(paths)
    approval = load_approval(paths)
    assert approval.excluded_directories == ()
    assert approval.approved_private_github_activity_urls == ()

def test_public_artifact_policy_rejects_private_evidence(tmp_path):
    policy = load_artifact_policy(WorkspacePaths.from_root(tmp_path))
    public_policy = policy.for_kind("portfolio_html")
    assert public_policy.visibility == "public"
    assert public_policy.include_private_github is False
```

- [ ] **Step 2: Run focused tests and confirm the new fields fail**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_approval.py tests/test_artifact_approval.py
```

Expected: FAIL because the new fields, policy file, and loader do not exist.

- [ ] **Step 3: Implement schema parsing and compatibility defaults**

Implement:

1. `excluded_directories` as a canonical path list; merge legacy `forbidden_paths` without duplicating entries.
2. `approved_private_github_activity_urls` with private-only URL validation.
3. Separate `.portfolio-maker/reviews/artifact-approval.json` loading and sample generation.
4. Defaults that preserve current behavior: local/public GitHub for private profile/draft, public GitHub only for public manifest/HTML.
5. Controlled `ApprovalFormatError` messages with no path content beyond safe labels.

- [ ] **Step 4: Run focused tests**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_approval.py tests/test_artifact_approval.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/application/approval.py src/portfolio_maker/application/artifact_approval.py src/portfolio_maker/application/models.py src/portfolio_maker/workspace.py tests/test_approval.py tests/test_artifact_approval.py
git commit -m "feat: add artifact evidence policy schema"
```

## Task 2: Local discovery를 제외 폴더 중심으로 전환

**Files:**
- Modify: `src/portfolio_maker/infrastructure/local_discovery.py`
- Modify: `src/portfolio_maker/infrastructure/policy.py`
- Modify: `src/portfolio_maker/application/discovery.py`
- Modify: `src/portfolio_maker/adapters/cli.py`
- Test: `tests/test_local_discovery.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- CLI option: `portfolio-maker discover --exclude-directory PATH` (repeatable)
- Application request: `DiscoverSourcesRequest.excluded_directories`
- Policy method: `FilePolicy.is_excluded_directory(path: Path) -> bool`

- [ ] **Step 1: Write discovery regressions**

```python
def test_discovery_keeps_normal_files_outside_selected_excluded_directories(tmp_path):
    included = tmp_path / "included" / "notes.md"
    excluded = tmp_path / "excluded" / "secret.md"
    included.parent.mkdir()
    excluded.parent.mkdir()
    included.write_text("included", encoding="utf-8")
    excluded.write_text("excluded", encoding="utf-8")

    candidates = discover_local_candidates(tmp_path, excluded_directories=(excluded.parent,))

    assert included in [candidate.path for candidate in candidates]
    assert excluded not in [candidate.path for candidate in candidates]
```

- [ ] **Step 2: Run the regression before implementation**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_local_discovery.py -k excluded
```

Expected: FAIL until the new exclusion semantics are implemented.

- [ ] **Step 3: Implement exclusion semantics**

Remove ordinary hidden/sensitive-directory filtering as an implicit user policy. Apply only the persisted `excluded_directories` plus legacy `forbidden_paths`. Keep `.portfolio-maker`, non-regular files, unsafe symlinks, descriptor safety, and extraction-time secret masking as hard operational boundaries.

Persist CLI `--exclude-directory` values in `source-approval.json` before ingest/profile/draft revalidation. The discovery report must distinguish `excluded_directory` from permission, symlink, and extraction failures.

- [ ] **Step 4: Run focused local and CLI tests**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_local_discovery.py tests/test_cli.py
```

Expected: PASS, including existing symlink and policy regressions.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/infrastructure/local_discovery.py src/portfolio_maker/infrastructure/policy.py src/portfolio_maker/application/discovery.py src/portfolio_maker/adapters/cli.py tests/test_local_discovery.py tests/test_cli.py
git commit -m "feat: support user-selected local exclusion folders"
```

## Task 3: GitHub private opt-in discovery

**Files:**
- Modify: `src/portfolio_maker/infrastructure/github_connector.py`
- Modify: `src/portfolio_maker/application/discovery.py`
- Modify: `src/portfolio_maker/application/approval.py`
- Test: `tests/test_github_connector.py`
- Test: `tests/test_local_discovery.py` or a new `tests/test_github_private_policy.py`

**Interfaces:**
- `discover_github_candidates(..., private_sources_allowed: bool, allowed_repositories: tuple[str, ...])`
- `GitHubRepositoryCandidate.is_private: bool`
- `GitHubActivityCandidate.is_private: bool`
- private approval field: `approved_private_github_activity_urls`

- [ ] **Step 1: Add fixture-backed private discovery regressions**

Cover:

- `gh auth status` failure is a controlled discovery status, not a credential print.
- private repositories are skipped when `private_sources_allowed=false`.
- private repositories appear with visibility when the opt-in and allowlist pass.
- excluded repositories win over allowlist and private opt-in.
- private activity URLs are never accepted by the public activity approval field.

- [ ] **Step 2: Run focused tests to establish the missing behavior**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_github_connector.py tests/test_github_private_policy.py
```

Expected: FAIL until private visibility and approval separation are implemented.

- [ ] **Step 3: Implement the private opt-in path**

Require:

1. authenticated `gh` for private discovery;
2. `private_sources_allowed=true`;
3. canonical repository in `allowed_repositories`;
4. repository not in `excluded_repositories`;
5. exact private activity approval before a private activity becomes eligible evidence.

Do not add GitHub file clone, raw repository reads, or token persistence.

- [ ] **Step 4: Run focused tests and verify private metadata is redacted from public reports**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_github_connector.py tests/test_github_private_policy.py
rg -n "ghp_|token=|Authorization|private-user-images" .portfolio-maker/reviews || true
```

Expected: tests pass and no credential-like output is present.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/infrastructure/github_connector.py src/portfolio_maker/application/discovery.py src/portfolio_maker/application/approval.py tests/test_github_connector.py tests/test_github_private_policy.py
git commit -m "feat: add opt-in private github discovery"
```

## Task 4: Visibility migration and common evidence selector

**Files:**
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Create: `src/portfolio_maker/application/evidence_selection.py`
- Modify: `src/portfolio_maker/domain/models.py`
- Modify: `src/portfolio_maker/application/models.py`
- Test: `tests/test_sqlite_repository.py`
- Create: `tests/test_evidence_selection.py`

**Interfaces:**
- `ArtifactKind = Literal["master_profile", "portfolio_draft", "portfolio_public_manifest", "portfolio_html"]`
- `ArtifactVisibility = Literal["public", "private"]`
- `EvidenceSelectionRequest(artifact_kind, policy, current_approval)`
- `EvidenceSelectionResult(included_source_ids, included_evidence_ids, included_claim_ids, excluded_decisions)`
- `select_evidence_for_artifact(repository: SQLiteRepository, request: EvidenceSelectionRequest) -> EvidenceSelectionResult`

- [ ] **Step 1: Write selection matrix tests**

```python
def test_public_html_selection_excludes_local_and_private_evidence(repository):
    result = select_evidence_for_artifact(repository, request_for("portfolio_html"))
    assert result.included_evidence_ids == public_github_ids
    assert {item.reason for item in result.excluded_decisions} >= {"private", "local_not_allowed"}

def test_private_profile_selection_requires_explicit_private_opt_in(repository):
    result = select_evidence_for_artifact(repository, request_for("master_profile"))
    assert private_evidence_id not in result.included_evidence_ids
```

- [ ] **Step 2: Run focused tests and confirm the selector is absent**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_evidence_selection.py
```

Expected: FAIL until the common selector and visibility model exist.

- [ ] **Step 3: Add additive SQLite visibility migration**

Add visibility columns with safe defaults to sources/evidence/projects/claims where required. Preserve existing `public_safe` values and map legacy rows conservatively. Do not rewrite or delete raw snapshots. Record migration version and validate old database fixtures.

- [ ] **Step 4: Implement the common selector**

The selector must:

- reload current source approval and artifact policy;
- reject unknown visibility;
- apply include flags and excluded source/repository/activity lists;
- require explicit private approval for private evidence;
- hard-reject private evidence for public artifact kinds;
- return included IDs plus excluded reasons;
- provide a deterministic policy hash for artifact provenance.

- [ ] **Step 5: Run migration and selector tests**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_sqlite_repository.py tests/test_evidence_selection.py
```

Expected: PASS with old and new workspace fixtures.

- [ ] **Step 6: Commit**

```bash
git add src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/application/evidence_selection.py src/portfolio_maker/domain/models.py src/portfolio_maker/application/models.py tests/test_sqlite_repository.py tests/test_evidence_selection.py
git commit -m "feat: centralize artifact evidence selection"
```

## Task 5: Apply the selector to every generator

**Files:**
- Modify: `src/portfolio_maker/application/build_profile.py`
- Modify: `src/portfolio_maker/application/draft_portfolio.py`
- Modify: `src/portfolio_maker/application/public_portfolio.py`
- Modify: `src/portfolio_maker/application/render_html.py`
- Modify: `src/portfolio_maker/infrastructure/static_site.py`
- Test: `tests/test_profile_and_portfolio.py`
- Test: `tests/test_public_portfolio.py`
- Test: `tests/test_render_html.py`

**Interfaces:**
- All four builders call `select_evidence_for_artifact` before writing output.
- Every `artifacts.input_manifest` includes policy hash, included IDs, excluded IDs, and reasons.
- `render_html` accepts only the artifact policy; it must not introduce an independent private/public filter.

- [ ] **Step 1: Add cross-artifact leakage regressions**

Create a fixture with local, public GitHub, and private GitHub evidence. Assert:

- profile/draft default policy includes local and public only;
- explicit private profile/draft policy includes approved private evidence;
- public manifest/HTML never includes private/local evidence;
- an excluded source disappears from only the selected artifact, not from the common pool;
- repeated builds are deterministic and provenance points to the same evidence IDs.

- [ ] **Step 2: Run focused builder tests before implementation**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_render_html.py
```

Expected: FAIL for private/internal selection and artifact-specific exclusion cases.

- [ ] **Step 3: Replace per-builder filters with the selector result**

Keep each builder responsible for presentation only. It may format included records but must not decide whether a source is public/private or included/excluded.

- [ ] **Step 4: Validate public HTML and private internal output gates**

Public HTML must reject private policy, private source IDs, local raw paths, and credential-shaped strings. A private HTML artifact must require `visibility=private` and may only be handed to private Sites deployment.

- [ ] **Step 5: Run focused builder tests**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_render_html.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/portfolio_maker/application/build_profile.py src/portfolio_maker/application/draft_portfolio.py src/portfolio_maker/application/public_portfolio.py src/portfolio_maker/application/render_html.py src/portfolio_maker/infrastructure/static_site.py tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_render_html.py
git commit -m "feat: apply evidence policy to all artifacts"
```

## Task 6: CLI, skill, and user-facing policy workflow

**Files:**
- Modify: `src/portfolio_maker/adapters/cli.py`
- Modify: `.agents/skills/portfolio-maker/SKILL.md`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- `portfolio-maker approve --write-sample-artifact-policy [--force]`
- `portfolio-maker discover --exclude-directory PATH` repeatable option
- All profile/draft/render commands load artifact-approval.json from the repository root.

- [ ] **Step 1: Add CLI regressions**

Cover sample policy creation, missing policy compatibility, invalid private approval, exclusion option persistence, and commands executed from repository root.

- [ ] **Step 2: Implement the CLI and skill workflow**

Document:

1. install dependencies;
2. run `gh auth login` when private discovery is desired;
3. set `private_sources_allowed=true` and selected `allowed_repositories`;
4. review public and private candidates separately;
5. approve public/private activity URLs in their separate fields;
6. create artifact-approval.json and choose exclusions per artifact;
7. run generators from repository root;
8. never send private evidence to public hosting.

- [ ] **Step 3: Run CLI tests**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_cli.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/portfolio_maker/adapters/cli.py .agents/skills/portfolio-maker/SKILL.md README.md tests/test_cli.py
git commit -m "docs: expose unified evidence policy workflow"
```

## Task 7: Sites/private hosting gates and privacy validation

**Files:**
- Modify: `src/portfolio_maker/application/render_html.py`
- Modify: `src/portfolio_maker/infrastructure/static_site.py`
- Modify: `web/portfolio/src/main.ts`
- Modify: `web/portfolio/src/styles.css`
- Test: `tests/test_static_site.py`
- Test: `tests/test_render_html.py`
- Create: `docs/reviews/2026-07-14-unified-evidence-policy-verification.md`

**Interfaces:**
- public HTML refuses private evidence regardless of policy values;
- private HTML output is labeled internal and cannot call public Sites deployment;
- static output validator scans for private source names, URLs, raw paths, tokens, and runtime fetch;
- private deployment metadata is kept separate from public artifact metadata.

- [ ] **Step 1: Add public/private output regressions**

```python
def test_public_html_rejects_private_evidence_even_when_requested(tmp_path):
    policy = private_enabled_public_html_policy(tmp_path)
    with pytest.raises(HtmlRenderError, match="private evidence"):
        render_html(RenderHtmlRequest(workspace=tmp_path))
```

- [ ] **Step 2: Run build and static validation**

```bash
(cd web/portfolio && npm ci && npm run build)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_static_site.py tests/test_render_html.py
```

Expected: PASS with no private marker or runtime fetch in public output.

- [ ] **Step 3: Browser verification**

Verify public output with empty and public evidence, and private output only in a private test workspace. Check filter/detail/timeline/keyboard/mobile/reduced-motion. Do not use public deployment during tests.

- [ ] **Step 4: Commit verification**

```bash
git add src/portfolio_maker/application/render_html.py src/portfolio_maker/infrastructure/static_site.py web/portfolio/src/main.ts web/portfolio/src/styles.css tests/test_static_site.py tests/test_render_html.py docs/reviews/2026-07-14-unified-evidence-policy-verification.md
git commit -m "test: verify artifact visibility gates"
```

## Task 8: Migration, documentation, and final verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md`
- Modify: `docs/superpowers/specs/2026-07-14-unified-evidence-policy-design.md`
- Modify: `README.md`
- Modify: `.agents/skills/portfolio-maker/SKILL.md`
- Update: GitHub Issue #12
- Keep open: Issue #3

**Interfaces:**
- current behavior is documented as compatibility baseline;
- new private/internal artifact behavior is explicitly opt-in;
- public docs never imply that private source is public by default.

- [ ] **Step 1: Run the complete validation set**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
(cd web/portfolio && npm ci && npm run build)
git diff --check
git show --check --format=short HEAD
```

Expected: all Python tests, Vite build, and Git checks pass.

- [ ] **Step 2: Verify migration behavior**

Open a pre-policy workspace fixture and confirm it retains current default artifact selection. Open a new workspace fixture and confirm the sample policies are explicit.

- [ ] **Step 3: Synchronize public documentation**

Document:

- local discovery as scan root minus selected excluded directories;
- private GitHub discovery as gh-authenticated opt-in;
- common evidence pool and per-artifact include/exclude policy;
- public hard deny for private evidence;
- private artifact/private Sites deployment gate;
- current non-goals: private raw repository ingestion and #3 tailoring.

- [ ] **Step 4: Final self-review**

Confirm no builder bypasses EvidenceSelectionService, no public output contains private/local raw provenance, and old approval/workspaces remain readable.

- [ ] **Step 5: Commit docs and verification**

```bash
git add docs/superpowers/specs/2026-07-14-unified-evidence-policy-design.md docs/superpowers/plans/2026-07-14-unified-evidence-policy.md docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md README.md .agents/skills/portfolio-maker/SKILL.md docs/reviews/2026-07-14-unified-evidence-policy-verification.md
git commit -m "docs: define unified evidence policy rollout"
```

## Definition of Done

- [ ] Local scan uses selected excluded directories as the ordinary user-controlled exclusion policy.
- [ ] Private GitHub discovery requires gh authentication and explicit opt-in/allowlist/approval.
- [ ] Existing normalized evidence graph is shared by every generator.
- [ ] Artifact policy can exclude selected sources/evidence independently per output.
- [ ] Public output always excludes private evidence.
- [ ] Private output requires explicit private visibility and private deployment.
- [ ] Input manifests record policy and selection decisions.
- [ ] Existing workspace migration and complete regression suite pass.
- [ ] README, skill, Phase spec, design spec, plan, Issue #12, and verification report agree.