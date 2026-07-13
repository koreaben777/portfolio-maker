# Portfolio Maker

> 현재 공개 버전: `0.1.0`

승인한 내 작업 자료를 바탕으로, **근거를 확인할 수 있는 커리어 프로필**과 **검토용 포트폴리오 초안**, **public-safe 정적 HTML 포트폴리오**를 만드는 로컬 우선 도구입니다.

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

> 현재 버전은 **근거 기반 마스터 프로필**, **검토가 필요한 포트폴리오 뼈대**, **일반형 public-safe HTML 포트폴리오**를 제공합니다. 회사·채용공고별 맞춤 문장과 서술 생성은 다음 단계입니다. 일반형 HTML 구현 세부 사항은 [구현 계획](https://github.com/koreaben777/portfolio-maker/blob/main/docs/superpowers/plans/2026-07-13-portfolio-maker-general-interactive-html-sites.md)에 기록합니다.

## 이렇게 동작합니다

```text
후보 탐색 → 사용자 승인 → 자료 수집 → 마스터 프로필 생성 → 포트폴리오 초안 골격 생성
```

1. 로컬 파일과 GitHub 활동을 후보로 탐색합니다.
2. 사용자가 승인 파일에서 처리할 로컬 소스를 직접 선택합니다.
3. 승인된 자료만 로컬 작업 공간에 수집하고, 마스터 프로필을 생성합니다.
4. 마지막으로 검토·편집할 포트폴리오 Markdown 초안을 만듭니다.

## 빠른 시작

### 준비물

- macOS
- Codex app
- Python 3.11 이상
- Git
- GitHub 활동 탐색용 [GitHub CLI](https://cli.github.com/) `gh`

### 설치

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

### Codex app에서 실행

이 저장소를 열고 다음 스킬을 호출합니다.

```text
$portfolio-maker
```

처음에는 승인 파일 예시를 만듭니다.

```bash
portfolio-maker approve --workspace . --write-sample
```

탐색 전에는 `forbidden_paths`, `excluded_repositories`, `allowed_repositories`,
`private_sources_allowed`, `excluded_file_patterns`만 검토합니다. 정확한 activity URL은
아직 존재하지 않으므로 `approved_github_activity_urls`는 비워 둡니다.

그다음 후보를 확인하고 승인 내용을 검토합니다.

```bash
portfolio-maker discover --workspace .
```

```text
.portfolio-maker/reviews/discovery-report.md
.portfolio-maker/reviews/source-approval.json
```

discovery report의 `GitHub Activities`에서 URL을 고르기 전에, 대응하는
`GitHub Repositories` 항목이 `(public)`으로 표시되는지 확인합니다. 선택한 정확한 URL을
`approved_github_activity_urls`에 복사한 뒤, 로컬 `approved_source_uris`를 함께
검토·완성합니다. private, excluded, missing, stale activity는 승인하지 않습니다.

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
- `allowed_repositories`가 비어 있지 않으면 그 `owner/repo`만 GitHub 탐색 대상으로 남습니다.
- `excluded_file_patterns`는 대소문자를 구분하지 않는 파일명 glob으로 로컬 후보와 재수집을 제외합니다.
- `approved_github_activity_urls`는 discovery가 저장한 공개 GitHub activity URL을 정확히 지정합니다. private activity 또는 allowlist 밖·excluded repository activity는 승인되어도 산출물 입력으로 쓰지 않습니다.
- 공개 포트폴리오에는 비밀값, 토큰, 원본의 비공개 경로를 넣지 않아야 합니다.
- `portfolio-public.json`과 `portfolio.html`은 public-safe claim/evidence와 승인된 공개 GitHub URL만 사용합니다. 프로젝트별 timeline은 해당 evidence의 날짜와 provenance만 표시합니다.
- `.portfolio-maker/`는 Git에 커밋하지 마세요.
- `portfolio.db`와 journal/WAL/SHM sidecar는 하나의 관리 단위입니다. 개별 sidecar를 임의로 바꾸거나 삭제하지 마세요.

기존 승인 파일을 새 예시로 바꾸려면 명시적 강제 옵션이 필요합니다.

```bash
portfolio-maker approve --workspace . --write-sample --force
```

## 현재 범위와 로드맵

0.1.0에서는 승인된 로컬 자료와 명시 승인된 공개 GitHub activity를 profile 근거로 사용하고, 검토용 포트폴리오 초안과 일반형 public-safe HTML을 제공합니다. HTML은 build-time manifest를 번들한 정적 결과이며 SQLite, 원본, snapshot, credential을 runtime에 읽지 않습니다. 작업 이력은 사용자가 `.portfolio-maker/`를 직접 정리할 때까지 로컬에 남으며, 자동 보존·정리 기능은 아직 제공하지 않습니다. 회사·채용공고별 맞춤 작성(#3), Google Drive 연동, 이력서·자기소개서·면접 자료, OCR, 시맨틱 검색, MCP/app-server 인터페이스는 [GitHub Issues](https://github.com/koreaben777/portfolio-maker/issues)에서 관리합니다.

로컬 제외 폴더, GitHub private opt-in, 생성물별 근거 선택 정책은 [Issue #12](https://github.com/koreaben777/portfolio-maker/issues/12)와 [구현 계획](https://github.com/koreaben777/portfolio-maker/blob/main/docs/superpowers/plans/2026-07-14-unified-evidence-policy.md)에서 관리합니다. 이 정책은 아직 구현 전이므로 현재 `portfolio-public.json`과 `portfolio.html`은 기존처럼 public-safe 공개 GitHub 근거만 사용합니다.

#12 구현 후에도 두 파일명의 `public`은 호환성 이름입니다. 기본 결과는 **제한 공유(restricted)**로 생성되어, 승인된 로컬 자료·승인된 공개 GitHub·명시 승인된 private GitHub 근거를 포함할 수 있습니다. 이는 자동 인터넷 공개를 뜻하지 않으며 로컬 사용, 검증된 수신자 전달, private hosting을 위한 범위입니다. 누구나 접근 가능한 배포는 별도 `open_public` 선택과 재검증을 거쳐야 합니다.

## 버그와 제안

버그 리포트, 기능 아이디어, 개선 제안은 모두 [GitHub Issues](https://github.com/koreaben777/portfolio-maker/issues)로 남겨 주세요. 재현 방법, 기대한 결과, 실제 결과를 함께 적어 주시면 빠르게 확인할 수 있습니다.

## README 갱신 원칙

기능, 실행 방법, 요구 사항, 보안 정책, 생성 산출물이 달라지는 업데이트는 **같은 푸시에 README를 함께 갱신**합니다. 아직 구현되지 않은 제안과 논의는 README의 현재 기능으로 표현하지 않고 Issues에서 추적합니다.

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

권한이 없는 경로는 건너뛰고 보고서에 기록됩니다. 민감한 폴더는 `.portfolio-maker/reviews/source-approval.json`의 `forbidden_paths`에 추가하세요.

### 데이터베이스 복구

Portfolio Maker는 `portfolio.db`와 journal/WAL/SHM sidecar를 함께 검사하며, 협력하는 Portfolio Maker 프로세스 사이의 저장소 작업을 조율합니다. CLI가 안전하지 않은 관리 데이터베이스 경로를 보고하면 먼저 `.portfolio-maker/`를 보존하거나 백업하세요. 보고된 데이터베이스 패밀리 항목은 직접 확인한 뒤에만 정리하고, 그다음 명령을 다시 실행하세요. 데이터베이스 손상 메시지가 나오면 `portfolio.db`를 복구하거나 교체하기 전에 작업 공간 상태를 먼저 보존하세요.

### GitHub rate limit 또는 탐색 실패

GitHub 탐색 실패는 로컬 파일 탐색을 멈추지 않고 탐색 보고서에 기록됩니다. 제한이 해제된 뒤 다시 시도하거나 `--no-github` 옵션으로 GitHub 탐색을 끌 수 있습니다.

### 탐색 결과가 일부만 보일 때

로컬 파일 후보는 최대 500개, GitHub 저장소·PR·Issue 명령은 최대 100개까지 요청합니다. GitHub repository와 activity endpoint는 페이지를 자동으로 끝까지 읽지 않으며, 요청 한도에 도달하면 incomplete로 기록합니다. cap에 없는 repository와 cap에 도달한 activity endpoint의 기존 visibility는 철회하지 않지만, cap 안에서 private으로 확인된 repository의 activity는 즉시 제외합니다.
