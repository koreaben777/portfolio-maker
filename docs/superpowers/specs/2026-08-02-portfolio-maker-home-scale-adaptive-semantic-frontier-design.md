<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Portfolio Maker 전수 로컬 인덱스·적응형 Codex 의미 프런티어 설계

> 2026-09-24 추가 계약: [목적 중심 개정](2026-09-24-portfolio-outcome-recovery-design.md)을 함께 적용한다. Task 10은 새 R1 사이클의 receipt 검증으로 닫고, Tasks 11/12에 자원 gate E, Task 12 뒤·Task 13 전에 포트폴리오 gate P를 적용한다. Tasks 13–16 및 기존 full-home/release gate H는 생략하지 않는다. 상세 protocol·budget·승인 계약과 과거 판정은 유지한다.

날짜: 2026-08-02
상태: 사용자 승인 설계, 구현 전 권위 계약
대상: 0.2.2 Cycle 15 Task 15
기준 커밋: `0c18d600dae091c717ef22d9feba7c1f74d36459`

> [!IMPORTANT]
> 이 문서는 기존 `codex-semantic-v2`의 전수 구조 보존 원칙을 유지하면서,
> full-home 규모에서 의미 실행을 실제로 완료·중단·재개할 수 있도록 저장 구조와
> Codex 작업 선택 방식을 `codex-semantic-v3`로 교체하는 구현 계약이다. 이 문서가
> 승인된 뒤 별도의 구현 계획을 작성한다. 문서 작성 시점에는 코드, CLI, skill,
> plugin, README 또는 실제 pilot 상태가 변경되지 않았다.

## 1. 결정 요약

Task 15는 다음 세 계층을 분리한다.

1. **전수 구조 카탈로그**는 승인 범위의 모든 허용 노드와 parent/child 관계를
   저장한다. 전역 파일 수 상한이나 top-K로 구조를 생략하지 않는다.
2. **전수 로컬 기본 분석**은 모든 허용 노드에 대해 결정론적·bounded 분석을
   시도하고 `complete`, `sampled`, `unsupported`, `unreadable`, `failed` 중 하나의
   명시적 coverage를 남긴다. `sampled`는 전수 분석 완료를 뜻하지 않는다.
3. **적응형 Codex 의미 프런티어**는 로컬 신호로 프로젝트 경계 판단에 필요한
   노드만 단계적으로 확장한다. 예산 밖의 노드는 삭제·제외·완료 처리하지 않고
   이유, digest, resume cursor와 함께 `deferred`로 보존한다.

저장은 단일 거대 JSON 재파싱 대신 다음 구조를 사용한다.

- 봉인된 immutable indexed catalog
- append-only private payload packs
- transactional SQLite lifecycle ledger
- 모든 구성 요소를 결합하는 canonical manifest
- 한 번에 한 bounded unit만 노출하는 reusable exchange slot

기본 프런티어가 경계를 찾은 뒤 사용자가 선택한 프로젝트만 별도의 승인과 예산으로
deepening한다. source 승인, Codex 의미 실행 승인, 프로젝트 포함, 근거 포함,
산출물 전달 및 공개 배포 권한은 서로 독립적이다.

## 2. 문제와 직접 근거

2026-08-02 fresh full-home prepare는 다음 규모를 만들었다.

| 항목 | 관찰값 |
|---|---:|
| 구조 노드 | 1,364,721 |
| 분석 대상 | 343,470 |
| 의미 work unit | 346,750 |
| managed bytes | 2,434,204,560 |
| prepare 시간 | 2,395.79초 |
| 하위 directory timeout | 0 |
| unreadable | 51 |

prepare 자체는 성공했지만 직후 `semantic-index-status`가 safe failure로 종료됐다.
현재 v2 loader는 약 2.335 GB capsule 원문, 136만 개 decoded capsule 객체,
약 99 MB work plan과 34만 개 unit/mapping을 한 프로세스에 함께 유지한다. 또한
한 target을 처리할 때마다 거대 상태를 다시 읽는 inline 흐름은 최소 1,040,250회
CLI 호출과 1,387,000회 full-state load를 요구할 수 있다. 이는 경로 제외나 메모리
상한 증가로 해결할 수 있는 결함이 아니라 저장·실행 단위의 구조적 결함이다.

따라서 기존 pilot과 2.43 GB staging은 보존 증거일 뿐 Task 15 acceptance 실행에
재사용하지 않는다. Task 15 구현과 독립 review PASS 전에는 fresh pilot을 시작하지
않는다.

### 2.1 검토한 대안

| 대안 | 장점 | 기각 또는 채택 이유 |
|---|---|---|
| 거대 manifest/capsule 전체 재로딩 | 현재 v2와 가장 유사 | 상태 크기만큼 매 command가 느려지고 full-home에서 memory failure가 재현되어 기각 |
| unit별 JSON 파일과 shard status scan | 구현이 단순하고 부분 읽기 가능 | 수십만~백만 개 작은 파일, directory scan, 원자 receipt/aggregate 유지 비용 때문에 기각 |
| immutable catalog + packs + lifecycle ledger | 구조 truth, 큰 payload, mutable 실행 상태를 분리 | bounded query, streaming verify, atomic lifecycle을 함께 만족해 채택 |

Codex 선택도 세 대안을 비교했다. 모든 노드의 전수 Codex 분석은 정확성 표현은 단순하지만
시간·호출량이 비현실적이다. 전역 top-K는 싸지만 놓친 node를 non-project처럼 숨긴다.
따라서 **전수 구조 + 전수 로컬 baseline + 적응형 frontier + on-demand deepening**을
채택한다. budget 밖 node는 반드시 deferred로 남기므로 비용 한계와 구조 전수성을 함께
표현할 수 있다.

## 3. 목표, 비목표, 불변 조건

### 3.1 목표

- 홈 디렉터리 전체처럼 백만 단위 노드에서도 구조 누락 없이 prepare한다.
- 모든 허용 노드에 bounded 로컬 기본 분석 상태를 남긴다.
- status, next, validate를 전체 상태 크기와 무관한 bounded operation으로 만든다.
- Codex가 parent/child 맥락과 대표 근거를 따라 프로젝트 경계를 적응적으로 찾는다.
- 예산 소진, 사용자 중지, process crash 뒤에도 승인된 receipt 다음부터 재개한다.
- 경계 판단 완료와 전체 의미 분석 완료를 혼동하지 않는다.
- 선택 프로젝트에만 별도 deepening을 실행한다.
- v2 active revision을 깨지 않고 명시적 staging migration을 제공한다.
- locator, private URL, credential과 artifact 권한 경계를 유지한다.

### 3.2 비목표

- 모든 파일 전체 본문을 Codex에 보내는 것
- 모든 노드를 Codex가 한 번씩 읽어야만 성공으로 보는 것
- 임의의 전역 top-K, 최대 파일 수 또는 암묵적 directory 제외
- local baseline의 prefix fingerprint를 전체 content identity로 간주하는 것
- 같은 사용자가 모든 workspace 파일을 일관되게 다시 쓰는 공격을 암호학적으로 방어하는 것
- Google Drive, 범용 지식 그래프, vector database 또는 외부 LLM API를 이번 작업에 추가하는 것
- project approval, evidence approval, artifact 생성, Sites 배포, Git push를 자동 승인하는 것
- 보존 pilot을 삭제·정리·자동 migration하는 것

