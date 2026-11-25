<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Portfolio Maker 실제 파일시스템 실사용 파일럿 설계

- 작성일: 2026-07-30
- 대상: Portfolio Maker 0.2.2 로컬 릴리스 후보
- 기준 후보 HEAD:
  `0e156bfb289119ece65e67a9bc9e8b000bf6bf2c`
- 상태: 사용자 승인 설계
- 실행 여부: 이 문서는 설계만 정의하며 실제 탐색·생성은 아직 수행하지 않음

> [!WARNING]
> 2026-07-31 실제 실행은 일반 문구 기반 의미 출력이 quality gate를 충족하지 못해
> 중단되었다. 이 historical 설계에서 의미 인덱스 이후 단계를 재개하지 않는다.
> 새 실행은 [재개 가능한 의미 인덱스 설계](2026-07-31-portfolio-maker-resumable-codex-semantic-index-design.md),
> [구현 계획](../plans/2026-07-31-portfolio-maker-resumable-codex-semantic-index.md),
> [합성 검증 기록](../../reviews/2026-07-31-resumable-codex-semantic-index-verification.md)의
> restart gate와 별도 사용자 승인을 모두 통과해야 한다.

## 1. 목적

이 파일럿의 목적은 합성 fixture가 아니라 사용자의 실제 Mac 파일시스템과
GitHub 계정을 Portfolio Maker의 정상 사용자 흐름으로 처리했을 때 다음 질문에
답하는 것이다.

1. 사용자의 홈 디렉터리 전체에서 의미 있는 큰 작업 단위를 프로젝트로 찾을 수
   있는가?
2. 로컬 파일, 공개 GitHub 활동, 명시 승인한 비공개 GitHub 활동을 하나의 근거
   체계로 안전하게 결합할 수 있는가?
3. 보험 RAG, PlayMCP, Portfolio Maker처럼 사용자가 프로젝트라고 인식하는 큰
   작업을 과도하게 분할하지 않고 후보로 제시할 수 있는가?
4. 승인된 프로젝트와 근거만으로 마스터 프로필, 포트폴리오 초안, manifest,
   인터랙티브 HTML을 만들 수 있는가?
5. 제한 공유와 공개 공유의 자료 범위가 실제 산출물에서 정확히 분리되는가?
6. 실제 원본 파일과 원격 GitHub 상태를 변경하지 않고 전체 흐름을 완료할 수
   있는가?

## 2. 범위

### 2.1 포함

- 사용자의 `$HOME` 전체를 대상으로 하는 로컬 discovery
- 사용자가 선택한 추가 제외 폴더
- Portfolio Maker가 기본적으로 읽지 않는 자격증명·키·보안 민감 영역
- GitHub CLI 로그인 상태를 이용한 공개 GitHub discovery
- private opt-in, 저장소 allowlist, 정확한 activity URL 승인을 통과한 비공개
  GitHub discovery
- source 및 artifact approval
- 승인 소스 ingest와 관리 snapshot
- 계층형 의미 인덱스 준비·적용
- Codex가 수행하는 v2 프로젝트 후보 분석과 그룹핑
- review 모드 및 automatic 모드의 결정 동작
- 마스터 프로필 JSON·Markdown
- 포트폴리오 Markdown 초안
- `restricted` manifest와 인터랙티브 정적 HTML
- 별도 `open_public` manifest와 정적 HTML
- 사용자가 고른 실제 파일의 선택형 Codex 비전 분석
- 파일럿이 생성한 관리 이력의 plan, export, apply, resume/recover 검증
- 원본 불변성, 승인 경계, 비공개 정보 노출 검사
- 사용자의 정성 평가를 포함하는 최종 파일럿 보고서

### 2.2 제외

- 원격 Git push, merge, tag, release 또는 package publication
- GitHub Issue 생성·수정·종료
- Sites 또는 다른 서비스에 대한 실제 배포
- 원본 사용자 파일의 이동·수정·삭제
- GitHub 저장소 또는 activity의 원격 변경
- 승인되지 않은 비공개 저장소 내용 사용
- `.env`, 개인 키, 자격증명 저장소, 브라우저 프로필 또는 비밀번호 관리 도구
  내용 검사
