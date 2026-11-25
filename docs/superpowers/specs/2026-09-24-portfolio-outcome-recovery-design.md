<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Portfolio Maker 목적 중심 복구 설계 개정

날짜: 2026-09-24
상태: 사용자의 설계·계획 수정 위임에 따라 개정. 구현·독립 승인·release 완료를 뜻하지 않음.
실행 계획: [목적 중심 복구 계획](../plans/2026-09-24-portfolio-outcome-recovery.md)

## 1. 목적과 선택

제품의 목적은 승인된 사용자 자료를 검증 가능한 프로젝트 근거로 정리하고, 검토 가능한 프로필·Markdown·HTML 포트폴리오를 만들며 변경 뒤에도 안전하게 갱신하는 것이다. 전체 홈 분석은 자료 발견 범위를 확장하는 수단이지 제품 성공의 유일한 기준이 아니다.

- 선택: 기존 v3 구조와 안전 계약을 유지하되, 작은 합성 범위의 종단 간 산출물 검증과 조기 자원 검증을 추가한다.
- 기각: 전체 홈 성공 뒤에만 사용자 가치를 확인하는 방식. 통합 오류를 너무 늦게 발견한다.
- 보류: history/vision을 즉시 분리 release하는 방식. 누적 브랜치의 의존성과 분리 비용을 검증하지 않았으므로 현재 개정은 분리·cherry-pick·배포를 허용하지 않는다.

사용자는 설계·계획 개정을 위임했다. 코드 수정, 자료 열람, 실행 승인, Git 작업, 배포 승인은 별개다.

## 2. 문서 권위와 보존

이 개정은 제품 성공 기준, 검증 시점, receipt 검증 경로의 동등성에 대한 추가 계약이다. 충돌하는 제품 우선순위는 이 문서를 따른다. 기존 protocol/schema/budget 수치, 승인, 개인정보, migration, crash recovery 계약은 완화하지 않는다.

- 7월 16일 history/vision 설계·계획: 해당 기능의 계약과 과거 검증 기록 유지.
- 8월 2일 v3 설계·계획: 저장 구조, 상태 전이, Tasks 1–16의 기술 권위 유지. 개발 브랜치의 `docs/superpowers/specs/2026-08-02-portfolio-maker-home-scale-adaptive-semantic-frontier-design.md` 및 동명 주제 계획을 참조한다. master에 없는 원문을 재생성하지 않는다.
- 9월 8일 Task 10 closeout: NEEDS_WORK로 종료한 역사적 사이클. 체크박스나 판정을 소급 변경하지 않는다.
- 9월 24일 계획: 새 실행의 시작점. 기존 Task 번호를 재사용하지 않고 R0/R1과 추가 gate 이름으로 구분한다.

이 개정과 계획은 master 및 개발 작업트리에 동일 내용으로 보관한다. 실행 전 두 사본의 SHA-256 일치를 확인하며, 차이가 있으면 자동 덮어쓰기하지 않고 차이를 검토한다. 구현 상세 권위는 계속 개발 작업트리에 있다. 향후 병합 시 같은 상대 경로의 문서를 하나로 통합하며 별도 truth source를 만들지 않는다.

## 3. 변경하지 않는 안전 경계

- 원본 사용자 자료를 복사·이동·삭제하거나 보존 pilot을 재사용·정리하지 않는다.
- source scope, Codex 분석, project 결정, evidence 선택, delivery, 배포 권한은 독립적이다.
- 기존 active revision과 manual include/exclude를 보존한다.
- 구조 전수성, 로컬 baseline coverage, 의미 판단 완료, artifact readiness를 구분한다.
- sampled/deferred/unsupported/unreadable을 complete로 승격하지 않는다.
- secret·locator·private URL은 공개 표면에 노출하지 않는다. restricted도 공개 배포 허가가 아니다.
- CLI에 외부 LLM API·토큰 관리·신규 서비스나 DB를 추가하지 않는다.

## 4. 두 개의 완료 기준

### P: 범위가 명확한 포트폴리오 생성·갱신

