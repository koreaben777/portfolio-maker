# Portfolio Maker 아키텍처 설계

날짜: 2026-07-09
상태: 승인된 역사적 아키텍처, 구현된 0.1.0 MVP는 GitHub discovery-only

이 문서는 승인된 아키텍처 설계와 구현된 0.1.0 MVP 경계를 기록합니다.

## 기획 논의 요약

이 프로젝트는 사용자가 소유한 로컬 컴퓨터 파일과 GitHub 활동 등을 탐색하고, 그 근거를 바탕으로 포트폴리오 또는 채용 대비 자료를 생성하는 프로그램을 만드는 것을 목표로 합니다.

초기 제품 형태는 일반적인 로컬 앱에서 **Codex-native 로컬 워크플로우**로 좁혔습니다.

- 초기 아키텍처는 **로컬 앱 + CLI/엔진**이지만, 첫 MVP는 macOS에 설치된 **Codex app**을 기본 실행 환경으로 사용합니다.
- MVP는 **Codex Skill + CLI Engine** 형태로 시작합니다.
- 향후 더 깊은 **Codex app-server companion**으로 확장할 수 있도록 핵심 엔진 경계를 분리합니다.
- 우선순위는 다음과 같습니다.
  1. 승인된 로컬 파일로 커리어 지식베이스를 구축하고, GitHub 메타데이터는 discovery 검토에 사용합니다.
  2. 근거 기반 마스터 프로필과 포트폴리오 초안을 생성합니다.
  3. 회사별 전략과 맞춤형 취업 자료는 이후에 추가합니다.
- 초기 데이터 소스는 **로컬 파일과 GitHub**입니다. Google Drive는 명시적으로 후순위로 둡니다.
- GitHub 범위에는 repository, commit, pull request, issue, review, Actions 활동이 discovery 메타데이터로 포함되며, 0.1.0 MVP에서는 snapshot, profile, portfolio draft에 들어가지 않습니다.
- 로컬 discovery는 홈 디렉터리를 후보 탐색 대상으로 삼을 수 있지만, 사용자는 열람 금지 폴더를 지정할 수 있어야 합니다.
- 사용자가 발견된 source를 검토하고 승인하기 전에는 ingestion을 진행할 수 없습니다.
- 저장소는 **SQLite 중심**이며, DB로 정규화하지 않을 추출 텍스트와 메타데이터를 위한 최소 파일 기반 raw snapshot 저장소를 둡니다.
- 원본 파일은 복사하지 않습니다. 추출 텍스트, 메타데이터, 해시, source URI, locator, 마스킹 결과만 저장합니다.
- 초기 산출물은 다음 두 가지입니다.
  - 근거 기반 마스터 프로필
  - 공개용 포트폴리오 초안
- 이력서, 자기소개서, 회사별 전략, 면접 대비 자료는 후속 목표로 둡니다.
- 배포는 비공개 소규모 방식입니다. 코드는 사용자의 GitHub repository에 올리고, 승인된 사람만 pull해서 사용합니다.

## 제품 경계

MVP는 독립 소비자용 애플리케이션이 아니라 **Codex app에서 구동되는 로컬 커리어 지식 워크스페이스**입니다.

사용자의 기본 흐름은 다음과 같습니다.

```text
Codex app
  -> repo-scoped portfolio-maker skill
  -> portfolio-maker CLI
  -> reusable application use cases
  -> local SQLite, snapshots, and generated artifacts
```

Codex app은 사용자 프롬프트, 로컬 실행, 권한 검토, 필요 시 웹 검색, Git 워크플로우, 산출물 검토를 담당하는 상호작용 오케스트레이션 계층입니다.

이 repository가 제공하는 것은 다음입니다.

- 워크플로우를 정의하는 repo-scoped Codex skill
- 로컬 실행을 위한 CLI adapter
- 재사용 가능한 application engine
- 로컬 파일 시스템과 GitHub source connector
- SQLite 및 snapshot storage
- Markdown 및 JSON artifact writer
- 승인된 소규모 사용자를 위한 테스트와 setup 문서

