# Portfolio Maker

> 현재 버전: `0.2.1`
>
> 계층형 의미 인덱스, Project Boundary Detection, 명시적 automatic 모드,
> 가역적 제외·재포함, multi-skill Codex plugin을 제공합니다.

승인한 내 작업 자료를 바탕으로, **근거를 확인할 수 있는 커리어 프로필**과 **검토용 포트폴리오 초안**, **public-safe 정적 HTML 포트폴리오**를 만드는 로컬 우선 도구입니다.

## 라이선스 / License

Portfolio Maker의 소스 코드는 [Portfolio Maker Personal Non-Commercial
License 1.0](LICENSE)에 따라 제공됩니다. 이 저장소는 소스가 공개되어 있지만,
OSI 승인 오픈소스 프로젝트는 아닙니다.

개인은 개인 학습, 연구, 테스트, 개인 포트폴리오 작성 및 개인 취업 준비를
위해 소프트웨어를 다운로드·설치·실행하고 자신의 기기에서 비공개로 수정할 수
있습니다. 수정본의 공개·재배포·판매·서브라이선스, SaaS·호스팅 제공, 회사 업무나
그 밖의 상업적 이용은 허용되지 않습니다. 상업적 이용은 저작권자와 별도 라이선스
계약이 필요합니다.

The source code of Portfolio Maker is provided under the [Portfolio Maker Personal
Non-Commercial License 1.0](LICENSE). The repository is source-available, but it is
not an OSI-approved Open Source project.

Individuals may download, install, run, and privately modify the Software for personal
study, research, testing, personal portfolio creation, and personal job preparation.
Public or private redistribution of modified versions, sale, sublicensing, SaaS or
hosting for others, company use, and other Commercial Use are prohibited. Commercial
use requires a separate license from the copyright holder.

이 라이선스는 소프트웨어 자체에 적용됩니다. 사용자가 입력한 파일, 개인정보 및
소프트웨어로 생성한 포트폴리오 산출물은 이 저장소 라이선스의 자동 적용 대상이
아니며, 해당 자료에 필요한 권리와 별도 라이선스는 사용자가 확인해야 합니다.

This license applies to the Software itself. Your input files, personal data, and
portfolio artifacts generated with the Software are not automatically covered by this
repository license; you remain responsible for the rights and third-party licenses
applicable to those materials.

Portfolio Maker는 원본 파일을 자동으로 업로드하거나 공개하지 않습니다. 먼저 후보를 확인하고, 사용자가 명시적으로 승인한 로컬 자료만 처리합니다. 공개 GitHub 활동도 `approved_github_activity_urls`에서 URL 단위로 명시 승인한 항목만 profile과 portfolio draft의 검토 근거로 반영하며, 자동 프로젝트 서술은 만들지 않습니다.

## 지금 할 수 있는 일

- 로컬 파일과 GitHub 활동 후보 찾기
- 처리할 로컬 자료를 직접 검토·승인하기
- 승인된 자료에서 텍스트 스냅샷과 커리어 근거 저장하기
- SQLite 데이터베이스와 journal/WAL/SHM sidecar를 하나의 작업 공간 상태로 안전하게 관리하기
- source, snapshot, activity와 생성 claim/artifact의 추적용 evidence 관계를 로컬 SQLite에 보관하기
- 동시에 실행된 Portfolio Maker 프로세스 사이의 저장소 작업을 조율하기
- 마스터 프로필을 JSON·Markdown으로 만들기
- 승인 자료 목록을 바탕으로 검토용 포트폴리오 초안 골격 만들기
- public-safe claim/evidence manifest와 프로젝트별 timeline이 있는 정적 HTML 만들기
- 승인한 scan root의 전체 허용 구조를 전역 파일 개수 상한 없이 계층형 의미 인덱스로 만들기
- 파일·폴더 요약에서 parent/child/cross-directory 프로젝트 경계 후보 만들기
- 모든 후보를 검토하는 review 모드 또는 `high`·`medium`을 포함하는 explicit automatic 모드 사용하기
- 자동 포함 프로젝트를 source·evidence·index 삭제 없이 제외하고 다시 포함하기
- source governance, semantic index, project curation/review, artifact 생성을 분리한 Codex plugin 사용하기

