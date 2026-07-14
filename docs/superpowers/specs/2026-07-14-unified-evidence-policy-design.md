# 통합 근거 풀과 생성물별 근거 선택 정책 설계

날짜: 2026-07-14
상태: Issue #12 구현 기준
추적 Issue: #12

## 1. 목적

Portfolio Maker가 로컬 파일, 공개 GitHub repository/activity, 사용자가 명시적으로 허용한 비공개 GitHub repository/activity를 하나의 추적 가능한 evidence pool로 관리하도록 확장한다. master profile, Markdown draft, `portfolio-public.json`, `portfolio.html` 등 모든 생성물은 같은 evidence selection 경계를 사용하되, 생성물마다 사용자가 일부 source/evidence를 제외할 수 있게 한다.

기존 파일명의 `public`은 **기술적 호환성 이름**이다. #12 이후에도 `portfolio-public.json`과 `portfolio.html`은 인터넷에 자동 공개해도 된다는 뜻이 아니다. 기본 산출물은 사용자가 자신 또는 검증된 수신자에게 전달하는 **제한 공유(restricted)** 결과이며, 완전 공개 배포는 별도의 `open_public` 선택과 검증을 요구한다.

이번 설계의 일반 원칙은 다음과 같다.

- 로컬 discovery는 사용자가 선택한 scan root에서 사용자가 지정한 제외 폴더만 제외한다.
- 로컬 evidence의 artifact 사용은 discovery 후보가 아니라 사용자가 승인한 source/evidence만 기준으로 한다.
- 공개 GitHub evidence는 승인된 공개 repository/activity만 사용한다.
- GitHub private discovery와 artifact 사용은 gh 인증, private opt-in, repository allowlist, source/activity 승인을 모두 통과한 경우에만 허용한다.
- 공통 evidence pool은 source, snapshot, GitHub activity, evidence, claim, project를 기존 SQLite normalized model로 추적한다.
- 생성물은 공통 EvidenceSelectionService를 사용하며, 생성물마다 별도의 ad-hoc filtering을 구현하지 않는다.
- `restricted` 산출물은 승인된 로컬 자료, 승인된 공개 GitHub, 명시 승인된 비공개 GitHub를 사용할 수 있다.
- `open_public` 산출물은 더 엄격한 공개 적합성 검증을 통과한 근거만 사용하며, 자동 public hosting을 하지 않는다.

## 2. 결정된 범위

### 2.1 로컬 파일

기존 workspace 및 home scan root를 유지한다. 일반적인 디렉터리 제외는 source approval의 `excluded_directories`로만 제어한다. 기존 `forbidden_paths`는 하위 호환 alias로 계속 읽고 `excluded_directories`와 합쳐 적용한다.

다음은 사용자가 선택하지 않아도 유지되는 운영상 하드 경계다.

- Portfolio Maker가 생성하는 `.portfolio-maker` workspace 자체
- 일반 파일이 아닌 디렉터리, FIFO, socket, unsafe symlink
- snapshot과 managed artifact의 containment 및 regular-file 검사

하드 경계는 파일 내용의 자동 공개를 의미하지 않는다. ingest에서는 텍스트 추출과 secret masking을 계속 수행하고, artifact selection에서는 현재 승인 상태와 생성물별 제외 목록을 다시 검사한다.

### 2.2 GitHub

GitHub connector는 현재 범위인 repository/activity metadata discovery를 유지한다. private repository의 파일을 자동 clone하거나 raw content로 복사하는 기능은 추가하지 않는다.

private activity discovery 조건:

1. `gh auth status`가 성공한다.
2. `source-approval.json`에서 `private_sources_allowed`가 `true`다.
3. `allowed_repositories`가 비어 있지 않으며 사용자가 선택한 repository가 포함된다.
4. `excluded_repositories`가 우선 적용된다.
5. public/private activity metadata는 위 discovery 조건을 통과한 repository에 대해 탐색한다. exact URL approval은 fresh local discovery의 선행 조건이 아니라, discovery report에서 사용자가 선택한 뒤 artifact eligibility에 적용하는 별도 gate다.

private repository 또는 private activity가 discovery report에 나타나는 것은 artifact 사용 승인과 다르다. `restricted` 생성물에 넣으려면 위 조건과 artifact policy의 포함 설정을 모두 통과해야 한다.

### 2.3 공통 evidence pool

