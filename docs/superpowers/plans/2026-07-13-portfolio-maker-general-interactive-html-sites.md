
# 일반형 인터랙티브 HTML 포트폴리오 + @sites 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 승인된 public-safe 근거로 사용자의 기본 포트폴리오를 본인과 방문자가 확인할 수 있는 정적 인터랙티브 HTML로 만들고, 같은 사이트 표면을 Codex `@sites`에서 디자인·빌드·선택적 호스팅한다.

**Architecture:** Portfolio Maker의 Python/SQLite 계층은 승인, evidence/claim 검증, public manifest 생성의 단일 권위로 유지한다. Sites 프로젝트는 이 빌드 시점 manifest를 번들에 포함하는 UI/정적 export 계층이며, 런타임에 SQLite·원본 파일·원격 API를 읽지 않는다. 생성된 로컬 `.portfolio-maker/artifacts/portfolio.html`은 Sites 배포 여부와 무관하게 canonical artifact로 남긴다.

**Tech Stack:** Python 3.11+, 기존 Portfolio Maker CLI/application/infrastructure, SQLite, Codex `@sites` bundled starter/vinext, TypeScript/React, Vite-compatible static build, Codex in-app Browser, 설치된 `emilkowalski/skills`의 디자인·모션 리뷰 원칙

## Global Constraints

- 이번 단계의 콘텐츠는 회사·채용공고 대상이 없는 **일반형 기본 포트폴리오**다.
- 회사/JD별 맞춤 문장과 대상별 재작성은 Issue #3 후속 확장으로 구현하지 않는다.
- 공개 페이지 입력은 `public_safe=true`이고 승인·근거 연결·정책 재검증을 통과한 project/claim/evidence만 사용한다.
- `portfolio.db`, SQLite sidecar, `.portfolio-maker/reviews/`, 원본 파일, raw/local path, private snapshot, credential은 Sites 입력과 HTML/JS 번들에 포함하지 않는다.
- HTML은 외부 tracker, CDN, remote API, runtime data fetch 없이 로컬 파일로 직접 열려야 한다.
- `emilkowalski/skills`는 디자인·모션·접근성 리뷰 기준으로만 사용하며 runtime dependency로 번들하지 않는다.
- `@sites` 디자인 선택은 정확히 3개의 비교안만 순차적으로 제시하고 사용자 선택 후 구현한다.
- `npm run build`, 출력 안전성 검사, 로컬 파일 열기, 브라우저 수동 검증이 통과하기 전에는 Sites 호스팅을 실행하지 않는다.
- private 호스팅이 기본이며 public URL 배포는 별도 명시적 승인을 받은 경우에만 실행한다.
- 기존 `#4 → #2 → #1` runtime 계약과 0.1.0 Markdown/JSON 산출물 호환성을 깨지 않는다.
- 모든 작업은 현재 `origin/main`과 `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`의 구현 경계를 먼저 확인하고, 부모 checkout의 기획용 미추적 파일을 덮어쓰지 않는다.

---

## Task 1: Public portfolio manifest 계약과 안전성 projection

**Files:**
- Create: `src/portfolio_maker/application/public_portfolio.py`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/workspace.py`
- Test: `tests/test_public_portfolio.py`

**Interfaces:**

- Consumes: 현재 SQLite의 projects, career_claims, evidence_items, claim_evidence, artifacts와 기존 approval/policy 재검증 결과
- Produces:
  - `PublicPortfolioRequest(workspace: Path)`
  - `PublicPortfolioResult(manifest_path: Path, project_count: int, claim_count: int)`
  - `build_public_portfolio_manifest(request: PublicPortfolioRequest) -> PublicPortfolioResult`

Manifest의 최소 형태는 다음과 같이 고정한다.

```json
{
  "version": 1,
  "portfolio_type": "general",
  "audience": "self-and-public-visitors",
  "projects": [
    {
      "id": "project-1",
      "title": "safe project label",
      "summary": "evidence-backed summary",
      "technologies": [],
      "outcomes": [],
      "claims": [
        {
          "text": "public-safe claim",
          "evidence": [
            {
              "label": "safe source label",
              "url": "https://github.com/owner/repo/pull/1"
            }
          ]
        }
      ]
    }
  ]
}
```

- [ ] **Step 1: Write failing manifest tests**

```python
def test_public_manifest_contains_only_public_safe_evidence(tmp_path):
    result = build_public_portfolio_manifest(PublicPortfolioRequest(workspace=tmp_path))
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert manifest["version"] == 1
    assert manifest["portfolio_type"] == "general"
    assert all(
        "public_safe=false" not in json.dumps(project)
        for project in manifest["projects"]
    )


