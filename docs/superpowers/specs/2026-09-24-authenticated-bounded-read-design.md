<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Authenticated bounded reads — 선행 아키텍처 명세

상태: 설계 방향 승인됨 / 작성된 명세 검토 대기
날짜: 2026-09-24
대상: semantic v3 immutable catalog/structural pack의 steady-state 읽기

## 1. 목적과 승인 범위

Tasks 11/12의 조기 자원 gate E에서 next/validate가 catalog와 structural pack 전체를 반복 검증함을 확인했다. 사용자는 2026-09-24 정렬 레코드·페이지의 Merkle 인증 인덱스를 추가하는 방향과 부분/전체 검증의 구분을 승인했다. 이 명세는 그 승인된 방향을 구체화한다. 제품 구현, Git 쓰기, 배포, 실자료 접근, 기존 staging 자동 migration은 승인하지 않는다. 작성된 명세 승인 뒤 구현 계획을 별도로 검토한다.

기존 root 및 개발 worktree WIP, HEAD, stash, 기존 active revision을 보존한다. R1-I1 PASS와 E NEEDS_WORK는 서로 다른 범위의 판정이며 Tasks11/12는 미완료다.

## 2. 보장과 비보장

- 부분 검증은 이번 연산에서 소비한 레코드의 값, 승인된 generation 소속, 정렬 및 조회 범위의 완전성을 증명한다.
- 읽지 않은 모든 바이트의 현재 무결성은 부분 검증으로 증명하지 않는다. 미조회 영역의 손상은 이후 접근 또는 full verification에서 발견될 수 있다.
- 전체 검증은 기존 catalog file/logical seal, structural pack file/record seal 및 새 인증 인덱스의 상호 대응을 streaming 검증한다. finalize/apply의 필수 gate로 유지한다.
- 부분 검증 결과를 full_verified로 표시하지 않는다. 기본 aggregate status도 그대로 aggregate_bound다.
- 공격자가 신뢰 기준인 lifecycle DB와 approval 및 모든 바인딩을 함께 재작성할 수 있는 경우까지 방어하는 외부 서명 시스템은 이 범위가 아니다. 기준 root는 조회 대상 sidecar에서 스스로 가져와 신뢰하지 않는다.

## 3. 선택한 구조

기존 catalog.sqlite와 structural.pm-pack 및 그 전체 seal을 유지한다. 새 generation에는 필요한 조회를 표현하는 immutable 정렬 인증 인덱스를 추가한다. 인증 인덱스는 별도 서비스나 외부 DB를 요구하지 않는다. SQLite는 준비 단계 정렬과 디스크 기반 중간 처리에 재사용할 수 있지만 runtime SQL 결과 자체는 검증 권위가 아니다.

인덱스는 아래 논리 namespace를 제공한다.

- node ID → node record
- node ID → baseline 및 payload/content identity binding
- (parent ID, child ID) → ordered child relationship와 관련 record key
- source/frontier/representative 조회에 필요한 정렬 projection
- 승인된 deferred summary 및 bounded selector metadata

현행 next/validate가 실제 사용하는 모든 catalog 조회와 summary를 구현 계획에서 열거한다. 인증되지 않은 SQL fallback으로 gate를 통과시키지 않는다. 세 계층 semantic cache 및 runtime receipt history 전체의 재설계는 별도 작업이다.

## 4. 인증 tree와 wire contract

고정 fanout 상한과 bounded leaf byte 크기를 가진 정렬 Merkle tree를 사용한다. leaf에는 namespace, 정렬 key와 canonical value를 저장한다. internal node에는 자식 digest, key 범위, subtree count를 저장한다. node kind, format version, namespace를 hash domain에 포함해 서로 다른 종류의 bytes를 같은 leaf로 해석하지 못하게 한다.

