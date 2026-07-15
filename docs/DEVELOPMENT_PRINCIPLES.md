# Portfolio Maker 개발 원칙

> 상태: 살아 있는 프로젝트 운영 문서
> 적용 범위: 기획, 설계, 구현, 테스트, 문서화, 리뷰, release, GitHub Issue 관리
> 갱신 기준: 제품 경계, 데이터 처리, 승인 정책, plugin/skill 책임, 공개 산출물 또는 실행 방법이 달라지는 변경과 같은 단위로 갱신한다.

## 1. 목적과 제품 정의

Portfolio Maker는 사용자가 허용한 자신의 자료를 바탕으로, 근거를 확인할 수 있는 기본 포트폴리오와 career artifact를 만드는 local-first 도구다.

제품의 핵심은 그럴듯한 문장을 임의 생성하는 것이 아니라 다음 흐름을 안전하고 재현 가능하게 만드는 것이다.

```text
source scope와 제외 정책
  -> local/GitHub evidence
  -> project discovery와 사용자 결정
  -> artifact별 evidence selection
  -> profile / Markdown / interactive HTML
```

현재 동작과 승인된 미래 설계를 구분한다.

- 현재 공개 runtime: 0.2.0
- 호환 기준선: 0.1.0 workspace와 기존 CLI/artifact 경로
- 다음 제품 확장 목표: 회사·JD 맞춤 artifact와 별도 source/graph adapter
- 0.2.0 권위 명세: [0.2.0 계층형 의미 인덱스·플러그인 설계](superpowers/specs/2026-07-14-portfolio-maker-0.2.0-semantic-index-plugin-design.md)
- 현재 semantic composition 기준선: [Codex 기반 프로젝트 구성 설계](superpowers/specs/2026-07-14-codex-assisted-project-composition-design.md)
- 공개 사용자 안내: [README](../README.md)
- 특정 변경의 검증 근거: `docs/reviews/`

문서가 충돌하면 현재 code와 현재 HEAD에서 실행한 test를 먼저 확인한다. 그다음 최신 사용자 결정, 최신 승인 명세, 이 문서, README를 정렬한다. historical plan, handoff, review를 현재 runtime의 단독 근거로 사용하지 않는다.

## 2. 버전과 roadmap 원칙

### 2.1 0.1.0 호환 기준선

0.1.0은 현재 release가 보존하는 호환 기준선이다.

- repo-scoped `$portfolio-maker` skill과 Python CLI
- local file 및 GitHub activity discovery
- source approval과 artifact별 include/exclude policy
- local/public GitHub/명시 승인 private GitHub evidence pool
- SQLite provenance와 additive schema
- Codex candidate review와 사용자 승인 semantic project
- master profile, Markdown draft, static interactive HTML
- restricted/open_public delivery scope 분리

구현된 기능은 반드시 현재 code, test, 실제 artifact로 확인한 뒤 설명한다.

### 2.2 현재 0.2.0 runtime

현재 0.2.0 runtime은 다음 기능을 제공한다.

- global local file-count cap을 대체하는 hierarchical semantic index
- file semantic extraction과 bottom-up directory summary
- parent/child/cross-directory Project Boundary Detection
- review mode와 explicit automatic mode
- high/medium automatic inclusion과 reversible exclusion
- incremental refresh, partial failure, interrupted-run recovery
- source-independent node/provenance model과 additive migration
- responsibility-based multi-skill Codex plugin
- full automated validation과 보호된 사용자 자료를 제외한 선택 scan-root smoke test

0.2.0의 semantic index, project composition, plugin, artifact projection은 현재 release의 기능이다.
다만 smoke는 선택한 repository subtree에 한정되므로 전체 사용자 home이나 live GitHub 데이터의
성능 보증으로 확대하지 않는다. 회사·JD 맞춤 artifact와 외부 source/graph adapter는 별도 release다.

### 2.3 후속 범위

personal evidence knowledge graph, portfolio semantic ontology, Google Drive, OCR, semantic search, MCP/App, 회사·JD 맞춤 artifact는 별도 Issue와 설계를 통해 확장한다. 0.2.0 문서에 미래 확장점이 있다는 이유만으로 현재 제공 기능처럼 표현하지 않는다.

## 3. 아키텍처 원칙

### 3.1 deterministic engine과 Codex workflow 분리

```text
Codex plugin / skills
  -> CLI adapter
  -> application use cases
  -> local SQLite / snapshots / artifacts
  -> static web renderer
```