def test_public_manifest_excludes_private_paths_and_unlinked_claims(tmp_path):
    result = build_public_portfolio_manifest(PublicPortfolioRequest(workspace=tmp_path))
    manifest_text = result.manifest_path.read_text(encoding="utf-8")

    assert "/Users/" not in manifest_text
    assert ".portfolio-maker/" not in manifest_text
    assert "unlinked internal claim" not in manifest_text
```

- [ ] **Step 2: Run the focused tests and verify the missing contract fails**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_public_portfolio.py
```

Expected: FAIL because the request/result types and manifest builder do not exist.

- [ ] **Step 3: Implement the minimal projection**

Implement `build_public_portfolio_manifest` so that it:

1. reloads the current approval/policy state;
2. selects only public-safe projects and claims;
3. requires every emitted claim to have at least one linked public-safe evidence row;
4. emits safe labels and approved public GitHub URLs only;
5. strips absolute paths, snapshot paths, private repository data, and secret-shaped strings;
6. writes `.portfolio-maker/artifacts/portfolio-public.json` through the existing managed-artifact writer;
7. records an `artifacts` row with kind `portfolio_public_manifest` and an input manifest of project/claim/evidence IDs.

Do not generate new career claims or infer outcomes in this task.

- [ ] **Step 4: Run focused tests and verify they pass**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_public_portfolio.py
```

Expected: PASS.

- [ ] **Step 5: Commit the contract slice**

```bash
git add src/portfolio_maker/application/public_portfolio.py src/portfolio_maker/application/models.py src/portfolio_maker/workspace.py tests/test_public_portfolio.py
git commit -m "feat: add public portfolio manifest"
```

## Task 2: Sites project surface and build-time data boundary

**Files:**
- Create: `web/portfolio/` using the bundled Sites starter
- Create/Modify: `web/portfolio/app/page.tsx`
- Create/Modify: `web/portfolio/app/layout.tsx`
- Create/Modify: `web/portfolio/app/globals.css`
- Create/Modify: `web/portfolio/vite.config.ts` or the starter's equivalent build config
- Create: `web/portfolio/src/generated/portfolio-data.ts` as a generated, gitignored file
- Create: `web/portfolio/.openai/hosting.json` only when the Sites project is created by the hosting flow
- Modify: `.gitignore`

**Interfaces:**

- Consumes: `.portfolio-maker/artifacts/portfolio-public.json`
- Produces: a static Sites build whose JavaScript bundle contains the manifest at build time and never fetches local JSON at runtime

- [ ] **Step 1: Initialize the Sites project before editing product UI**

Use the bundled Sites initializer once with `web/portfolio` as its target. Preserve the starter package manager and lockfile.

Run:

```bash
mkdir -p web/portfolio
cd web/portfolio
/path/to/sites/scripts/init-site.sh "$PWD"
npm run dev
```

Use the exact local URL printed by the starter for Codex Browser preview. Do not initialize a second Sites project.

- [ ] **Step 2: Configure local-file-safe static output**

Set the build's asset base to relative paths (for example, Vite `base: "./"`) so the exported HTML can be opened with `open portfolio.html` or a browser `file://` URL. Do not use runtime `fetch("/data/...")`; embed the generated data in `src/generated/portfolio-data.ts`.

The generated module must have this shape:

```ts
export const portfolioData = {
  version: 1,
  portfolio_type: "general",
  audience: "self-and-public-visitors",
  projects: []
} as const;
```

- [ ] **Step 3: Verify the data boundary**

Run:

```bash
rg -n "portfolio\.db|\.portfolio-maker/reviews|/Users/|file://|fetch\(" web/portfolio
```

Expected: no product source or runtime fetch references. Generated build input is allowed to contain only the public manifest fields.