### 3.3 불변 조건

1. 승인 범위의 모든 허용 item은 structural catalog에 존재하거나 구체적인
   terminal error record를 가진다.
2. excluded subtree는 구조, 요약, 프런티어, 후보, 산출물 어디에도 포함되지 않는다.
3. budget exhaustion은 `paused_budget`이지 completion이나 exclusion이 아니다.
4. Codex output은 topology를 추가·삭제·대체할 수 없다.
5. partial, sampled, unreadable 또는 deferred 상태를 `complete`로 승격하지 않는다.
6. 현재 source policy hash가 달라지면 실행·finalize·apply는 fail closed한다.
7. 새 revision이 완전히 검증·활성화되기 전까지 기존 active revision을 유지한다.
8. 원본 파일은 복사·수정·이동·삭제하지 않는다.
9. private repository 이름은 사용자가 승인한 restricted display text로 사용할 수
   있지만 private URL, raw locator, token, credential은 산출물에 넣지 않는다.
10. `public` manifest/HTML이라는 이름은 배포 권한을 뜻하지 않는다. local, public
    GitHub, explicit-approved private GitHub의 동일 evidence policy를 적용한다.

## 4. 용어와 권한 경계

| 용어 | 정의 |
|---|---|
| structural catalog | 허용 노드, hierarchy, metadata, 구조 digest를 보존한 전수 인덱스 |
| local baseline | LLM 없이 각 노드에 수행한 bounded 결정론적 분석과 coverage |
| frontier | Codex 경계 판단을 위해 현재 승인 예산 안에서 선택된 작업 집합 |
| frontier unit | 하나의 원자적 Codex 입력·출력·receipt 단위 |
| representative | 구조 순서가 아니라 역할·불확실성·독립성 신호로 고른 근거 노드 |
| deferred | 아직 판단에 쓰지 않았으며 이유와 재개 정보를 보존한 상태 |
| boundary-critical coverage | 특정 candidate 경계의 확정에 필요한 parent, child, 반대 신호의 검증 범위 |
| deepening | 승인된 한 프로젝트의 설명·근거 품질을 높이는 별도 의미 실행 |
| sealed | byte length와 record root가 manifest에 고정되어 더 쓸 수 없는 상태 |
| receipt | 특정 revision, unit, input, output, validator 결과를 결합한 원자 기록 |

권한은 다음 순서로 자동 승격되지 않는다.

```text
source scope approval
  != Codex frontier execution approval
  != project inclusion decision
  != evidence/artifact selection
  != restricted delivery
  != public deployment or Git operation
```

- `source-approval.json`은 탐색 root와 제외, GitHub private opt-in/allowlist를 결정한다.
- source approval은 허용된 local item의 구조 수집과 local baseline을 허용한다.
- Codex frontier/deepening은 estimate와 binding을 본 뒤 별도 명시 승인을 받아야 한다.
- automatic mode는 충분히 검증된 high/medium candidate의 project 포함 상태만 정한다.
- 사용자의 manual include/exclude/split/merge/reassign은 자동 추론보다 우선한다.
- project inclusion은 artifact나 deployment permission을 만들지 않는다.

### 4.1 정보 계층

| 계층 | 저장·사용 범위 |
|---|---|
| private locator/structure | raw locator, private URL, absolute path, full topology; application 재검증 전용 |
| private masked baseline | safe metadata, sampled payload, coverage, local feature; selector 입력 |
| private frontier rationale | score, 대표 선택 이유, Codex rationale/counter-signal; 실행·감사 전용 |
| safe project-review projection | opaque node/evidence ID와 검증된 title/overview 후보; 사용자 검토 전용 |
| restricted/public artifact projection | 현재 evidence/artifact policy를 다시 적용한 산출물 전용 |

locator-free라는 이유만으로 public-safe라고 간주하지 않는다. filename, relative hierarchy,
score와 프로젝트 존재 자체도 민감할 수 있으므로 일반 status/stdout에는 safe aggregate와
opaque revision/run ID만 낸다. public/restricted artifact는 cached frontier projection을
그대로 복사하지 않고 현재 policy와 materialized project state를 다시 계산한다.

## 5. 전체 아키텍처

```text
approved source policy
  -> streaming structural crawl
  -> exhaustive bounded local baseline
  -> sealed v3 catalog + private packs + canonical manifest
  -> deterministic frontier proposal + exact estimate
  -> explicit execution approval receipt
  -> bounded next / Codex / validate / receipt loop
  -> adaptive accept | expand | stop | defer decisions
  -> streaming full verification and finalization
  -> atomic active revision apply
  -> project curation/review
  -> optional separately-approved project deepening
  -> evidence selection -> portfolio artifacts
```

Python application은 policy, storage, hashing, selector, validation, state transition의
결정론적 권위를 가진다. Codex skill은 locator-free bounded input을 해석해 허용된
action과 grounded semantic proposal만 반환한다. Codex가 SQLite나 pack을 직접
수정하지 않는다.

### 5.1 revision 디렉터리

v3 staging은 다음 논리 객체를 가진다. 실제 파일명은 구현 계획에서 이 계약을 그대로
매핑하되, 한 객체를 여러 shard로 나누는 것은 허용한다.

```text
.portfolio-maker/raw/semantic-index/<revision-id>/
  manifest.v3.json
  catalog.sqlite
  packs/
    content.pm-pack
    structural.pm-pack
    locators.pm-pack
  runs/<run-id>/
    results.pm-pack
    exchange/
      input.json
      output.json
  private-reviews/

.portfolio-maker/portfolio.db
  semantic v3 staging binding + lifecycle ledger tables
```

- `catalog.sqlite`는 seal 뒤 read-only인 검색 카탈로그다.
- `content.pm-pack`은 prepare 시점의 masked local-baseline payload를 보존한다. 원본
  파일 사본이 아니며, 이후 선택되는 representative 원문을 pack에 append하지 않는다.
- `structural.pm-pack`은 full child list와 topology payload를 보존한다.
- `locators.pm-pack`은 raw local locator/private source locator를 분리해 보존한다.
- 기존 workspace `portfolio.db`의 additive lifecycle table은 유일한 mutable
  **transactional authority**다. queue, lease, receipt, aggregate, checkpoint, pause
  state와 current staging binding을 같은 SQLite transaction으로 관리한다.
- `results.pm-pack`은 한 run의 validated semantic output canonical bytes를 append-only로
  보존하는 durable authority다. prepare pack과 달리 run finalize 때 seal한다.
- 각 run의 `exchange`는 매 unit마다 원자 교체되는 단일 bounded slot이다. unit별
  exchange 파일을 누적 생성하지 않는다.
- 준비·pause된 run은 여러 개 보존할 수 있지만 workspace execution mutex와 DB unique
  invariant로 `running` 또는 active lease를 가진 run은 정확히 하나만 허용한다.

### 5.2 `pm-pack-v1`

pack은 append-only length-delimited record stream이다. 각 record는 다음을 가진다.

```text
magic/version | record length | canonical JSON payload | payload SHA-256
```

catalog은 `pack_kind`, `offset`, `length`, `record_sha256`를 저장한다. 작성 중에는
임시 이름을 사용하고 fsync, record count/byte length/root 검증 뒤 원자 rename으로
seal한다. seal 이후 append나 in-place rewrite를 거부한다.