먼저 합성 workspace에서 승인→근거→프로젝트 결정→profile/Markdown/manifest/HTML을 연결한다. 신규 runtime mode나 별도 인덱스를 만들지 않고 기존 application 경로를 검증한다.

- 각 프로젝트 overview·claim은 사용 가능한 근거와 연결되거나 명시적으로 검토 필요로 남는다. 역할·성과·수치를 임의 생성하지 않는다.
- 동작 여부뿐 아니라 project/evidence/claim ID를 통해 입력에서 산출물까지 추적한다.
- source 변경·승인 철회·project 제외 뒤 재생성하면 오래된 근거가 제거되거나 stale로 차단된다. 재포함은 현재 유효한 근거만 복원한다.
- restricted와 open_public 결과를 각각 검사한다. 로컬 근거가 자동으로 open_public으로 승격되지 않는다.
- 미완료·미지원 영역을 검토 화면에 명확히 남긴다. 해시만 유효한 일반 문구를 의미 품질의 증거로 사용하지 않는다.

P 통과는 작은 합성 범위의 통합 검증이며 release/full-home 완료가 아니다. 실제 사용자 자료를 이용한 관찰은 별도 범위 승인이 있어야 한다.

### H: 전체 홈 발견·재개 가능성

기존 v3 Tasks 1–16, 독립 리뷰, 별도 승인된 fresh pilot을 모두 유지한다. prepare 3,600초/3 GiB/기존 RSS 제한, status median 2초·max 5초, next/validate p95 5초 등 기존 수치를 낮추거나 생략하지 않는다. 32 receipt pause/resume, 3개 알려진 프로젝트 recall, 300-sample 감사도 유지한다.

H 통과는 명시된 환경·범위의 acceptance 통과다. 모든 프로젝트를 빠짐없이 이해했다는 보증은 아니다. 기존 파일럿과 과거 PASS를 새 H 증거로 재사용하지 않는다.

## 5. receipt 신뢰 경계

새 저장, repository 직접 replay, application 기존-row replay, 향후 full verifier가 같은 canonical binding 규칙을 사용해야 한다.

1. 저장 row를 단순 dict로 읽었다는 이유로 신뢰하지 않는다. self-hash, canonical commit bytes/digest, typed field와 현재 run/revision/unit·result pack 결합을 확인한다.
2. self-hash는 현재 writer가 사용하는 input_sha256, lease_nonce, output_sha256, run_id, unit_id, validator_version의 canonical JSON으로 재계산한다. 기존 형식을 임의 변경하지 않는다.
3. commit body/digest는 기존 `_v3_receipt_binding` 규칙을 재사용하고 row의 중복 필드와 비교한다. validator input hash와 전체 exchange byte hash를 혼동하지 않는다.
4. sequence/input-byte accounting 등 commit body 밖 필드는 기존 unit/run/checkpoint 권위와 일관성을 검사한다. 과거 receipt의 pack prefix와 현재 append된 pack 전체 길이가 같아야 한다고 잘못 요구하지 않는다. 과거 결과는 현재 committed prefix 안에 포함되어야 한다.
5. 성공 반환·exchange 정리·tail 복구 전에 손상된 receipt를 거부한다. 정상 receipt에 대한 기존 orphan-tail 복구는 유지한다.
6. replay는 카운터를 증가시키거나 새 receipt를 만들지 않는다. 저장된 필드 손상을 입력의 새 해시로 덮어 고치지 않는다.
7. 검증은 bounded unit과 관련 권위만 읽는다. replay 하나 때문에 전체 catalog/pack을 다시 로딩하는 해결책은 허용하지 않는다.

기존 v3 row에서 필수 self-hash/commit bytes/digest가 없거나 malformed이면 fail closed한다. 요청으로 값을 추정·보충하거나 성공 receipt로 자동 migration하지 않는다. v1/v2는 기존 명시 dispatcher와 호환 계약을 유지하며 v3 검증을 우회하는 fallback으로 사용하지 않는다.

보장 범위는 부분 손상·stale·불일치 탐지다. 같은 사용자가 전체 상태를 일관되게 다시 쓰는 공격에 대한 암호학적 방어를 주장하지 않는다.