- [ ] **Step 4: Commit the Sites scaffold**

```bash
git add web/portfolio .gitignore
git commit -m "feat: scaffold sites portfolio surface"
```

## Task 3: General portfolio design direction and review gate

**Files:**
- Create: `docs/design/2026-07-13-general-portfolio-design-brief.md`
- Create: `docs/design/2026-07-13-general-portfolio-motion-review.md`

**Interfaces:**

- Consumes: the general-portfolio goal, current public manifest contract, and user-selected Sites design option
- Produces: one selected visual direction and an implementation-ready design brief

- [ ] **Step 1: Prepare the design brief**

The brief must define:

- audience: the portfolio owner and first-time public visitors;
- primary action: understand what the person built and inspect supporting evidence;
- sections: introduction, selected projects, capabilities, evidence/provenance, contact or public links;
- interaction: project filter, project detail expansion, evidence links;
- responsive behavior: mobile-first stacked layout and desktop multi-column layout;
- accessibility: visible focus, semantic headings, keyboard-operable controls, contrast, reduced-motion behavior;
- content rule: no company/JD targeting and no unsupported claims.

- [ ] **Step 2: Present exactly three sequential Sites design options**

Use Codex `@sites` design picker with exactly three comparable options. Wait for the user's selection before editing the product UI. Each option must include title, image path, and an implementation brief.

- [ ] **Step 3: Apply the installed emilkowalski review criteria**

Record the selected option's decisions for typography, spacing, hover/focus states, transition duration, gesture behavior, reduced motion, and animation purpose. Prefer transform/opacity and interruptible transitions; do not add motion to keyboard-only actions.

- [ ] **Step 4: Save the brief and review record**

Run:

```bash
git diff --check
```

Expected: PASS with the selected design brief and motion review files containing no trailing whitespace.

- [ ] **Step 5: Commit the design contract**

```bash
git add docs/design/2026-07-13-general-portfolio-design-brief.md docs/design/2026-07-13-general-portfolio-motion-review.md
git commit -m "docs: record general portfolio design direction"
```

## Task 4: Implement the general portfolio UI

**Files:**
- Modify: `web/portfolio/app/page.tsx`
- Modify: `web/portfolio/app/layout.tsx`
- Modify: `web/portfolio/app/globals.css`
- Modify: `web/portfolio/src/generated/portfolio-data.ts`
- Test: `tests/test_public_portfolio.py`
- Test: `web/portfolio/scripts/validate-static-output.mjs`

**Interfaces:**

- Consumes: `portfolioData` from the generated module and the selected design brief
- Produces: one responsive, keyboard-operable, static general portfolio page

- [ ] **Step 1: Write output contract tests**

```python
def test_general_html_contract_requires_sections_and_relative_assets(tmp_path):
    html = (tmp_path / "portfolio.html").read_text(encoding="utf-8")

    assert "Selected projects" in html
    assert "Evidence" in html
    assert 'href="./' in html or 'src="./' in html
    assert "http://cdn" not in html
    assert "https://cdn" not in html
```

- [ ] **Step 2: Implement page structure**

Implement semantic regions for introduction, projects, capabilities, evidence, and public links. Project cards must be generated from `portfolioData.projects`; an empty project list must render a truthful empty state rather than invented content.

- [ ] **Step 3: Implement interactions**

Implement:

- a keyboard-operable filter control;
- project detail expansion with `aria-expanded` and a stable accessible name;
- evidence links that use only manifest-provided approved URLs;
- visible `:focus-visible` styles;
- mobile layout without horizontal scrolling;
- reduced-motion fallback using `prefers-reduced-motion: reduce`.

Do not add analytics, login, comments, editing, server state, or remote search.

- [ ] **Step 4: Implement the motion review findings**

Use motion only for state changes that clarify navigation or detail expansion. Keep transitions interruptible, under 300ms where applicable, and avoid animation on keyboard focus itself. Record any exception in the motion review file.

- [ ] **Step 5: Build and run output validation**

Run:

```bash
cd web/portfolio
npm run build
node scripts/validate-static-output.mjs dist
```

Expected: build succeeds, required sections exist, assets are relative, no external tracker/CDN/API is referenced, and the output can be copied as a standalone HTML artifact.