`pack_root`는 순서가 고정된 각 record digest와 record length를 domain-separated
SHA-256 accumulator에 streaming 입력해 계산한다. 이 정의는 구현·validator가
공유하며 단순 파일 mtime/size를 integrity로 사용하지 않는다.

catalog은 WAL을 본 파일로 checkpoint하고 WAL/SHM이 남지 않은 상태에서 integrity
check, `VACUUM`, fsync를 거쳐 seal한다. `catalog_file_sha256`은 seal된 SQLite 파일
전체 bytes의 SHA-256이고, `catalog_logical_root`는 primary-key 순서의 권위 row를
canonical encode해 계산한 streaming root다. manifest는 둘 다 bind한다.

runtime `results.pm-pack`은 같은 record framing을 사용하되 receipt commit 전까지
append-only open 상태다. validate는 canonical output record를 append하고 fsync한 뒤,
DB transaction에서 offset, length, record digest, committed pack length와 누적 result
root를 receipt/checkpoint에 기록한다. crash가 append 후 DB commit 전에 발생하면 마지막
committed pack length 밖의 tail은 권위가 아니다. recovery는 committed prefix root를
검증하고 그 tail을 무시하거나 잘라낸 뒤 재개한다. DB commit 뒤 exchange cleanup 전에
crash하면 existing receipt/result reference를 idempotently 재생한다. in-place record
rewrite는 언제나 금지한다.

### 5.3 immutable catalog

catalog은 최소한 다음 논리 table과 index를 제공한다.

| table | 핵심 내용 |
|---|---|
| `nodes` | node ID, kind, parent, safe label, relative hierarchy, source ID |
| `children` | full ordered parent/child topology와 structural payload reference |
| `local_baselines` | analyzer version, coverage, roles/topics, inspected bytes, 오류 상태 |
| `payload_refs` | pack kind/offset/length/digest |
| `content_identities` | metadata identity, prefix/sample digest, 선택 시 full content digest |
| `evidence_links` | 승인된 기존 evidence ID 연결 |
| `frontier_features` | 결정론적 project-likeness, ambiguity, independent-child 신호 |

필수 index는 `node_id`, `(parent_node_id, node_id)`, `source_id`, coverage,
frontier feature와 stable traversal order를 포함한다. catalog seal 후 application은
query만 수행하며 lifecycle counter를 catalog에 쓰지 않는다.

### 5.4 transactional lifecycle ledger

`portfolio.db`의 ledger는 최소한 다음 상태를 transaction으로 관리한다. 대용량 node와
payload를 이 DB에 복제하지 않고, 한 실행의 bounded frontier unit과 aggregate만 둔다.

| table | 책임 |
|---|---|
| `revision_binding` | exact manifest hash, catalog root, policy hash, lifecycle schema |
| `frontier_units` | unit ID, phase, dependency, priority, terminal/deferred state |
| `leases` | 현재 unit, input hash, owner nonce, 발급·만료 상태 |
| `receipts` | validated output hash, result-pack offset/length/hash, action, validator version, committed sequence |
| `aggregates` | 상태·phase별 count, bytes, elapsed, coverage와 next-ready count |
| `checkpoints` | receipt sequence, result-pack root/length, queue root, aggregate root, resume cursor |
| `approvals` | 실행 종류, plan/estimate/budget binding과 명시 승인 기록 |
| `deepening_runs` | project별 별도 revision, authority, budget, cancel/resume 상태 |

`status`는 aggregate row만 읽는다. `next`는 ready-state index에서 한 unit을 lease하고,
`validate`는 exact lease/input/output을 확인한 뒤 receipt, queue, aggregate,
checkpoint를 하나의 transaction에 반영한다. mutable counter 하나만 보고 finalize하지
않고 receipt sequence/root와 catalog/manifest binding을 streaming 재검증한다.

main DB의 staging binding은 exact revision ID, manifest hash, catalog file/logical roots,
lifecycle genesis root와 current checkpoint root를 가진다. manifest directory만 남고 이
binding transaction이 없는 crash residue는 current staging이 아니다. receipt와 queue,
aggregate, current checkpoint root가 같은 DB에 있으므로 cross-file commit을 correctness
조건으로 만들지 않는다.

### 5.5 canonical manifest v3

manifest는 canonical JSON으로 다음을 결합한다.

- `schema_version = codex-semantic-v3`
- revision/source ID, 생성 시각, tool/analyzer/selector/validator version
- source approval policy hash와 approved root identity
- structural/local-baseline node count 및 coverage aggregates
- 각 pack의 format, byte length, record count, record root
- catalog schema version, byte length, catalog root
- locator/structural/content root
- frontier algorithm ID, deterministic parameters, traversal order
- proposed phase별 unit/byte/time estimate와 기본 budget
- exchange limit와 per-unit limit
- run result-pack format, output limit와 initial empty root
- lifecycle schema version와 initial checkpoint root
- v2 migration이면 `derived_from`과 migration report digest
- manifest self-hash

manifest가 bind하지 않은 파일은 revision truth가 아니다. lifecycle의 mutable root는
manifest에 직접 덮어쓰지 않고, manifest-bound genesis root에서 이어지는 checkpoint
chain으로 검증한다.

## 6. Streaming prepare와 전수 로컬 기본 분석

### 6.1 event/sink 계약

탐색기는 전체 node list를 메모리에 만들지 않고 다음 event를 storage sink로 보낸다.

```text
node_seen(node metadata)
file_analyzed(node, baseline, payload refs)
directory_closed(node, child aggregates, baseline)
source_closed(source aggregates)
```

writer는 bounded batch로 catalog와 pack에 반영한다. directory summary에 필요한
상태는 현재 depth와 bounded batch만 유지하며 목표 working memory는 `O(depth + batch)`다.
모든 허용 node는 stable ID 순서와 full topology를 보존한다.

prepare publication 순서는 다음과 같다.

1. 새 private temporary revision directory 생성
2. streaming crawl/local baseline과 append-only pack 작성
3. catalog index 구축 및 integrity scan
4. deterministic frontier proposal과 exact estimate 작성
5. pack/catalog seal
6. manifest canonicalize 및 self-hash
7. 전체 preflight 후 staging directory를 원자 publish
8. `portfolio.db` 단일 transaction에서 staging binding과 lifecycle genesis 생성

어느 단계에서든 실패하면 기존 active/staging을 덮어쓰지 않는다. 실패한 temporary
state는 안전한 private failure review가 참조하는 경우에만 보존하고, 자동 삭제하지
않는다. 7 이후 8 이전 crash로 생긴 unbound directory는 신뢰하거나 자동 재개하지 않는다.

### 6.2 구조 전수성과 제외 정책

- hidden directory, 큰 directory, high fan-out은 단독 제외 사유가 아니다.
- `Library`, `Containers` 또는 다른 common path를 암묵적으로 제외하지 않는다.
- 사용자 승인 `excluded_directories`, `excluded_file_patterns`, GitHub allow/exclude만
  pruning 권위를 가진다.
- symlink와 alias는 승인 root 이탈·cycle을 방지하되, 발생 사실과 terminal reason을
  기록한다.
- directory timeout/watchdog은 하위 실패를 terminal status로 격리한다. root timeout,
  worker protocol failure, 전체 timeout budget 소진은 revision publish 전에 fail closed한다.