> 현재 버전은 **근거 기반 마스터 프로필**, **검토가 필요한 포트폴리오 뼈대**, **일반형 public-safe HTML 포트폴리오**를 제공합니다. 회사·채용공고별 맞춤 문장과 서술 생성은 다음 단계입니다. 일반형 HTML 구현 세부 사항은 [구현 계획](https://github.com/koreaben777/portfolio-maker/blob/main/docs/superpowers/plans/2026-07-13-portfolio-maker-general-interactive-html-sites.md)에 기록합니다.

## 이렇게 동작합니다

```text
scope 승인 → 후보 탐색·근거 승인 → 의미 인덱스 → 프로젝트 검토·결정 → artifact 생성
```

1. 로컬 파일과 GitHub 활동을 후보로 탐색합니다.
2. 사용자가 승인 파일에서 처리할 로컬 소스를 직접 선택합니다.
3. 승인한 scan root의 안전한 chunk로 계층형 의미 인덱스를 만들고 프로젝트 경계 후보를 검토합니다.
4. 현재 source/artifact policy와 활성 프로젝트의 교집합만 profile, Markdown, manifest, HTML로 만듭니다.

## 빠른 시작

### 준비물

- macOS
- Codex app
- Python 3.11 이상
- Git
- Node.js LTS와 npm (정적 HTML renderer 최초 실행 시)
- GitHub 활동 탐색용 [GitHub CLI](https://cli.github.com/) `gh`

### 설치

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

정적 HTML renderer를 처음 실행하는 checkout에서는 Node.js LTS와 npm을 준비한 뒤
workspace의 Sites 의존성을 먼저 설치합니다.

```bash
(cd web/portfolio && npm ci)
```

그 다음 일반 실행 순서에서 `portfolio-maker render-html --workspace .`를 실행합니다.
전역 Vite 설치나 `npx` fallback은 사용하지 않습니다.

### Codex app에서 실행

Portfolio Maker plugin이 설치된 Codex 환경에서는 새 task에서 다음 router를 호출합니다.

```text
$portfolio-maker
```

plugin은 Python package나 Sites 의존성을 대신 설치하지 않습니다. 배포 marketplace에서 설치할 때는
해당 marketplace가 먼저 구성되어 있어야 하며, 설치 후 새 task를 엽니다.

```bash
codex plugin add portfolio-maker@<configured-local-marketplace>
```

marketplace 설치 경로를 사용하지 않는 source checkout에서는 repository root를 Codex app으로 열고
`.agents/skills/portfolio-maker/SKILL.md`의 호환 entrypoint를 사용할 수 있습니다. plugin router는
다섯 child skill을 순서대로 호출하지만, source scope·project 결정·artifact delivery 승인은 서로
대체하지 않습니다.

### 첫 포트폴리오 튜토리얼

아래 순서는 Codex app에서 **처음으로 자신의 기본 포트폴리오를 만드는 전체 흐름**입니다. 후보를
자동으로 포트폴리오에 넣지 않으며, 각 단계에서 사용자가 근거와 project 구성을 승인합니다.

먼저 두 경로를 구분합니다.

- `--workspace .`는 이 저장소를 clone한 작업 공간입니다. `.portfolio-maker/`의 승인 파일·로컬
  데이터베이스·생성물과 HTML renderer가 여기에 생깁니다.
- `discover --home PATH`는 포트폴리오 근거 후보를 찾을 로컬 탐색 루트입니다. 기본값은 홈
  디렉터리지만, 튜토리얼에서는 명시적으로 지정합니다. 필요한 범위가 더 좁다면 `"$HOME"` 대신
  해당 폴더를 사용하세요.

아래 명령은 모두 clone한 저장소의 root에서 실행합니다. `$PWD`는 도구 저장소 자체이므로 첫
탐색에서 제외합니다.

#### 1. 설치하고 Codex에서 열기

다른 사용자는 `main` 브랜치를 clone한 뒤, 자신의 컴퓨터에서 아래 준비를 마칩니다.

```bash
git clone https://github.com/koreaben777/portfolio-maker.git
cd portfolio-maker
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
(cd web/portfolio && npm ci)
```

그 다음 Codex app에서 repository root를 열고 새 task에 `$portfolio-maker`를 입력합니다.
plugin 또는 repository skill이 목록에 바로 보이지 않으면 새 task를 열거나 Codex app을 다시 시작합니다. GitHub 활동을
탐색할 때만 `gh auth login`이 필요하며, 로컬 파일만 사용할 때는 GitHub 인증 없이 진행할 수 있습니다.

#### 2. 탐색 제외 규칙을 먼저 승인하기

각 사용자는 자신의 `.portfolio-maker/` 작업 공간을 별도로 만듭니다. 이 디렉터리에는 승인 파일,
로컬 SQLite 데이터베이스, 스냅샷, 생성물이 들어갈 수 있으므로 다른 사용자와 공유하거나 Git에
커밋하지 않습니다.

처음에는 승인 파일 예시를 만듭니다.

```bash
portfolio-maker approve --workspace . --write-sample
```

`.portfolio-maker/reviews/source-approval.json`을 열어 탐색에서 제외할 폴더와 저장소를 정합니다.
새 제외 폴더는 `excluded_directories`에 기록하고, 이전 형식의 `forbidden_paths`는 하위 호환용으로
유지합니다. 비공개 GitHub을 검토하려면 `private_sources_allowed`를 `true`로 바꾸고 필요한
`allowed_repositories`만 `owner/repo` 형식으로 추가합니다. 정확한 activity URL과
`approved_source_uris`는 아직 후보를 확인하지 않았으므로 비워 둡니다.

#### 3. 후보를 탐색하고 근거를 승인하기

홈 디렉터리 전체를 기준으로 시작하려면 다음을 실행합니다. `--exclude-directory "$PWD"`는 현재
도구 저장소를 후보에서 빼고 그 선택을 승인 파일에 기록합니다. 로컬 파일만 먼저 검토하려면
마지막에 `--no-github`를 추가하세요.

그다음 후보를 확인하고 승인 내용을 검토합니다.

```bash
portfolio-maker discover --workspace . --home "$HOME" --exclude-directory "$PWD"
```

```text
.portfolio-maker/reviews/discovery-report.md
.portfolio-maker/reviews/source-approval.json
```

discovery report의 `GitHub Activities`에서 URL을 고르기 전에, 대응하는
`GitHub Repositories` 항목이 `(public)`으로 표시되거나 private opt-in/allowlist를
통과했는지 확인합니다. 선택한 정확한 URL을
공개 activity는 `approved_github_activity_urls`에, private activity는 private opt-in과
allowlist를 확인한 뒤 `approved_private_github_activity_urls`에 복사합니다. 로컬
후보의 `file://` URI만 `approved_source_uris`에 복사합니다. excluded, missing, stale activity는
승인하지 않습니다.

#### 4. 생성물별 공유 범위를 정하기

로컬·공개 GitHub·private GitHub 근거를 생성물별로 선택하려면 artifact policy 예시도
초기화합니다.

```bash
portfolio-maker approve --workspace . --write-sample-artifact-policy
```

`.portfolio-maker/reviews/artifact-approval.json`에서 생성물별 `delivery_scope`와
`include_*`, 제외 목록을 검토합니다. 기본은 `restricted`이며, `open_public`은 별도
재생성과 공개 적합성 검증이 필요한 공개 GitHub 전용 범위입니다.

#### 5. 의미 있는 포트폴리오 project를 구성하기

0.2.0 semantic project path에서는 먼저 승인된 scan root의 safe semantic index를 준비하고
적용합니다. `<confirmed-root>`는 discovery에서 사용자가 선택하고 승인한 동일한 root로 바꿉니다.

```bash
portfolio-maker ingest --workspace .
portfolio-maker prepare-semantic-index --workspace . --root <confirmed-root>
```

Codex의 `portfolio-semantic-index` skill은 managed input chunk만 읽어 output chunk를 작성합니다.
그 뒤 hash·coverage 검증을 거쳐 index를 적용합니다.

```bash
portfolio-maker apply-semantic-index --workspace .
portfolio-maker prepare-project-review --workspace . --version v2
```

Codex에게는 다음처럼 요청합니다. Codex는 지정된 v2 review bundle만 읽어 후보 파일을 작성하며,
원본 파일·로컬 경로·private GitHub URL을 다시 탐색하거나 추론해서는 안 됩니다.

```text
$portfolio-maker
`.portfolio-maker/reviews/project-review-input-v2.json`만 근거로 읽고,
의미 있는 작업 단위의 후보를 `.portfolio-maker/reviews/project-candidates.json`과
`.portfolio-maker/reviews/project-candidates.md`에 작성해 주세요. 각 후보는 검토 필요 상태로
두고, 확신이 없거나 작은 단발성 근거는 unassigned로 남겨 주세요.
```

Codex는 `.portfolio-maker/reviews/project-review-input-v2.json`만 읽어
`project-candidates.json`과 `project-candidates.md`를 작성할 수 있습니다. candidate는
검토 보조물일 뿐 database truth가 아닙니다. v2에서는 후보를 검토한 뒤 다음 명령으로
`review_required` 결정을 materialize하고, 사용자가 `set-project-state`로 각 project를
`included` 또는 `excluded`로 결정합니다.

```bash
portfolio-maker compose-projects --workspace . --mode review
portfolio-maker set-project-state --workspace . --project-id ID --state included
```

`approve --write-sample-project-approval`은 0.1.0 호환 경로의 빈 v1 template만 만드는
명령이며 v2 materialization에 사용하지 않습니다. v2는 후보 없이 `project-approval.json`을
읽지 않으며, 직접 결정할 project가 없으면 정직한 zero-project 상태를 유지합니다.

모든 후보를 사용자 결정으로 materialize하려면 `--mode review`를 사용합니다. `high`와 `medium`을
명시적으로 자동 포함하려면 사용자가 선택한 경우에만 다음 명령을 사용합니다.

```bash
portfolio-maker compose-projects --workspace . --mode review
portfolio-maker compose-projects --workspace . --mode automatic
portfolio-maker list-projects --workspace . --format table
portfolio-maker set-project-state --workspace . --project-id ID --state excluded
portfolio-maker set-project-state --workspace . --project-id ID --state included
```

`automatic`은 evidence·artifact approval을 대신하지 않으며, `excluded` project를 삭제하지 않습니다.
재포함 후에도 현재 policy와 approved evidence link를 다시 통과해야 합니다.

활성 상태로 결정된 semantic project만 profile summary, draft, manifest, HTML의 project로 표시됩니다.
활성 project decision이 없으면 evidence inventory는 유지되지만 project 목록은 빈 상태입니다.
각 artifact는 자기 policy로 evidence를 다시 선택한 뒤 승인 project link와 교차하며,
candidate·rejected·unassigned·stale evidence는 project output에 포함되지 않습니다.

#### 6. 프로필·초안·인터랙티브 HTML 생성하기

기존 workspace에서 provenance가 없는 legacy workflow activity는 안전을 위해 profile과
portfolio draft의 입력에서 제외됩니다. 이를 복구하려면 `portfolio-maker discover
--workspace .`가 성공하도록 다시 실행한 뒤 discovery report에서 해당 공개 activity의
정확한 URL을 확인하고, 필요한 경우 그 URL만 다시 승인 파일에 넣어 재승인합니다.
그 다음 아래 순서로 실행합니다.

```bash
portfolio-maker ingest --workspace .
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
portfolio-maker render-html --workspace .
```

`portfolio.html`은 정적 파일이므로 웹 브라우저로 열어 filter, project detail, timeline을
확인할 수 있습니다. `restricted` 결과는 로컬 사용·검증된 수신자 전달·private hosting을 위한
범위이며, 파일명이 `public`이라고 자동 인터넷 공개되는 것은 아닙니다.

생성 결과는 다음 위치에 저장됩니다.

```text
.portfolio-maker/artifacts/master-profile.json
.portfolio-maker/artifacts/master-profile.md
.portfolio-maker/artifacts/portfolio-draft.md
.portfolio-maker/artifacts/portfolio-public.json
.portfolio-maker/artifacts/portfolio.html
```

## 개인정보와 안전

- 승인 파일이 없으면 자료 수집은 시작되지 않습니다.
- 원본 파일은 `.portfolio-maker/`에 복사하지 않습니다.
- 추출된 스냅샷에서는 일반적인 비밀값 패턴을 마스킹합니다.
- 프로필 생성 전에는 승인 상태, 금지 경로, 원본 파일 해시, 최신 스냅샷을 다시 확인합니다.
- 비공개 GitHub 저장소는 `private_sources_allowed`를 명시적으로 허용하지 않는 한 건너뜁니다.
- `excluded_repositories`에 넣은 저장소는 GitHub 탐색에서 제외합니다.
- GitHub repository 목록은 먼저 계정 범위를 전역 열거한 뒤 allowlist로 필터링하며, allowlist 밖 저장소에는 repository-scoped activity endpoint만 호출하지 않습니다.
- 공개 GitHub는 `allowed_repositories`가 비어 있으면 기존처럼 공개 저장소 전체를 탐색하지만, private GitHub discovery는 `private_sources_allowed=true`와 비어 있지 않은 canonical allowlist가 모두 있어야 하며 allowlist 밖 저장소는 endpoint를 호출하지 않습니다.
- `excluded_file_patterns`는 대소문자를 구분하지 않는 파일명 glob으로 로컬 후보와 재수집을 제외합니다.
- `approved_github_activity_urls`는 discovery가 저장한 공개 GitHub activity URL을 정확히 지정합니다. private activity 또는 allowlist 밖·excluded repository activity는 승인되어도 산출물 입력으로 쓰지 않습니다.
- `approved_private_github_activity_urls`는 `private_sources_allowed`, repository allowlist, 제외 정책을 모두 통과한 private activity에만 사용합니다. private provenance는 restricted 결과에서 안전한 label로만 표시합니다.
- 정확한 private activity URL은 discovery report라는 로컬 승인 표면에서만 확인·선택할 수 있으며, 생성 artifact와 safe semantic review bundle에는 표시하지 않습니다.
- restricted semantic project의 title/overview에는 사용자가 approval file에서 직접 승인한 private repository name을 display text로 포함할 수 있습니다. 이는 자동 source label 공개가 아니며, private GitHub URL·raw locator·snapshot/database path·credential/token은 계속 출력하지 않습니다.
- 어떤 delivery scope에도 비밀값, 토큰, credential, raw local path를 넣지 않습니다.
- `portfolio-public.json`과 `portfolio.html`은 파일명과 무관하게 artifact policy의 delivery scope를 따릅니다. 승인된 semantic project별 timeline은 선택된 evidence의 날짜와 provenance만 표시합니다.
- `.portfolio-maker/`는 Git에 커밋하지 마세요.
- `portfolio.db`와 journal/WAL/SHM sidecar는 하나의 관리 단위입니다. 개별 sidecar를 임의로 바꾸거나 삭제하지 마세요.

기존 승인 파일을 새 예시로 바꾸려면 명시적 강제 옵션이 필요합니다.

```bash
portfolio-maker approve --workspace . --write-sample --force
```

## 현재 범위와 로드맵

0.2.0에서는 승인된 로컬 자료, 명시 승인된 공개 GitHub activity, 조건을 충족한 private GitHub activity를 공통 evidence pool로 관리합니다. 모든 생성물은 artifact별 `EvidenceSelectionService`를 거치며, `portfolio-public.json`과 `portfolio.html`도 기본 `restricted` 결과입니다. HTML은 build-time manifest를 번들한 정적 결과이며 SQLite, 원본, snapshot, credential을 runtime에 읽지 않습니다.

현재 HTML의 `projects` 배열은 사용자가 승인한 semantic portfolio project만 표시합니다. local file, repository, activity 하나는 자동 project가 되지 않습니다. `portfolio-maker prepare-project-review`가 현재 artifact policy를 통과한 안전한 evidence bundle을 만들고, Codex candidate는 검토 보조물로만 사용됩니다. v2에서 사용자가 review/automatic decision을 확정한 뒤 `compose-projects`를 실행해야만 project가 materialize됩니다.

승인된 project가 없으면 master profile의 evidence inventory는 유지하면서 draft, manifest, HTML은 정직한 zero-project 상태를 생성합니다. 승인된 project의 effective evidence는 각 artifact policy의 선택 결과와 semantic link의 교집합이며, candidate·rejected·unassigned·stale evidence는 project output에 포함되지 않습니다. project approval에는 merge, split, reassign, reject, unassigned 결정을 직접 반영할 수 있고, 외부 LLM API나 token 저장은 사용하지 않습니다.

로컬 제외 폴더, GitHub private opt-in, 생성물별 근거 선택 정책은 [Issue #12](https://github.com/koreaben777/portfolio-maker/issues/12)와 [구현 계획](https://github.com/koreaben777/portfolio-maker/blob/main/docs/superpowers/plans/2026-07-14-unified-evidence-policy.md)에서 관리합니다. artifact policy가 없는 기존 workspace는 0.1.0 호환 경로로 public GitHub evidence만 manifest/HTML에 사용합니다.

두 파일명의 `public`은 호환성 이름입니다. `restricted`는 자동 인터넷 공개를 뜻하지 않으며 로컬 사용, 검증된 수신자 전달, private hosting을 위한 범위입니다. 누구나 접근 가능한 배포는 별도 `open_public` 선택과 재검증을 거쳐야 하며, 이 구현에서는 local/private origin을 거부합니다.

### 0.2.0 현재 구현

현재 release는 0.1.0의 승인·근거·산출물 경계를 유지하면서, 다음 기능을 추가로 제공합니다.

- 허용된 전체 폴더·파일 구조를 전역 개수 상한 없이 기록하는 계층형 의미 인덱스
- 코드·문서·테스트·설정 파일의 역할 요약과 하위에서 상위로 합성하는 폴더 요약
- 상위 폴더의 공통 맥락과 독립 하위 제품·공모전·배포물을 구분하는 Codex Project Boundary Detection
- 검토 모드와, `high`·`medium` 후보를 포함하는 명시적 자동 모드
- 자동 포함 project를 원본이나 evidence 삭제 없이 선택 제외하고 다시 포함하는 검토 흐름
- Codex plugin의 source governance, semantic indexing, candidate curation, project review, artifact 생성을 나눈 multi-skill workflow
- 향후 개인 근거 지식 그래프와 Google Drive 등 추가 source로 확장할 수 있는 공통 node/provenance model

0.2.0 자동 포함은 evidence 승인, delivery scope 또는 공개 배포 승인을 대신하지 않습니다. 의미
index와 후보는 분석·검토 계층이며, 실제 project와 artifact는 기존 승인 및 현재 policy의 교집합으로
materialize됩니다. `portfolio-maker prepare-semantic-index`와 `apply-semantic-index` 사이의
Codex 분석은 외부 LLM API나 token 저장 없이 safe chunk를 통해 수행합니다.

`0.2.1`은 정적 HTML 안전성 검사를 보완한 patch release입니다. 근거 텍스트에
`.portfolio-maker`라는 일반 명칭이 포함돼도 허용하되, 실제 내부 workspace 경로 형태는 계속
차단합니다.

Task 21의 read-only smoke는 보호된 사용자 legacy/runtime 자료를 건드리지 않기 위해 별도 workspace에서
사용자가 선택한 repository `src` subtree만 대상으로 수행했습니다. 따라서 이 release 기록은 전체 home,
대규모 외부 project, live GitHub 응답의 성능·내용을 보증하지 않습니다. 검증 수치와 browser 결과는
[0.2.0 검증 기록](docs/reviews/2026-07-14-portfolio-maker-0.2.0-verification.md)에 고정합니다.

개인 근거 knowledge graph, Google Drive connector, MCP/App UI, 회사·JD별 맞춤 생성, 자동 Sites
deployment는 0.2.0 후속 범위입니다. 전체 설계와 구현 순서는
[0.2.0 설계 명세](docs/superpowers/specs/2026-07-14-portfolio-maker-0.2.0-semantic-index-plugin-design.md)와
[0.2.0 구현 계획](docs/superpowers/plans/2026-07-14-portfolio-maker-0.2.0.md)에서 관리합니다.

## 버그와 제안

버그 리포트, 기능 아이디어, 개선 제안은 모두 [GitHub Issues](https://github.com/koreaben777/portfolio-maker/issues)로 남겨 주세요. 재현 방법, 기대한 결과, 실제 결과를 함께 적어 주시면 빠르게 확인할 수 있습니다.

## README 갱신 원칙

기능, 실행 방법, 요구 사항, 보안 정책, 생성 산출물이 달라지는 업데이트는 **같은 푸시에 README를 함께 갱신**합니다. 설계나 구현 계획이 승인되어 roadmap이 달라진 경우에도 별도 요청을 기다리지 않고 권위 명세, roadmap, 개발 원칙과 README의 future section을 함께 검토합니다. 아직 구현되지 않은 제안과 논의는 README의 현재 기능으로 표현하지 않고 Issues에서 추적합니다.

## 문제 해결

### GitHub 인증

```bash
gh auth status
```

인증이 필요하면 다음을 실행합니다.

```bash
gh auth login
```

활동 읽기에 필요한 가장 좁은 권한만 사용하세요.

### 권한 오류

권한이 없는 경로는 건너뛰고 보고서에 기록됩니다. 민감한 폴더는 `.portfolio-maker/reviews/source-approval.json`의 `excluded_directories`에 추가하세요. 기존 `forbidden_paths`도 하위 호환 alias로 읽습니다.

### 데이터베이스 복구

Portfolio Maker는 `portfolio.db`와 journal/WAL/SHM sidecar를 함께 검사하며, 협력하는 Portfolio Maker 프로세스 사이의 저장소 작업을 조율합니다. CLI가 안전하지 않은 관리 데이터베이스 경로를 보고하면 먼저 `.portfolio-maker/`를 보존하거나 백업하세요. 보고된 데이터베이스 패밀리 항목은 직접 확인한 뒤에만 정리하고, 그다음 명령을 다시 실행하세요. 데이터베이스 손상 메시지가 나오면 `portfolio.db`를 복구하거나 교체하기 전에 작업 공간 상태를 먼저 보존하세요.

### GitHub rate limit 또는 탐색 실패

GitHub 탐색 실패는 로컬 파일 탐색을 멈추지 않고 탐색 보고서에 기록됩니다. 제한이 해제된 뒤 다시 시도하거나 `--no-github` 옵션으로 GitHub 탐색을 끌 수 있습니다.

### 탐색 결과가 일부만 보일 때

기존 flat local discovery 경로의 보고서 후보는 최대 500개이며, GitHub 저장소·PR·Issue 명령은 최대 100개까지 요청합니다. 0.2.0 semantic index 경로는 승인된 scan root의 구조를 전역 파일 개수 상한 없이 분석합니다. GitHub repository와 activity endpoint는 페이지를 자동으로 끝까지 읽지 않으며, 요청 한도에 도달하면 incomplete로 기록합니다. cap에 없는 repository와 cap에 도달한 activity endpoint의 기존 visibility는 철회하지 않지만, cap 안에서 private으로 확인된 repository의 activity는 즉시 제외합니다.
