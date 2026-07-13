# Portfolio Maker 로드맵 Phase 1 구현 명세

날짜: 2026-07-11
상태: 현재 구현 slice는 #13 semantic project composition까지 포함하며, 아래 #4 → #2 → #1 설명은 역사적 Phase 1 기준선이다.
역사적 기준선 대상 Issue: #4 → #2 → #1
현재 구현 slice: #12 통합 근거 정책, 일반형 #11 renderer, #13 Codex 기반 semantic project composition
다음 단계: #3 회사/JD별 맞춤 생성 (`@sites`는 presentation/hosting 계층)

2026-07-13 정렬 메모: #4, #2, #1 기준선, 일반형 #11 renderer, #12 통합 근거 정책이 현재 worktree에 구현되어 있다. 파일·repository 기반 technical grouping이 semantic portfolio project로 잘못 노출되지 않도록 #13 composition 계층을 추가했다.

## 1. 목적과 범위

Portfolio Maker 0.1.0은 승인된 로컬 파일에서 근거 기반 master profile과 검토용 Markdown 포트폴리오 골격을 생성한다. GitHub repository와 activity는 기본적으로 discovery metadata다. 다만 policy 재검증을 통과한 confirmed public repository의 activity 중 `approved_github_activity_urls`에 정확히 있는 URL만 profile과 portfolio draft의 public-safe evidence로 사용할 수 있다. 이는 자동 project narrative를 만들지 않는다.

이번 Phase 1의 목적은 다음 두 가지다.

1. 수집·공개 범위를 더 세밀하게 통제한다.
2. 근거와 주장을 추적 가능한 모델로 정리한 뒤, 사용자가 명시적으로 승인한 **공개 GitHub 활동만** profile/portfolio 입력으로 쓸 수 있게 한다.

이 명세는 다음을 구현 대상으로 포함한다.

