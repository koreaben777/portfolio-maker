# Portfolio Maker 0.2.0 계층형 의미 인덱스·플러그인 설계

날짜: 2026-07-14
상태: 사용자 승인 설계, 0.2.0 개발 완료 기준
추적 범위: Issue #13 후속 확장 및 0.2.0 릴리스 목표

## 1. 기획 요약

Portfolio Maker 0.1.0의 Codex 기반 semantic project composition은 승인된 evidence를 사람이 이해할 수 있는 프로젝트로 묶는 안전한 검토·승인 계층을 제공한다. 그러나 현재 입력은 평면적인 evidence 목록이며 로컬 discovery는 최대 500개 후보에서 멈춘다. 이 구조에서는 폴더의 상하위 맥락이 사라지고, 보험 RAG 챗봇이나 PlayMCP 공모전 작업처럼 명확한 대형 프로젝트도 후보가 0건이 되거나 지나치게 작은 단위로 분리될 수 있다.

0.2.0은 다음 목표를 하나의 개발 완료 기준으로 묶는다.

1. 전역 파일 개수 상한에 의존하지 않고 승인된 탐색 범위의 전체 구조를 인덱싱한다.
2. 파일의 역할과 내용을 요약하고, 하위에서 상위로 폴더 의미를 합성한다.
3. Codex가 구조적 신호, 의미적 신호, 반대 신호를 함께 사용해 프로젝트 경계를 판단한다.
4. 검토 모드와 `medium` 이상 자동 포함 모드를 모두 제공한다.
5. 자동 포함된 프로젝트를 사용자가 손쉽게 제외·재포함할 수 있게 한다.
6. 단일 repo skill을 책임별 여러 skill을 포함하는 Codex plugin으로 확장한다.
7. 계층형 의미 인덱스를 향후 개인 근거 지식 그래프와 Google Drive 등 외부 파일 소스로 확장할 수 있게 한다.

이 문서는 아직 구현되지 않은 0.2.0 목표를 설명한다. 현재 공개 버전과 실행 가능한 명령은 README의 0.1.0 설명과 현재 코드·테스트를 기준으로 한다.

## 2. 제품 정의와 범위

### 2.1 프로젝트의 의미

`portfolio project`는 파일, 폴더, repository 또는 activity 하나가 아니라 다음 특성을 가진 큰 작업 단위다.

- 사람이 이해할 수 있는 목적 또는 해결하려는 문제가 있다.
- 코드, 문서, 테스트, 데이터, 배포물 등 하나 이상의 근거가 연결된다.
- 하위 자료가 하나의 제품·공모전·연구·업무 맥락을 공유한다.
- 사용자 또는 명시적 자동 모드가 최종 포함 상태를 결정한다.

보험 RAG 챗봇, PlayMCP 공모전 준비, Portfolio Maker 개발처럼 독립적인 목적과 산출물을 가진 작업이 대표 사례다. 단발성 메모, 개별 함수, 임시 스크립트, 빌드 산출물은 그 자체로 프로젝트가 아니다.

### 2.2 0.2.0 포함 범위

- local source의 계층형 의미 인덱스
- 파일 형식별 제한적 의미 추출
- bottom-up directory summary
- Codex 기반 Project Boundary Detection
- candidate schema v2와 결정 근거
- review mode와 explicit automatic mode
- `high`와 `medium` 자동 포함
- 자동 포함 project의 가역적 제외·재포함
- content fingerprint 기반 증분 갱신
- 부분 실패와 실행 중단 복구
- additive SQLite migration
- 책임별 skill을 묶는 Codex plugin 구조
- 기존 profile, Markdown, manifest, static HTML pipeline 연동

### 2.3 0.2.0 제외 범위

- 전체 개인 근거 지식 그래프의 완성
- Google Drive connector 실제 구현
- 범용 vector database 또는 hosted graph database
- 외부 LLM API key 저장과 CLI 내부 LLM 호출
- MCP server 또는 별도 Codex App UI의 필수 도입
- 회사·JD별 맞춤 포트폴리오
- 자동 인터넷 공개 또는 자동 Sites deployment
- 원본 파일 이동·수정·삭제

## 3. 용어

| 용어 | 정의 |
|---|---|
| 계층형 의미 인덱스 | source의 구조와 각 파일·폴더의 의미 요약을 함께 저장한 분석 계층 |
| 디렉터리 의미 요약 노드 | 직접 파일과 하위 폴더 요약을 합성한 directory node |
| 프로젝트 경계 탐지 | parent/child/cross-directory 맥락을 비교해 portfolio project 단위를 제안하는 과정 |
| 개인 근거 지식 그래프 | 파일, 프로젝트, 활동, 기술, 산출물, 근거와 그 관계를 저장하는 후속 데이터 계층 |
| 포트폴리오 의미 온톨로지 | Project, Evidence, Artifact, Technology 같은 개념과 관계의 정의 |
| 다층 이종 지식 그래프 | 구조, 의미, portfolio, provenance 관점을 공통 node ID로 연결한 장기 아키텍처 |