- canonical encoding은 기존 canonical JSON 규칙과 호환되며 duplicate key, 비정상 숫자, 비canonical 표현을 거부한다.
- key 비교는 versioned deterministic byte order로 정의한다. locale 순서를 사용하지 않는다.
- ordinal/count/offset/length는 정확한 비음수 정수이며 상한 검사 후 allocation/read한다.
- root metadata는 tree height, total count, namespace, bounds, empty-tree 표현을 결합한다.
- proof 길이, tree 깊이, leaf bytes, 반환 row 수, 총 proof bytes에 제한을 둔다. 제한 초과는 controlled failure이며 무제한 fallback을 허용하지 않는다.
- 단일 value가 leaf 상한을 넘으면 기존 bounded pack reference 방식으로 분리하고 digest로 결합한다. 거대 row 하나를 메모리에 무제한 적재하지 않는다.

구체적인 상수와 byte-level test vector는 구현 계획에서 정하고 명세 보장을 약화시키지 않는다. SHA-256 기반 digest와 표준 라이브러리를 우선한다.

## 5. publication과 신뢰 기준

prepare에서 catalog/pack을 작성·seal한 뒤 인증 인덱스를 만든다. 전체 데이터를 메모리에 적재하지 않고 디스크 기반 정렬과 bounded 순회로 작성한다. 게시 전에 기존 catalog/pack과 인증 레코드를 대조하여 누락·중복·잘못된 reference 및 projection 차이를 검증한다.

새 versioned manifest 항목은 기존 seals, 인증 format/root/count, projection contract, generation/revision 식별자를 함께 결합한다. manifest digest는 기존 proposal→approval authority에 포함되므로 root 교체도 승인 바인딩을 깨뜨려야 한다. manifest의 root와 sidecar의 자기보고 root가 같다는 사실만으로 승인됐다고 판단하지 않는다.

publication은 완성된 immutable generation 단위다. 부분 sidecar를 운영 generation으로 노출하지 않는다. fsync와 원자적 게시 및 작은 DB/manifest binding 복원 원칙을 지킨다. 실패 시 기존 active와 기존 승인 상태를 유지하며 자동 재승인하지 않는다.

## 6. 조회 완전성

point lookup은 leaf membership proof를 검증한다. 없는 key는 인증된 이웃 경계 또는 인증된 빈 namespace를 통해 부재를 증명한다.

ordered page는 요청 namespace/prefix, exclusive cursor, limit를 입력으로 받는다. root에서 검증한 경로와 leaf 구간이 다음을 입증해야 한다.

1. 첫 반환 항목보다 앞서 반환돼야 할 항목이 생략되지 않았다.
2. 반환 구간 내부에 누락·중복·순서 역전이 없다.
3. limit 도달 시 다음 cursor가 마지막 반환 key와 결합된다.
4. 결과가 limit보다 짧거나 비어 있으면 해당 요청 범위가 끝났음을 증명한다.

SQL 결과의 각 row membership만 확인하는 것은 불충분하다. page boundary와 연속성을 검증할 수 없으면 실패한다. node/baseline/child projection 간 join도 인증된 key/value를 통해 일치 확인한다. 정상 작업의 read cost 목표는 조회마다 O(log N + 반환 데이터 크기)이며 catalog 크기에 비례한 scan을 포함하지 않는다.

## 7. structural pack 읽기

인증된 payload reference는 pack identity, offset, length, payload digest를 결합한다. 읽기 전에 승인된 pack binding과 일치 확인한다. PMPackReader의 framing/canonical payload/digest 검사 로직을 재사용하되 길이 및 allocation 상한을 먼저 적용한다.

symlink-follow를 금지하고 검증할 bytes는 동일한 열린 descriptor에서 읽는다. 파일 identity의 pre/post 변화, 잘림, 교체, 잘못된 framing은 fail closed한다. descriptor에서 읽은 정확한 bytes를 digest 검증 후 소비한다. stat/mtime은 보조 검사이지 인증 대체물이 아니다. index node도 같은 원칙을 따른다.

사용하지 않은 pack record의 현재 손상을 매번 확인하지 않는다. 그 의무는 전체 검증에 남는다.

## 8. lifecycle와 호환성

인증 형식이 있는 새 generation에만 bounded reader를 적용한다. 기존 seal-only staging과 v1/v2 compatibility는 기존 전수 검증 경로를 유지한다. 자동 sidecar 생성 또는 manifest/proposal 재결합을 수행하지 않는다. 알 수 없는 version이나 불완전한 새 형식은 fail closed한다.