- 지원하지 못하는 파일 형식을 이해한 것으로 간주하는 fallback
- 회사·채용공고별 맞춤 포트폴리오 작성

## 3. 후보와 실행 환경

### 3.1 후보 기준

파일럿은 다음 exact candidate를 사용한다.

```text
branch: codex/portfolio-maker-0.2.2-cycle-01
HEAD: 0e156bfb289119ece65e67a9bc9e8b000bf6bf2c
```

현재 `master`에는 이 후보가 병합되지 않았으므로, 사전 파일럿은 exact candidate의
깨끗한 checkout을 사용한다. 병합·푸시가 나중에 승인되면 별도의 post-release
설치 smoke test로 공개 저장소 clone 흐름을 다시 확인한다.

### 3.2 파일럿 workspace

파일럿은 기존 개발 checkout과 기존 `.portfolio-maker/` 상태를 재사용하지 않는
새 checkout에서 시작한다. 실행 시 선택한 실제 checkout 경로, commit, Python,
Node.js, npm, GitHub CLI 버전을 최종 보고서에 기록한다.

파일럿 workspace는 다음 데이터만 소유한다.

```text
.portfolio-maker/reviews/
.portfolio-maker/reviews/real-filesystem-pilot/
.portfolio-maker/reviews/semantic-index/
.portfolio-maker/raw/snapshots/
.portfolio-maker/derived/vision/
.portfolio-maker/artifacts/
.portfolio-maker/quarantine/
.portfolio-maker/portfolio.db
```

파일럿 전에 생성되는 정확한 파일 목록을 baseline으로 기록하며, 실행 중 새로
생긴 관리 파일만 파일럿 소유로 본다.

### 3.3 입력과 출력의 분리

- `$HOME`과 GitHub는 읽기 입력이다.
- `.portfolio-maker/`는 승인·snapshot·DB·검토·산출물 출력이다.
- 이력 정리는 `.portfolio-maker/` 안의 관리 파일과 SQLite 이력만 대상으로 한다.
- 실제 source URI는 원본을 찾고 변경 여부를 검증하기 위한 provenance이며, 정리
  대상 경로가 아니다.
- 파일럿 종료 후 생성물을 임의로 삭제하지 않고 사용자가 검토할 수 있게 보존한다.

문서의 대문자 명령 인자 `SOURCE_ID`, `PROJECT_ID`, `RESULT_FILE`, `PLAN_ID`,
`BACKUP_DIRECTORY`는 literal 문자열이 아니라 직전 단계에서 검토·선택하거나
CLI가 출력한 실제 값을 뜻한다. `$HOME`과 `$PWD`는 파일럿 shell의 실제 환경
변수다.

## 4. 실제 사용자 데이터 흐름

```text
$HOME 전체 + GitHub
        |
        v
source scope와 제외 규칙 승인
        |
        v
discovery report
        |
        v
로컬 source URI와 GitHub activity URL 명시 승인
        |
        v
ingest와 관리 snapshot
        |
        v
계층형 의미 인덱스와 safe v2 review bundle
        |
        v
Codex 프로젝트 후보 분석
        |
        v
review 또는 automatic 결정 + 사용자 포함·제외
        |
        v
artifact policy와 활성 프로젝트의 교집합
        |
        +--> master profile JSON/Markdown
        +--> portfolio draft Markdown
        +--> restricted manifest/HTML
        +--> open_public manifest/HTML
```

후보 파일은 검토 보조물이지 database truth가 아니다. 활성 v2 project decision과
현재 source/artifact approval을 모두 통과한 근거만 최종 산출물에 들어간다.

## 5. 정상 사용자 흐름과 파일럿 단계

### P0. 설치 및 후보 고정

1. exact candidate의 깨끗한 checkout을 준비한다.
2. Python 3.11 이상 가상환경을 만든다.
3. `pip install -e ".[dev]"`로 package와 CLI를 설치한다.
4. `web/portfolio`에서 `npm ci`를 실행한다.
5. source와 installed CLI의 버전이 `0.2.2`인지 확인한다.
6. `$portfolio-maker` router와 여섯 child skill이 노출되는지 확인한다.