기존 `sources`, `source_snapshots`, `github_activities`, `evidence_items`, `projects`, `career_claims`, `claim_evidence`, `artifacts` 테이블을 유지하고 origin visibility와 selection provenance를 확장한다.

권장 origin visibility 값:

- `public`: 공개 URL/설명이 확인된 공개 GitHub 근거
- `private`: 승인된 로컬 자료 또는 승인된 private GitHub 근거
- `unknown`: policy 재검증 전이므로 어떠한 artifact에도 사용하지 않음

기존 `public_safe` 필드는 하위 호환을 위해 유지하되, 앞으로는 origin visibility, source/activity approval, artifact policy, delivery scope의 파생 결과로 취급한다.

### 2.4 생성물별 artifact policy와 전달 범위

source approval과 artifact approval을 분리한다.

경로:

~~~text
.portfolio-maker/reviews/source-approval.json
.portfolio-maker/reviews/artifact-approval.json
~~~

`artifact-approval.json` 예시:

~~~json
{
  "version": 1,
  "artifacts": {
    "master_profile": {
      "delivery_scope": "restricted",
      "include_local": true,
      "include_public_github": true,
      "include_private_github": true,
      "excluded_source_uris": [],
      "excluded_repositories": [],
      "excluded_activity_urls": []
    },
    "portfolio_draft": {
      "delivery_scope": "restricted",
      "include_local": true,
      "include_public_github": true,
      "include_private_github": true,
      "excluded_source_uris": [],
      "excluded_repositories": [],
      "excluded_activity_urls": []
    },
    "portfolio_public_manifest": {
      "delivery_scope": "restricted",
      "include_local": true,
      "include_public_github": true,
      "include_private_github": true,
      "excluded_source_uris": [],
      "excluded_repositories": [],
      "excluded_activity_urls": []
    },
    "portfolio_html": {
      "delivery_scope": "restricted",
      "include_local": true,
      "include_public_github": true,
      "include_private_github": true,
      "excluded_source_uris": [],
      "excluded_repositories": [],
      "excluded_activity_urls": []
    }
  }
}
~~~

규칙:

- `include_*`가 `true`여도 source/activity 승인, origin visibility, secret/path safety 검증을 통과해야 한다.
- `restricted`는 기본 전달 범위다. 승인된 로컬 source, 승인된 공개 GitHub activity, 명시 승인된 private GitHub activity를 포함할 수 있다.
- private GitHub evidence는 `private_sources_allowed=true`, repository allowlist, 정확한 private activity approval이 모두 필요하다.
- `open_public`은 사용자의 명시 선택이 있어야 하며, 공개 적합성 검증을 통과한 evidence만 허용한다. 초기 구현에서는 공개 GitHub evidence만 `open_public` 후보로 인정한다. 로컬 또는 private 근거의 공개 적합성 라벨/별도 릴리스 승인은 후속 확장이다.
- `open_public`에서 `include_local=true` 또는 `include_private_github=true`는 초기 구현에서 validation error가 된다.
- `excluded_*` 목록은 `include_*`보다 우선한다.
- 정책 파일이 없으면 기존 0.1.0 동작을 보존하는 compatibility default를 사용한다. #12를 명시적으로 초기화한 새 workspace의 sample policy는 위 `restricted` 기본값을 쓴다.
- restricted private GitHub provenance는 기본적으로 안전한 label로 표시한다. private URL 자체의 표시 여부는 별도 공유 locator 승인으로 분리하며, token·credential·raw local path는 어느 전달 범위에도 넣지 않는다.

## 3. 산출물별 기본 정책

| 산출물 | 기본 전달 범위 | 로컬 | 공개 GitHub | 비공개 GitHub | 배포/전달 |
|---|---|---:|---:|---:|---|
| master profile | restricted | 승인 시 포함 | 승인 시 포함 | 명시 승인 시 포함 | 로컬 또는 검증된 수신자 |
| portfolio draft | restricted | 승인 시 포함 | 승인 시 포함 | 명시 승인 시 포함 | 로컬 또는 검증된 수신자 |
| portfolio-public manifest | restricted | 승인 시 포함 | 승인 시 포함 | 명시 승인 시 포함 | 로컬, 직접 전달, private hosting |
| portfolio.html | restricted | 승인 시 포함 | 승인 시 포함 | 명시 승인 시 포함 | 로컬, 직접 전달, private hosting |

