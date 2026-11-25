# R1-I1 accounting 교정 — PASS (한정 범위)

날짜: 2026-09-24

## 승인 및 범위

사용자가 canonical envelope accounting 최소 확장과 후속 교정을 승인했다. goose가 구현하고 별도 독립 서브에이전트(20260924_9)가 누적 7/7 파일을 검토했다. 이전 R1 NEEDS_WORK 보고서는 이전 후보의 역사로 보존한다.

- 개발 작업트리: `.worktrees/portfolio-maker-0.2.2-cycle-01`
- HEAD: `15ca55093cf7bcaed1182bc08af0d1e38d2a6f0e` (변경 없음)
- 직전 후보: `ebc57593e36c73cd085ce3863f91b73f5eac1b3ce91cde314ae84415090764e7`
- 최종 누적 `git diff -- src tests` SHA-256: `cd37578e809ac613d3900a993a5c20874806063ebf72ce6a51a0e2115b9b8a0b`
- 이번 제품 수정: `sqlite_repository.py`, `test_semantic_lifecycle_v3.py` 두 파일. 다른 5개 src/tests WIP는 보존했다.

## 교정

canonical commit envelope의 accounting에 `sequence`, `input_bytes`, `cumulative_input_bytes`를 결합한다. 6필드 self-hash, 외부 API 및 DB 스키마는 유지한다. 저장 projection/type, 첫 receipt 누계, 인접 receipt 누계 연결, 최신 receipt/run 누계 일치를 검증한다. 신규 commit은 이전 receipt 권위를 먼저 검사한다. 전체 receipt/catalog 스캔은 추가하지 않았다. 기존 application의 pack-prefix 검증 비용이 사라졌다는 주장은 아니다.

accounting 결합이 없는 기존 v3 receipt는 fail closed한다. 요청으로 채우거나 자동 migration하지 않으며 v1/v2는 유지한다. 이 호환성 영향은 사용자 승인된 설계 §5.1 예외다.

## 검증 증거

- 기존 최종 probe: 수정 전 1 failed (`DID NOT RAISE`), 독립 리뷰 후 재실행 1 passed (0.84s).
- 영구 application matrix RED: 12 failed / 3 passed. sequence 손상은 이미 거부됐고 cumulative 사례는 accounting 부재를 명시적으로 실패시켰다. 초기 fixture가 두 unit만 생성하던 문제와 잘못된 최신 output 복원 문제를 수정한 뒤 측정한 결과다.
- GREEN: lifecycle 218 + 기존 probe 1 = 219 passed (32.45s).
- affected 5 suites: 581 passed (64.16s).
- full: 1766 passed (133.68s), exit 0.
- 독립 리뷰: PASS, Critical/Important 없음, 누적 7/7 skip 0. 자체 full 1766 passed (115.65s), focused 41 passed, lifecycle 218 passed. 첫 full 시도는 timeout으로 중단됐으며 재실행 완료 결과만 통과 근거로 사용했다.
- application/repository 두 진입점과 first/middle/latest 위치에서 input-byte, sequence, legacy, 재해시된 누계, run-total 손상을 검사한다. DB/pack/exchange 무변경 및 정상 3개 과거 receipt replay를 확인했다. 직접 repository 확장 사례는 GREEN 후 추가했으므로 별도 RED를 관찰했다고 주장하지 않는다.

## 최종 판정과 경계

**R1-I1 교정 PASS.** 원래 재현 결함은 영구 회귀와 원본 probe로 교정 확인했다. Task 10 전체 acceptance, 다른 WIP 전체, Tasks 11–16, E/P/H, 실제 reboot, scale/resource, 실자료 품질, release는 승인하지 않는다. 해당 범위 미판정은 누락이 아니라 기존 범위 제한이다. 새 Critical/Important 또는 deferred Minor는 보고되지 않았다.

커밋·stage·push·배포·실자료 접근 없음. 기존 HEAD/stash 및 범위 밖 파일을 보존했다. 증거는 개발 작업트리 `.superpowers/sdd/2026-09-24-portfolio-outcome-recovery/r1-i1/`의 baseline, candidate, red/lifecycle/affected/full 로그, reviewer-report, preservation-check, progress에 있다.