- Codex skill은 workflow, semantic judgment, 사용자 확인을 담당한다.
- CLI는 argument, workspace, safe output, exit code를 담당하고 business logic을 소유하지 않는다.
- application use case는 CLI, Codex task, 특정 UI와 분리된 명시적 input/result를 가진다.
- infrastructure는 filesystem, GitHub, SQLite, snapshot, analyzer, renderer 같은 side-effect boundary를 담당한다.
- SQLite와 validator가 policy, hash, state transition의 결정론적 authority다.
- Codex candidate는 proposal이며 acceptance gate를 우회할 수 없다.
- external LLM API token을 CLI가 읽거나 저장하는 기능은 별도 승인 설계 없이 추가하지 않는다.

### 3.2 plugin과 skill 책임

0.2.0 plugin은 source governance, semantic index, project curation, project review, artifact generation을 별도 skill로 나눈다. `$portfolio-maker`는 representative router로 유지한다.

새 skill은 다음 경우에만 추가한다.

- independent user intent
- separate authority or approval boundary
- independently evolving and testable workflow
- 기존 skill에 포함하면 unrelated responsibility가 결합됨

command 하나마다 skill을 만들거나 같은 application logic을 여러 skill에 복제하지 않는다. MCP/App은 실제 remote tool surface가 필요해질 때 같은 application use case를 호출하는 adapter로 추가한다.

### 3.3 dependency와 storage

- standard library와 현재 dependency로 해결 가능한 문제에 새 framework/service를 먼저 추가하지 않는다.
- vector/graph database는 공통 node model로 해결할 수 없는 검증된 query와 운영 요구가 생긴 뒤 도입한다.
- semantic graph 확장을 위해 current evidence/provenance를 중복 저장하거나 truth source를 둘로 만들지 않는다.
- migration은 additive이고 기존 workspace를 파괴하지 않아야 한다.

## 4. source, 개인정보와 승인

### 4.1 scope와 제외

- 0.2.0 local semantic index는 사용자가 선택한 scan root에서 excluded directory와 excluded file policy를 먼저 적용하고, 제외되지 않은 item을 per-file approval 없이 구조·의미 분석 대상으로 삼는다.
- excluded subtree는 content summary, semantic relation, candidate, artifact에 들어가지 않는다.
- private GitHub는 authentication, explicit opt-in, non-empty repository allowlist, source/activity approval을 모두 통과해야 한다.
- semantic index 포함, ingest/evidence 승인, artifact inclusion은 서로 다른 authority다. index에 존재한다는 이유만으로 원문 snapshot이나 artifact 근거가 되지 않는다.
- automatic project inclusion은 source approval이나 artifact delivery scope를 우회하지 않는다.

### 4.2 locator와 원본

- 원본 file은 `.portfolio-maker/`에 복사하지 않고 수정·이동·삭제하지 않는다.
- absolute path와 private locator는 local revalidation 또는 diagnostics에 필요하면 내부에서 보관할 수 있다.
- 일반 Codex review bundle, manifest, HTML에는 raw local path, private URL, token, credential을 넣지 않는다.
- secret-shaped text는 semantic summary 전에 mask한다.
- `.portfolio-maker/`와 실제 사용자 artifact는 Git에 commit하지 않는다.

### 4.3 artifact scope

- restricted는 local use, verified recipient, private hosting을 위한 범위이며 automatic public permission이 아니다.
- open_public은 별도의 explicit selection과 regeneration/validation을 요구한다.
- project가 auto-included 되었더라도 Sites deployment permission을 얻지 않는다.
- artifact는 current EvidenceSelectionService 결과와 active project link의 교집합만 사용한다.

## 5. semantic index와 project decision

- global file count 같은 단일 휴리스틱으로 project 존재를 판단하지 않는다.
- 모든 허용 item은 structure node로 기록하되 content read는 format/size/budget policy를 따른다.
- file summary를 bottom-up으로 directory summary에 합성한다.
- parent context가 child 전체를 설명하면 parent project를 우선한다.
- child가 독립 제품·공모전·배포물·lifecycle이면 separate project로 제안한다.
- cross-directory grouping은 explicit rationale과 counter signal을 남긴다.
- `.git`, README, manifest 하나는 supporting signal이지 project 판정 그 자체가 아니다.
- file 수는 confidence의 직접 점수나 최소 조건이 아니다.

review mode에서는 user decision 전 candidate를 materialize하지 않는다. explicit automatic mode에서는 high와 medium을 포함하고, medium은 review recommended로 표시한다. low, conflict, stale, policy failure는 review/unassigned에 남긴다.

사용자의 manual approve/exclude는 automatic inference보다 우선한다. exclusion은 project/evidence/index 삭제가 아니라 reversible artifact-projection state다.