- [ ] **Step 6: Commit the UI slice**

```bash
git add web/portfolio tests/test_public_portfolio.py
git commit -m "feat: build general interactive portfolio"
```

## Task 5: Integrate the CLI and export the canonical local artifact

**Files:**
- Create: `src/portfolio_maker/infrastructure/site_renderer.py`
- Modify: `src/portfolio_maker/adapters/cli.py`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/workspace.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_public_portfolio.py`
- Modify: `web/portfolio/src/generated/portfolio-data.ts` through the build input generator

**Interfaces:**

- CLI command: `portfolio-maker render-html --workspace PATH`
- Application function: `render_general_portfolio(request: RenderPortfolioRequest) -> RenderPortfolioResult`
- Renderer responsibility: write the public manifest, generate the TypeScript data module, invoke the existing Sites build command, validate the build, and copy the entry HTML/assets into managed artifact paths
- Renderer must return non-zero controlled errors for missing approval, unsafe manifest data, missing Sites project, build failure, or invalid static output

- [ ] **Step 1: Write CLI and integration regressions**

```python
def test_cli_render_html_requires_approved_public_inputs(workspace, capsys):
    exit_code = main(["render-html", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "approval" in captured.err.lower()
    assert "Traceback" not in captured.err


def test_cli_render_html_reports_canonical_artifact(workspace, capsys, monkeypatch):
    monkeypatch.setattr("portfolio_maker.infrastructure.site_renderer.run_site_build", lambda _: None)

    exit_code = main(["render-html", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert ".portfolio-maker/artifacts/portfolio.html" in captured.out
```

- [ ] **Step 2: Implement the renderer adapter**

The adapter must:

1. call `build_public_portfolio_manifest`;
2. write the generated module under the ignored build-input directory;
3. run `npm run build` with `web/portfolio` as the working directory;
4. run the static-output validator;
5. copy the built entry HTML and relative assets into managed artifact paths;
6. record the `portfolio_html` artifact with manifest hash and build metadata;
7. never upload or read the SQLite database from the Sites process.

- [ ] **Step 3: Add the CLI parser branch**

Add `render-html` to `build_parser` and map it to `render_general_portfolio`. Keep business logic out of the CLI adapter.

- [ ] **Step 4: Run focused CLI and portfolio tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_cli.py tests/test_public_portfolio.py
```

Expected: PASS.

- [ ] **Step 5: Commit the integrated export slice**

```bash
git add src/portfolio_maker/infrastructure/site_renderer.py src/portfolio_maker/application/models.py src/portfolio_maker/adapters/cli.py src/portfolio_maker/workspace.py tests/test_cli.py tests/test_public_portfolio.py
git commit -m "feat: export general portfolio html"
```

## Task 6: Browser, accessibility, and privacy verification

**Files:**
- Modify: `tests/test_public_portfolio.py`
- Modify: `web/portfolio/scripts/validate-static-output.mjs`
- Create: `docs/reviews/2026-07-13-general-portfolio-html-verification.md`

**Interfaces:**

- Consumes: the built `portfolio.html`, output validator results, and Codex in-app Browser observations
- Produces: a reproducible verification record and a release decision

- [ ] **Step 1: Run automated checks**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_public_portfolio.py tests/test_cli.py
cd web/portfolio
npm run build
node scripts/validate-static-output.mjs dist
```

Expected: all commands pass.

- [ ] **Step 2: Run browser checks in the Codex in-app Browser**

Check the built local page at the exact URL printed by the development server or the exported file:

- project filter changes visible cards and preserves keyboard focus;
- detail expansion exposes evidence links and correct `aria-expanded`;
- Tab order reaches every control;
- Escape or the close control collapses an expanded detail;
- mobile viewport has no horizontal overflow;
- reduced-motion preference removes nonessential transitions;
- no console/runtime error occurs with an empty manifest and a populated manifest.

Do not install standalone Playwright, Chromium, or another browser.

- [ ] **Step 3: Inspect public output for unsafe content**

Run:

```bash
rg -n "ghp_|token=|password=|/Users/|\.portfolio-maker/reviews|portfolio\.db|file://" .portfolio-maker/artifacts web/portfolio/dist
```

Expected: no matches.

- [ ] **Step 4: Record the verification result**

The review file must record the commit, commands, browser observations, generated artifact paths, and any non-blocking residual risk. It must not contain personal source content or credentials.

- [ ] **Step 5: Commit verification evidence**

```bash
git add tests/test_public_portfolio.py web/portfolio/scripts/validate-static-output.mjs docs/reviews/2026-07-13-general-portfolio-html-verification.md
git commit -m "test: verify general portfolio html"
```

## Task 7: Optional private Sites hosting

**Files:**
- Modify: `web/portfolio/.openai/hosting.json`
- Do not commit: source write credentials, temporary archives, generated user data

**Interfaces:**

- Consumes: the exact successful Sites build and verification record from Task 6
- Produces: a private Sites deployment only when the user authorizes it

- [ ] **Step 1: Create or reuse the Sites project**

Use `create_site` once for a new project and persist only `project_id` plus logical bindings in `.openai/hosting.json`.

- [ ] **Step 2: Package the validated build**

Use the Sites packaging helper. The archive must contain only the validated build output, hosting metadata, and required migrations; it must not contain `.portfolio-maker/`, SQLite, source approval, or raw files.

- [ ] **Step 3: Save and deploy a private version**

Save one version using the validated commit SHA and archive, then prefer `deploy_private_site_version`. Do not call public deployment without a separate explicit approval.

- [ ] **Step 4: Poll and verify**

Poll deployment status until success or failure. After success, open the exact deployed URL in Codex Browser and record the URL without exposing credentials.

- [ ] **Step 5: Commit only safe hosting metadata**

```bash
git add web/portfolio/.openai/hosting.json
git commit -m "chore: record private portfolio site"
```

## Task 8: Full verification and documentation sync

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md`
- Modify: `docs/design/2026-07-13-general-portfolio-design-brief.md`
- Modify: `docs/reviews/2026-07-13-general-portfolio-html-verification.md`
- Update: GitHub Issue #11
- Keep open: GitHub Issue #3 as the company/JD tailoring extension

**Interfaces:**

- Consumes: implemented behavior and verification evidence from Tasks 1–7
- Produces: public docs that distinguish the implemented general portfolio from the planned company-specific extension

- [ ] **Step 1: Run the complete validation set**

Run:

```
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider
cd web/portfolio
npm run build
node scripts/validate-static-output.mjs dist
git diff --check
git show --check --format=short HEAD
```

Expected: Python tests, Sites build, output validator, and Git checks all pass.

- [ ] **Step 2: Update public docs**

README must state:

- general interactive HTML is the next/current public presentation stage;
- it is for self and public visitors, not company/JD tailoring;
- `@sites` handles design/build/optional hosting;
- Issue #3 remains a later extension.

The Phase 1 spec must link this plan and preserve the completed #4/#2/#1 contract.

- [ ] **Step 3: Reconcile Issue #11**

Issue #11 must list #4/#2/#1 as completed prerequisites, remove #3 as a hard prerequisite, and state that #3 consumes the same manifest later for tailored variants.

- [ ] **Step 4: Final self-review**

Confirm:

- no runtime code reads SQLite or local files from the Sites page;
- no unsupported project narrative is invented;
- empty and populated portfolios render truthfully;
- direct local HTML opening works;
- public/private deployment state matches explicit approval;
- company/JD tailoring remains entirely outside this implementation.

- [ ] **Step 5: Commit documentation synchronization**

```bash
git add README.md docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md docs/design docs/reviews
git commit -m "docs: define general portfolio html scope"
```

## Definition of Done

- [ ] A public-safe manifest is generated from the current approved evidence graph.
- [ ] A general-purpose interactive portfolio is rendered without company/JD input.
- [ ] The page works from a local static HTML file.
- [ ] The page passes automated output, privacy, keyboard, responsive, and reduced-motion checks.
- [ ] The same validated site surface builds through Codex `@sites`.
- [ ] Sites hosting is private by default and never automatic for public deployment.
- [ ] `.portfolio-maker/artifacts/portfolio.html` remains the canonical local artifact.
- [ ] README, Phase spec, design brief, verification report, and Issue #11 describe the same behavior.
- [ ] Issue #3 remains open as a separate company/JD tailoring extension.