초기 repository가 제공하지 않는 것은 다음입니다.

- standalone GUI
- hosted backend
- multi-user account system
- Codex app-server integration
- MCP server

## 목표

### MVP 목표

1. Codex app에서 커리어 데이터 discovery workflow를 안내할 수 있습니다.
2. 로컬 파일과 GitHub source 후보를 발견합니다.
3. 사용자가 열람 금지 폴더, repository, source class를 제외할 수 있습니다.
4. source approval이 명시되기 전까지 본문 ingestion을 차단합니다.
5. 승인된 로컬 파일을 SQLite 및 최소 snapshot으로 ingest하고, GitHub repository와 activity는 discovery 메타데이터로만 유지합니다.
6. JSON과 Markdown 형식의 근거 기반 master profile을 생성합니다.
7. Markdown 형식의 공개용 portfolio draft를 생성합니다.
8. 공개 artifact에 secret, token, private raw path가 노출되지 않게 합니다.
9. 핵심 정책과 저장소 동작에 대한 자동 테스트를 제공합니다.
10. 승인된 소규모 사용자를 위한 setup 및 사용 문서를 제공합니다.

### 후순위 목표

- Google Drive connector
- 회사 및 job description 조사
- 회사별 전략 생성
- 이력서 및 자기소개서 초안 작성
- 면접 대비 자료
- OCR/image analysis
- vector database 및 semantic search
- MCP server
- Codex app-server companion
- standalone GUI
- hosted product, account system, 대규모 배포

## 아키텍처 개요

시스템은 adapter, application use case, domain model, infrastructure로 나눕니다.

```text
.agents/
  skills/
    portfolio-maker/
      SKILL.md

src/portfolio_maker/
  adapters/
    cli/
    codex_skill_support/
    future_app_server/
  application/
    discover_sources
    approve_sources
    ingest_sources
    build_profile
    draft_portfolio
  domain/
    source
    evidence
    project
    skill
    work_item
    career_claim
    artifact
  infrastructure/
    local_fs_connector
    github_connector
    extractors
    sqlite_repository
    raw_snapshot_store
    secret_filter
    audit_log
```

정확한 파일 배치는 구현 중 조정될 수 있지만, 이 경계는 안정적으로 유지해야 합니다.

## 계층별 책임

### Codex Skill

Repo-scoped Codex skill은 핵심 지능 계층이 아니라 워크플로우 가이드입니다.

책임:

- 사용자가 원하는 target artifact를 확인합니다.
- 열람 금지 폴더와 제외 repository를 수집합니다.
- 필요한 순서대로 CLI 명령을 호출합니다.
- discovery 이후 멈추고 사용자가 source 후보를 검토하게 합니다.
- 생성된 artifact를 검토하여 근거 부족이나 안전하지 않은 공개 내용을 지적합니다.
- 필요 시 특정 CLI 명령 재실행을 제안합니다.

Skill은 approval checkpoint를 우회하면 안 됩니다.

### CLI Adapter

CLI는 의도적으로 얇은 adapter로 둡니다.

책임:

- command argument를 파싱합니다.
- config 및 workspace path를 해석합니다.
- application use case를 호출합니다.
- 안전한 progress output을 출력합니다.
- 의미 있는 exit code를 반환합니다.

CLI 안에 비즈니스 로직을 넣어서는 안 됩니다. 그래야 향후 app-server 또는 MCP adapter가 같은 동작을 다시 구현하지 않아도 됩니다.

초기 명령:

```bash
portfolio-maker discover
portfolio-maker approve
portfolio-maker ingest
portfolio-maker build-profile
portfolio-maker draft-portfolio
portfolio-maker run-mvp
```

`run-mvp`는 전체 pipeline을 오케스트레이션할 수 있지만, approval이 없으면 ingestion 전에 반드시 멈춰야 합니다.