합격 조건:

- candidate HEAD가 정확하다.
- 시작 worktree의 tracked/staged 상태가 깨끗하다.
- CLI help가 정상 종료된다.
- HTML renderer production build가 가능하다.

### P1. 새 workspace 초기화

다음 명령으로 source approval sample을 만든다.

```bash
portfolio-maker approve --workspace . --write-sample
```

기존 `.portfolio-maker/`, 이전 approval, SQLite, snapshot, project candidate,
artifact를 복사하거나 재사용하지 않는다.

합격 조건:

- 새 승인 파일만 생성된다.
- 승인된 source URI와 activity URL의 초기 목록은 비어 있다.
- discovery 전에는 profile, project 또는 portfolio artifact가 없다.

### P2. 홈 디렉터리 및 GitHub 정책 승인

탐색 루트는 사용자의 `$HOME` 전체다. 실행 checkout 자체는 튜토리얼과 동일하게
추가 제외한다. 프로젝트의 기본 보안 차단 영역은 유지하며, 그 밖의 추가 제외
폴더는 사용자가 직접 정한다.

비공개 GitHub을 포함하려면 discovery 전에 다음을 승인한다.

- `private_sources_allowed: true`
- 선택한 `owner/repo` 형식의 `allowed_repositories`
- 사용자가 선택한 `excluded_repositories`

정확한 public/private activity URL은 아직 승인하지 않는다.

합격 조건:

- 제외 규칙이 승인 파일에 지속된다.
- allowlist에 없는 비공개 저장소는 근거 후보로 사용할 수 없다.
- credential 값은 명령 출력이나 보고서에 나타나지 않는다.

### P3. 실제 discovery

정상 튜토리얼과 같은 형태로 실행한다.

```bash
portfolio-maker discover \
  --workspace . \
  --home "$HOME" \
  --exclude-directory "$PWD"
```

로컬 파일만을 위한 `--no-github`는 사용하지 않는다. GitHub 로그인 실패,
rate limit, 파일 접근 거부, 심볼릭 링크 또는 비정상 파일은 우회하지 않고 안전한
건너뜀 또는 실패 상태로 기록한다.

합격 조건:

- discovery가 사용자가 승인한 scope를 벗어나지 않는다.
- 제외 폴더가 source 후보로 나타나지 않는다.
- public/private GitHub repository 상태가 구분된다.
- 비밀값이나 파일 원문이 discovery report에 불필요하게 나타나지 않는다.
- 발견 수, 건너뜀 수, 실패 원인을 보고할 수 있다.

### P4. Source 및 artifact 승인

사용자는 다음 파일을 직접 검토한다.

```text
.portfolio-maker/reviews/discovery-report.md
.portfolio-maker/reviews/source-approval.json
```

사용자는 다음 항목만 복사해 승인한다.

- 선택한 로컬 `file://` URI
- 선택한 정확한 공개 GitHub activity URL
- private opt-in과 allowlist를 통과한 정확한 비공개 GitHub activity URL

이후 artifact policy sample을 만들고 각 산출물의 delivery scope와 include/exclude
규칙을 검토한다.

```bash
portfolio-maker approve \
  --workspace . \
  --write-sample-artifact-policy
```

합격 조건:

- 승인 전 ingest를 실행하지 않는다.
- 제외·missing·stale activity는 승인하지 않는다.
- `restricted`와 `open_public`을 하나의 승인으로 취급하지 않는다.
- 각 승인 항목은 discovery provenance와 현재 policy를 다시 통과한다.

### P5. Ingest 및 계층형 의미 인덱스

```bash
portfolio-maker ingest --workspace .
portfolio-maker prepare-semantic-index \
  --workspace . \
  --root "$HOME"
```

Codex의 semantic-index child skill은 관리 input chunk만 읽고 관리 output chunk를
작성한다. 적용 전에 hash, coverage, schema를 검증한다.

```bash
portfolio-maker apply-semantic-index --workspace .
portfolio-maker prepare-project-review --workspace . --version v2
```