## 4. 전체 아키텍처

```text
approved source scope
  -> source adapter
  -> complete structural crawl
  -> file semantic extraction
  -> bottom-up directory summaries
  -> hierarchical semantic index
  -> Codex project boundary detection
  -> candidate v2 + rationale + confidence
  -> review mode OR explicit automatic mode
  -> approved/auto-included/excluded project state
  -> EvidenceSelectionService
  -> profile / Markdown / manifest / interactive HTML
```

분석 계층과 승인 계층은 분리한다.

- semantic index는 project 후보를 찾기 위한 분석 자료다.
- candidate는 database truth가 아니다.
- review skill만 project 상태를 materialize할 수 있다.
- artifact는 materialized project와 현재 evidence policy의 교집합만 사용한다.
- project 자동 포함은 공개 배포 승인이 아니다.

## 5. Codex plugin과 skill 경계

0.2.0은 repository 자체를 installable Codex plugin으로 확장한다. Python application과 SQLite는 결정론적 엔진으로 유지하고, plugin은 workflow·Codex 분석·승인 조율을 담당한다.

```text
portfolio-maker/
  .codex-plugin/plugin.json
  skills/
    portfolio-maker/
    portfolio-source-governance/
    portfolio-semantic-index/
    portfolio-project-curation/
    portfolio-project-review/
    portfolio-artifacts/
  scripts/
  assets/
  src/
  web/
```

### 5.1 `portfolio-maker`

대표 entrypoint와 router다. 사용자의 목표와 workspace 상태를 확인하고 필요한 skill을 순서대로 호출한다. 기존 `$portfolio-maker` 사용 경험을 유지한다.

### 5.2 `portfolio-source-governance`

탐색 root, 제외 폴더, 파일 패턴, GitHub public/private opt-in, repository allow/exclude, artifact policy를 관리한다. 다른 skill은 이 범위를 넓힐 수 없다.

### 5.3 `portfolio-semantic-index`

전체 구조 수집, file analysis, directory summary, incremental refresh, coverage/error report를 담당한다. candidate나 artifact를 확정하지 않는다.

### 5.4 `portfolio-project-curation`

safe semantic review input을 읽고 project boundary, overview, grouping rationale, counter signal, confidence를 제안한다. candidate review file만 쓸 수 있다.

### 5.5 `portfolio-project-review`

approve, reject, merge, split, reassign, exclude, re-include 및 automatic mode를 담당한다. current hash와 policy를 재검증한 뒤에만 database project state를 변경한다.

### 5.6 `portfolio-artifacts`

승인 또는 자동 포함된 project를 현재 artifact policy와 교차해 profile, Markdown, manifest, static HTML로 만든다. `sites`와 디자인 skill은 설치되어 있을 때 협업하는 외부 기능이며 plugin에 복제하지 않는다.

### 5.7 skill 추가 원칙

CLI command마다 skill을 만들지 않는다. 다음 중 하나가 성립할 때만 새 skill을 추가한다.

- 사용자 목적이 독립적이다.
- 별도의 승인 또는 권한 경계가 필요하다.
- 독립적으로 발전하고 테스트할 workflow다.
- 기존 skill에 넣으면 서로 다른 책임이 결합된다.

초기 plugin에는 `.mcp.json`이나 `.app.json`을 필수로 넣지 않는다. Google Drive나 원격 tool surface가 실제 구현될 때 같은 application use case를 호출하는 adapter로 별도 설계한다.

## 6. source-independent semantic node model

모든 source item은 공통 node ID를 가진다.

| field | responsibility |
|---|---|
| `node_id` | 내부 참조용 stable ID |
| `source_id` | local, GitHub, future Drive source 식별자 |
| `node_kind` | source, directory, file, GitHub activity 등 |
| `parent_node_id` | 구조상 parent |
| `display_name` | 검토 가능한 safe label |
| `relative_hierarchy` | source 내부 상대 계층 |
| `content_fingerprint` | 변경 감지 hash |
| `semantic_summary` | file/directory 역할과 내용 요약 |
| `semantic_roles` | code, docs, test, config, deployment 등 |
| `topics` | domain, technology, purpose signal |
| `evidence_ids` | 기존 evidence pool 연결 |
| `analysis_status` | complete, partial, unsupported, failed 등 |
| `analyzer_version` | 재분석 판단용 version |
| `updated_at` | 마지막 성공 분석 시각 |