### Application Use Cases

Application use case는 재사용 가능한 engine boundary입니다.

초기 use case:

- `discover_sources`
- `approve_sources`
- `ingest_sources`
- `build_profile`
- `draft_portfolio`

규칙:

- Use case는 terminal에 직접 출력하지 않습니다.
- Use case는 Codex thread state에 의존하지 않습니다.
- Use case는 명시적인 request model을 받고 명시적인 result model을 반환합니다.
- 장시간 실행되는 use case는 callback 또는 반환된 event record를 통해 progress event를 노출합니다.
- Use case는 Codex app 없이 직접 테스트 가능해야 합니다.

이 규칙들이 향후 Codex app-server companion으로 확장할 수 있는 기반입니다.

### Domain Layer

구현된 0.1.0 domain은 MVP runtime에서 사용하는 개념만 유지합니다.

- `Source`: 승인된 로컬 파일 또는 발견된 GitHub repository record
- `GitHubActivity`: repository/activity discovery 메타데이터

회사별 맞춤 생성 단계에서 runtime reader와 writer가 필요해질 때 normalized evidence, project, claim, artifact model을 추가할 수 있습니다.

### Infrastructure Layer

Infrastructure는 side effect를 구현합니다.

초기 infrastructure:

- local file system discovery
- local document text extraction
- GitHub API 또는 GitHub CLI 기반 collection
- SQLite repository
- raw snapshot file store
- secret masking 및 policy filter

Infrastructure module은 raw command output이나 secret 값을 log에 흘리지 않고 structured error를 반환해야 합니다.

## 데이터 흐름

### 1. Discovery

Discovery는 source 후보를 찾습니다.

Local discovery:

- 홈 디렉터리에서 후보 폴더와 파일을 스캔합니다.
- 기본 제외 규칙을 적용합니다.
- 사용자 forbidden-folder 규칙을 적용합니다.
- 불필요한 본문 읽기 없이 후보 metadata를 기록합니다.

GitHub discovery:

- repository 및 activity 후보를 나열합니다.
- commit, pull request, issue, review, Actions 활동을 포함합니다.
- private resource와 organization resource를 별도로 표시합니다.
- 사용자의 직접 활동이 확인되는 resource를 우선순위로 둡니다.
- repository와 activity를 discovery 메타데이터로만 저장하며, GitHub 본문을 ingest하거나 현재 artifact에 GitHub activity를 사용하지 않습니다.

Output:

- `.portfolio-maker/reviews/discovery-report.md`
- SQLite의 초기 source record

### 2. Approval

사용자는 discovery output을 검토하고 ingest 가능한 대상을 확정합니다.

Approval state는 다음 파일에 저장합니다.

```text
.portfolio-maker/reviews/source-approval.json
```

0.1.0 approval field는 다음과 같습니다.

- `approved_source_uris`: 승인된 로컬 source URI
- `forbidden_paths`: 읽거나 artifact에 사용하면 안 되는 로컬 경로
- `excluded_repositories`: discovery에서 제외할 GitHub repository
- `private_sources_allowed`: private GitHub repository를 discovery에 표시할지 여부

Repository allowlist와 제외 file pattern은 0.1.0에서 후순위이며 구현되지 않았습니다.

Approval이 없으면 ingestion은 fail closed 방식으로 실패해야 합니다.

0.1.0에서는 GitHub approval setting이 discovery 가시성만 제어합니다. GitHub artifact input을 승인하는 기능은 아닙니다.

### 3. Ingestion

Ingestion은 승인된 로컬 파일 source만 읽습니다.

저장 항목:

- extracted text snapshot
- source URI
- content hash
- metadata
- locator information
- extractor version
- masking result

원본 파일은 project store로 복사하지 않습니다.

### 4. Synthesis

