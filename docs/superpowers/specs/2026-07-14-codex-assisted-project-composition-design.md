# Codex 기반 프로젝트 식별·구성 설계

날짜: 2026-07-14
상태: 사용자 승인 설계 / 구현 계획 작성 전
추적 Issue: #13

## 1. 문제 정의

Portfolio Maker의 현재 evidence pipeline은 승인된 source, snapshot, GitHub activity를 안전하게 추적하고 선택할 수 있다. 그러나 현재 runtime은 local source 하나를 `local:{source_id}` project로 만들고, public GitHub activity는 repository 단위로, private GitHub activity는 하나의 generic group으로 묶는다.

테스터 실행에서 이 규칙은 승인된 local evidence 486개를 486개 local project로, public GitHub evidence 100개를 5개 repository group으로, private GitHub evidence 125개를 하나의 generic group으로 보였다. 이것은 portfolio project가 아니라 origin별 technical grouping이다.

이 설계는 Portfolio Maker가 목표로 하는 “보험 RAG 챗봇”, “PlayMCP 공모전 준비” 같은 목적·기간·산출물·여러 근거를 가진 큰 작업 단위를 표현하지 못한다.

## 2. 결정

### 2.1 프로젝트의 의미

이후 `portfolio project`는 다음을 모두 만족하는 사용자가 확정한 작업 단위다.

- 사람이 이해할 수 있는 제목과 목적을 가진다.
- 하나 이상의 승인된 evidence와 연결된다.
- 관련 local file, GitHub repository, GitHub activity를 여러 개 가질 수 있다.
- overview와 주요 근거가 연결되며, overview는 evidence가 말하는 범위를 넘지 않는다.
- artifact에 포함되기 전에 사용자가 승인한다.

source, snapshot, GitHub activity, evidence, claim은 project가 아니라 project를 뒷받침할 수 있는 근거다. local file 하나, repository 하나, activity 하나는 자동으로 portfolio project가 되지 않는다.

### 2.2 Codex의 역할

Codex는 portfolio-maker skill workflow 안에서 approval과 masking을 통과한 review bundle을 분석한다. Codex는 다음을 수행한다.

- 내용상 연관된 local evidence와 GitHub activity를 cluster로 제안한다.
- cluster별 후보 제목, 짧은 overview, grouping rationale, 연결 evidence ID를 작성한다.
- 같은 프로젝트의 여러 파일·repository·activity를 하나의 후보로 합치거나, 큰 repository 안의 서로 다른 작업을 나눌 수 있다.
- 의미 있는 연결을 찾지 못한 evidence는 `unassigned`로 남긴다.

Codex의 후보는 결정이 아니다. Codex는 근거에 없는 역할·성과·기술·기간을 만들지 않으며, 불확실하면 overview를 축소하거나 후보를 만들지 않는다.

Portfolio Maker CLI는 외부 LLM API를 호출하거나 token을 저장하지 않는다. Codex app의 agent가 안전한 review bundle을 읽고 candidate review 파일을 쓰는 사용자 통제 workflow다.

### 2.3 사람의 역할

사용자는 candidate review에서 다음을 한다.

- 후보를 approve 또는 reject한다.
- 후보의 제목과 overview를 수정한다.
- 후보를 merge하거나 split한다.
- evidence를 다른 project로 reassign하거나 unassigned로 되돌린다.
- 필요하면 후보가 없어도 project를 직접 만든다.

approval 이후에만 portfolio project가 materialize된다. portfolio artifact는 approved project만 표시하며, approved project가 없으면 정직한 empty state를 보여 준다.

## 3. 설계 범위

### 3.1 안전한 review bundle

새 CLI/application 단계는 current source approval과 artifact policy를 재검증하고, Codex가 읽을 `project-review-input.json`을 만든다.

bundle은 다음 safe field만 포함한다.