runtime receipt self-hash, canonical accounting, checkpoint, result-pack authority 검사는 별개다. structural/catalog bounded read 도입을 이유로 R1 검사를 제거하지 않는다. result prefix 검증의 receipt 수 비례 비용은 별도 계측하고 숨기지 않는다.

proof/source/policy/approval 오류는 정상 receipt 성공으로 처리하지 않는다. 실패 시 결과 commit·used counters·active 전환·exchange cleanup이 일어나지 않아야 한다. 필요한 lease 예약 등 기존 lifecycle side effect는 명시적으로 구분하고 rollback/release 계약을 테스트한다.

## 9. full verification, finalize, apply

full verifier는 기존 파일/논리 roots를 재계산하고 인증 tree와 각 namespace projection을 catalog/pack과 streaming 대조한다. 인증 인덱스가 독자적으로 일관되더라도 catalog/pack과 다르면 실패한다. 누락·중복·count/order·dangling reference를 검사한다.

성공 기록은 정확한 generation/root 및 검증 시점에 결합한다. 이후 파일 변경을 과거 full_verified 기록으로 덮어쓰지 않는다. Task12 finalize/apply가 실제로 구현될 때 current authority 재검증, staging import binding, activation transaction에 이 조건을 통합한다. 본 선행 작업만으로 Task12 완료를 선언하지 않는다.

## 10. acceptance

### 무결성·호환

- leaf/internal/proof/root/count 손상, 다른 generation proof 혼용, index 교체 거부
- 위조된 빈 결과, 중간 child 누락, 순서 변경, 잘못된 cursor/terminal boundary 거부
- point hit/miss, 빈 namespace, 마지막 page, limit 경계, 과대 value/proof 입력
- pack reference offset/length/digest 변조, 동일 크기 손상, 잘림, symlink, 읽기 중 변경
- 새 프로세스에서도 동일 검증; process-local checked flag 없음
- stale policy/approval/proposal 실패 및 정상 historical receipt replay 보존
- 미조회 영역 손상은 partial read 성공 가능성을 명시적으로 테스트하고 full verify/finalize/apply gate에서 차단
- publish 실패/crash 이후 이전 generation 및 작은 binding 복원
- legacy staging 전수 경로와 기존 R1 회귀 유지

### 자원 gate E

정확히 1k/10k-node synthetic generation에서 process-cold, 동일 unit replay, 다음 신규 unit을 구분한다. OS storage-cold라고 주장하지 않는다. catalog authority scan과 structural full verification은 새 형식의 steady-state 경로에서 0이어야 한다. proof node/bytes, 실제 반환 row, 명시 I/O, SQLite 관측 범위, process RSS high-water와 elapsed를 구분한다. 단순 wall-time 통과만으로 승인하지 않는다.

준비 단계 추가 시간/공간 및 전체 verification의 bounded memory도 기록한다. 인증 index 생성의 O(N) 비용을 next/validate로 이동시키지 않는다. 기존 full suite와 별도 E를 모두 실행하고 독립 리뷰를 받는다.

## 11. 제외 범위와 재개 순서

미포함: Task11 semantic cache 구현, Task12 finalize/apply 구현 자체, deepening/migration, full-home pilot, 1M-node release acceptance, 실자료 분석, commit/push/deploy.

순서: 작성된 명세 승인 → 구현 계획 승인 → 선행 구현/TDD/독립 리뷰 → E 재검증 → Task11 → Task12. 기존 미커밋 변경을 baseline으로 보존하고 이 선행 작업의 delta를 별도로 추적한다.

## 12. 자체 검토

- 전체 현재 무결성과 부분 membership/range proof를 혼동하지 않도록 보장 범위를 분리했다.
- 승인된 root의 근거와 sidecar self-hash를 구분했다.
- membership만으로 누락/부재를 증명하지 않도록 range completeness를 요구했다.
- cold process, legacy, runtime receipt authority, publication 실패를 별도 계약으로 남겼다.
- 현재 명세는 구현 완료나 E PASS를 의미하지 않는다. 상수/파일별 작업/시험 벡터는 승인 후 구현 계획 단계에서 확정한다.