absolute path, private URL, provider credential처럼 source access에 필요한 locator는 별도 local locator table에 둔다. Codex review bundle과 artifact는 locator 대신 node ID, safe relative hierarchy, summary, evidence ID를 사용한다. absolute path는 로컬 재검증이나 진단에 실제로 필요할 때만 local surface에서 사용한다.

### 6.1 0.2.0 edge subset

- `contains`, `child_of`: 구조 관계
- `summarizes`: directory summary와 하위 node 관계
- `same_context_as`: 동일 작업 맥락 추정
- `supports_boundary`, `conflicts_with_boundary`: project 경계 판단 근거
- `part_of_project`, `supports_project`, `produced_by_project`: materialized project 관계

`uses`, `implements`, `tests`, `deploys`, `describes` 같은 세밀한 관계는 0.2.0에서는 semantic role 또는 fact로 저장할 수 있다. 후속 지식 그래프에서 검증된 관계만 정식 edge로 승격한다.

## 7. 계층형 의미 인덱스

### 7.1 complete structural crawl

전역 local candidate 수 500 상한을 제거한다. 모든 허용된 directory와 file을 구조 node로 기록하되, 모든 file 내용을 무제한으로 LLM에 전달하지 않는다.

- file별 읽기 크기와 parser budget
- large file의 header·structure 우선 분석
- README, manifest, entrypoint, test, deployment config 우선순위
- binary/unsupported file의 구조·metadata 보존
- directory별 summary budget
- content hash와 analyzer version 기반 cache

### 7.2 file semantic extraction

- code: module 역할, entrypoint, dependency, 제공 기능
- Markdown/document: 목적, 요구사항, 결정, 결과
- config: build, runtime, deployment, tool 역할
- test: 검증 대상과 품질 signal
- generated artifact: source와 구분된 output/deployment signal
- image/binary: supported analyzer가 없으면 metadata-only

secret masking과 excluded path pruning은 summary 생성 전 적용한다.

### 7.3 bottom-up directory summary

```text
file summaries
  -> direct child summaries
  -> directory purpose / components / outputs / lifecycle
  -> parent directory summary
```

file 하나의 변경은 해당 node와 ancestor summary만 invalidate한다. cross-directory semantic link가 있으면 연결 candidate만 추가 재평가한다.

## 8. Codex Project Boundary Detection

### 8.1 parent project 판단

Codex는 다음 신호가 일치하면 parent directory를 하나의 project로 제안한다.

- child가 공통 목적이나 문제를 해결한다.
- code, docs, tests, data, deployment가 상호 보완적이다.
- 공통 entrypoint, product description 또는 lifecycle이 있다.
- child만으로 독립적인 사용자 가치나 배포 정체성이 약하다.
- parent summary가 child 내용을 자연스럽게 설명한다.

### 8.2 independent child 분리

다음 신호가 충분하면 child를 별도 project로 제안한다.

- 독립 제품, 공모전, 고객, 연구 목적
- 독립 실행, build, deployment
- 별도 README와 entrypoint가 설명하는 독립 목적
- 자체 version, release, repository 또는 운영 주기

`.git`, README, manifest 하나는 보조 signal이며 단독 판정 규칙이 아니다.

### 8.3 cross-directory cluster

동일 project name, repository/deployment target, explicit document reference, 목적·domain·technology, time/output 관계가 강하게 일치하면 서로 다른 directory의 node를 하나의 candidate로 연결할 수 있다. directory 밖 연결은 rationale과 counter signal을 반드시 기록한다.

### 8.4 candidate v2

```json
{
  "version": 2,
  "index_revision": "sha256:...",
  "policy_hash": "sha256:...",
  "candidates": [
    {
      "id": "candidate-insurance-rag",
      "title": "보험 RAG 챗봇",
      "overview": "근거 범위 안의 프로젝트 개요",
      "boundary_type": "directory_root",
      "boundary_node_ids": ["node-insurance-rag"],
      "evidence_ids": [101, 102, 205],
      "grouping_rationale": ["공통 목적과 배포 설정"],
      "counter_signals": [],
      "confidence": "high",
      "review_reasons": []
    }
  ]
}
```

`boundary_type`은 `directory_root`, `independent_child`, `cross_directory_cluster`, `manual`을 지원한다. candidate 단계에서는 대안 비교를 위해 evidence overlap을 허용할 수 있지만 materialized active project끼리는 현재 계약대로 evidence ownership이 충돌할 수 없다.