### 5.1 R1-I1 후속 승인 — 2026-09-24

사용자가 R1-I1 후속 교정 설계를 승인했다. 위 §5.3–4의 기존 commit 형식 유지 조건에 한정된 예외로, canonical commit envelope에 `accounting` 객체(`sequence`, `input_bytes`, `cumulative_input_bytes`)를 추가하고 같은 digest에 결합한다. 기존 6필드 self-hash와 외부 request/result protocol은 유지한다. 입력 크기는 신규 commit의 issued lease, 순번과 누계는 트랜잭션 안의 이전 receipt/run 권위에서 얻는다.

replay는 typed projection, 첫 receipt의 누계, 대상과 인접 receipt의 누계 연결, 최신 receipt와 run 누계의 일치를 bounded 조회로 검증한다. 새 commit은 이전 prefix의 권위를 검증한 뒤 확장한다. accounting 결합이 없는 기존 v3 receipt는 자동 보정·migration 없이 fail closed하며 v1/v2 호환 경로는 유지한다. 전체 receipt/catalog 스캔은 추가하지 않는다. 이 확장은 사용자 승인된 교정 범위이며 과거 검토 판정을 소급 변경하지 않는다.

## 6. 의미 품질과 검증 계층

- protocol 테스트: schema, hash, coverage, 개인정보.
- lifecycle 테스트: 실제 application/CLI 진입점의 반복·손상·중단·재개.
- 품질 평가: 목적 문서와 code/test/deployment 등 서로 다른 종류의 grounded signal, parent/child 경계, 비프로젝트 오승격.
- 자원 평가: 전체 상태 크기가 아니라 한 bounded unit에 비례하는 steady-state 비용.
- 제품 평가: 생성물의 근거 추적과 변경 뒤 안전한 재생성.

Task 16에 고정 holdout 사례를 추가한다. 기존 세 알려진 프로젝트에만 맞추지 않도록 한국어 문서 중심 프로젝트, 독립 child, cross-directory 근거, build/cache 비프로젝트를 포함한다. 정답은 실행 전 고정하고 예외에 맞춰 바꾸지 않는다. 합성 출력 주입 테스트와 실제 Codex 의미 판단 평가는 별도 결과로 보고한다. 실패를 빈 summary나 낮은 confidence로 숨기지 않는다.

## 7. 규모와 코드 복잡성

catalog/pack/SQLite ledger/exchange 분리는 유지한다. 새 framework, generic repository 추상화, 전면 module 분해를 이번 복구에 포함하지 않는다. 먼저 receipt 검증의 중복·우회만 기존 경계에서 정리한다.

Task 11과 Task 12의 리뷰 전에 작은/큰 합성 catalog 비교를 수행한다. steady-state status/next/validate가 총 payload를 전부 decode하거나 전수 scan하지 않는지 호출·I/O 계측으로 확인한다. O(N)이 허용되는 prepare/full verify/finalize와 구분한다. 큰 fixture와 최종 1M-node gate는 Task 16에서 유지한다.

shared managed reader의 무제한 EOF read와 실제 OS reboot clock 연속성은 알려진 잔여 위험이다. 안전 검사를 제거하지 말고, 각각 byte-limit/streaming 검증 및 clock epoch 변경 fail-closed를 Task 16의 자원·복구 점검에 포함한다. 문서 개정으로 해결됐다고 표시하지 않는다.

## 8. release 및 후속 제품 범위

현재 0.2.2 개발 브랜치의 release gate는 기존대로 H까지 유지한다. P는 조기 통합 피드백이지 release 우회 경로가 아니다. master의 버전 정합성 보수는 별도 작은 작업으로 수행할 수 있지만 버전 번호 상승·release는 별도 결정이다.

P와 H 이후에만 회사/JD 맞춤 artifact, 외부 connector, 독립 OCR, MCP/GUI 확장을 검토한다. 확장 전에 승인 근거로 생성·검토·갱신하는 기본 흐름의 실제 사용자 관찰을 별도 승인하에 확보한다.