## 6. incremental update와 failure recovery

- content fingerprint, analyzer version, hierarchy, policy를 기준으로 affected node와 ancestor만 invalidate한다.
- 새 index revision은 staging에 작성하고 structural crawl 성공 후 active로 전환한다.
- interrupted run은 previous active revision을 유지한다.
- permission, unsupported format, analyzer error는 partial status로 기록하며 unrelated subtree를 버리지 않는다.
- temporary source failure만으로 existing node나 project를 삭제하지 않는다.
- successful complete crawl에서 실제 부재가 확인된 경우에만 tombstone을 적용한다.
- policy/index/candidate/decision hash가 맞지 않으면 downstream state를 stale로 처리한다.

## 7. 구현과 검증

### 7.1 기본 작업 순서

1. branch, worktree, dirty files, code, tests, authoritative docs를 확인한다.
2. 요구사항과 current behavior 차이를 reproducible fixture로 정의한다.
3. 가능하면 failing regression test를 먼저 작성한다.
4. 가장 작은 cause-focused implementation을 적용한다.
5. focused test, full test, build, static validation, manual observation을 수행한다.
6. requirement, migration, security, docs, temporary artifact를 자체 점검한다.

### 7.2 0.2.0 필수 검증

- 500개보다 많은 file fixture
- coherent parent project와 independent child fixture
- cross-directory grouping과 overlap conflict
- automatic high/medium, low review, exclude/re-include
- manual decision precedence와 split/merge lineage
- interrupted crawl, permission, symlink cycle, cache corruption
- 0.1.0 workspace migration
- plugin manifest와 모든 skill validation
- focused/full Python tests
- TypeScript check와 Vite build
- static artifact safety validation
- browser keyboard/mobile/reduced-motion interaction
- read-only selected scan-root smoke test; the current record uses a repository `src` subtree and excludes protected user data
- `git diff --check`

test count나 과거 PASS를 현재 증거로 재사용하지 않는다. 완료 주장은 current HEAD의 명령과 결과를 기록한다.

## 8. 문서 자동 동기화

사용자가 별도로 문서 수정을 언급하지 않아도 승인된 설계나 구현 계획이 추가·변경되면 다음을 검토하고 필요한 범위에서 같은 작업으로 갱신한다.

- authoritative design/spec
- implementation plan
- release/Phase roadmap
- README의 current/future 구분
- 이 개발 원칙
- repository skill/plugin instruction
- relevant GitHub Issue
- implementation review/verification record

문서에는 `현재 구현`, `승인 설계`, `구현 계획`, `historical baseline`, `검증 완료`를 구분한다. future feature를 현재 명령, tutorial, release note에서 제공되는 것처럼 표현하지 않는다.

README는 public visitor와 user의 첫 안내다. runtime, setup, privacy, artifact, troubleshooting이 달라지는 push에는 README를 함께 갱신한다.

## 9. GitHub Issue 관리

- bug, feature, proposal, release slice는 GitHub Issue에서 추적한다.
- issue에는 user value, current limitation, scope, non-goals, dependency, completion criteria를 포함한다.
- 큰 release는 independently testable issue 또는 task로 분해하되 overall release gate를 별도로 유지한다.
- 완료된 slice와 release 전체 완료를 혼동하지 않는다.
- issue를 닫기 전에 implementation, tests, manual verification, docs, migration, residual risk를 확인한다.
- secret, credential, private raw locator, user document content는 public issue에 넣지 않는다.

## 10. Git과 변경 관리

- 작업 전 `git status --short --branch`를 확인한다.
- dirty/untracked file은 user-owned로 보고 unrelated change를 보존한다.
- 필요한 file만 stage하고 commit한다.
- reset, destructive cleanup, source mutation은 explicit approval 없이 실행하지 않는다.
- remote push, PR, main integration은 explicit user request 또는 approval이 있을 때만 수행한다.
- commit 전 diff, Markdown, link, secret, absolute user path, temporary artifact를 확인한다.

## 11. 자체 점검

복잡한 변경을 완료하기 전에 다음을 확인한다.

- 요구사항과 승인 설계 범위에 맞는가?
- current runtime과 future roadmap을 구분했는가?
- source/evidence/project/artifact authority를 우회하지 않는가?
- unit, integration, build, manual verification이 위험에 비례하는가?
- migration과 interrupted-run failure가 기존 state를 보존하는가?
- debug code, generated test data, 실제 user artifact가 repository에 남지 않았는가?
- README, spec, roadmap, skills, Issue가 실제 상태와 일치하는가?
- 변경 file, 검증 명령, 결과, residual risk를 보고할 수 있는가?
