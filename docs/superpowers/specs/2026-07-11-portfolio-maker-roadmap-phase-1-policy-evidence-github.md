# Portfolio Maker 로드맵 Phase 1 구현 명세

날짜: 2026-07-11  
상태: developer 구현 준비 완료  
대상 Issue: #4 → #2 → #1  
후속 렌더링: #3 → #11

## 1. 목적과 범위

Portfolio Maker 0.1.0은 승인된 로컬 파일에서 근거 기반 master profile과 검토용 Markdown 포트폴리오 골격을 생성한다. GitHub 활동은 discovery-only이며, 현재 저장소는 `sources`, `source_snapshots`, `github_activities`만 runtime에 사용한다.

이번 Phase 1의 목적은 다음 두 가지다.

1. 수집·공개 범위를 더 세밀하게 통제한다.
2. 근거와 주장을 추적 가능한 모델로 정리한 뒤, 사용자가 명시적으로 승인한 **공개 GitHub 활동만** profile/portfolio 입력으로 쓸 수 있게 한다.

이 명세는 다음을 구현 대상으로 포함한다.

- [#4 승인 정책에 저장소 allowlist와 파일 제외 패턴 추가](https://github.com/koreaben777/portfolio-maker/issues/4)
- [#2 근거·주장·산출물 정규화 스키마 도입](https://github.com/koreaben777/portfolio-maker/issues/2)
- [#1 GitHub 활동을 프로필·포트폴리오 근거로 반영](https://github.com/koreaben777/portfolio-maker/issues/1)

다음은 **이번 구현 범위 밖**이다.

- 회사/JD별 맞춤 문장 생성 (#3)
- 이력서·자기소개서·면접 자료 (#7)
- Google Drive, OCR, 시맨틱 검색 (#6, #8, #10)
- MCP/app-server (#9)
- 공개용 인터랙티브 HTML 렌더러 (#11)
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
#4 세밀한 승인 정책
  ↓
#2 근거·주장·산출물 모델
  ↓
#1 명시적으로 승인된 공개 GitHub 활동 반영
  ↓
#3 회사/JD별 맞춤 포트폴리오
  ↓
#11 공개용 인터랙티브 HTML 포트폴리오
```

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
- private repository, revoked source, forbidden path, stale/damaged snapshot, malformed metadata는 evidence/claim/artifact 입력에서 제외한다.

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
- public artifact에 token, raw local path, private repo name, unescaped title이 나타나지 않는다.

## 7. 후속 Stage — #3과 #11

### 7.1 #3 회사/JD별 맞춤 포트폴리오

#3은 Stage B/C에서 생성한 evidence/claim graph를 읽어, 사용자가 준 회사/JD 요구와 근거의 대응을 검토 가능한 형태로 만든다. 역할·기술적 접근·결과 문장은 evidence가 없는 경우 생성하지 않거나 review-required로 남긴다.

### 7.2 #11 공개용 인터랙티브 HTML

[#11](https://github.com/koreaben777/portfolio-maker/issues/11)은 #3 이후의 **renderer**다. 새 비즈니스 모델을 만들지 않고, public-safe claim/evidence/artifact manifest만 읽는다.

목표 출력은 기본적으로 다음과 같다.

```text
.portfolio-maker/artifacts/portfolio.html
```

요구 사항:

- 외부 tracker, CDN, remote API 없이 브라우저에서 직접 열리는 정적 HTML
- 프로젝트 카드, 필터/탐색, 근거 상세 보기, keyboard navigation, mobile layout
- safe source label과 public GitHub URL만 provenance로 표시
- raw local path, snapshot path, `public_safe=false` data, secret-shaped text는 HTML/JS data에도 포함하지 않음
- HTML/attribute/JavaScript context별 escaping과 CSP-friendly inline-free 또는 hashed asset strategy
- automated output tests와 실제 브라우저 수동 검증

## 8. developer 작업 지시

1. 현재 `origin/main`에서 작업을 시작하고 dirty/untracked 파일을 먼저 분리한다.
2. #4에 대해 failing test를 작성하고 Stage A만 구현한다.
3. #4 focused tests, 전체 test, `git diff --check`를 통과시킨다.
4. 같은 방식으로 #2, 그다음 #1을 순서대로 진행한다.
5. 각 stage가 끝날 때 README, sample approval, Issue의 현재 상태를 code behavior와 맞춘다.
6. #3, #11 또는 다른 roadmap Issue는 구현하지 않는다. 필요한 model/renderer 확장은 Issue와 이 명세의 후속 stage로 남긴다.
7. 최종 보고에는 변경 파일, HEAD, 실행한 검증 명령, 결과, 남은 위험을 포함한다.

## 9. 명세 자체 점검

- #4의 policy 변화가 기존 approval 파일과 호환되도록 정의했다.
- #2가 #1과 #11에 필요한 근거 추적 모델을 제공한다.
- #1은 explicit activity approval과 public/private 경계를 분리한다.
- #11은 public-safe data만 렌더링하며 #3 이전 구현 범위에 섞이지 않는다.
- 현재 0.1.0의 local-first, approval-first, GitHub fail-open, review-required portfolio 경계를 유지한다.
