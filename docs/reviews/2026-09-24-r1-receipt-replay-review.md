# R1 receipt replay 교정 및 독립 리뷰 — 최종 NEEDS_WORK

날짜: 2026-09-24

## 범위와 후보

- 사용자 승인에 따라 goose가 구현하고 별도 독립 리뷰 서브에이전트가 검토했다. 지정 Developer/Review Team 재사용·대체 금지 지침은 양쪽 작업트리의 9월 8일·9월 24일 계획에서 제거했다. 과거 참여자·판정 기록은 보존했다.
- 개발 작업트리: `.worktrees/portfolio-maker-0.2.2-cycle-01`
- HEAD: `15ca55093cf7bcaed1182bc08af0d1e38d2a6f0e`
- 기존 7파일 WIP SHA-256: `89c9b9fbff4897ca2ea81cc4cc43fa62b69208d01b133541feb997dae1770d5d`
- 검토 후보 7파일 누적 diff SHA-256: `ebc57593e36c73cd085ce3863f91b73f5eac1b3ce91cde314ae84415090764e7`
- R1 코드 수정은 `sqlite_repository.py`, `semantic_lifecycle_v3.py`, `test_semantic_lifecycle_v3.py`에 한정했다. 기존 다른 4파일 WIP·HEAD·stash는 변경하지 않았다. 커밋·배포·실자료 접근은 하지 않았다.

## 구현 및 검증

- 신규 commit·repository replay·application replay에 canonical body/digest, self-hash, 중복 저장 필드, unit/run/pack/checkpoint 검증을 적용했다.
- application replay의 tail 복구·exchange 정리를 검증 이후로 옮겼다.
- malformed/missing binding, 저장 필드 손상, key lookup miss, 정상 replay, newer receipt 이후 과거 replay 회귀를 추가했다. 직접 repository 테스트의 임의 self-hash는 명세에 맞는 테스트 fixture hash로 교정했다.
- 기준 lifecycle: 151 passed.
- application 손상 RED: 20 failed / 5 passed. writer self-hash RED: 1 failed.
- 수정 후보 lifecycle: 188 passed; affected 5 suites: 551 passed; full: 1736 passed (126.63s, exit 0).
- 독립 리뷰: 누적 7/7 파일, skip 0; lifecycle+CLI 274 passed, focused 102 passed, affected 551 passed, full 1736 passed. 독립 판정은 R1 PASS였으나 아래 최종 probe가 누락 계약을 드러냈다. 따라서 이 PASS를 최종 승인으로 채택하지 않는다.
- 양쪽 `git diff --check` 통과. HEAD/stash 불변 및 기존 파일 유실 없음 확인.

## OPEN — R1-I1 (Important): 과거 receipt input-byte accounting 손상 미검출

**재현:** 합성 application 경로로 2개 receipt를 확정한 뒤 첫 번째 row의 `input_bytes`만 1 감소시키고 첫 번째 요청을 replay한다.

**기대:** `SemanticLifecycleV3Error`, 성공 반환 및 DB/pack/exchange mutation 없음.

**관찰:** 예외 없이 성공. 최종 pytest probe는 `DID NOT RAISE SemanticLifecycleV3Error`로 실패했다 (1 failed, exit 1).

**원인:** `_v3_checked_receipt_row`는 각 receipt의 `input_bytes <= run.used_input_bytes`와 receipt가 한 개일 때만 exact equality를 확인한다. 두 개 이상이면 작은 단일 필드 손상을 검출하지 못한다. `input_bytes`는 기존 canonical commit body에 포함되지 않아 그 digest도 이를 잡지 못한다.

**영향:** 손상된 영속 accounting 상태를 유효한 replay로 인정한다. R1 설계 §5.4의 commit body 밖 필드 일관성 조건을 충족하지 못한다. 이 재현은 과금 중복이나 데이터 유출을 입증한 것이 아니다.

**다음 교정 조건:** bounded receipt/input accounting의 권위 결합을 보완하고, 2개 이상 receipt 이후 첫/중간/최신 receipt의 input-byte 손상 및 정상 과거 replay를 함께 검증해야 한다. 전체 receipt/catalog scan으로 우회하지 않는다. 변경한 후보는 새 hash로 독립 리뷰해야 한다.

## 판정과 제외 범위

**최종 NEEDS_WORK.** 최초 self-hash replay 결함과 다수 손상 경로는 교정됐지만 R1 전체 완료·Task 10 승인·Task 11 진행으로 확대하지 않는다. 한 focused correction + 독립 누적 리뷰 사이클에서 새 Important가 남으면 종료한다는 계획에 따라 두 번째 수정 사이클을 시작하지 않았다.

독립 리뷰가 판단을 유보한 실제 reboot, E/P/H, 1M-node scaling, fresh full-home pilot, 실제 renderer/package build 및 실자료 품질은 이번 R1 범위 밖으로 유지한다. 이는 미검증이지 PASS가 아니다. reviewer의 임시 외부 probe는 multiprocessing harness 오류로 제품 경로에 도달하지 못했으며 근거에서 제외했다. 본 최종 accounting probe는 pytest에서 실제 application 경로를 실행해 별도로 재현했다.

## 로컬 상세 증거

개발 작업트리 `.superpowers/sdd/2026-09-24-portfolio-outcome-recovery/`:
- `baseline.json`, `baseline.patch`, `candidate.patch`
- `red.log`, `writer-red.log`, `lifecycle.log`, `affected.log`, `full.log`
- `independent-review.md` (원판정 보존)
- `final-accounting-probe.py`, `final-accounting-probe.log`
- `preservation-check.txt`, `progress.md`

최종 probe 실행 예: 개발 작업트리에서 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests ../../.venv/bin/python3 -m pytest -q -p no:cacheprovider .superpowers/sdd/2026-09-24-portfolio-outcome-recovery/final-accounting-probe.py`.

## 후속 기록

위 NEEDS_WORK는 당시 후보 판정으로 보존한다. 사용자 승인 후 R1-I1을 별도 교정한 결과는 [R1-I1 한정 범위 PASS](2026-09-24-r1-i1-accounting-review.md)에 기록했다. Task 10 전체 승인은 아니다.