`portfolio-public.json`과 `portfolio.html`의 파일명은 기존 CLI·artifact record·문서 링크와의 호환을 위해 유지한다. 이 이름만으로 인터넷 공개, public Sites deployment, 누구나 접근 가능한 URL을 허용하지 않는다.

`open_public`은 restricted 결과를 그대로 재배포하는 스위치가 아니다. 새로 선택·검증·생성해야 하며, 초기 구현에서는 공개 GitHub 근거만 사용한다. public Sites deployment는 `open_public` output의 정적 검증과 사용자의 명시적인 배포 명령이 모두 있을 때만 가능하다.

## 4. 공통 데이터 흐름

~~~text
local scan root - excluded_directories
GitHub public/private discovery - gh auth + opt-in
        ↓
source/activity approval + artifact approval
        ↓
common evidence pool
        ↓
EvidenceSelectionService(artifact kind, delivery scope, include/exclude policy)
        ↓
master profile / Markdown draft / portfolio-public manifest / HTML
        ↓
restricted: local, direct transfer, private Sites
open_public: explicit revalidation and optional public Sites deployment
~~~

명시적 artifact policy가 있는 #12 경로에서는 공통 `EvidenceSelectionResult.input_manifest()`가 각 `artifacts.input_manifest`에 다음을 기록한다.

- `artifact_kind`와 `delivery_scope`
- 활성 artifact policy와 source approval 입력을 함께 반영한 결정론적 selection-input `policy_hash` (artifact policy 파일만의 hash가 아님)
- 포함된 source/evidence/claim ID와 하위 호환용 `source_ids`/`evidence_ids`/`claim_ids` 별칭
- 포함되지 않은 evidence의 `excluded_decisions` (ID와 reason)
- 선택 결과의 origin별 합계인 `origin_counts`

`artifact-approval.json`이 없는 legacy workspace의 `master_profile`과 `portfolio_draft` record는 기존 ID-only `claim_ids`/`evidence_ids` manifest 형식을 유지한다.

`portfolio_html` artifact record에는 위 manifest에 더해 HTML 전용 in-memory payload의 결정론적 JSON bytes에서 계산한 `manifest_sha256`을 기록한다.

## 5. 보안 및 하위 호환성

- token, credential, raw local path, secret-shaped text는 어느 artifact의 HTML/JS/manifest에도 들어가지 않는다.
- restricted output은 자동으로 public URL에 배포되지 않으며, public Sites deployment는 거부한다.
- private source/activity의 이름·URL을 restricted output에 넣는 경우도 artifact policy와 locator 공유 승인을 재검증한다.
- approval 파일과 artifact policy 파일의 JSON 오류는 traceback 없는 controlled error가 된다.
- 기존 `source-approval.json`에는 artifact policy가 없어도 기존 0.1.0 workspace가 열려야 한다.
- artifact policy가 없는 기존 workspace는 0.1.0 호환 경로로 private/local evidence를 포함하지 않는 보수적 선택을 유지한다.
- 전달 범위를 바꾸면 이전 artifact를 그대로 재사용하지 않고 해당 artifact를 다시 생성한다.
- source/evidence의 origin visibility와 `delivery_scope`를 혼동하지 않는다. private origin 근거가 restricted artifact에 들어갈 수 있어도 open public 허용을 뜻하지 않는다.

## 6. 범위 밖

- private repository 파일의 clone/raw ingestion
- restricted artifact의 자동 public hosting
- 현재 private/local evidence를 open public으로 승격하는 자동 판정
- 회사/JD 맞춤 문장 생성(#3)
- Google Drive, OCR, semantic search, MCP/app-server, external LLM

## 7. 완료 기준

- 선택한 제외 폴더 밖의 일반 로컬 파일이 discovery 후보가 된다.
- private opt-in과 gh 인증을 통과한 선택 repository가 private origin visibility로 discovery report에 표시된다.
- 모든 artifact builder가 동일한 EvidenceSelectionService를 사용한다.
- artifact별 include/exclude 정책이 적용되고 input_manifest에 결정이 기록된다.
- 기본 restricted manifest/HTML은 승인된 로컬, 공개 GitHub, 명시 승인된 private GitHub 근거를 사용할 수 있다.
- open_public manifest/HTML은 초기 구현에서 private/local evidence를 validation error로 차단한다.
- restricted output은 private hosting 또는 검증된 수신자 전달에만 사용되며, public hosting 요청은 거부된다.
- 기존 approval/workspace migration, focused tests, full pytest, static HTML validation이 통과한다.