합격 조건:

- 승인되지 않은 source는 snapshot과 index에 들어가지 않는다.
- 원본 파일은 수정되지 않는다.
- index는 전역 파일 수 상한으로 임의 절단되지 않는다.
- v2 review bundle은 원본 경로·private URL·원문 비밀값을 Codex project
  grouping 입력으로 노출하지 않는다.

### P6. Codex 프로젝트 후보 분석

Codex는 다음 파일만 프로젝트 그룹핑 근거로 읽는다.

```text
.portfolio-maker/reviews/project-review-input-v2.json
```

Codex는 다음 검토 보조물을 작성한다.

```text
.portfolio-maker/reviews/project-candidates.json
.portfolio-maker/reviews/project-candidates.md
```

판단 원칙:

1. 상위 폴더와 하위 폴더가 하나의 내용 맥락이면 상위 단위를 프로젝트로 본다.
2. 하위 폴더가 독립 제품·공모전·배포물일 때만 별도 프로젝트로 분리한다.
3. 단일 코드 파일, 설정 변경, 임시 문서, 빌드 출력은 프로젝트로 만들지 않는다.
4. 확신이 부족하거나 사소한 근거는 `unassigned`로 남긴다.
5. 후보 설명은 safe review bundle의 근거 범위를 벗어나 창작하지 않는다.

사전 기준 프로젝트:

- 보험 RAG
- PlayMCP
- Portfolio Maker

정책상 제외되지 않은 사전 기준 프로젝트는 모두 후보로 발견되거나, 발견되지 않은
구체적인 원인이 보고되어야 한다.

### P7. Review 및 automatic 결정 비교

먼저 review 모드로 모든 후보를 materialize한다.

```bash
portfolio-maker compose-projects --workspace . --mode review
portfolio-maker list-projects --workspace . --format table
```

사용자가 각 project를 포함하거나 제외한다.

```bash
portfolio-maker set-project-state \
  --workspace . \
  --project-id PROJECT_ID \
  --state included

portfolio-maker set-project-state \
  --workspace . \
  --project-id PROJECT_ID \
  --state excluded
```

별도 비교 실행에서 automatic 모드를 사용한다.

```bash
portfolio-maker compose-projects --workspace . --mode automatic
```

합격 조건:

- review 모드에서는 사용자 결정 전 candidate가 활성 프로젝트가 되지 않는다.
- automatic 모드는 high와 medium만 자동 포함한다.
- 자동 포함된 medium 프로젝트를 제외하고 다시 포함할 수 있다.
- 제외·재포함은 source, evidence, index를 삭제하지 않는다.
- rejected, unassigned, stale 또는 policy-excluded evidence는 활성 project
  output에 들어가지 않는다.

### P8. Restricted 산출물 생성

```bash
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
portfolio-maker render-html --workspace .
```

기대 산출물:

```text
.portfolio-maker/artifacts/master-profile.json
.portfolio-maker/artifacts/master-profile.md
.portfolio-maker/artifacts/portfolio-draft.md
.portfolio-maker/artifacts/portfolio-public.json
.portfolio-maker/artifacts/portfolio.html
```

합격 조건:

- 활성 project와 artifact policy가 선택한 evidence의 교집합만 사용한다.
- 모든 주요 claim을 승인 evidence로 추적할 수 있다.
- 승인되지 않은 source와 activity가 나타나지 않는다.
- 절대경로, private GitHub URL, private request/result ID, hash, credential,
  raw secret가 나타나지 않는다.
- HTML은 외부 runtime fetch 없이 브라우저에서 열린다.
- 필터, project detail, timeline이 정상 작동한다.
- project가 0개이면 내용을 창작하지 않고 정직한 empty state를 보여 준다.

공개 범위 재생성 전에는 restricted 산출물의 workspace-relative 파일 목록과
content hash를 기록하고, 파일럿 전용 비교 디렉터리에 복사본을 보존한다. 이
복사본은 전달용 artifact가 아니라 scope 차이를 검증하기 위한 로컬 test evidence다.

```text
.portfolio-maker/reviews/real-filesystem-pilot/restricted/
```