- 파일 수, directory 수, depth에 전역 correctness cap을 두지 않는다.

### 6.3 local baseline coverage

모든 허용 file/directory는 local analyzer를 거쳐 다음 coverage 중 하나를 가진다.

| coverage | 의미 |
|---|---|
| `complete` | 해당 analyzer 계약이 요구한 전체 bounded 입력을 성공 처리 |
| `sampled` | 파일 일부만 읽었고 bytes/ranges와 생략 이유를 기록 |
| `unsupported` | 형식을 구조·metadata 이상 해석할 analyzer가 없음 |
| `unreadable` | 권한, I/O, timeout 등으로 읽지 못함 |
| `failed` | analyzer가 실행됐지만 controlled error로 실패 |

`complete`는 원본 파일 전체 byte를 읽었다는 뜻이 아니라 versioned analyzer contract를
완료했다는 뜻이다. analyzer가 prefix/section만 읽는다면 반드시 `sampled`로 기록한다.
현재와 같은 최대 128 KiB prefix digest는 sample identity일 뿐 full content identity가
아니다.

baseline은 가능한 경우 다음을 만든다.

- code: module/entrypoint/build/test/deploy 역할 신호
- document: title, purpose, decision/result heading과 문서 역할
- config: runtime/build/deployment/tool 신호
- generated/binary/image: format, size, safe metadata와 지원 상태
- directory: direct child coverage aggregate, 역할 조합, 독립 lifecycle 신호

secret masking은 payload write 전에 수행한다. raw locator와 absolute path는 locator
pack에만 저장한다. safe label과 relative hierarchy도 private review 입력에만 쓰고,
artifact로 자동 승격하지 않는다.

### 6.4 content identity와 대표 파일 검증

metadata, prefix digest, sampled digest를 cache key의 full-content 부분으로 쓰지 않는다.
프런티어 또는 deepening에 실제로 선택되어 Codex 근거나 candidate activation에 영향을
주는 regular file은 원본을 streaming 재검증해 full content SHA-256을 얻는다. application은
symlink-follow를 금지한 동일한 open file descriptor에서 pre-`fstat`, full hash와 bounded
slice 수집, post-`fstat`을 한 pass로 수행한다. device, inode, size, mtime-ns 또는 읽은
bytes가 중간에 바뀌면 input을 publish하지 않고 stale로 중단한다. hash 뒤 locator를
다시 열어 slice를 읽지 않는다.

원본이 prepare 이후 바뀌었거나 source policy가 달라졌으면 해당 unit과 downstream
candidate를 stale 처리하고 재승인 가능한 새 plan을 요구한다. unreadable/unsupported
노드는 그 상태 자체가 반대 또는 불확실성 신호가 될 수 있지만 내용은 추정하지 않는다.

## 7. 적응형 계층 의미 프런티어

### 7.1 selector 원칙

프런티어 selector는 결정론적 로컬 신호로 시작하며 전역 top-K나 파일 개수 상한을
사용하지 않는다. budget은 한 실행에서 **지금 처리할 범위**를 제한할 뿐, 나머지 노드를
삭제하거나 비프로젝트로 판정하지 않는다.

seed는 다음 신호의 조합으로 만든다.

- README/manifest/entrypoint/test/deploy 등 상호 보완 역할 조합
- 목적·제품·공모전·연구·운영 lifecycle 신호
- parent summary와 child summary 사이의 coherence 또는 ambiguity
- 독립 build/release/repository/deployment를 가진 child
- 기존 승인 evidence와 연결된 node
- sampled/unreadable/충돌로 경계 불확실성이 큰 node
- 사용자가 명시적으로 선택한 node/project

각 seed는 ancestor closure를 포함해 상위 맥락 없이 child만 판정하지 않는다. 동일
parent 아래 node는 stable ID와 versioned feature로 순서를 정해 같은 입력에서 같은
plan과 estimate를 만든다.

스케줄링은 source/root 사이 breadth-before-depth round-robin을 기본으로 하고, 하나의
대형 subtree나 고점수 decoy가 orientation/expansion 예산을 독점하지 못하게 source와
subtree별 fairness lane을 둔다. audit 64 units는 accepted boundary 주변, high-signal
deferred, coverage/error strata에 versioned 비율로 배분한다. 이 lane은 실행 순서만 정하며
eligibility를 제거하는 cap이 아니다. 예산에 들어오지 못한 node는 stable deferred digest와
on-demand 경로를 유지한다.

### 7.2 representative 선택

대표 근거는 다음 우선순위를 조합한다.

1. 역할 다양성: entry, build, test, deploy, docs, data/output
2. 독립성: 별도 lifecycle·release·entrypoint를 보이는 child
3. 상보성: 같은 역할 파일 반복보다 서로 다른 boundary signal
4. 불확실성: parent/child coherence를 뒤집을 수 있는 counter-signal
5. 근거 연결: 현재 승인 evidence와 연결된 node
6. 변경성: cache 이후 변경되었거나 sampled/failed 상태인 node
7. stable node ID tie-break

작은 directory는 하나의 bounded unit으로 묶을 수 있고, 파일은 기본 Codex target이
아니다. directory/경계 단위 target에 검증된 대표 payload를 붙인다.

기존 `direct-child-attestation-v1`의 정렬된 앞 64개 ID는 full topology의 count/digest를
검증하기 위한 표본일 뿐 의미 대표 표본이 아니다. v3 selector는 이를 representative
selection으로 재사용하지 않는다. full child list는 private structural pack이 소유하고,
Codex output은 topology field를 반환하지 않는다.

### 7.3 Codex action 계약

Codex는 각 boundary target에 대해 다음 네 action 중 하나만 반환한다.

| action | 의미 |
|---|---|
| `accept_boundary` | 현재 node/cluster가 프로젝트 경계로 충분히 grounded됨 |
| `expand_children` | 특정 child/counter-signal을 더 봐야 경계를 판단할 수 있음 |
| `stop_non_project` | 현재 subtree가 프로젝트가 아니라는 근거가 충분함 |
| `defer_on_demand` | 현재 예산/권한/coverage로는 판단하지 않고 보존 |

모든 output은 input node ID와 representative full-content digest, 사용한 local signal,
rationale, counter-signal, confidence, requested next node를 참조한다. output이 input에
없는 locator, node, evidence 또는 topology를 만들면 validation 실패다.

`expand_children`는 selector가 검증한 child 후보만 queue에 추가한다. Codex가 무제한
fan-out을 요청할 수 없고, 한 unit의 dependency/representative fan-in과 한 expansion
page는 각각 최대 64개다. 이는 topology 또는 eligibility cap이 아니며 다음 page cursor가
남는다. budget에 들어오지 못한 child는 `deferred`로 남는다.

### 7.4 phase

기본 프런티어는 세 phase로 수행한다.

1. **orientation**: source/root와 강한 seed의 목적·큰 경계를 파악한다.
2. **expansion**: ambiguity, independent child, counter-signal을 따라 선택적으로 확장한다.
3. **audit**: accepted/stopped 경계 주변과 deferred high-signal node를 검토해 false
   completion을 방지한다.

phase budget을 남겼다는 이유로 불필요한 unit을 만들지 않는다. 반대로 phase budget을
다 썼다는 이유로 해당 phase를 완료 처리하지 않는다. convergence 조건과 budget 상태를
각각 기록한다.