0.1.0 synthesis 단계는 최신 승인 로컬 snapshot에서 master profile을 만듭니다. ingest된 source 목록과 source별 `project_evidence` claim 하나를 만듭니다. GitHub activity는 후속 회사별 맞춤 생성 단계까지 artifact input이 아닙니다.

상세 project summary, skill inventory, role 분석, confidence-scored claim은 회사별 맞춤 생성과 함께 후순위로 둡니다.

### 5. Portfolio Drafting

Portfolio draft는 현재 로컬 snapshot 기반 master-profile content에서 생성합니다.

포함해야 할 항목:

- project title
- short summary
- problem 또는 context
- user's role
- technical approach
- implementation details
- outcome 또는 impact, 단 근거가 있을 때만
- public-safe technology stack
- 검토용 internal evidence reference

Public draft에는 secret, token, private raw path가 노출되면 안 됩니다.

## 로컬 저장소 구조

MVP는 project-local state를 다음 경로 아래에 저장합니다.

```text
.portfolio-maker/
  portfolio.db
  raw/
    snapshots/
      local/
  artifacts/
    master-profile.json
    master-profile.md
    portfolio-draft.md
  reviews/
    discovery-report.md
    source-approval.json
```

`.portfolio-maker/`는 로컬 작업 데이터입니다. 어떤 subpath를 Git에서 ignore할지, example template을 따로 commit할지는 implementation plan에서 결정합니다.

## SQLite 모델

초기 table:

```text
sources
  id
  type
  uri
  display_name
  owner
  status
  discovered_at
  approved_at

source_snapshots
  id
  source_id
  snapshot_path
  content_hash
  extractor
  extracted_at

github_activities
  id
  source_id
  repo
  activity_type
  url
  title
  state
  author
  created_at
  merged_at

```

`evidence_items`, `projects`, `career_claims`, `claim_evidence`, `artifacts`는 회사별 맞춤 생성에 runtime reader와 writer가 생길 때까지 의도적으로 미룹니다. Vector database는 MVP에 포함하지 않습니다.

## 근거 규칙

1. 현재 profile claim은 승인된 로컬 snapshot에서 생성합니다.
2. GitHub URL과 activity는 0.1.0 MVP에서 discovery report 메타데이터로만 남깁니다.
3. Public artifact는 private raw path나 sensitive content를 노출하면 안 됩니다.
4. ingest된 로컬 source가 사라지면 stale로 표시하고, hash가 바뀌면 artifact 생성 전에 최신 snapshot을 만듭니다.

## 보안과 프라이버시

### Local File Policy

기본 discovery는 홈 디렉터리를 후보로 볼 수 있지만, 다음 class는 제외하거나 보수적으로 처리해야 합니다.

- `.Trash`
- `Library`
- `Applications`
- browser profiles
- password-manager exports
- private keys
- `.env` and credential files
- package caches
- virtual environments
- `node_modules`
- `.git/objects`
- large binary media

사용자는 forbidden folder를 추가할 수 있습니다. Forbidden-folder 하위 항목은 ingest하지 않으며, report에도 불필요하게 민감한 이름이 드러나지 않아야 합니다.

### Secret Handling

시스템은 다음을 지켜야 합니다.

- 추출된 snapshot에서 감지된 secret을 masking합니다.
- terminal output에 secret을 출력하지 않습니다.
- raw token이나 credential을 log에 남기지 않습니다.
- token-like value는 생성된 draft 안에서도 unsafe로 취급합니다.
- GitHub token을 repository 밖에 둡니다.

### GitHub Policy

MVP는 GitHub CLI auth 또는 fine-grained token을 사용할 수 있습니다.

규칙:

- read-only access를 우선합니다.
- token value를 저장하거나 출력하지 않습니다.
- public, private, organization repository를 구분합니다.
- private repository는 명시적으로 허용된 경우에만 표시합니다.
- GitHub rate-limit 및 repository별 실패는 관련 없는 discovery 결과를 버리지 않고 report에 표시합니다.
- 0.1.0 MVP에서는 GitHub repository와 activity를 profile 또는 portfolio input으로 사용하지 않습니다.