### P9. Open-public 산출물 분리

`open_public` artifact policy는 `restricted`와 별도로 승인하고 재생성한다.
재생성된 open-public 산출물도 별도 파일럿 비교 디렉터리에 보존하고 restricted
baseline과 구조·내용·노출 패턴을 비교한다. 비교가 끝나면 restricted policy를
복원해 최종 전달용 artifact를 다시 생성한다. 따라서 파일럿 종료 시 기본
`.portfolio-maker/artifacts/`에는 사용자가 검토할 수 있는 restricted 결과가 남는다.

```text
.portfolio-maker/reviews/real-filesystem-pilot/open_public/
```

합격 조건:

- 로컬 evidence가 0건이다.
- 비공개 GitHub evidence와 URL이 0건이다.
- 공개 GitHub에서 정확히 승인된 evidence만 남는다.
- 공개 근거가 부족하면 축소되거나 비어 있는 결과를 정직하게 보여 준다.
- 파일명이나 version이 배포 권한으로 해석되지 않는다.

### P10. 실제 선택형 Codex 비전 분석

사용자는 source approval을 통과한 실제 파일 중 다음 두 종류를 각각 하나 선택한다.

1. 현재 session이 직접 이해할 가능성이 높은 일반 이미지
2. 다중 페이지 PDF 또는 Office 문서처럼 capability 확인이 필요한 파일

각 파일은 별도 확인으로 한 번에 하나만 준비한다.

```bash
portfolio-maker prepare-vision-analysis \
  --workspace . \
  --source-id SOURCE_ID \
  --confirm-codex-share
```

`$portfolio-vision-analysis`는 request에 결속된 파일 하나만 현재 session의
capability로 검토한다. 결과를 적용한 뒤 pending 목록을 확인한다.

```bash
portfolio-maker apply-vision-analysis \
  --workspace . \
  --result RESULT_FILE

portfolio-maker list-vision-analyses --workspace .
```

사용자가 내용을 검토한 뒤 포함 또는 제외한다.

```bash
portfolio-maker set-vision-analysis-state \
  --workspace . \
  --analysis-id ANALYSIS_ID \
  --state included
```

합격 조건:

- 명시 확인 전 request가 만들어지지 않는다.
- 완료 결과도 처음에는 pending이며 evidence가 아니다.
- medium/high 결과만 명시 포함 후 derived evidence가 될 수 있다.
- low-confidence 및 unsupported, unavailable, failed, cancelled는 evidence가
  되지 않는다.
- 지원하지 못하는 형식을 이해한 것처럼 서술하지 않는다.
- multi-page 자료를 지원한다고 판정하려면 실제로 필요한 전체 페이지 범위를
  검토했음이 결과에 드러나야 한다.
- 포함 전후 및 제외 후 restricted artifact의 변화가 결정과 일치한다.
- `open_public`에는 로컬 vision evidence가 들어가지 않는다.

### P11. 파일럿 관리 이력

반복 생성으로 파일럿 소유 snapshot과 SQLite 이력이 생긴 뒤 sample policy와
dry-run plan을 만든다.

```bash
portfolio-maker history-policy --workspace . --write-sample
portfolio-maker history-plan --workspace .
```

실행 가능한 후보가 있을 때만 workspace 밖의 비어 있는 backup directory로
export한다.

```bash
portfolio-maker history-export \
  --workspace . \
  --plan-id PLAN_ID \
  --backup-dir BACKUP_DIRECTORY
```

잘못된 receipt 또는 확인 문구가 변경 전에 거부되는지 먼저 확인한다. 실제 apply는
private plan의 exact confirmation phrase와 matching receipt를 사용자가 다시 확인한
경우에만 수행한다.

합격 조건:

- policy가 없으면 모든 이력을 보존한다.
- plan은 원본 source를 후보로 선택하지 않는다.
- export directory는 workspace 밖의 비어 있는 실제 directory다.
- mismatched receipt·confirmation·plan은 journal이나 파일 이동 전에 거부된다.
- apply는 파일럿 소유 managed history와 exact SQLite row만 처리한다.
- 실제 실행이 중단 상태를 만들면 새 실행으로 덮지 않고 execution ID로 resume
  또는 recover한다.