### 7.5 경계와 medium 자동 포함

parent가 하위 전체의 공통 목적·산출물·lifecycle을 자연스럽게 설명하면 parent
project를 우선한다. child는 독립 제품, 공모전, 배포물, 고객/연구 목적, 자체 실행·release
lifecycle이 grounded될 때만 별도 project로 분리한다. cross-directory cluster는 명시적
연결과 counter-signal을 함께 기록한다.

automatic mode에서 `medium`까지 자동 포함할 수 있지만 다음 조건을 모두 만족해야 한다.

- boundary-critical parent/child/representative coverage가 완전하다.
- 모든 사용 representative가 full content identity에 bind되어 있다.
- material한 deferred, partial, sampled, unreadable 또는 failed counter-signal이 없다.
- conflicting boundary나 evidence ownership conflict가 없다.
- current policy, analyzer, selector, receipt root가 candidate와 일치한다.

조건 하나라도 충족하지 않으면 medium 자동 포함이 아니라 review/deferred 상태로 남긴다.
사용자는 자동 포함된 project를 언제든 가역적으로 exclude/re-include할 수 있다.

## 8. 완료 상태 모델

다음 상태는 독립적으로 계산·표시한다.

| 상태 | true 조건 |
|---|---|
| `structural_complete` | 모든 허용 item이 structural record 또는 terminal crawl error를 가짐 |
| `local_baseline_complete` | 모든 structural node가 terminal baseline coverage를 가짐 |
| `planned_receipts_complete` | 현재 승인 plan의 모든 생성 unit이 validated receipt 또는 명시적 terminal state를 가짐 |
| `frontier_converged` | 승인된 selector가 더 이상 boundary-critical expansion을 요구하지 않음 |
| `paused_budget` | 승인 budget을 소진해 deferred work와 resume cursor를 보존함 |
| `project_discovery_ready` | 구조·baseline이 완료되고 frontier가 수렴했으며 material blocker 없이 후보 생성 가능 |
| `deepening_complete` | 특정 project deepening plan의 검증된 receipt와 quality gate가 완료됨 |

`planned_receipts_complete`는 전체 catalog가 Codex 분석됐다는 뜻이 아니다.
`frontier_converged`와 `paused_budget`은 동시에 true일 수 없다. budget 소진 시 selector가
더 볼 필요가 없음을 이미 증명한 경우에만 마지막 receipt 이전에 convergence가 확정되어야
하며, 그렇지 않으면 `paused_budget`이다.

`project_discovery_ready`는 다음을 모두 요구한다.

- `structural_complete = true`
- `local_baseline_complete = true`
- `planned_receipts_complete = true`
- `frontier_converged = true`
- `paused_budget = false`
- manifest/catalog/checkpoint full verification PASS
- current source policy와 approval binding 일치
- accepted boundary에 material deferred/partial counter-signal 없음

이 상태도 evidence approval, artifact completion 또는 release readiness를 뜻하지 않는다.

lifecycle의 단일 `run_state`는
`prepared`, `awaiting_approval`, `running`, `paused_user`, `paused_budget`, `blocked`,
`converged`, `finalizing`, `finalized`, `failed` 중 하나다. 위 completion boolean은 이
운영 상태를 대신하지 않는다. `blocked`는 missing authority/input 또는 반복 가능한
환경 실패를 safe code로 가지며, unreadable node 하나를 전체 run blocker로 자동
승격하지 않는다.

## 9. 실행 승인과 예산

### 9.1 기본 프런티어 예산

| 범위 | unit | materialized input | wall time |
|---|---:|---:|---:|
| orientation | 64 | 16 MiB | 15분 |
| expansion | 384 | 96 MiB | 90분 |
| audit | 64 | 16 MiB | 15분 |
| **합계** | **512** | **128 MiB** | **7,200초** |

- checkpoint 간격: validated receipt 16개
- per-unit input hard limit: 262,144 bytes
- per-unit output hard limit: 262,144 bytes
- per-unit execution hard limit: 300초

어느 축(unit, bytes, wall time)이든 먼저 소진되면 새 lease를 발급하지 않고
`paused_budget`로 전환한다. 자동으로 budget을 늘리거나 다른 phase 잔여량을 숨겨서
재배정하지 않는다.

### 9.2 프로젝트 deepening 예산

선택 project마다 별도 승인 receipt와 다음 기본 budget을 사용한다.

- 256 units
- 64 MiB materialized input
- 3,600초
- checkpoint 16 units
- 동일한 per-unit input/output 262,144 bytes / 300초 hard limit

deepening은 active project ID, 현재 boundary digest, evidence policy, 목적을 bind한다.
project를 split/merge/exclude하거나 policy가 바뀌면 기존 deepening은 stale이며 자동으로
다른 project에 적용하지 않는다.

### 9.3 승인 receipt

prepare 뒤 skill은 다음 exact proposal을 사용자에게 보여 주고 명시 승인을 받는다.

- revision ID와 manifest hash
- source policy hash와 safe source label
- structural/local-baseline coverage aggregates
- frontier algorithm/version과 plan hash
- phase별 예상 unit, input bytes, wall time
- audit selector rule/version, sealed catalog root와 private-context 범위
- hard limits와 checkpoint 간격
- deferred semantics와 자동 budget 증가 금지
- private data가 Codex context에 노출되는 범위

승인 receipt는 proposal digest, revision, policy, frontier/audit selector algorithm,
sealed catalog root, private-context scope, budgets,
approval kind(`frontier` 또는 `project_deepening`), project ID(해당 시), 승인 시각과
user-confirmed state를 저장한다. hash나 estimate가 바뀌면 기존 승인은 재사용할 수 없다.
단순 CLI 실행, source approval 또는 과거 pilot 승인을 실행 승인으로 추정하지 않는다.

wall-time budget은 monotonic clock으로 측정한 active run time의 누적값이다. lease된
unit의 materialize/Codex/validate 시간과 lifecycle overhead는 포함하고, 명시적으로
paused인 시간과 사용자 승인 대기 시간은 제외한다. process restart 뒤에도 ledger의
누적 active time에서 이어지며 wall clock 변경의 영향을 받지 않는다.

## 10. next / validate / pause / resume

### 10.1 bounded next

`next-semantic-work`는 다음 절차만 수행한다.

1. manifest binding과 approval receipt를 bounded query로 확인
2. DB transaction에서 pause/budget/active lease를 확인하고 workspace execution mutex,
   run ID, unit ID, lease nonce를 `materializing` 상태로 먼저 예약
3. existing lease면 같은 nonce의 input을 idempotently 재생하고, 아니면 ready index에서
   예약된 한 unit만 처리
4. 필요한 catalog row와 pack slice만 읽기
5. locator를 application 내부에서만 사용해 selected regular file을 no-follow로 한 번
   열고, 같은 descriptor의 한 streaming pass에서 full content identity와 필요한 bounded
   slice를 함께 얻어 current policy 및 pre/post identity를 재검증
6. 검증된 slice를 mask한 뒤 262,144 bytes 이하 canonical input을 temporary file에 작성
7. input fsync/atomic replace 뒤 exact input hash를 같은 nonce에 bind하며 lease를 `issued`로
   전환