## 9. confidence와 project decision

### 9.1 confidence

- `high`: 목적·산출물·경계가 명확하고 독립 근거가 일치하며 중대한 반대 신호가 없다.
- `medium`: project 가능성은 충분하지만 parent/child 또는 cross-directory 경계에 확인 가치가 있다.
- `low`: 이름·keyword 유사성 중심이거나 목적·산출물이 불명확하고 overlap 또는 분석 실패가 크다.

file 수는 confidence의 직접 점수나 필수 조건으로 사용하지 않는다.

### 9.2 modes

review mode에서는 모든 candidate가 사용자 결정 전까지 materialize되지 않는다.

explicit automatic mode에서는 다음을 적용한다.

| confidence | behavior |
|---|---|
| `high` | `auto_included_high` |
| `medium` | `auto_included_medium`, review recommended |
| `low` | `review_required` |
| undecidable | `unassigned` |

policy violation, stale hash, unresolved evidence conflict, boundary-critical analysis failure, prohibited broad root, generated/cache-only candidate는 confidence와 무관하게 자동 포함을 차단한다.

### 9.3 reversible exclusion

자동 포함 project는 사용자가 선택해 `excluded`로 바꿀 수 있다.

- project, evidence, semantic index를 삭제하지 않는다.
- artifact projection에서만 제외한다.
- 언제든 re-include할 수 있다.
- decision origin, timestamp, stable project ID, boundary fingerprint를 기록한다.
- 같은 boundary가 다시 분석되면 exclusion을 유지한다.
- split/merge처럼 identity가 크게 바뀌면 이전 exclusion을 자동 전파하지 않고 review를 요구한다.

수동 approve/exclude는 이후 자동 분석보다 우선한다. 자동 포함은 Sites/public deployment permission을 의미하지 않는다.

## 10. skill contract와 hash chain

```text
source-governance -> policy_hash
semantic-index -> index_revision
project-curation -> candidate_input_hash
project-review -> approval/decision_hash
portfolio-artifacts -> artifact input_manifest
```

이전 단계의 hash가 달라지면 downstream candidate와 decision을 stale로 처리한다. skill은 자기 책임 밖의 파일이나 database state를 직접 변경하지 않는다.

## 11. incremental update와 failure recovery

새 index revision은 staging state로 작성하고 최소 structural crawl이 성공한 뒤에만 active로 교체한다. 실행 중단 시 이전 active revision을 유지한다.

| failure | behavior |
|---|---|
| permission denied | unreadable node로 기록하고 계속 |
| unsupported file | metadata-only node 유지 |
| analyzer failure | partial/failed 상태와 ancestor incompleteness 기록 |
| changed during scan | 제한된 재시도 후 상태 기록 |
| symlink cycle | cycle을 끊고 diagnostics 기록 |
| cache corruption | affected item만 재분석 |
| malformed Codex output | candidate 적용 금지, 재생성 요청 |
| stale policy/index hash | candidate와 decision 적용 금지 |
| interrupted run | incomplete revision 폐기, previous active 유지 |
| temporary source failure | existing node 삭제 금지, unavailable 표시 |

file 부재는 성공한 complete structural crawl에서 확인된 경우에만 tombstone으로 전환한다. 원본은 어떤 복구 과정에서도 수정·이동·삭제하지 않는다.

## 12. migration과 compatibility

- 기존 source, snapshot, evidence, claim, artifact, portfolio project를 삭제하지 않는다.
- semantic node, edge, analysis revision, decision provenance는 additive table로 추가한다.
- 기존 user-approved project는 `manually_approved`로 이관한다.
- candidate v1은 historical review record로 보존하되 v2 index hash 없이 자동 재적용하지 않는다.
- migration 실패 시 기존 0.1.0 database를 계속 열 수 있어야 한다.
- 기존 repo skill은 plugin의 representative router로 의미를 이전하고 호환 entrypoint `$portfolio-maker`를 유지한다.
- plugin 설치가 Python dependency setup을 암묵적으로 대체한다고 설명하지 않는다. 설치·runtime bootstrap 계약은 구현 계획에서 별도로 검증한다.

## 13. privacy와 policy