- 원본 사용자 파일은 전후 hash, size, modification time 비교에서 변하지 않는다.

정상 파일럿을 일부러 강제 종료해 중단 상태를 만들지는 않는다. plan, export,
negative binding, 정상 apply는 실제 파일럿의 필수 검증이다. resume/recover는
실제 중단이 발생했을 때의 조건부 검증이며, 중단이 없으면 기존 자동화 회귀
검증 결과와 함께 `not exercised in live pilot`로 명시한다.

## 6. 프로젝트 품질 판정

### 6.1 필수 합격 조건

- 정책상 제외되지 않은 사전 기준 프로젝트가 100% 발견되거나 구체적인 제외
  사유가 있다.
- 하나의 상위 맥락이 근거 없이 여러 활성 프로젝트로 분할되지 않는다.
- 독립 제품·공모전·배포물만 별도 프로젝트로 분리된다.
- 명백히 사소한 파일·폴더가 사용자 결정 없이 활성 프로젝트가 되는 사례는 0건이다.
- `unassigned` evidence가 산출물 project로 유입되는 사례는 0건이다.
- project include/exclude/reinclude가 source와 index를 손상하지 않는다.

### 6.2 정성 평가

사용자는 각 포함 프로젝트에 대해 다음을 평가한다.

- 프로젝트 경계가 실제 인식과 일치하는가?
- 제목과 개요가 프로젝트 목적을 설명하는가?
- 흩어진 파일과 GitHub 활동이 같은 맥락으로 묶였는가?
- 별도 프로젝트여야 할 하위 제품이 잘못 흡수되지 않았는가?
- 작은 작업이나 복제 checkout이 독립 프로젝트로 과대평가되지 않았는가?
- 근거가 부족한 역할·기술·성과가 창작되지 않았는가?

정성 결과는 합격/수정 필요와 사용자 설명을 함께 기록한다. 문제 수를 줄이기 위해
후보를 임의 삭제하지 않고 grouping·confidence·unassigned 판단의 개선 근거로
사용한다.

## 7. 보안과 원본 불변성

### 7.1 Representative source baseline

파일럿 전 사용자가 승인한 대표 source 표본에 대해 다음을 기록한다.

- canonical path
- file type
- size
- modification time
- content hash

최종 산출물 생성과 이력 검증 후 같은 값을 다시 확인한다. 결과 보고서에는 실제
절대경로나 hash 값을 노출하지 않고 일치 여부만 기록한다.

### 7.2 노출 검사

restricted 및 open-public JSON, Markdown, HTML을 대상으로 다음 패턴을 검사한다.

- `/Users/` 등 실제 사용자 절대경로
- `file://` source URI
- private GitHub URL
- request/result ID
- source/result hash
- API key, token, password, private key 표식
- managed workspace 내부 locator
- 승인되지 않은 repository/activity 식별자

테스트용 합성 문자열과 실제 사용자 값은 구분하고, 실제 민감값을 검색 로그에
출력하지 않는다.

## 8. 즉시 중단 조건

다음 중 하나라도 발생하면 현재 단계에서 중단하고 다음 단계로 진행하지 않는다.

1. 사용자 승인 전 ingest 또는 artifact 생성이 필요하다.
2. 제외 폴더가 source 후보에 포함된다.
3. allowlist 밖의 비공개 저장소가 evidence가 된다.
4. credential, key, secret 원문이 화면·로그·산출물에 나타난다.
5. source가 바뀌었는데 기존 승인이 유효한 것으로 처리된다.
6. 명백히 사소한 항목이 다수의 활성 프로젝트로 자동 materialize된다.
7. 사전 기준 프로젝트가 모두 누락되고 원인을 설명할 수 없다.
8. restricted 산출물에 raw path 또는 private URL이 나타난다.
9. open-public 산출물에 로컬 또는 private evidence가 나타난다.
10. history plan이 원본 source를 대상으로 삼는다.
11. backup receipt, confirmation, plan binding 불일치 뒤에도 mutation이 시작된다.
12. 테스트 과정이 원본 또는 원격 GitHub 상태를 변경한다.

