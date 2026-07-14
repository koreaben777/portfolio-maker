# #13 Codex 기반 프로젝트 식별·구성 구현 계획

날짜: 2026-07-14
상태: 승인 설계 기반 구현 계획
추적 Issue: [#13](https://github.com/koreaben777/portfolio-maker/issues/13)
선행 설계: [Codex 기반 프로젝트 식별·구성 설계](../specs/2026-07-14-codex-assisted-project-composition-design.md)

## 1. 목표와 범위

이 구현은 현재의 source/evidence technical grouping을 사람이 이해하는 portfolio project로 바꾸는 것이 아니라, 둘을 **분리**한다.

- local file, GitHub repository, GitHub activity는 계속 evidence/source다.
- Codex는 현재 정책을 통과한 안전한 review bundle에서 candidate를 제안한다.
- candidate는 검토용 파일이며 데이터베이스나 생성물의 project가 아니다.
- 사용자가 승인 파일에서 확정한 semantic project만 별도 모델에 materialize한다.
- master profile, Markdown draft, restricted/open public manifest, interactive HTML은 승인된 semantic project만 project로 표시한다.
- 승인된 project가 없으면 evidence는 계속 안전하게 수집·보관하되 portfolio project 목록은 빈 상태로 생성한다.

이번 구현은 #13만 다룬다. #3 회사/JD 맞춤 서술, CLI의 외부 LLM API·token 저장, Sites hosting, private repository raw clone, Drive/OCR/MCP는 포함하지 않는다.

## 2. 확정 인터페이스와 데이터 흐름

### 2.1 두 단계 선택 규칙

기존 evidence selection과 새 project composition을 분리한다.

```text
source approval + ingestion
  → evidence/claim graph (기존 technical grouping은 내부 호환용)
  → master_profile 정책으로 safe review bundle 생성
  → Codex candidate 제안
  → user project approval
  → approved semantic project materialization
  → 각 artifact의 자체 evidence selection
  → selected evidence ∩ approved project links
  → master profile / draft / manifest / HTML
```

review bundle은 현재 `master_profile` artifact policy를 기준으로 만든다. 이 policy는 기본적으로 가장 넓은 restricted evidence inventory다. 그 뒤 각각의 생성물은 자기 artifact policy를 다시 적용한다. 따라서 한 project의 evidence가 HTML 정책에서 제외되면 HTML에는 그 evidence가 나타나지 않으며, 연결된 evidence가 하나도 남지 않은 project는 그 HTML에서 표시하지 않는다.

이 결정은 artifact별 제외 정책을 유지하면서 Codex가 policy 밖의 원본을 읽지 못하게 한다. project review가 필요한 local/private evidence는 `master_profile` 정책을 restricted scope로 포함해야 한다.

### 2.2 review 파일 계약

`WorkspacePaths`에 다음 경로를 추가한다.

```text
.portfolio-maker/reviews/project-review-input.json
.portfolio-maker/reviews/project-candidates.json
.portfolio-maker/reviews/project-candidates.md
.portfolio-maker/reviews/project-approval.json
```

`project-review-input.json`은 CLI가 생성하는 canonical input이다.

```json
{
  "version": 1,
  "artifact_kind": "master_profile",
  "delivery_scope": "restricted",
  "policy_hash": "<sha256>",
  "input_sha256": "<sha256>",
  "evidence": [
    {
      "evidence_id": 101,
      "stable_id": "source-snapshot:7:<hash>",
      "origin": "local",
      "source_label": "안전한 표시명",
      "excerpt": "마스킹된 근거 발췌"
    },
    {
      "evidence_id": 205,
      "stable_id": "github-activity:12",
      "origin": "private_github",
      "source_label": "Private GitHub activity",
      "activity_type": "pull_request",
      "title": "안전한 활동 제목",
      "created_at": "2026-07-01T00:00:00Z"
    }
  ]
}
```

- 원본 경로, `file://` URI, snapshot/database 경로, private repository URL, credential/token은 절대 넣지 않는다. 자동 review bundle은 private activity를 generic safe label로 표현한다.
- private activity의 정확한 URL은 discovery report라는 로컬 승인 표면에서만 확인·선택할 수 있으며, 자동 review bundle과 생성 artifact에는 표시하지 않는다.
- safe label, mask된 local excerpt, public repository label, activity metadata만 허용한다.
- private GitHub activity는 URL 없는 일반 label만 사용한다.

사용자가 approval file에서 직접 승인한 semantic project title/overview는 restricted output에서 private repository name을 display text로 포함할 수 있다. private URL과 raw locator는 어떤 scope에서도 허용하지 않는다.
- `input_sha256`은 canonical JSON에서 자기 자신을 제외하고 계산한다.
- candidate와 approval은 이 hash를 참조해 다른 review input과 섞이지 않게 한다.

Codex가 만드는 `project-candidates.json`은 다음 구조를 따른다.

```json
{
  "version": 1,
  "review_input_sha256": "<sha256>",
  "candidates": [
    {
      "id": "candidate-insurance-rag",
      "status": "candidate",
      "title": "보험 RAG 챗봇",
      "overview": "근거가 보여 주는 범위의 검토용 개요",
      "grouping_rationale": "연결된 evidence가 하나의 목적 있는 작업을 가리키는 이유",
      "evidence_ids": [101, 205],
      "confidence": "medium",
      "review_required": true
    }
  ]
}
```

`project-candidates.md`은 같은 후보의 제목, overview, rationale, confidence, evidence ID와 unassigned count만 표시한다. 두 후보 파일은 candidate 내 evidence ID 중복, 알 수 없는 ID, unsafe text를 허용하지 않는다. candidate 간에는 사용자가 대안적 grouping을 검토할 수 있으므로 evidence ID가 겹칠 수 있지만, artifact에 투영되는 최종 approval project 간에는 겹칠 수 없다.

`project-approval.json`은 사용자가 최종 결정하는 파일이다.

```json
{
  "version": 1,
  "review_input_sha256": "<sha256>",
  "projects": [
    {
      "id": "insurance-rag-chatbot",
      "title": "보험 RAG 챗봇",
      "overview": "사용자가 검토·승인한 근거 기반 개요",
      "evidence_ids": [101, 205],
      "status": "approved"
    }
  ],
  "rejected_candidate_ids": [],
  "unassigned_evidence_ids": []
}
```

- stable project ID는 ASCII kebab-case, title/overview는 비어 있지 않은 안전한 display text다.
- 사용자 직접 project 작성은 허용한다. 즉 candidate file은 선택적 검토 보조물이고 approval의 선행 truth가 아니다.
- merge/split/reassign은 approval의 `projects`와 evidence ID 배치를 수정하는 것으로 표현한다.
- project는 적어도 하나의 current review evidence를 가져야 한다.
- 서로 다른 approved project가 같은 evidence ID를 참조하면 실패한다.
- 명시 unassigned ID는 approved evidence와 겹치거나 review input 밖일 수 없다. 기재되지 않은 non-approved evidence도 결과상 unassigned로 계산해 composition manifest에 기록한다.
- rejected candidate ID는 candidate file이 있으면 그 ID에만 한정한다. candidate 자체는 artifact input이 될 수 없다.

### 2.3 CLI 사용자 흐름

새 명령은 business logic을 application 계층에 두고 CLI는 입력·출력만 연결한다.

```bash
portfolio-maker prepare-project-review --workspace .
# Codex가 $portfolio-maker skill 흐름으로 input을 읽고
# project-candidates.json / project-candidates.md를 작성
portfolio-maker approve --workspace . --write-sample-project-approval
# 사용자가 project-approval.json을 수정
portfolio-maker compose-projects --workspace .
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
portfolio-maker render-html --workspace .
```

- `prepare-project-review`은 master-profile selection을 재계산해 review input을 원자적으로 쓴다.
- `approve --write-sample-project-approval`은 기존 sample writer와 같은 overwrite/--force 원칙을 따른다.
- `compose-projects`는 review input, optional candidate file, approval file, current approval/policy/evidence 상태를 검증한 뒤에만 DB를 갱신한다.
- validation 실패는 입력값을 echo하지 않는 controlled error가 되고 기존 semantic project rows와 기존 artifact 파일을 바꾸지 않는다.
- project approval 파일이 없는 상태에서 build/draft/render는 오류가 아니라 zero-project projection을 만든다. 다만 `compose-projects`만 approval 누락 오류를 낸다.
- Portfolio Maker CLI는 candidate 생성 중 외부 LLM API를 호출하지 않으며 token을 읽거나 저장하지 않는다.

## 3. 구현 작업

### 작업 1 — 실패하는 #13 계약 테스트와 공용 모델 추가

**파일**

- 수정: `src/portfolio_maker/application/models.py`
- 수정: `src/portfolio_maker/domain/models.py`
- 신규: `src/portfolio_maker/application/project_composition.py`
- 신규: `tests/test_project_composition.py`

**구현**

1. `PrepareProjectReviewRequest/Result`, `ComposeProjectsRequest/Result`를 application models에 추가한다.
2. domain/application dataclass로 safe review evidence, candidate, approved project, project projection을 정의한다. 기존 `PublicEvidenceRecord`의 technical `project_id/project_name`은 evidence selection 호환 필드로 유지하고 semantic project의 ID로 재해석하지 않는다.
3. `ProjectCompositionError`와 parse/validation 함수를 작성한다.
4. 먼저 아래 test를 실패 상태로 만든다.
   - review input은 current master selection의 evidence ID만 포함하고 raw local URI, snapshot path, private GitHub URL, secret-shaped text를 포함하지 않는다.
   - candidate/approval의 unknown ID, duplicate ID, empty project, duplicate approved ownership, unsafe ID/text, hash mismatch, stale/revoked/policy-excluded evidence가 controlled error가 된다.
   - candidate가 없어도 valid direct approval은 가능하다.
   - unapproved candidate와 unassigned evidence는 project projection이 아니다.
5. safe text validation은 기존 `mask_public_value`, `safe_local_public_label`, `normalize_label`, secret/control-character helper를 재사용한다. private locator 및 private identifier는 내부 비교만 하고 오류 메시지에 노출하지 않는다.

### 작업 2 — 별도 semantic SQLite 모델과 additive migration

**파일**

- 수정: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- 수정: `tests/test_sqlite_repository.py`
- 수정: `tests/test_project_composition.py`

**구현**

1. guarded `initialize()` transaction의 additive schema에 다음 테이블을 추가한다.

```text
portfolio_projects
  id TEXT PRIMARY KEY
  title TEXT NOT NULL
  overview TEXT NOT NULL
  status TEXT NOT NULL CHECK(status = 'approved')
  approval_sha256 TEXT NOT NULL
  review_input_sha256 TEXT NOT NULL
  created_at / updated_at

portfolio_project_evidence
  project_id TEXT REFERENCES portfolio_projects(id)
  evidence_id INTEGER REFERENCES evidence_items(id)
  support_level TEXT CHECK(direct/contextual)
  PRIMARY KEY(project_id, evidence_id)
  UNIQUE(evidence_id)
```

2. legacy `projects`, `career_claims`, `claim_evidence`, `evidence_items`는 삭제·rename·자동 변환하지 않는다. technical `projects`는 기존 claim/evidence join의 호환용으로만 유지한다.
3. repository에 다음 경계를 추가한다.
   - approved semantic project 및 evidence link 전체를 단일 transaction으로 replace하는 method
   - semantic project와 link를 조회하는 method
   - selected evidence ID와 교차한 ordered semantic projection을 조회/구성하는 method
   - `project_composition` artifact에 project IDs, linked evidence IDs, computed unassigned IDs, approval/review hash를 기록하는 method 또는 기존 `record_artifact`의 안전한 사용
4. replace 전에 approval 전체를 검증하고, 중간 실패 시 기존 semantic table을 그대로 보존한다.
5. focused test로 foreign key, evidence owner uniqueness, legacy tables/rows 보존, replacement의 merge/split/reassign 결과, migration된 기존 workspace의 초기 semantic project 0개를 검증한다.

### 작업 3 — review bundle 및 approval composition application service

**파일**

- 구현: `src/portfolio_maker/application/project_composition.py`
- 수정: `src/portfolio_maker/workspace.py`
- 수정: `src/portfolio_maker/application/build_profile.py`
- 수정: `src/portfolio_maker/adapters/cli.py`
- 수정: `tests/test_project_composition.py`
- 수정: `tests/test_cli.py`

**구현**

1. `prepare_project_review()`은 기존 `build_profile(... invalidate_portfolio_draft=False)`로 evidence/claim graph를 최신화한 후 `EvidenceSelectionService`를 `master_profile` policy로 실행한다.
2. selected `PublicEvidenceRecord`를 review-safe object로 변환한다. local evidence는 safe source label/masked excerpt만, public activity는 canonical public metadata만, private activity는 URL과 repository name이 없는 metadata만 넣는다.
3. review input 파일을 managed-file helper로 원자적으로 쓰고, current selection policy hash와 evidence set hash를 기록한다.
4. `write_sample_project_approval()`은 빈 projects/rejected/unassigned와 현재 review input hash를 가진 sample을 생성한다. review input이 없으면 controlled error를 낸다.
5. `compose_projects()`은 current master selection을 다시 계산하고 input hash, policy hash, available evidence set을 비교한다. 후보 파일이 존재하면 schema와 safety를 검증하되 candidate를 DB truth로 쓰지 않는다.
6. approval을 검증한 뒤 approved projects만 materialize하고 draft/manifest/HTML을 invalidation한다. master profile도 다음 build에서 semantic summary가 최신이 되도록 invalidate하거나 즉시 다시 생성한다.
7. CLI에는 `prepare-project-review`, `compose-projects`, `approve --write-sample-project-approval`를 연결하고 success count와 path만 출력한다. 오류는 traceback 없이 처리한다.
8. CLI test는 sample overwrite guard, missing review/approval, stale input, successful direct approval, candidate safety failure, existing state preservation을 검증한다.

### 작업 4 — approved-project projection을 생성물에 적용

**파일**

- 수정: `src/portfolio_maker/application/build_profile.py`
- 수정: `src/portfolio_maker/application/draft_portfolio.py`
- 수정: `src/portfolio_maker/application/public_portfolio.py`
- 수정: `src/portfolio_maker/application/render_html.py`
- 수정: `src/portfolio_maker/application/models.py` (필요 시 projection result)
- 수정: `tests/test_profile_and_portfolio.py`
- 수정: `tests/test_public_portfolio.py`
- 수정: `tests/test_issue12_builders.py`
- 수정: `tests/test_render_html.py`

**구현**

1. `build_profile`은 기존 sources/claims evidence inventory를 유지하고, 별도 `approved_projects` summary를 추가한다. summary는 current master selection과 교차한 approved semantic project만 포함한다. technical project name을 summary로 쓰지 않는다.
2. `draft_portfolio`은 source 하나당 section 및 별도 GitHub activity section을 만들던 경로를 제거한다. approved project 하나당 하나의 section을 만들고 title, review-required overview, safe supporting evidence 목록을 표시한다.
3. `public_portfolio`은 origin 기반 `local:{id}`, `github:{repo}`, `github:private` grouping을 제거한다. manifest `projects` 배열에는 approval의 stable project ID, approved title/overview, selected linked claims/evidence/timeline만 넣는다.
4. project별 effective evidence는 **현재 artifact selection과 semantic link의 교집합**이다. nonempty project만 output에 남긴다. 따라서 restricted/open public 정책 및 artifact별 exclude가 semantic approval을 우회하지 않는다.
5. artifact input manifest는 raw selection 전체가 아니라 output에 실제 쓰인 source/evidence/claim IDs를 기록한다. 여기에 `portfolio_project_ids`, `project_approval_sha256`, `project_review_input_sha256`를 추가한다.
6. `render_html`은 public manifest와 HTML policy 모두 같은 semantic projection provenance를 사용한다. `manifest_sha256`는 계속 HTML 입력 payload 기준으로 계산한다.
7. project approval이 없거나 effective evidence가 0이면 master profile은 evidence inventory를 유지하되 draft/manifest/HTML project array는 빈 배열로 쓴다. 이전 artifact를 재사용하지 않는다.
8. 회귀 test를 다음 기대값으로 전환한다.
   - local evidence 하나가 자동 project가 되지 않는다.
   - 여러 local/public/private evidence가 하나의 승인 project에만 표시된다.
   - approval 없는 candidate, rejected candidate, unassigned evidence는 어느 생성물 project에도 나타나지 않는다.
   - private URL/raw path/secret은 profile, draft, manifest, HTML, artifact provenance에 나타나지 않는다.
   - artifact policy가 project의 모든 linked evidence를 제외하면 해당 artifact에서는 project가 보이지 않는다.
   - 기존 #12 origin policy와 open public gate는 그대로 동작한다.

### 작업 5 — interactive HTML의 semantic project 표현과 empty state

**파일**

- 수정: `web/portfolio/src/main.ts`
- 필요 시 수정: `web/portfolio/src/styles.css`
- 수정: `tests/test_render_html.py`
- 수정: `tests/test_static_site.py`

**구현**

1. TypeScript `Project` type에 `overview`을 추가하고 origin/repository를 project identity로 해석하는 문구를 제거한다.
2. project list는 “승인된 project”임을 표시하고 title/overview/evidence count를 보여 준다.
3. detail panel은 overview와 project 내부의 safe evidence timeline만 보여 준다. candidate, rejected, unassigned data는 manifest에 전혀 전달하지 않는다.
4. empty state는 “근거는 수집됐지만 아직 portfolio project가 승인되지 않았다”는 의미를 명확히 전달한다. evidence를 승인하면 자동 project가 된다는 문구는 사용하지 않는다.
5. 기존 filter, keyboard navigation, mobile layout, visible focus, reduced-motion, static-only/fetch 금지 규칙은 유지한다.
6. Vite output과 inline HTML에 raw path, private URL, database/review filename, candidate text가 없는 regression test를 추가한다.

### 작업 6 — Codex skill, README, roadmap/Issue 정합성

**파일**

- 수정: `.agents/skills/portfolio-maker/SKILL.md`
- 수정: `README.md`
- 수정: `docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md`
- 수정: `docs/superpowers/specs/2026-07-14-codex-assisted-project-composition-design.md` (구현된 CLI/file contract만)
- 수정: GitHub Issue #13

**구현**

1. skill workflow에 review input 생성, Codex candidate 작성, user approval, composition, artifact generation 순서를 추가한다.
2. skill은 Codex에게 safe review input만 읽고 candidate JSON/Markdown을 쓰도록 지시한다. 원본 파일 재탐색, private URL 추론, unsupported narrative, external LLM API/credential 사용을 금지한다.
3. README에는 “현재 구현됨”과 “candidate는 사용자 승인 전 출력 project가 아님”을 구분해 설명한다. 기존 technical grouping 설명은 새 runtime에 맞게 제거 또는 historical note로 축소한다.
4. roadmap/spec은 실제 명령어, hash binding, zero-project behavior, approved-only projection과 일치하게 갱신한다.
5. Issue #13에는 구현 완료 후에만 changed scope, verification evidence, 남은 non-goals를 comment로 남기고, acceptance criteria가 모두 충족될 때만 close한다.

## 4. 검증 순서

개발은 test-first로 수행한다. 각 작업은 해당 focused test가 먼저 실패하는 것을 확인한 뒤 최소 구현으로 통과시킨다.

```bash
pytest -q tests/test_project_composition.py tests/test_sqlite_repository.py tests/test_cli.py
pytest -q tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_issue12_builders.py tests/test_render_html.py tests/test_static_site.py
pytest -q
(cd web/portfolio && npm run build)
git diff --check
```

마지막으로 별도 temporary workspace에서 다음 acceptance fixture를 실행한다.

1. 최소 3개의 local evidence, 1개의 approved public GitHub activity, 1개의 explicitly approved private GitHub activity를 준비한다.
2. `prepare-project-review` 결과에 raw local path/private URL이 없음을 확인한다.
3. Codex candidate 또는 수동 approval로 2개 semantic project를 만든 뒤 `compose-projects`를 실행한다.
4. 한 project에는 여러 origin의 evidence를 연결하고, 나머지 evidence 하나는 unassigned로 남긴다.
5. draft/manifest/HTML이 정확히 2개의 approved project만 보이는지, unassigned evidence가 project가 아닌지 확인한다.
6. `portfolio_html` policy를 `open_public` public-GitHub-only로 바꿔 재생성하고 local/private evidence와 해당 project가 안전하게 제외되는지 확인한다.
7. 생성 HTML을 브라우저에서 열어 project filter, keyboard navigation, empty state, mobile viewport, reduced-motion을 수동 점검한다.

## 5. 완료 판단

다음이 모두 충족되어야 #13을 완료로 본다.

- current evidence selection과 semantic project approval이 독립적으로 작동한다.
- Codex 후보가 자동 승인되거나 raw source를 읽는 경로가 없다.
- 여러 파일/활동을 하나의 승인 project로 연결할 수 있고, single file/repository/activity는 자동 project가 아니다.
- merge/split/reassign/reject/unassigned를 approval JSON으로 결정적으로 표현하고 검증한다.
- 생성물은 approved project만 표시하며 policy 교집합 밖 evidence를 포함하지 않는다.
- project approval이 없는 workspace는 안전한 zero-project output을 만든다.
- migration은 additive이며 기존 evidence/technical tables/artifacts를 삭제하거나 자동 변환하지 않는다.
- focused tests, full pytest, Vite build, static output validation, browser manual verification, `git diff --check`가 모두 통과한다.
- README, skill, roadmap/spec, Issue #13의 현재 상태가 실제 runtime과 일치한다.