같은 active lease에는 byte-identical input을 반환한다. lease 없이 output을 받지 않는다.
on-demand representative input은 immutable pack에 원본 사본으로 남기지 않는다. receipt
commit 전에는 exchange slot이 정확한 bytes를 보존하고, 이후 재실행은 full content
digest가 같은 원본에서 결정론적으로 다시 materialize한다. digest가 달라지면 stale로
중단한다.

### 10.2 bounded validate

`validate-semantic-work`는 exact unit/lease/input hash, output schema, allowed action,
grounding, byte/time budget, dependency, policy와 content identity를 확인한다. 성공 시
canonical output bytes를 run result pack에 append/fsync하고, 그 exact record reference와
output receipt, 후속 queue/aggregate/checkpoint를 한 DB transaction에 commit한 뒤에만
exchange output을 비운다. 동일 output 재전송은 같은 receipt와 result reference를 반환하는
idempotent success이며, 다른 output은 conflict로 거부한다.

### 10.3 pause와 recovery

- 사용자 pause 요청의 10초 grace window는 요청이 durable하게 수락된 시점부터다.
  deadline 이상 시점의 미확정 결과는 receipt가 될 수 없으며, 기존 확정 prefix에서
  checkpoint와 lease 해제를 수행한다. 현재 atomic unit 완료를 무기한 기다리지 않는다.
- deadline과 clock 유효성 authority를 영속화하고 receipt transaction이 lock을 얻은 뒤
  현재 시각으로 다시 검사한다. 반복 pause나 pause caller 종료는 기한을 연장·소거하지 않는다.
  기존 receipt replay와 단일 budget 정산은 보존한다. DB contention에 따른 응답 지연과
  receipt 허용 기한은 별개다. 상세 수용 기준은
  [2026-09-08 Task 10 교정 계획](../plans/2026-09-08-semantic-v3-task10-pause-closeout.md)에 따른다.
- process crash 전 commit되지 않은 output은 receipt가 아니며 다시 실행한다.
- exchange write는 temporary + fsync + atomic replace를 사용한다.
- checkpoint는 receipt sequence, result-pack root/length, queue root, aggregate root,
  resume cursor를 bind한다.
- resume는 마지막 검증 checkpoint와 active/expired lease를 복구하고 다음 미완료 unit부터
  시작한다.
- 한 unit을 두 번 commit하거나 budget counter를 두 번 차감하지 않는다.
- cancellation은 project/deepening 상태나 기존 active semantic revision을 삭제하지 않는다.

### 10.4 failure artifact

failure는 `.portfolio-maker/.../private-reviews/` 아래 bounded JSON review만 남긴다.
필수 필드는 revision, phase, safe error code, last checkpoint, affected node ID, retryability,
policy/manifest binding이다. credential, token, raw content, private URL은 넣지 않는다.
absolute path는 오류 재현에 실제로 필요한 private diagnostic에서만 별도 locator reference로
연결하며 기본 review text에는 넣지 않는다. public manifest/HTML로 전파하지 않는다.

## 11. status, verify, finalize, apply

### 11.1 status integrity level

기본 `semantic-index-status`는 lifecycle aggregate만 읽으며 다음을 명시한다.

- revision/protocol/status
- 구조·baseline·receipt·frontier/deepening 상태
- phase별 used/approved unit, bytes, time
- ready/leased/validated/deferred/error counts
- last checkpoint, safe `last_verified_at`, estimated remaining budget와 resume 가능 여부
- `integrity_level = aggregate_bound`

이는 full pack/catalog 검증 주장이 아니다. `--verify full`만 catalog/pack/receipt/checkpoint를
streaming 검증하고 `integrity_level = full_verified`를 반환할 수 있다.

### 11.2 finalize

finalize는 mutable counter만 신뢰하지 않고 다음을 streaming O(N), bounded memory로
검증한다.

- manifest self-hash와 pack/catalog roots
- 모든 receipt sequence, input/output hash와 dependency
- 모든 committed result record의 offset/length/hash와 checkpoint result root
- selector convergence 또는 deepening completion 조건
- deferred/partial material counter-signal
- source policy/current content binding
- private full topology와 semantic output 분리

검증된 semantic node를 새 immutable final pack/catalog에 stream write한다. Codex output에
topology가 없으므로 full child list는 structural pack에서 복원한다. publication 전 byte
size/root preflight를 수행하고 receipt가 가리키는 result-pack canonical bytes만 streaming
읽는다. 이전 상태 snapshot은 다중 GB generation 복사가 아니라 기존의 작은 DB
binding/pointer와 manifest bytes/absence만 의미한다. post-write failure 시 이 작은 이전
binding과 manifest bytes/absence를 복원해 확인한다.

### 11.3 apply

apply는 final manifest와 DB staging binding을 재검증한 뒤 기존 import receipt 다음부터
bounded chunk로 idempotent import한다. 모든 chunk와 graph invariant를 확인한 단일
transaction에서 새 revision을 active로 바꾸고 이전 active를 superseded로 만든다.
중단·실패·policy drift 시 이전 active를 유지한다.

## 12. cache와 invalidation

cache는 의미가 다른 세 계층을 구분한다.

1. **local baseline cache**: analyzer 결과와 coverage
2. **representative/frontier cache**: selector feature와 선택 이유
3. **validated semantic result cache**: Codex output과 receipt

local baseline cache는 metadata/sample identity와 analyzer contract가 같을 때만 같은
coverage 범위에서 재사용한다. `sampled` 결과를 full-content 동일성으로 승격하지 않는다.

revision 간 semantic result 재사용은 다음이 모두 같아야 한다.

- full content SHA-256
- source policy hash와 masking policy version
- local analyzer/version/coverage contract
- frontier algorithm/version과 target projection contract
- parent context와 dependency digests
- input schema, output schema, validator version
- project boundary digest(deepening인 경우)

하나라도 다르면 cache miss다. cross-directory link나 ancestor summary가 바뀌면 관련
candidate와 ancestor closure를 invalidate한다. source가 일시적으로 unreadable해졌다는
이유만으로 기존 node/project를 삭제하지 않고 stale 상태로 둔다.

## 13. v2 호환과 migration

### 13.1 active legacy revision

- 기존 active legacy v1과 `codex-semantic-v2` revision은 계속 읽을 수 있다.
- project/artifact consumer는 protocol dispatch로 legacy active를 유지한다.
- v3 apply가 완전히 성공하기 전에는 legacy active를 supersede하지 않는다.
- 현재 CLI 이름은 유지하고 manifest protocol로 v2/v3 handler를 dispatch한다.
- 기존 manual project decision, exclusion, split/merge lineage는 보존한다. v3 boundary
  identity가 달라지면 자동 전파하지 않고 explicit review 대상으로 남긴다.

### 13.2 staging v2

v2 staging은 자동 resume·자동 upgrade하지 않는다. 선택지는 다음 두 가지다.

1. source approval을 다시 확인하고 fresh v3 `prepare-semantic-index` 실행
2. 명시적 `migrate-semantic-staging` 실행

migration은 raw source를 다시 읽지 않고 v2 manifest, capsule, structural, locator,
plan을 streaming 읽어 새 v3 catalog/packs를 만든다. source ID, policy hash, node count,
topology, payload digests를 검증하고 v3 manifest의 `derived_from`과 private migration
report에 기록한다.