- evidence ID와 stable ID
- origin type(local/public GitHub/private GitHub)
- safe source label, masked evidence excerpt, activity type, time, public repository label
- private activity의 URL 없는 safe label
- 현재 delivery scope와 policy hash

bundle에는 raw absolute path, `file://` URI, snapshot path, private GitHub URL, credential, token, database path를 넣지 않는다. current approval 또는 artifact policy에서 제외된 evidence도 넣지 않는다.

### 3.2 Codex candidate review

Codex는 bundle에서 다음 두 review artifact를 만든다.

~~~text
.portfolio-maker/reviews/project-candidates.json
.portfolio-maker/reviews/project-candidates.md
~~~

candidate JSON schema의 각 항목은 다음을 가진다.

~~~json
{
  "id": "candidate-insurance-rag",
  "status": "candidate",
  "title": "보험 RAG 챗봇",
  "overview": "근거가 보여 주는 범위 안에서 작성한 검토용 개요",
  "grouping_rationale": "왜 이 evidence들이 하나의 작업으로 보이는지",
  "evidence_ids": [101, 102, 205],
  "confidence": "medium",
  "review_required": true
}
~~~

`evidence_ids`는 bundle에 있던 ID만 참조해야 하며 중복될 수 없다. Codex가 grouping을 제안하는 데 사용할 수 있는 신호는 safe text의 의미적 유사성, public repository metadata, activity 유형/시간, 사용자 승인된 local evidence의 safe label이다. 폴더명·repository·파일 하나만으로 candidate를 자동 생성하는 규칙은 사용하지 않는다.

`project-candidates.md`는 후보별 evidence ID, overview, rationale, confidence와 unassigned evidence 개수만 보여 준다. raw locator나 private URL은 표시하지 않는다.

### 3.3 사용자 승인 파일

사용자는 candidate 결과를 검토해 다음 파일을 수정하거나, skill workflow를 통해 동일 구조를 만들게 한다.

~~~text
.portfolio-maker/reviews/project-approval.json
~~~

~~~json
{
  "version": 1,
  "projects": [
    {
      "id": "insurance-rag-chatbot",
      "title": "보험 RAG 챗봇",
      "overview": "사용자가 검토·승인한 근거 기반 개요",
      "evidence_ids": [101, 102, 205],
      "status": "approved"
    }
  ],
  "rejected_candidate_ids": ["candidate-unrelated-notes"],
  "unassigned_evidence_ids": [301]
}
~~~

MVP에서는 approved project 간 evidence ID 중복을 허용하지 않는다. 같은 evidence가 여러 작업에 문맥상 필요하면 user는 더 작은 evidence record를 연결하거나 해당 evidence를 unassigned로 남긴다. 이는 한 근거가 여러 project에 중복 노출되는 것을 방지한다.

approval validation은 다음을 보장한다.

- project ID와 title은 비어 있지 않고 중복되지 않는다.
- overview, title, evidence ID는 candidate input과 current selection의 안전 규칙을 통과한다.
- project는 적어도 하나의 evidence를 가진다.
- approved/rejected/unassigned evidence 상태는 서로 충돌하지 않는다.
- revoked, stale, unknown, policy-excluded evidence는 승인할 수 없다.
- candidate status는 artifact 입력이 될 수 없고 `approved`만 가능하다.

### 3.4 SQLite semantic project model

현재 `projects` 테이블은 파일/저장소 기반 technical grouping을 포함하므로 portfolio semantic model로 재사용하지 않는다. additive migration으로 다음을 추가한다.

| 모델 | 책임 |
|---|---|
| `portfolio_projects` | 사용자가 승인한 portfolio project의 stable ID, title, overview, 상태, 생성/갱신 시각 |
| `portfolio_project_evidence` | approved portfolio project와 evidence item의 연결 및 support level |
| `artifacts.input_manifest` 확장 | project ID, linked evidence ID, project approval hash, candidate input hash, delivery scope |