### Approval Gate

`discover`는 candidate report를 만들 수 있습니다. `ingest`는 `source-approval.json`이 존재하고 target source를 승인하기 전까지 source body를 읽으면 안 됩니다.

이 gate는 `run-mvp`를 사용할 때도 동일하게 적용됩니다.

`build-profile`은 기존 snapshot을 사용하기 전에 현재 approval과 forbidden-path policy를 다시 확인합니다.

## 오류 처리

Pipeline은 resumable해야 하며, 실패가 발생해도 기존 state를 파괴하지 않고 부분 실패로 처리해야 합니다.

예상 로컬 source state는 `skipped_policy`, `extract_failed`, `stale_source`, `approved`, `ingested`입니다.

Failure handling:

- File extraction failure는 raw content 없이 기록합니다.
- 로컬 evidence가 부족하면 현재 artifact claim을 생성하지 않습니다.
- Public-risk finding은 public artifact에서 제외합니다.

## 테스트 전략

### Unit Tests

검증 대상:

- path exclusion rules
- forbidden-folder matching
- secret masking
- source와 GitHub discovery model 생성
- artifact writer structure
- approval gate behavior

### Integration Tests

검증 대상:

- fixture home-directory discovery
- fixture GitHub API response discovery 및 메타데이터 저장
- SQLite persistence and reload
- fixture 기반 master-profile generation
- fixture profile 기반 portfolio draft generation
- approval이 없을 때 ingestion 차단

### Manual Verification

Codex-guided sequence를 실행합니다.

```text
discover -> approve -> ingest -> build-profile -> draft-portfolio
```

그 뒤 다음을 확인합니다.

- discovery report가 이해 가능해야 합니다.
- forbidden path가 지켜져야 합니다.
- master profile claim이 승인된 로컬 snapshot에서 생성되어야 합니다.
- portfolio draft가 private raw path와 secret을 노출하지 않아야 합니다.
- generated artifact가 문서화된 경로에 저장되어야 합니다.

## 배포 모델

Repository는 사용자의 GitHub 계정을 통해 매우 작은 승인 사용자 그룹에 배포합니다.

MVP에 포함해야 할 것:

- README setup instructions
- required tools and supported macOS version assumptions
- GitHub authentication setup notes
- Codex app usage notes
- local file scanning에 대한 safety warning
- permission, GitHub auth, rate-limit failure troubleshooting

포함하지 않을 것:

- hosted license checks
- user accounts
- auto-updater
- telemetry by default
- remote storage

## 향후 App-Server 확장 경계

MVP는 향후 Codex app-server를 사용하는 companion application으로 확장할 수 있어야 합니다.

그 경로를 보존하기 위해 다음을 지킵니다.

- core use case는 CLI와 Codex thread state로부터 독립적이어야 합니다.
- progress는 structured event로 표현해야 합니다.
- request와 result model은 serializable해야 합니다.
- approval state는 현재 thread 밖에 persist되어야 합니다.
- storage path와 schema는 안정적으로 유지되어야 합니다.
- 장시간 작업은 resumable해야 합니다.

미래 구조:

```text
Desktop or web companion
  -> Codex app-server client
  -> application use cases
  -> same SQLite and snapshot storage
  -> same generated artifacts
```

필요하다면 app-server보다 먼저 MCP를 추가할 수 있습니다. 이 경우 같은 use case를 다음과 같은 tool로 노출합니다.

- `discover_sources`
- `get_discovery_report`
- `ingest_approved_sources`
- `build_profile`
- `draft_portfolio`

MCP는 명시적으로 MVP에 포함하지 않습니다.

## 참고 자료

- Codex app features: https://developers.openai.com/codex/app/features
- Codex skills: https://developers.openai.com/codex/skills
- Codex app-server: https://developers.openai.com/codex/app-server
- Codex MCP: https://developers.openai.com/codex/mcp