기존 v2 work receipt는 archive/provenance일 뿐 v3 frontier receipt로 자동 승격하지
않는다. v3 cache 재사용 조건을 모두 독립적으로 만족한 semantic payload만 cache candidate가
될 수 있으며, migration 자체가 그 조건을 증명하지 않는다. fresh release pilot은 migration을
사용하지 않는다. `direct-child-attestation-v1` 이전 staging처럼 현재 projection contract를
검증할 수 없는 plan은 migration을 fail closed하고 fresh prepare를 요구한다.

### 13.3 rollback

migration 또는 v3 prepare 실패는 v2 파일/DB status를 수정하지 않는다. v3 staging을
삭제하는 명령은 이번 범위가 아니며 사용자의 별도 명시 승인이 없으면 보존한다.

## 14. CLI와 application API 변경

### 14.1 CLI

| command | v3 계약 |
|---|---|
| `prepare-semantic-index` | 기본적으로 streaming v3 prepare와 proposal 생성 |
| `semantic-index-status` | bounded aggregate status; `--verify full`은 streaming audit |
| `next-semantic-work` | 승인된 revision에서 한 bounded leased unit materialize |
| `validate-semantic-work` | exact lease output 검증과 atomic receipt commit |
| `finalize-semantic-index` | completion gate와 full streaming verify 후 final state 작성 |
| `apply-semantic-index` | v2/v3 dispatch, idempotent import와 atomic activation |
| `migrate-semantic-staging` | 명시된 v2 staging을 v3 staging으로 lossless 변환 |
| `prepare-semantic-deepening --project-id` | 선택 project의 별도 proposal/plan 생성 |
| `approve-semantic-execution --revision-id --proposal-digest` | exact frontier/deepening proposal을 사용자 확인 뒤 기록 |
| `pause-semantic-work --run-id` | 현재 atomic unit 뒤 durable pause 요청 |
| `resume-semantic-work --run-id` | current binding을 재검증한 뒤 checkpoint에서 재개 |

Codex skill은 proposal을 보여 준 뒤 user confirmation을 exact proposal digest와 함께
`approve-semantic-execution` use case에 전달한다. 이 command는 이미 생성된 proposal과
digest가 정확히 일치할 때만 receipt를 기록하며, 임의 budget 값만 받아 승인 파일을 만드는
shortcut을 허용하지 않는다. v3 `next`/`validate`는 `--run-id`를 받을 수 있고, 생략 시
실행 가능한 run이 정확히 하나일 때만 선택한다. 둘 이상이면 fail closed한다.

`prepare-semantic-deepening`은 opaque current project ID만 받으며 raw/relative/absolute
path나 source URL을 target으로 받지 않는다. 취소·재시도·resume는 기존 baseline/frontier
receipt를 삭제하지 않고 byte-idempotent해야 한다. deepening 결과만으로 project,
evidence, artifact 또는 delivery state가 달라지는 것을 금지한다.

동시 `next` 두 개가 서로 다른 run을 요청하면 workspace execution mutex를 먼저 획득한
정확히 한 run만 lease를 얻는다. 다른 호출은 bounded `run_busy` 응답을 받고 exchange를
쓰지 않는다. mutex/lease reservation은 어떤 pack/source read나 exchange write보다 먼저
commit한다. `materializing` 중 crash하면 같은 nonce만 결정론적으로 input을 복원할 수 있고,
다른 run은 slot을 건드릴 수 없다. 같은 run·같은 lease의 재호출만 byte-identical
idempotent replay다.

### 14.2 application port

구현은 다음 책임을 분리한다.

- `StructuralEventSource`: 승인된 source에서 streaming event 생성
- `SemanticCatalogSink`: catalog/pack append, seal, manifest publication
- `LocalBaselineAnalyzer`: versioned bounded local analysis와 coverage
- `FrontierSelector`: seed, representative, phase, convergence, defer 결정
- `SemanticLifecycleStore`: approval, lease, receipt, aggregate, checkpoint transaction
- `SemanticExchange`: 한 bounded input/output slot 원자 publication
- `SemanticFullVerifier`: streaming catalog/pack/receipt verification
- `SemanticRevisionMigrator`: v2 managed state를 v3로 변환
- `ProjectDeepeningPlanner`: 선택 project 범위와 별도 plan/approval binding

CLI adapter와 Codex skill은 이 use case만 호출한다. pack offset 계산, SQL mutation,
manifest hash 계산을 skill prompt에 복제하지 않는다.

## 15. 성능·자원 acceptance gate

fresh full-home run은 다음을 만족해야 한다.

### 15.1 prepare

- wall time ≤ 3,600초
- managed disk ≤ 3 GiB
- peak RSS ≤ `min(4 GiB, physical RAM의 25%)`
- 구조 node count와 terminal baseline count 일치
- 암묵적 제외, global node/file cap, pilot-state 재사용 없음

### 15.2 bounded operations

| operation | gate |
|---|---|
| status | median ≤ 2초, max ≤ 5초, RSS ≤ 512 MiB |
| status 20회 반복 | 각 ≤ 5초, process RSS growth ≤ 64 MiB |
| next / validate | p95 ≤ 5초, RSS ≤ 512 MiB |
| full verify | ≤ 600초, RSS ≤ 1 GiB |
| finalize / project review | 각각 ≤ 600초, RSS ≤ 2 GiB |

측정은 fresh process와 기록된 command/environment로 수행한다. status 20회 RSS growth는
동일한 application process에서 API를 반복 호출해 측정하고, 별도 CLI process 측정도 함께
기록한다. cache warm/cold 여부와 physical RAM을 report에 남긴다. 단위 테스트 fixture의
성능을 full-home acceptance로 대체하지 않는다.

### 15.3 pause/resume

- 32 validated receipts 직후 의도적으로 pause
- durable checkpoint와 정확한 aggregate 확인
- process 종료 후 resume
- duplicate/missing receipt, unit 재과금, output drift 없음
- 기존 active revision 유지

## 16. 프로젝트 발견 품질 acceptance

fresh pilot은 최소 다음 세 프로젝트를 3/3 recall해야 한다.

- 보험 RAG
- Portfolio Maker
- PlayMCP

각 candidate는 적어도 서로 다른 두 종류의 grounded signal을 가져야 한다. 예: 목적 문서
+ code entrypoint, build config + test/deployment, repository metadata + 결과 문서. 단순 폴더명,
keyword 또는 파일 수만으로는 통과하지 않는다.

경계는 다음을 독립 검토한다.

- 공통 맥락의 하위 폴더가 parent project에 잘못 분리되지 않음
- 독립 제품·공모전·배포물인 child가 parent에 잘못 흡수되지 않음
- build/cache/temp folder가 project로 승격되지 않음
- private repository 이름 display 허용과 private URL/locator 비노출이 동시에 지켜짐

### 16.1 300-sample false-negative audit

300-sample 감사는 별도 deepening이나 무승인 작업이 아니라 같은 `frontier` approval에
결합된 audit phase다. 최초 proposal/approval은 아직 존재하지 않는 exact sample digest가
아니라 audit selector rule/version, sealed catalog root, private-context 범위와 최대
64 units/16 MiB/15분을 bind한다.

프런티어 expansion 종료 뒤 비선택/deferred 영역에서 정확히 세 층을 만든다.

1. relevance-weighted 100: 남은 node 중 local project-likeness/counter-signal이 높은 순
2. coverage-stratified 100: source, depth, size, role, coverage/error 상태를 층화해 선택
3. deterministic-random 100: revision ID와 policy hash를 seed로 stable hash 선택