- local semantic analysis의 읽기 권한은 사용자가 선택한 scan root와 excluded directory/file policy로 결정한다. per-file `approved_source_uris`를 semantic index 구조 수집의 선행 조건으로 사용하지 않는다.
- scan root 안에서 제외되지 않은 local item은 semantic index 대상이지만, index 포함이 artifact evidence 승인을 뜻하지 않는다. ingest와 artifact projection은 기존 source/evidence approval을 별도로 적용한다.
- private GitHub는 기존 auth, opt-in, allowlist, approval gate를 유지한다.
- locator와 credential은 Codex review bundle과 artifact에 넣지 않는다.
- absolute path는 local revalidation/diagnostics에 필요한 경우 내부에서 보관할 수 있지만 일반 review/artifact에는 사용하지 않는다.
- secret-shaped text는 summary 전에 mask한다.
- automatic project inclusion은 evidence approval이나 delivery scope를 우회하지 않는다.
- restricted/open_public artifact 규칙과 Sites deployment approval은 기존 contract를 유지한다.

## 14. test strategy

### 14.1 unit and contract

- stable node ID와 locator 분리
- excluded directory pruning
- analyzer별 extraction과 unsupported fallback
- content/analyzer version invalidation
- bottom-up summary invalidation
- candidate v2 validation
- hash chain과 stale blocking
- automatic state와 exclude/re-include transition
- manual decision precedence

### 14.2 structural fixtures

- 하나의 product 아래 frontend/backend/docs/tests
- 보관 directory 아래 여러 independent project
- large repository 내부의 독립 contest/deployment
- 여러 directory에 흩어진 동일 project 자료
- README/manifest가 없는 비정형 project
- 500개보다 많은 file
- binary, generated, unreadable item 혼합

### 14.3 recovery and security

- interrupted crawl
- changed-during-scan race
- symlink cycle
- partial cache corruption
- permission and temporary source failure
- malformed Codex JSON
- candidate 이후 policy change
- split/merge와 excluded project recurrence
- excluded/private/secret/locator가 review와 artifact에 유출되지 않음
- automatic include가 deployment permission으로 변환되지 않음

### 14.4 acceptance

- 보험 RAG 유형 fixture가 coherent parent project로 제안된다.
- PlayMCP 유형 independent contest work가 별도 project로 분리된다.
- Portfolio Maker 유형 repository가 명확한 candidate가 된다.
- known project가 존재하는데 candidate 0건이 되는 회귀가 없다.
- automatic mode에서 high/medium이 포함되고 low는 review에 남는다.
- 자동 포함 project를 제외하고 다시 포함할 수 있다.
- focused Python tests, full pytest, TypeScript check, Vite build, static validator, browser interaction 검증이 통과한다.

실제 사용자 home smoke test는 synthetic fixture와 분리하고, 원본을 변경하지 않는 별도 workspace에서 실행한다.

## 15. 0.2.0 완료 기준

다음을 모두 충족해야 0.2.0 개발 완료로 본다.

1. 전역 local file count cap 없이 complete structural index가 생성된다.
2. partial file failure가 전체 index와 기존 active revision을 파괴하지 않는다.
3. parent, child, cross-directory project 판단에 grounded rationale과 counter signal이 남는다.
4. review mode와 explicit automatic mode가 모두 동작한다.
5. medium 이상 자동 포함과 가역적 exclude/re-include가 동작한다.
6. manual decision이 재분석으로 덮어써지지 않는다.
7. existing evidence selection과 artifact delivery policy가 유지된다.
8. plugin manifest와 책임별 skill이 validation을 통과한다.
9. 0.1.0 workspace migration과 rollback-safe failure behavior가 검증된다.
10. README, plugin skills, roadmap, development principles, Issue 상태가 실제 runtime과 일치한다.
11. 전체 자동 검증과 실제 user-scope smoke test가 모두 통과한다.

## 16. 장기 확장

0.2.0의 semantic node ID와 edge subset은 다음 단계의 기반이다.

1. file-level semantic fact와 verified relation 확대
2. 개인 근거 지식 그래프 구축
3. portfolio semantic ontology versioning
4. Google Drive 등 source adapter 추가
5. 구조·의미·시간·provenance graph projection
6. 선택적 MCP/app surface
7. portfolio 외 resume, application, retrospective, semantic search 활용

후속 graph는 별도 중복 데이터베이스를 무조건 만드는 대신 공통 node ID와 provenance를 공유하는 multi-layer model로 확장한다.

## 17. 문서 동기화 원칙

설계나 구현 계획이 승인되면 별도 요청이 없어도 다음 문서를 검토한다.

- authoritative design/spec
- implementation plan
- Phase/Release roadmap
- README의 현재 기능과 future roadmap
- `docs/DEVELOPMENT_PRINCIPLES.md`
- repository skill/plugin instructions
- relevant GitHub Issue

현재 구현, 승인 설계, historical baseline을 같은 시제로 섞지 않는다. runtime behavior가 바뀌는 push에는 사용자 안내와 검증 기록을 같은 변경 단위로 포함한다.
