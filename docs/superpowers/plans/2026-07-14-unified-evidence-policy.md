# 통합 근거 풀과 생성물별 근거 선택 정책 구현 계획

상태: Issue #12 구현 완료 기준 및 검증 기록
설계: docs/superpowers/specs/2026-07-14-unified-evidence-policy-design.md
추적 Issue: #12

현재 구현은 #4 승인·discovery 정책, #2 origin/migration/공통 selector, #1 공개 activity 반영과
artifact별 restricted/open_public 선택을 포함한다. Issue #12는 실제 public hosting이나 #3
회사/JD 맞춤 생성을 포함하지 않으므로 열린 상태로 유지한다.

구현 상태: Stage A(#4), Stage B(#2), Stage C(#1), artifact builder 적용, CLI·Sites gate와
현재 문서 정합성 갱신을 완료했다. 명령별 실행 근거는
`docs/reviews/2026-07-14-unified-evidence-policy-verification.md`에 기록한다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 승인된 로컬 파일, 승인된 공개 GitHub, 명시 승인된 private GitHub 근거를 하나의 evidence pool로 통합한다. `portfolio-public.json`과 `portfolio.html`은 기본적으로 제한 공유 산출물로 생성하며, artifact별로 포함·제외를 선택할 수 있게 한다.

**Architecture:** discovery는 scan root의 사용자가 선택한 제외 폴더와 GitHub private opt-in을 적용해 source/activity inventory를 만든다. 기존 SQLite normalized evidence graph는 origin visibility를 보유하고, 모든 artifact builder는 하나의 EvidenceSelectionService를 통해 approval, artifact include/exclude policy, delivery scope를 적용한다. 파일명에 `public`이 있어도 기본 `delivery_scope`는 `restricted`이며, `open_public` output은 별도 재검증 뒤에만 생성·배포할 수 있다.

**Tech Stack:** Python 3.11+, 기존 SQLite repository/migration, GitHub CLI gh, 기존 Portfolio Maker CLI/Codex skill, vanilla TypeScript/Vite static renderer, Sites hosting gate

## Global Constraints

- 일반 로컬 discovery는 사용자가 선택한 scan root에서 사용자가 지정한 제외 폴더만 제외한다.
- `.portfolio-maker` workspace, non-regular file, unsafe symlink는 운영상 하드 제외로 유지한다.
- `forbidden_paths`는 기존 approval 호환 alias로 유지하고 `excluded_directories`와 합쳐 적용한다.
- private GitHub discovery는 `gh auth status`, `private_sources_allowed=true`, repository allowlist, source/activity approval을 모두 요구한다.
- private repository 파일의 clone/raw ingestion은 이번 범위에 포함하지 않는다. 현재 GitHub metadata/activity connector만 확장한다.
- 모든 생성물은 공통 EvidenceSelectionService를 사용한다.
- 기본 `restricted` 산출물은 승인된 local, public GitHub, exact-approved private GitHub를 포함할 수 있다. 이는 자동 공개 허가가 아니다.
- `portfolio_public_manifest`와 `portfolio_html`도 위 기본 `restricted` 정책을 사용한다. 기존 파일명은 호환성을 위해 유지한다.
- `open_public`은 명시적인 선택이 필요하다. 초기 구현에서 local 또는 private GitHub는 `open_public`에 포함할 수 없으며 validation error가 된다.
- restricted output은 로컬 파일, 직접 전달, private Sites deployment에만 쓸 수 있다. public Sites deployment는 거부한다.
- credential, token, private raw path, secret-shaped text는 어떤 artifact에도 출력하지 않는다.
- artifact policy가 없는 기존 workspace는 0.1.0 호환 경로로 public manifest/HTML에서 private/local evidence를 계속 제외한다.
- 회사/JD 맞춤 포트폴리오(#3), Google Drive, OCR, semantic search, MCP/app-server, external LLM은 구현하지 않는다.

---

## Task 1: Source approval과 artifact delivery policy schema

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
- `ArtifactDeliveryScope = Literal["restricted", "open_public"]`
- `ArtifactPolicy(artifact_kind, delivery_scope, include_local, include_public_github, include_private_github, excluded_source_uris, excluded_repositories, excluded_activity_urls)`
- `load_artifact_policy(paths: WorkspacePaths) -> ArtifactPolicySet`
- `write_sample_artifact_policy(paths: WorkspacePaths, force: bool = False) -> Path`

- [ ] **Step 1: Write failing schema tests**

Cover all of the following.

~~~python
def test_new_default_html_policy_is_restricted_and_allows_approved_origins(tmp_path):
    policy = load_artifact_policy(WorkspacePaths.from_root(tmp_path))
    html = policy.for_kind("portfolio_html")

    assert html.delivery_scope == "restricted"
    assert html.include_local is True
    assert html.include_public_github is True
    assert html.include_private_github is True

def test_open_public_policy_rejects_local_or_private_include_flags(tmp_path):
    write_artifact_policy(tmp_path, {
        "portfolio_html": {
            "delivery_scope": "open_public",
            "include_local": True,
            "include_private_github": False,
        }
    })

    with pytest.raises(ApprovalFormatError, match="open_public"):
        load_artifact_policy(WorkspacePaths.from_root(tmp_path))
~~~

Also cover legacy `forbidden_paths` merge, an empty private-activity approval list, malformed delivery scope, and compatibility when `artifact-approval.json` is absent.

- [ ] **Step 2: Run focused tests and confirm the new fields fail**

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_approval.py tests/test_artifact_approval.py
~~~

Expected: FAIL because the new fields, policy file, and loader do not exist.

- [ ] **Step 3: Implement policy parsing and compatibility defaults**

Implement:

1. `excluded_directories` as a canonical path list; merge legacy `forbidden_paths` without duplicating entries.
2. `approved_private_github_activity_urls` with private-only URL validation.
3. Separate `.portfolio-maker/reviews/artifact-approval.json` loading and sample generation.
4. New sample defaults: every current generator is `restricted` and permits all three origin types subject to source/activity approvals.
5. Missing artifact policy preserves existing 0.1.0 behavior, including public-GitHub-only manifest/HTML.
6. Controlled `ApprovalFormatError` messages with no sensitive path, repository, or token output.

- [ ] **Step 4: Run focused tests**

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_approval.py tests/test_artifact_approval.py
~~~

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/application/approval.py src/portfolio_maker/application/artifact_approval.py src/portfolio_maker/application/models.py src/portfolio_maker/workspace.py tests/test_approval.py tests/test_artifact_approval.py
git commit -m "feat: add artifact delivery policy schema"
~~~

## Task 2: Local exclusion folders and private GitHub opt-in discovery

**Files:**
- Modify: `src/portfolio_maker/infrastructure/local_discovery.py`
- Modify: `src/portfolio_maker/infrastructure/policy.py`
- Modify: `src/portfolio_maker/infrastructure/github_connector.py`
- Modify: `src/portfolio_maker/application/discovery.py`
- Modify: `src/portfolio_maker/adapters/cli.py`
- Test: `tests/test_local_discovery.py`
- Test: `tests/test_github_connector.py`
- Create: `tests/test_github_private_policy.py`

**Interfaces:**
- CLI option: `portfolio-maker discover --exclude-directory PATH` (repeatable)
- Application request: `DiscoverSourcesRequest.excluded_directories`
- `discover_github_candidates(..., private_sources_allowed: bool, allowed_repositories: tuple[str, ...])`
- `GitHubRepositoryCandidate.is_private: bool`
- `GitHubActivityCandidate.is_private: bool`

- [ ] **Step 1: Write discovery regressions**

Cover:

- normal files under a selected scan root are candidates unless their parent lies in a selected excluded directory;
- `.portfolio-maker`, unsafe symlink, non-regular file remain hard exclusions;
- `gh auth status` failure is a controlled private discovery status without credential output;
- private repositories are skipped unless opt-in and allowlist pass;
- excluded repositories override allowlist and private opt-in;
- private activity URLs cannot be approved through the public activity field.

- [ ] **Step 2: Run focused tests before implementation**

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_local_discovery.py tests/test_github_connector.py tests/test_github_private_policy.py
~~~

Expected: FAIL for the new exclusion semantics and private origin path.

- [ ] **Step 3: Implement discovery semantics**

1. Persist `--exclude-directory` in `source-approval.json` before later ingest/build revalidation.
2. Remove ordinary implicit user-policy directory exclusions; preserve only selected excluded directories and operational hard boundaries.
3. Require authenticated gh, `private_sources_allowed=true`, canonical allowlist membership, non-exclusion, and exact private activity approval before a private activity becomes eligible evidence.
4. Do not clone repositories, retrieve raw repository files, or persist tokens.
5. Distinguish `excluded_directory`, permissions, symlink, extraction, GitHub-auth, and policy states in discovery output without leaking private paths or URLs.

- [ ] **Step 4: Run focused tests and perform redaction scan**

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_local_discovery.py tests/test_github_connector.py tests/test_github_private_policy.py
rg -n "ghp_|token=|Authorization" .portfolio-maker/reviews || true
~~~

Expected: PASS with no credential-like output.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/infrastructure/local_discovery.py src/portfolio_maker/infrastructure/policy.py src/portfolio_maker/infrastructure/github_connector.py src/portfolio_maker/application/discovery.py src/portfolio_maker/adapters/cli.py tests/test_local_discovery.py tests/test_github_connector.py tests/test_github_private_policy.py
git commit -m "feat: discover approved local and private github evidence"
~~~

## Task 3: Origin visibility migration and common selector

**Files:**
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Create: `src/portfolio_maker/application/evidence_selection.py`
- Modify: `src/portfolio_maker/domain/models.py`
- Modify: `src/portfolio_maker/application/models.py`
- Test: `tests/test_sqlite_repository.py`
- Create: `tests/test_evidence_selection.py`

**Interfaces:**
- `ArtifactKind = Literal["master_profile", "portfolio_draft", "portfolio_public_manifest", "portfolio_html"]`
- `EvidenceOrigin = Literal["local", "public_github", "private_github"]`
- `ArtifactDeliveryScope = Literal["restricted", "open_public"]`
- `EvidenceSelectionRequest(artifact_kind, policy, current_approval)`
- `EvidenceSelectionResult(included_source_ids, included_evidence_ids, included_claim_ids, excluded_decisions, policy_hash)`
- `select_evidence_for_artifact(repository: SQLiteRepository, request: EvidenceSelectionRequest) -> EvidenceSelectionResult`

- [ ] **Step 1: Write selection-matrix tests**

Use one fixture containing approved local, approved public GitHub, exact-approved private GitHub, excluded, and unknown evidence.

| Request | Expected selection |
|---|---|
| restricted `portfolio_html` | approved local + approved public GitHub + exact-approved private GitHub |
| restricted `portfolio_public_manifest` with one excluded URL | same, except that URL |
| open_public `portfolio_html` | approved public GitHub only |
| any scope with unknown/revoked/stale evidence | exclude with deterministic reason |

Assert that unapproved private activity is excluded even when `include_private_github=true`, and that an `open_public` policy asking for local/private origins fails validation before output generation.

- [ ] **Step 2: Run focused tests and confirm the selector is absent**

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_evidence_selection.py
~~~

Expected: FAIL until migration and selector exist.

- [ ] **Step 3: Add additive visibility migration**

Add origin visibility/origin type columns where necessary with safe defaults. Preserve legacy `public_safe` values and map old records conservatively. Do not rewrite or delete raw snapshots. Record migration version and validate pre-policy workspace fixtures.

- [ ] **Step 4: Implement EvidenceSelectionService**

The selector must:

- reload current source approval and artifact policy;
- reject unknown visibility and revoked/stale/damaged evidence;
- apply source/activity approval before artifact policy;
- apply artifact include flags, then selected source/repository/activity exclusions;
- require all private GitHub gates before admitting private origin evidence to `restricted`;
- admit approved local evidence to `restricted` without exposing raw absolute paths;
- reject local/private origins for `open_public` in the first implementation;
- return included IDs, excluded reasons, and a deterministic policy hash.

No builder may reinterpret this policy.

- [ ] **Step 5: Run migration and selector tests**

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_sqlite_repository.py tests/test_evidence_selection.py
~~~

Expected: PASS with old and new workspace fixtures.

- [ ] **Step 6: Commit**

~~~bash
git add src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/application/evidence_selection.py src/portfolio_maker/domain/models.py src/portfolio_maker/application/models.py tests/test_sqlite_repository.py tests/test_evidence_selection.py
git commit -m "feat: centralize evidence delivery selection"
~~~

## Task 4: Apply common selection to all generators

**Files:**
- Modify: `src/portfolio_maker/application/build_profile.py`
- Modify: `src/portfolio_maker/application/draft_portfolio.py`
- Modify: `src/portfolio_maker/application/public_portfolio.py`
- Modify: `src/portfolio_maker/application/render_html.py`
- Modify: `src/portfolio_maker/infrastructure/static_site.py`
- Test: `tests/test_profile_and_portfolio.py`
- Test: `tests/test_public_portfolio.py`
- Test: `tests/test_render_html.py`
- Create: `tests/test_static_site.py`

**Interfaces:**
- All four builders call `select_evidence_for_artifact` before writing output.
- Every `artifacts.input_manifest` records delivery scope, policy hash, included IDs, excluded IDs, reasons, and origin counts.
- `render_html` consumes its manifest policy only; it introduces no separate public/private origin filter.
- Restricted private repository provenance uses an approved/safe label. Any private URL display requires a separately approved shared locator.

- [ ] **Step 1: Add cross-artifact regressions**

Create a fixture with local, public GitHub, and private GitHub evidence. Assert:

- default restricted profile/draft/manifest/HTML include all and only approved origins;
- an excluded source disappears from one selected artifact but remains in the common pool;
- `portfolio-public.json` and `portfolio.html` carry `delivery_scope: "restricted"` provenance;
- open-public manifest/HTML reject local/private origins;
- raw local paths, tokens, credentials, secret-shaped text, and unapproved private repository names never appear in output;
- repeated builds are deterministic and reference the same source/evidence/claim IDs.

- [ ] **Step 2: Run focused builder tests before implementation**

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_render_html.py tests/test_static_site.py
~~~

Expected: FAIL for common selection, restricted sharing metadata, and scope gates.

- [ ] **Step 3: Replace builder-specific filtering**

Keep each builder responsible for presentation only. It can format selector-approved records but must not decide whether a source is local, public, private, excluded, or open-public eligible.

- [ ] **Step 4: Validate output contracts**

- Restricted HTML remains self-contained: no runtime fetch, SQLite access, source file access, or credential data.
- Restricted HTML/manifest may include approved local and private evidence, but only safe labels/approved locators; never raw local paths.
- Open-public HTML/manifest is regenerated from its own selector result and fails if any local/private origin remains.
- The visual renderer exposes no misleading “publicly hosted” claim merely because the artifact filename contains `public`.

- [ ] **Step 5: Run focused tests**

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_render_html.py tests/test_static_site.py
~~~

Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add src/portfolio_maker/application/build_profile.py src/portfolio_maker/application/draft_portfolio.py src/portfolio_maker/application/public_portfolio.py src/portfolio_maker/application/render_html.py src/portfolio_maker/infrastructure/static_site.py tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_render_html.py tests/test_static_site.py
git commit -m "feat: apply delivery policy to portfolio artifacts"
~~~

## Task 5: CLI, skill, Sites gate, and user workflow

**Files:**
- Modify: `src/portfolio_maker/adapters/cli.py`
- Modify: `.agents/skills/portfolio-maker/SKILL.md`
- Modify: `README.md`
- Modify: `web/portfolio/src/main.ts`
- Modify: `web/portfolio/src/styles.css`
- Test: `tests/test_cli.py`
- Test: `tests/test_render_html.py`
- Test: `tests/test_static_site.py`

**Interfaces:**
- `portfolio-maker approve --write-sample-artifact-policy [--force]`
- `portfolio-maker discover --exclude-directory PATH` (repeatable)
- artifact build/render commands load `artifact-approval.json` from the workspace root.
- rendering records `restricted` or `open_public` in the manifest and the generated HTML metadata.
- any Sites public deployment path rejects `restricted` output; private deployment/preview is allowed only after normal static validation.

- [ ] **Step 1: Add CLI and deployment-gate regressions**

Cover sample policy creation, missing policy compatibility, invalid open-public policy, exclusion option persistence, artifact metadata, and the following gates:

~~~python
def test_public_deployment_rejects_restricted_html(tmp_path):
    artifact = build_restricted_html(tmp_path)

    with pytest.raises(StaticSiteError, match="open_public"):
        prepare_public_deployment(artifact)

def test_private_deployment_accepts_validated_restricted_html(tmp_path):
    artifact = build_restricted_html(tmp_path)

    assert prepare_private_deployment(artifact).delivery_scope == "restricted"
~~~

- [ ] **Step 2: Implement documented workflow**

Document and implement this sequence:

1. initialize/review source approval;
2. set local excluded folders;
3. run `gh auth login` only when private discovery is desired;
4. set `private_sources_allowed=true` and selected `allowed_repositories`;
5. review and separately approve public/private activity URLs;
6. create `artifact-approval.json` and select exclusions per artifact;
7. generate `restricted` outputs for local use, direct transmission, or private Sites deployment;
8. select `open_public` only when its additional validation passes, then explicitly choose a public deployment;
9. never infer deployment authorization from the legacy `public` filename.

- [ ] **Step 3: Run focused CLI and frontend tests**

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_cli.py tests/test_render_html.py tests/test_static_site.py
(cd web/portfolio && npm ci && npm run build)
~~~

Expected: PASS.

- [ ] **Step 4: Commit**

~~~bash
git add src/portfolio_maker/adapters/cli.py .agents/skills/portfolio-maker/SKILL.md README.md web/portfolio/src/main.ts web/portfolio/src/styles.css tests/test_cli.py tests/test_render_html.py tests/test_static_site.py
git commit -m "docs: expose restricted portfolio sharing workflow"
~~~

## Task 6: Migration, documentation, and final verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md`
- Modify: `docs/superpowers/specs/2026-07-14-unified-evidence-policy-design.md`
- Modify: `README.md`
- Modify: `.agents/skills/portfolio-maker/SKILL.md`
- Create: `docs/reviews/2026-07-14-unified-evidence-policy-verification.md`
- Update: GitHub Issue #12
- Keep open: Issue #3

- [ ] **Step 1: Run the complete validation set**

~~~bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
(cd web/portfolio && npm ci && npm run build)
git diff --check
git show --check --format=short HEAD
~~~

Expected: all Python tests, Vite build, and Git checks pass.

- [ ] **Step 2: Verify migration behavior**

Open a pre-policy workspace fixture and confirm it preserves current artifact selection. Open a new policy-initialized workspace and confirm:

- default `restricted` manifest/HTML include only approved local/public/private origins;
- excluded evidence remains in the pool but is absent from the selected output;
- `open_public` rejects local/private origins;
- public deployment rejects restricted output.

- [ ] **Step 3: Synchronize public documentation**

Document:

- local discovery as scan root minus selected excluded directories;
- private GitHub discovery as gh-authenticated opt-in;
- common evidence pool and per-artifact include/exclude policy;
- `portfolio-public.json`/`portfolio.html` as legacy names with default restricted sharing;
- `open_public` as explicit, stricter, separate generation;
- no automatic public hosting, no private raw repository ingestion, and #3 tailoring remains out of scope.

- [ ] **Step 4: Final self-review**

Confirm no builder bypasses EvidenceSelectionService; no restricted or open-public output has credentials/raw local paths; no restricted artifact can enter public Sites deployment; old approval/workspaces remain readable.

- [ ] **Step 5: Commit docs and verification**

~~~bash
git add docs/superpowers/specs/2026-07-14-unified-evidence-policy-design.md docs/superpowers/plans/2026-07-14-unified-evidence-policy.md docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md README.md .agents/skills/portfolio-maker/SKILL.md docs/reviews/2026-07-14-unified-evidence-policy-verification.md
git commit -m "docs: define restricted evidence sharing rollout"
~~~

## Definition of Done

- [x] Local scan uses selected excluded directories as the ordinary user-controlled exclusion policy.
- [x] Private GitHub discovery requires gh authentication and explicit opt-in/allowlist/approval.
- [x] Every generator uses the normalized common evidence graph through EvidenceSelectionService.
- [x] Artifact policy can exclude selected sources/evidence independently per output.
- [x] Default restricted public-manifest/HTML output can use approved local, public GitHub, and exact-approved private GitHub evidence.
- [x] Open-public output is explicit and rejects local/private origins in the first implementation.
- [x] Restricted output cannot be deployed publicly; private hosting or verified-recipient delivery remains available.
- [x] Input manifests record policy, delivery scope, origin type, and selection decisions.
- [x] Existing workspace migration and complete regression suite pass.
- [x] README, skill, Phase spec, design spec, plan, and verification report agree; Issue #12 remains open.