중복 node는 다음 stable 후보로 대체해 총 300개를 유지한다. selector는 expansion의 마지막
validated checkpoint 이후 이 집합을 결정론적으로 freeze하고, exact ordered node-set digest,
모집단 digest와 selector version을 새 audit checkpoint에 원자 commit한다. audit unit은 그
checkpoint가 가리키는 집합만 읽을 수 있다. selector/version, catalog root, privacy scope 또는
budget이 최초 승인과 달라졌으면 집합을 만들거나 읽지 않고 새 proposal과 명시 승인을
요구한다. 동일 binding에서 결정론적으로 만들어진 post-expansion digest는 승인 범위를
확장하지 않으므로 별도 승인을 요구하지 않는다.

300개 node는 bounded unit으로 batch되며 승인된 audit 예산을 넘기면 `paused_budget`로
멈추고 `project_discovery_ready`가 될 수 없다. 더 진행하려면 변경된 proposal digest에
대한 새 frontier 승인을 받아야 한다. 독립 reviewer가 parent/child
맥락과 validated private audit projection을 검토한다. 이 사람/Review Team 검토는 새로운
raw source read나 Codex context 노출을 만들지 않고 이미 승인·검증된 audit output만
평가한다. 프런티어가 놓친 high/medium project가 1건이라도 있으면 release gate 실패다.
low/ambiguous 발견은 severity와 selector 개선 입력으로 기록하되 자동으로 source를
재탐색하거나 budget을 늘리지 않는다.

## 17. 테스트 전략

### 17.1 단위 테스트

- pack framing, truncated record, digest/root mismatch, seal 후 append 거부
- canonical manifest/self-hash와 checkpoint chain
- streaming event ordering, directory close, stable node ID
- 모든 baseline coverage와 prefix/full identity 구분
- deterministic seed/representative/ancestor closure/tie-break
- 네 Codex action schema와 invalid topology/locator rejection
- phase별 unit/byte/time budget과 no-auto-increase
- state truth table과 false completion 방지
- lease idempotency, duplicate/conflicting output, pause/recovery
- result-pack append/fsync/receipt 각 경계 crash, orphan tail, exchange cleanup 후 복원
- 두 run의 concurrent next에서 단일 workspace lease와 slot 격리
- full hash 직후 tail rewrite/동일 inode 변경의 TOCTOU 거부
- v2/v3 protocol dispatch와 migration binding
- deepening authority, split/merge/exclude invalidation

### 17.2 통합 테스트

- 100만 synthetic node를 bounded memory로 prepare/status/next/validate/finalize
- 3,584개와 10,000개 direct-child high fan-out topology 보존
- coherent parent, independent child, cross-directory cluster, nested mono-repo fixture
- sampled/unreadable/unsupported counter-signal로 medium auto-include 차단
- policy/content drift가 active revision을 보존하며 fail closed
- prepare/finalize/manifest publication 각 fsync/rename 지점 failure injection
- validate transaction과 checkpoint 직전/직후 crash recovery
- v2 active 유지 및 explicit staging migration losslessness
- cache poisoning: prefix 동일/후반부 다른 파일, parent-context 변경, validator 변경
- private locator/URL/token과 excluded source의 catalog/exchange/artifact 비노출

### 17.3 adversarial acceptance

- aggregate counter만 위조해도 finalize 실패
- pack과 catalog를 함께 부분 수정해도 manifest/root mismatch 실패
- expired lease replay와 다른 unit output 거부
- budget 소진 뒤 completion 강제 불가
- Codex가 nonexistent child/evidence 또는 full topology를 출력하면 거부
- unreadable subtree를 non-project로 자동 판정하지 않음
- deterministic audit sample을 임의로 교체하면 digest mismatch 실패
- 같은 prepare state에 서로 다른 유효 Codex action sequence를 적용하면 각
  post-expansion 모집단에서 다른 sample set을 만들 수 있지만, 모두 동일한 승인 selector
  계약과 각자의 checkpoint digest에 정확히 bind됨
- selector version/catalog root/privacy scope/budget drift 뒤 audit sample freeze/read 거부
- exchange 삭제·process restart 뒤 result pack에서 byte-identical finalize

같은 사용자가 manifest, pack, catalog, ledger와 검증 코드를 모두 일관되게 다시 쓰는 공격은
현재 threat model 밖이다. SHA-256과 checkpoint chain은 accidental corruption, partial write,
stale/mismatched component를 탐지하지만 외부 공격자에 대한 서명 증명이 아니다. 그 보장이
필요해지면 OS keychain 또는 remote transparency log 같은 외부 signing anchor를 별도
설계한다.

## 18. release blocker와 판정

다음 중 하나라도 존재하면 Task 15는 `NEEDS_WORK` 또는 `BLOCKED`다.

1. 숨은 global cap, top-K omission 또는 budget exhaustion을 completion으로 표시
2. mutable aggregate counter만 신뢰한 finalize/apply
3. unit/byte/time 중 하나라도 무제한이거나 자동 증가
4. 실제 threat model보다 강한 malicious-tamper 방어 주장
5. raw locator/private URL/credential 또는 excluded content leakage
6. 다중 GB publication이 atomic restore 없이 partial state를 남김
7. high-signal project가 stable ordering 때문에 영구 starvation
8. prefix/sample fingerprint를 full content cache identity로 사용
9. v2 active/staging의 암묵적 변환, receipt 승격 또는 데이터 손실
10. deepening이 project authority, cancel, idempotent resume를 bind하지 않음
11. 완료 상태를 합쳐 partial/deferred/unreadable을 숨김
12. fresh pilot에서 필수 3개 project recall/boundary/audit gate 실패
13. validated semantic output canonical bytes와 receipt 사이 durable result reference가 없음
14. multi-run exchange 충돌 또는 hash와 input slice 사이 TOCTOU가 가능

release candidate는 다음 증거를 모두 요구한다.

- Developer의 focused/full test와 resource measurement
- 독립 Review Team의 코드·schema·migration·privacy·performance 검토 PASS
- 위 fresh pilot gate와 300-sample audit PASS
- candidate worktree의 의도한 diff만 존재하고 temporary/pilot artifact가 없음
- README, development principles, roadmap, plugin skills가 구현된 실제 동작과 일치

PASS는 push, tag, GitHub release, plugin publish, deployment 또는 보존 pilot 삭제 권한을
포함하지 않는다.

## 19. 구현 순서 제약

구현 계획은 다음 dependency를 지켜 작업 단위로 분해한다.

1. schema/pack/catalog/lifecycle primitives와 migration boundary
2. streaming prepare와 local baseline coverage
3. deterministic frontier proposal, estimate, approval binding
4. bounded next/validate/checkpoint/pause-resume
5. convergence/completion/finalize/apply
6. selected-project deepening
7. CLI와 repository/plugin skill/docs 갱신
8. synthetic scale, adversarial, independent review
9. fresh detached full-home pilot와 project-quality audit

각 구현 slice는 failing test를 먼저 만들고, 한 Developer owner가 수정한 뒤 동일 scope를
독립 Review Team이 검토한다. review finding은 한 번의 focused fixback으로 닫고 같은
scope를 재검토한다. fresh pilot은 1~8이 current candidate에서 PASS한 뒤에만 실행한다.