중단은 파일럿 실패를 숨기지 않는다. 이미 생성된 파일럿 workspace는 보존하고,
실행 명령, 실패 단계, 안전하게 수행되지 않은 후속 단계를 보고한다.

## 9. 결과 보고서

최종 파일럿 보고서는 다음 private local 경로에 저장하고 아래 내용을 포함한다.

```text
.portfolio-maker/reviews/real-filesystem-pilot/report.md
```

### 9.1 실행 정보

- exact commit과 branch
- Python, Node.js, npm, GitHub CLI 버전
- 파일럿 checkout과 workspace
- 탐색 root
- 사용자가 승인한 제외 규칙의 안전한 요약
- 공개·비공개 GitHub 승인 범위의 안전한 요약
- 실행 명령과 exit status

### 9.2 수량과 품질

- 발견한 로컬 source 후보 수
- 발견한 public/private GitHub activity 후보 수
- 승인·제외·건너뜀 수
- project candidate 수
- included, excluded, review-required, unassigned 수
- automatic 모드와 review 모드 차이
- 사전 기준 프로젝트별 발견·그룹핑 판정
- 사용자의 정성 평가

### 9.3 산출물

- 생성된 파일의 workspace-relative path
- 각 산출물의 delivery scope
- 활성 project와 evidence 연결 요약
- HTML 정적 검증과 핵심 interaction 결과
- restricted/open-public 차이
- vision pending/include/exclude 결과
- history plan/export/negative binding/apply 결과와, 실제 중단이 있었을 때의
  resume/recover 결과 또는 미실행 사유

### 9.4 안전과 잔여 위험

- representative source 불변성 결과
- 비밀·경로·private locator 노출 검사 결과
- 건너뛴 source와 원인
- unavailable 또는 unsupported format
- incomplete 또는 중단 단계
- 사용 전 추가 수정이 필요한 문제

## 10. 최종 합격 판정

실사용 파일럿은 다음 조건을 모두 만족해야 PASS다.

1. 설치부터 HTML 생성까지 정상 사용자 흐름으로 완료된다.
2. 사용자 승인 전에는 source ingestion과 evidence 반영이 일어나지 않는다.
3. 정책상 제외되지 않은 사전 기준 프로젝트가 발견되거나 누락 원인이 명확하다.
4. 프로젝트 grouping을 사용자가 검토하고 포함·제외할 수 있다.
5. 프로필, 초안, restricted manifest, restricted HTML이 생성된다.
6. open-public 산출물에 로컬·private evidence가 0건이다.
7. 승인되지 않은 source/activity가 모든 산출물에서 0건이다.
8. raw path, private URL, credential, private request/result metadata 노출이 0건이다.
9. 원본 파일 변경과 원격 GitHub mutation이 0건이다.
10. capability가 없는 형식은 정직한 status-only 결과로 남는다.
11. history 기능이 파일럿 관리 이력만 대상으로 하고 원본 source를 보존한다.
12. 모든 실패·건너뜀·잔여 위험이 최종 보고서에 기록된다.

필수 조건 하나라도 충족하지 못하면 결과는 NEEDS_WORK다. 실행 환경이나 사용자의
필수 승인이 없어 검증 자체를 수행하지 못한 경우에만 BLOCKED로 판정한다.

## 11. 후속 단계

이 설계가 문서 검토를 통과하면 별도의 구현·실행 계획에서 다음을 작업 단위로
구체화한다.

1. 파일럿 checkout과 baseline 준비
2. 실제 exclusion 및 GitHub 승인 검토
3. full-home discovery
4. source/artifact approval
5. ingest와 semantic-index
6. Codex project grouping 및 사용자 결정
7. restricted/open-public artifact 생성과 비교
8. vision 실제 파일 matrix
9. managed-history lifecycle
10. 결과 수집, 개인정보 검사, 사용자 검수, 최종 판정

계획 승인 전에는 실제 `$HOME` 탐색, GitHub discovery, ingest, vision request,
history apply를 실행하지 않는다.