candidate proposal은 review artifact이며 database truth가 아니다. `project-approval.json` validation을 통과한 approved project만 database에 materialize한다. 기존 `projects`, `career_claims`, `claim_evidence`, evidence pool, 이전 artifact는 삭제하거나 자동 변환하지 않는다.

### 3.5 생성물 규칙

- master profile은 evidence inventory와 approved project 요약을 분리해 보여 준다. unassigned evidence는 project로 세지 않는다.
- Markdown draft는 approved project 하나당 하나의 section만 만들고, 해당 project에 연결된 evidence 목록과 review-required overview를 표시한다.
- `portfolio-public.json`과 `portfolio.html`은 approved portfolio project만 `projects` 배열에 넣는다.
- Project ID는 origin-based `local:{id}` 또는 `github:{repo}`가 아니라 approval file의 stable project ID를 쓴다.
- HTML filter, detail panel, timeline은 approved project 내부 evidence에만 작동한다.
- approved project가 0개면 “근거는 수집됐지만 아직 portfolio project가 승인되지 않았다”는 empty state를 표시한다.
- `restricted`와 `open_public` selection은 먼저 evidence를 결정하고, 그 결과 중 approved project에 연결된 evidence만 output에 넣는다. open public의 origin 제한은 그대로 유지한다.

## 4. 데이터 흐름

~~~text
discovery
  → source approval / ingest / evidence pool
  → artifact evidence selection
  → safe project-review input bundle
  → Codex semantic analysis
  → candidate JSON + candidate Markdown
  → user approve / reject / merge / split / reassign
  → project-approval validation + portfolio_projects materialization
  → master profile / draft / manifest / interactive HTML
~~~

Codex 분석 단계는 evidence selection 뒤, artifact generation 전이다. 따라서 Codex는 policy 밖의 원본이나 private locator를 새로 읽을 수 없고, candidate가 acceptance gate를 우회할 수 없다.

## 5. migration과 compatibility

- `project-approval.json`이 없는 기존 workspace는 legacy technical project grouping을 approved portfolio project로 간주하지 않는다.
- 새 project-composition 경로의 draft/manifest/HTML은 approved project가 없으면 zero-project empty state를 만든다.
- 기존 evidence and artifact record는 읽을 수 있어야 하며 source/snapshot을 재수집하거나 삭제하지 않는다.
- 사용자가 project review를 시작하기 전에는 기존 #12 evidence selection behavior를 바꾸지 않는다.
- legacy artifact는 historical output으로 남지만, 새 artifact가 그것을 재사용하거나 public deployment에 넘기지 않는다.

## 6. 보안과 품질

- Codex input/output은 source approval, artifact policy, secret masking, safe label validation을 모두 재사용한다.
- candidate overview/rationale은 evidence ID와 함께 review-required로 유지하며, grounded text를 검증할 수 없는 candidate는 materialize하지 않는다.
- local path, private repository name/URL, token, credential, secret-shaped text가 candidate bundle, candidate report, approval file, manifest, HTML에 나타나면 controlled error로 실패한다.
- Codex가 제안한 candidate 수에는 상한을 두지 않지만, single-file/single-activity를 자동 candidate로 만드는 heuristic은 금지한다.
- 실제 public hosting은 구현 범위 밖이며 existing delivery scope gate를 유지한다.

## 7. 완료 기준

- 486개의 local evidence가 있다 해도 user가 승인한 2개의 project만 artifact에 2개로 나타날 수 있다.
- 하나의 approved project는 여러 local evidence와 GitHub activity를 함께 가질 수 있다.
- Codex candidate는 grouped evidence IDs, grounded overview, rationale, confidence를 가진다.
- 후보를 approve하지 않으면 project artifact가 생성되지 않는다.
- merge, split, reassign, reject, unassigned validation이 deterministic하게 동작한다.
- existing workspace migration, focused Python tests, full pytest, Vite build, static HTML validation, browser interaction verification이 통과한다.
- README, skill, Phase spec, Issue #13, implementation plan이 현재 runtime과 future behavior를 구분한다.