- [#4 승인 정책에 저장소 allowlist와 파일 제외 패턴 추가](https://github.com/koreaben777/portfolio-maker/issues/4)
- [#2 근거·주장·산출물 정규화 스키마 도입](https://github.com/koreaben777/portfolio-maker/issues/2)
- [#1 GitHub 활동을 프로필·포트폴리오 근거로 반영](https://github.com/koreaben777/portfolio-maker/issues/1)

다음은 **현재 Phase 1 runtime 구현 범위 밖**이다.

- 회사/JD별 맞춤 문장 생성 (#3)
- 이력서·자기소개서·면접 자료 (#7)
- Google Drive, OCR, 시맨틱 검색 (#6, #8, #10)
- MCP/app-server (#9)
- 외부 LLM API, hosted backend, 계정, 자동 게시

## 2. 공통 불변식

모든 단계에서 다음을 지킨다.

- 승인 전에는 source 본문이나 GitHub 활동을 산출물 입력으로 사용하지 않는다.
- source, snapshot, activity, claim, artifact의 관계는 역추적 가능해야 한다.
- private raw path, secret, credential, password export, approval에서 제외된 source, stale/damaged snapshot은 공개 산출물에 포함하지 않는다.
- 안전 정책을 변경한 뒤에도 discovery, ingest, profile, portfolio generation에서 다시 적용한다.
- 기존 0.1.0 workspace와 `source-approval.json`은 호환되어야 한다.
- 새 기능은 현재 CLI/application/infrastructure 경계를 보존한다. CLI에 비즈니스 로직을 넣지 않는다.
- 기존 0.1.0의 GitHub discovery fail-open 동작을 유지한다. GitHub 실패가 로컬 discovery를 실패시키면 안 된다.
- migration은 추가적이고 되돌릴 수 있게 설계한다. 기존 runtime 데이터를 파괴하거나 원본 파일을 복사하지 않는다.

## 3. 구현 순서와 완료 게이트

```text
#4 세밀한 승인 정책                 완료
  ↓
#2 근거·주장·산출물 모델              완료
  ↓
#1 명시적으로 승인된 GitHub 활동       완료
  ↓
#11 일반형 HTML renderer               구현 완료
      ├─ emilkowalski/skills: 디자인·모션 검토 기준
      └─ @sites: 디자인 선택·빌드 검증·선택적 호스팅
  ↓
#12 통합 근거 풀·생성물별 선택 정책     구현 완료
  ↓
#13 Codex 기반 프로젝트 식별·구성·선정  다음 최우선
  ↓
#3 회사/JD별 맞춤 포트폴리오
```

#11에서는 `emilkowalski/skills`를 디자인·모션 review 기준으로, `@sites`를 디자인 선택·빌드 검증·선택적 hosting 표면으로 사용한다. #11의 현재 project 표시는 evidence origin을 위한 technical grouping이며, 사용자가 확정한 portfolio project는 #13부터 도입한다.

각 Issue의 focused test와 전체 `pytest`가 통과하기 전에는 다음 Issue의 runtime 동작을 구현하지 않는다. 문서·샘플 approval·README는 해당 Issue와 같은 변경 단위로 갱신한다.

## 4. Stage A — #4 세밀한 승인 정책

### 4.1 approval schema

기존 필드는 유지하고, 다음 optional 필드를 추가한다.

```json
{
  "version": 1,
  "approved_source_uris": [],
  "forbidden_paths": [],
  "excluded_repositories": [],
  "private_sources_allowed": false,
  "allowed_repositories": [],
  "excluded_file_patterns": []
}
```

- `allowed_repositories`: canonical `owner/repo` 목록이다. 빈 목록은 기존 동작을 보존하며 allowlist 제한을 적용하지 않는다.
- `excluded_file_patterns`: **파일명**에만 적용하는 대소문자 비구분 glob 목록이다. 경로 제외는 계속 `forbidden_paths`를 사용한다.
- 허용 목록과 제외 목록이 충돌하면 제외가 우선한다.
- private repository는 기존 `private_sources_allowed`가 `true`여도 allowlist 또는 activity approval 자체를 의미하지 않는다.
- pattern은 빈 문자열, 제어 문자, `/`, `\\`를 포함하면 거부한다. 패턴은 파일명 matcher로만 해석해 path traversal 의미를 갖지 않게 한다.

### 4.2 적용 위치

- GitHub discovery 전에 repository allowlist와 existing `excluded_repositories`를 함께 적용한다.
- local discovery에서 제외 파일명은 candidate가 되기 전에 `skipped_policy`로 기록한다.
- ingest, build-profile, draft-portfolio도 현재 source의 파일명을 다시 검사한다.
- policy 변경으로 제외된 source는 output에서 빠져야 하며, 필요한 경우 `SKIPPED_POLICY` 상태로 바꾼다.
- discovery report에는 skip reason을 남기되, policy 때문에 가려진 민감 경로는 기존 redaction 규칙을 유지한다.

### 4.3 테스트

최소한 다음을 추가한다.

- 빈 optional 필드가 기존 0.1.0 behavior를 바꾸지 않는다.
- malformed repository, malformed pattern, non-string list entry가 controlled approval error가 된다.
- allowlist, exclude list, private setting 조합에서 exclude 우선이 보장된다.
- mixed-case 파일명과 glob이 discovery/ingest/profile에서 동일하게 제외된다.
- approval 변경 뒤 기존 artifact가 재생성 시 제외된다.
- sample approval, CLI 오류, README 설명이 runtime schema와 일치한다.

## 5. Stage B — #2 근거·주장·산출물 정규화

### 5.1 데이터 모델

기존 세 테이블은 유지한다. 새 테이블은 `CREATE TABLE IF NOT EXISTS` migration으로 추가하고, 기존 workspace를 열 수 있어야 한다.

| 모델 | 핵심 책임 |
|---|---|
| `evidence_items` | source/snapshot/activity에서 추출한 인용 가능한 근거와 locator를 보관 |
| `projects` | 여러 근거·주장이 연결될 수 있는 프로젝트 단위 |
| `career_claims` | profile/portfolio에 사용할 검증 가능한 문장 후보 |
| `claim_evidence` | claim과 하나 이상의 evidence 관계 및 support level 보관 |
| `artifacts` | 생성 산출물의 종류, 버전, 입력 manifest, 생성 시각을 보관 |

각 `evidence_item`은 최소한 source 또는 GitHub activity, locator/URL, content hash 또는 stable identifier, 생성 시각을 가진다. 각 `career_claim`은 하나 이상의 evidence와 연결되지 않으면 공개 산출물에 사용하지 않는다.

### 5.2 공개 안전성

- 모든 새 evidence와 claim의 공개 적합성 기본값은 `false`다.
- `public_safe=true`는 추론이 아니라 명시적 승인 또는 deterministic policy 검증 결과여야 한다.
- local file evidence는 raw absolute path를 public artifact에 쓰지 않는다. 공개용 locator는 안전한 source label 또는 사용자가 승인한 설명을 쓴다.
- GitHub evidence는 public repository의 공개 URL만 public artifact에 쓸 수 있다.
- private repository raw data/URL, revoked source, forbidden path, stale/damaged snapshot, malformed metadata는 evidence/claim/artifact 입력에서 제외한다. 사용자가 직접 승인한 semantic project title/overview의 private repository name display text는 restricted presentation에서만 허용한다.

### 5.3 기존 산출물 호환성

- `master-profile.json`과 `master-profile.md`의 0.1.0 읽기 경로를 깨지 않는다.
- 새 model을 이용하더라도 Markdown portfolio는 #3 전까지 review-required skeleton을 유지한다.
- artifact record는 입력 manifest 또는 source/evidence/claim ID를 통해 재현 가능한 입력 집합을 가리킨다.
- 기존 source/snapshot/activity와 새 record 간 외래 키·상태 검증을 테스트한다.

### 5.4 테스트

- 빈 기존 database가 migration 뒤 기존 세 테이블과 새 테이블을 모두 안전하게 연다.
- 기존 0.1.0 database가 데이터 손실 없이 열린다.
- claim은 evidence 연결 없이는 public artifact에 나타나지 않는다.
- revoked/forbidden/stale/damaged evidence는 관련 claim과 artifact에서 제외된다.
- artifact manifest가 사용한 claim/evidence를 추적한다.
- malformed row, FK 오류, repeated run을 controlled error 또는 idempotent 결과로 처리한다.

## 6. Stage C — #1 승인된 공개 GitHub 활동 반영

### 6.1 별도 승인 게이트

GitHub discovery 성공 또는 `private_sources_allowed=true`는 artifact 사용 승인이 아니다.

Stage C는 approval schema에 다음 optional 필드를 추가한다.

```json
{
  "approved_github_activity_urls": []
}
```

- URL은 discovery가 저장한 public GitHub activity URL과 정확히 일치해야 한다.
- allowlist가 활성화되어 있으면 activity repository도 `allowed_repositories` 안에 있어야 한다.
- `excluded_repositories`는 언제나 우선한다.
- private repository activity는 이번 Stage에서 public artifact 입력으로 허용하지 않는다.
- approval에 없는 activity, malformed URL, state가 확인되지 않는 activity는 discovery metadata로만 남긴다.
- provenance가 없는 legacy workflow activity는 profile/portfolio artifact 입력에서 제외한다. 복구하려면 `portfolio-maker discover --workspace .`를 성공시킨 뒤 discovery report에서 해당 공개 activity의 정확한 URL을 확인하고, 필요한 경우 그 URL만 `approved_github_activity_urls`에 다시 넣어 재승인한다.

### 6.2 처리 규칙

- 기존 `github_activities`를 source of truth로 사용하고, HTTP 재수집이나 웹 scraping을 새로 추가하지 않는다.
- 승인된 public activity는 URL, type, title, author, time, state를 포함한 `evidence_item`으로 만든다.
- activity title은 Markdown/HTML-safe presentation helper를 통과한다.
- claim은 activity URL과 하나 이상의 evidence item을 참조한다.
- profile은 public-safe, approved GitHub claim을 구분해 표시할 수 있다.
- #3 전까지 portfolio 초안은 project narrative를 자동 생성하지 않는다. GitHub activity는 검토 가능한 evidence reference로만 표시한다.

### 6.3 테스트

- public repository의 정확히 승인된 URL만 profile input으로 반영된다.
- approved URL이라도 allowlist 밖 또는 excluded repository면 제외된다.
- private activity, malformed URL, duplicate activity, stale/missing source는 artifact 입력이 되지 않는다.
- GitHub discovery failure는 기존처럼 local output을 보존한다.
- GitHub claim은 evidence URL과 stable identifier로 추적된다.
- public artifact에 token, raw local path, private repository URL/locator, unescaped title이 나타나지 않는다. 사용자가 직접 승인한 semantic project title/overview의 private repository name은 restricted display text로만 허용한다.

## 7. #11 현재 renderer, #12 근거 정책, #13 프로젝트 구성, #3 후속 Stage

### 7.1 #11 일반형 공개용 인터랙티브 HTML

[#11](https://github.com/koreaben777/portfolio-maker/issues/11)은 구현 완료된 일반형 인터랙티브 HTML renderer다. 구현 세부 단계는 [일반형 인터랙티브 HTML + @sites 구현 계획](https://github.com/koreaben777/portfolio-maker/blob/main/docs/superpowers/plans/2026-07-13-portfolio-maker-general-interactive-html-sites.md)에 기록한다. 이 renderer는 #13이 승인한 semantic portfolio project와 그 안의 safe evidence를 정적 HTML로 표현하며, evidence 수집·claim 생성·project 승인 권한을 갖지 않는다.

### 7.2 #11 공개용 인터랙티브 HTML (현재 일반형 구현)

[#11](https://github.com/koreaben777/portfolio-maker/issues/11)은 Portfolio Maker가 만든 #13 approved-project projection을 정적 HTML로 표시하는 **renderer**다. 현재 일반형 구현은 #3 회사/JD 맞춤 서술을 포함하지 않으며, project 승인 전에는 honest zero-project state를 표시한다.

목표 출력은 기본적으로 다음과 같다.

```text
.portfolio-maker/artifacts/portfolio-public.json
.portfolio-maker/artifacts/portfolio.html
```

일반형 포트폴리오의 고정 정보 구조:

- 소개와 기본 프로필
- 사용자가 승인한 portfolio project와 연결된 evidence
- 기술·역량 요약
- 근거 및 provenance
- 공개 링크/연락 경로
- 프로젝트 필터와 상세 보기

요구 사항:

- 외부 tracker, CDN, remote API 없이 브라우저에서 직접 열리는 정적 HTML
- 프로젝트 목록, 필터/탐색, 근거 상세 보기, 프로젝트별 timeline, keyboard navigation, mobile layout
- safe source label, public GitHub URL, 그리고 private GitHub의 URL 없는 safe label만 provenance로 표시
- approval·artifact policy·delivery scope 재검증을 통과한 claim/evidence만 사용
- project·기술·성과 표현은 evidence가 제공하는 범위를 넘지 않음
- #12 restricted path의 HTML은 artifact policy를 통과한 evidence 중 #13 approved project에 연결된 항목만 사용할 수 있다.
- candidate·rejected·unassigned evidence와 semantic project approval이 없는 evidence는 project 목록에 넣지 않는다.
- raw local path, snapshot path, `public_safe=false` data, secret-shaped text는 HTML/JS data에도 포함하지 않음
- HTML/attribute/JavaScript context별 escaping
- keyboard navigation, mobile layout, visible focus, 색 대비, reduced-motion 지원
- 빈 manifest도 허위 콘텐츠 없이 설명 가능한 empty state로 렌더링
- 회사/JD 입력, 맞춤 문장, 실시간 협업, 편집 UI, 분석/텔레메트리는 포함하지 않음

### 7.2 #11 구현의 emilkowalski/skills + @sites 연동

두 도구는 다음 경계로 하나의 vertical slice에 결합한다.

| 계층 | 역할 | 금지 범위 |
|---|---|---|
| Portfolio Maker CLI/Python/SQLite | 승인, evidence/claim 검증, `portfolio-public.json` 생성, canonical artifact 기록 | Sites에 raw DB·원본·private snapshot 전달 |
| `web/portfolio` Sites 프로젝트 | manifest를 빌드 시점에 번들하고 일반형 UI·상호작용·정적 HTML을 생성 | runtime DB/API/fetch, 자체 claim 생성 |
| 설치된 `emilkowalski/skills` | typography, spacing, state, motion, accessibility 설계·리뷰 기준 | runtime dependency, 외부 데이터 소스 |
| Codex `@sites` | 정확히 3개 디자인 시안 선택, Sites build/preview, 검증 후 선택적 hosting | 근거 수집기, 비즈니스 로직, 자동 public 승인 |

권장 데이터 흐름:

```text
승인된 evidence/claim graph
  → #12 evidence selection
  → #13 approved portfolio project manifest
  → web/portfolio build-time data module
  → 정적 HTML/자산
  → .portfolio-maker/artifacts/portfolio.html
  → (선택) private Sites deployment
```

구현 절차:

1. #13이 만든 approved-project manifest와 HTML 정보 구조를 로컬에서 확정한다. #3 산출물 없이도 verified project evidence를 표시할 수 있으며, 원본 경로, `.portfolio-maker/` 내부 파일, SQLite, 자격 증명, candidate/unassigned evidence는 Sites 입력에서 제외한다.
2. 공개 대상과 방문자, 섹션, 탐색/필터, 근거 상세, 키보드·모바일·대비 요구를 포함한 디자인 brief를 만든다.
3. `@sites` 디자인 흐름에서 비교 가능한 시안을 **정확히 3개** 순차 제시하고 하나를 선택한다. 설치된 `emilkowalski/skills`는 선택안의 motion/interaction review checklist로 적용하며 런타임 의존성으로 포함하지 않는다.
4. 선택한 방향으로 정적 HTML 표면을 빌드하고 `npm run build`, 로컬 파일 직접 열기, Codex 브라우저의 키보드·모바일·접근성 수동 검증을 통과시킨다.
5. 검증을 통과한 뒤에만 `sites-hosting`을 사용한다. 기본은 private 배포이며, public URL 배포는 별도 명시적 승인을 받은 경우에만 수행한다.
6. Sites 배포가 있더라도 로컬 `.portfolio-maker/artifacts/portfolio.html`을 canonical artifact로 유지한다. 현재 구현은 hosting을 실행하지 않았으며, Sites는 presentation/hosting 계층으로만 남고 Portfolio Maker의 승인·근거 모델을 대체하지 않는다.

### 7.3 Stage E — #13 Codex 기반 프로젝트 식별·구성·선정

[#13](https://github.com/koreaben777/portfolio-maker/issues/13)은 #12의 승인된 evidence pool과 #11의 HTML renderer 사이에 semantic project composition 계층을 추가한 현재 구현 slice다.

설계 문서: [Codex 기반 프로젝트 식별·구성 설계](https://github.com/koreaben777/portfolio-maker/blob/main/docs/superpowers/specs/2026-07-14-codex-assisted-project-composition-design.md)

- Codex는 approval·masking·artifact policy를 통과한 safe review bundle만 분석해 후보 제목, overview, grouping rationale, evidence ID를 제안한다.
- candidate는 review-required이며 user가 approve/reject/merge/split/reassign하기 전에는 artifact의 project가 될 수 없다.
- local file, repository, activity 하나를 자동 project로 승격하지 않는다. 연결되지 않은 근거는 unassigned로 보존한다.
- approved portfolio project만 master profile 요약, Markdown draft, `portfolio-public.json`, `portfolio.html`에 나타난다.
- restricted approved project의 title/overview에는 사용자가 직접 승인한 private repository name을 display text로 포함할 수 있지만, private URL과 raw locator는 계속 withheld한다.
- legacy technical grouping을 자동 이관하지 않으며, project approval이 없는 새 artifact는 zero-project empty state를 보여 준다.
- CLI의 외부 LLM API 호출·token 저장은 구현하지 않는다. Codex app의 portfolio-maker skill이 안전한 review bundle을 읽는 사용자 통제 분석 workflow다.

### 7.4 #3 회사/JD별 맞춤 포트폴리오 후속 확장

[#3](https://github.com/koreaben777/portfolio-maker/issues/3)은 #11이 생성한 동일한 public-safe manifest를 입력으로 받아 회사·JD별 우선순위, 표현, 섹션 구성을 추가하는 후속 단계다.

#3은 일반형 포트폴리오 renderer와 안전성 경계를 대체하지 않는다.

- 일반형 기본 포트폴리오는 항상 독립적으로 생성 가능해야 한다.
- 회사/JD 입력이 없으면 일반형 콘텐츠만 사용한다.
- 회사/JD 맞춤 문장은 근거가 없으면 생성하지 않거나 review-required로 남긴다.
- #3 구현은 #11의 UI·manifest 계약을 확장하되, raw source와 runtime 외부 API를 새로 노출하지 않는다.

### 7.5 Stage D — 통합 근거 풀과 생성물별 근거 선택 정책 (Issue #12 구현 기준)

[#12](https://github.com/koreaben777/portfolio-maker/issues/12)은 로컬 파일, 공개 GitHub activity, 명시적으로 허용한 private GitHub activity를 하나의 evidence pool로 통합하고, master profile·Markdown draft·public manifest·HTML에 artifact별 include/exclude policy를 적용하는 현재 정책이다.

설계 문서: [통합 근거 정책 설계](https://github.com/koreaben777/portfolio-maker/blob/main/docs/superpowers/specs/2026-07-14-unified-evidence-policy-design.md)
구현 계획: [통합 근거 정책 구현 계획](https://github.com/koreaben777/portfolio-maker/blob/main/docs/superpowers/plans/2026-07-14-unified-evidence-policy.md)

artifact policy가 없는 기존 #11 workspace는 0.1.0 호환 경로로 `portfolio-public.json`과 `portfolio.html`에서 local/private evidence를 계속 제외하며, private GitHub discovery는 metadata opt-in 범위로만 남는다.

현재 구현은 같은 파일명을 유지하되 기본 전달 범위를 `restricted`로 둔다. 이 범위의 manifest와 HTML은 승인된 로컬 근거, 승인된 공개 GitHub 근거, 정확히 명시 승인된 private GitHub 근거를 사용할 수 있다. 이는 인터넷 공개 허가가 아니라 로컬 사용, 검증된 수신자에게의 직접 전달, private Sites deployment를 위한 결과다. raw local path, credential, token은 restricted output에도 포함하지 않는다.

누구나 접근 가능한 배포는 `open_public`을 사용자가 별도로 선택하고 재생성·검증한 결과에만 허용한다. 초기 `open_public` 구현은 공개 GitHub 근거만 허용하고 local/private origin 요청을 validation error로 거부한다. `@sites` public deployment는 restricted output을 거부하며, 사용자의 명시적인 공개 배포 명령 없이는 실행하지 않는다.

## 8. developer 작업 지시

1. 현재 `origin/main`에서 작업을 시작하고 dirty/untracked 파일을 먼저 분리한다.
2. #4, #2, #1, #11, #12는 현재 기준선으로 재구현하거나 의미를 넓히지 않는다.
3. 다음 구현은 #13만 대상으로 한다. 먼저 candidate와 approved portfolio project를 technical evidence grouping에서 분리하는 failing test를 작성한다.
4. Codex analysis input은 approval·artifact policy·masking을 통과한 safe review bundle로 제한한다. CLI 내부 외부 LLM API, token 저장, raw local path/private URL 전달을 추가하지 않는다.
5. candidate는 user approval 전 artifact project가 될 수 없고, unassigned evidence는 portfolio project 수에 포함하지 않는다.
6. #13 focused tests, 전체 test, Vite build, static HTML/browser 검증, `git diff --check`를 통과시킨다.
7. #3 회사/JD 문장 생성, actual Sites hosting, private repository raw clone/ingestion, Google Drive/OCR/semantic search/MCP는 구현하지 않는다. `@sites`는 #13이 만든 approved-project manifest의 presentation 검증 계층으로만 유지한다.
8. 각 stage가 끝날 때 README, portfolio-maker skill, sample review files, Issue #13의 현재 상태를 실제 code behavior와 맞춘다.
9. 최종 보고에는 변경 파일, HEAD, 실행한 검증 명령, candidate/approved/unassigned counts, 결과, 남은 위험을 포함한다.

## 9. 명세 자체 점검

- #4, #2, #1, #11, #12는 evidence discovery·approval·selection·renderer 기준선을 제공한다.
- #13은 technical evidence grouping과 semantic portfolio project를 분리하고, Codex proposal과 user approval을 별도의 gate로 둔다.
- #13은 single file/repository/activity 자동 project 승격을 막고 unassigned evidence를 보존한다.
- #11은 #13의 approved project manifest를 렌더링하되 evidence authority나 Codex candidate approval을 대체하지 않는다.
- #3은 approved semantic project를 입력으로 쓰는 후속 단계이며 #13보다 먼저 구현하지 않는다.
- 현재 local-first, approval-first, GitHub fail-open, review-required, raw-path/credential non-disclosure 경계를 유지한다.
