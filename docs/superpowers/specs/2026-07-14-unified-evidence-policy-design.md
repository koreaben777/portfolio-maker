# 통합 근거 풀과 생성물별 근거 선택 정책 설계

날짜: 2026-07-13
상태: 제안 설계 / 구현 전
추적 Issue: #12

## 1. 목적

Portfolio Maker가 로컬 파일, 공개 GitHub repository/activity, 사용자가 명시적으로 허용한 비공개 GitHub repository/activity를 하나의 추적 가능한 evidence pool로 관리하도록 확장한다. master profile, Markdown draft, public manifest, HTML 등 모든 생성물은 같은 evidence selection 경계를 사용하되, 생성물의 visibility와 목적에 따라 사용자가 일부 source/evidence를 제외할 수 있게 한다.

이번 설계의 일반 원칙은 다음과 같다.

- 로컬 discovery는 사용자가 선택한 scan root에서 사용자가 지정한 제외 폴더만 제외한다.
- GitHub private discovery는 gh 인증, private opt-in, repository allowlist, source/activity 승인 뒤에만 실행한다.
- 공통 evidence pool은 source, snapshot, GitHub activity, evidence, claim, project를 기존 SQLite normalized model로 추적한다.
- 생성물은 공통 EvidenceSelectionService를 사용하며, 생성물마다 별도의 ad-hoc filtering을 구현하지 않는다.
- public artifact는 private evidence를 항상 차단한다.
- private artifact는 별도 artifact policy와 private hosting gate를 통과할 때만 private evidence를 사용할 수 있다.

## 2. 결정된 범위

### 2.1 로컬 파일

기존 workspace 및 home scan root를 유지한다. 일반적인 디렉터리 제외는 source approval의 excluded_directories로만 제어한다. 기존 forbidden_paths는 하위 호환 alias로 계속 읽고 excluded_directories와 합쳐 적용한다.

다음은 사용자가 선택하지 않아도 유지되는 운영상 하드 경계다.

- Portfolio Maker가 생성하는 .portfolio-maker workspace 자체
- 일반 파일이 아닌 디렉터리, FIFO, socket, unsafe symlink
- snapshot과 managed artifact의 containment 및 regular-file 검사

하드 경계는 파일 내용의 자동 공개를 의미하지 않는다. ingest에서는 텍스트 추출과 secret masking을 계속 수행하고, artifact selection에서 source/evidence visibility를 다시 검사한다.

### 2.2 GitHub

GitHub connector는 현재 범위인 repository/activity metadata discovery를 유지한다. private repository의 파일을 자동 clone하거나 raw content로 복사하는 기능은 추가하지 않는다.

private activity discovery 조건:

1. gh auth status가 성공한다.
2. source-approval.json에서 private_sources_allowed가 true다.
3. allowed_repositories가 비어 있지 않으며 사용자가 선택한 repository가 포함된다.
4. excluded_repositories가 우선 적용된다.
5. public activity는 approved_github_activity_urls, private activity는 approved_private_github_activity_urls에서 정확히 승인된다.

private repository 또는 private activity가 discovery report에 나타나는 것은 artifact 사용 승인과 다르다.

### 2.3 공통 evidence pool

기존 sources, source_snapshots, github_activities, evidence_items, projects, career_claims, claim_evidence, artifacts 테이블을 유지하고 visibility와 selection provenance를 확장한다.

권장 visibility 값:

- public: 공개 URL/설명이 public artifact에 허용될 수 있음
- private: 사용자 workspace 또는 private artifact에서만 허용
- unknown: policy 재검증 전이므로 어떠한 artifact에도 사용하지 않음

기존 public_safe 필드는 하위 호환을 위해 유지하되 visibility와 명시적 artifact policy의 파생 결과로 취급한다.

### 2.4 생성물별 artifact policy

source approval과 artifact approval을 분리한다.

경로:

```text
.portfolio-maker/reviews/source-approval.json
.portfolio-maker/reviews/artifact-approval.json
```

artifact-approval.json 예시:

```json
{
  "version": 1,
  "artifacts": {
    "master_profile": {
      "visibility": "private",
      "include_local": true,
      "include_public_github": true,
      "include_private_github": true,
      "excluded_source_uris": [],
      "excluded_repositories": [],
      "excluded_activity_urls": []
    },
    "portfolio_draft": {
      "visibility": "private",
      "include_local": true,
      "include_public_github": true,
      "include_private_github": true,
      "excluded_source_uris": [],
      "excluded_repositories": [],
      "excluded_activity_urls": []
    },
    "portfolio_public_manifest": {
      "visibility": "public",
      "include_local": false,
      "include_public_github": true,
      "include_private_github": false,
      "excluded_source_uris": [],
      "excluded_repositories": [],
      "excluded_activity_urls": []
    },
    "portfolio_html": {
      "visibility": "public",
      "include_local": false,
      "include_public_github": true,
      "include_private_github": false,
      "excluded_source_uris": [],
      "excluded_repositories": [],
      "excluded_activity_urls": []
    }
  }
}
```

규칙:

- include_*가 true여도 source/activity 승인과 visibility 검증을 통과해야 한다.
- public artifact에서 include_private_github=true는 validation error가 된다.
- private artifact에서 private evidence를 사용하려면 private_sources_allowed=true, repository allowlist, 정확한 private activity approval이 모두 필요하다.
- excluded 목록은 include보다 우선한다.
- 정책 파일이 없으면 기존 동작을 보존하는 compatibility default를 사용한다.

## 3. 산출물별 기본 정책

| 산출물 | 기본 visibility | 로컬 | 공개 GitHub | 비공개 GitHub |
|---|---|---:|---:|---:|
| master profile | private | 포함 | 포함 | 제외, 명시 정책 시 포함 |
| portfolio draft | private | 포함 | 포함 | 제외, 명시 정책 시 포함 |
| public manifest | public | 제외 | 포함 | 차단 |
| portfolio.html | public | 제외 | 포함 | 차단 |

private HTML을 만들 경우 artifact policy의 visibility를 private으로 명시하고 private Sites deployment만 허용한다. public deployment 호출은 항상 거부해야 한다.

## 4. 공통 데이터 흐름

```text
local scan root - excluded_directories
GitHub public/private discovery - gh auth + opt-in
        ↓
source approval + artifact approval
        ↓
common evidence pool
        ↓
EvidenceSelectionService(artifact kind, visibility, include/exclude policy)
        ↓
master profile / Markdown draft / public manifest / HTML
```

각 artifacts.input_manifest에는 다음을 기록한다.

- artifact kind와 visibility
- policy file hash
- 포함된 source/evidence/claim/project ID
- 제외된 ID와 reason
- 생성 시각과 source policy version

## 5. 보안 및 하위 호환성

- private source의 이름과 URL도 public artifact의 HTML/JS/manifest에 들어가지 않는다.
- approval 파일과 artifact policy 파일의 JSON 오류는 traceback 없는 controlled error가 된다.
- 기존 source-approval.json에는 artifact_policies가 없어도 기존 0.1.0 workspace가 열려야 한다.
- 기존 public HTML은 private/local evidence를 포함하지 않는 보수적 기본값을 유지한다.
- raw file content, GitHub token, gh credential, private path를 로그·report·artifact에 출력하지 않는다.
- public/private visibility를 바꾸면 이전 artifact를 그대로 재사용하지 않고 해당 artifact를 다시 생성한다.

## 6. 범위 밖

- private repository 파일의 clone/raw ingestion
- public HTML에 private evidence를 넣는 예외 플래그
- 회사/JD 맞춤 문장 생성(#3)
- Google Drive, OCR, semantic search, MCP/app-server, external LLM
- 자동 public hosting

## 7. 완료 기준

- 선택한 제외 폴더 밖의 일반 로컬 파일이 discovery 후보가 된다.
- private opt-in과 gh 인증을 통과한 선택 repository가 private visibility로 discovery report에 표시된다.
- 모든 artifact builder가 동일한 EvidenceSelectionService를 사용한다.
- artifact별 include/exclude 정책이 적용되고 input_manifest에 결정이 기록된다.
- public artifact에서 private/local evidence가 누출되지 않는다.
- private artifact는 별도 visibility와 private hosting gate 없이는 생성·배포되지 않는다.
- 기존 approval/workspace migration, focused tests, full pytest, static HTML validation이 통과한다.