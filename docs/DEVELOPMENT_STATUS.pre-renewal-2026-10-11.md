# 개발 현황과 문서 기준

## 2026-09-24 분석 및 설계 개정

[목적 중심 설계](superpowers/specs/2026-09-24-portfolio-outcome-recovery-design.md)와 [후속 계획](superpowers/plans/2026-09-24-portfolio-outcome-recovery.md)을 추가했다. 구현은 실행하지 않았다. 다음 개발은 R1 receipt replay 교정과 독립 리뷰이며, master 버전 정합성 R0는 별도 승인 보수다. R1 PASS 뒤 기존 Tasks 11–16을 유지하되 Tasks 11/12에 조기 자원 gate E, Task 12 뒤에 작은 포트폴리오 수직 검증 P를 추가한다. 전체 홈 gate H와 별도 파일럿 승인은 유지한다.

같은 날 앞선 읽기 전용 분석에서 source-pinned 전체 테스트는 master 516 passed/1 failed, 개발본 1699 passed였다. master runtime 0.1.0/package·plugin 0.2.1/test 기대값 0.2.0 불일치를 확인했다. 합성 workspace에서 receipt_sha256 단일 필드 손상 replay가 성공하는 T10-I1을 재현했다. 7파일 후보 해시는 아래 9월 8일 값과 일치했다. 웹 build/브라우저, OS reboot, 전체 홈 파일럿, 원격 GitHub는 재검증하지 않았다. 이 수치는 문서 개정 뒤 새 runtime 검증이 아니라 앞선 분석 증거다.

아래 9월 8일 closeout은 역사적 기록으로 유지하며 PASS로 바꾸지 않는다.

## 2026-09-08 보존 기록

확인일: 2026-09-08. 이 문서는 공개 기준선, 미병합 개발본, 검증되지 않은 범위를 구분한다.
상태가 바뀌면 실제 diff와 독립 리뷰 결과를 확인한 뒤 갱신한다.

## 현재 기준선

| 범위 | 상태 |
|---|---|
| master 작업 폴더 | 0.2.1. 검증 범위는 repository subtree이며 전체 홈 의미 분석 완료를 보증하지 않음 |
| codex/portfolio-maker-0.2.2-cycle-01 개발본 | 0.2.2 기본 이력 관리·선택형 Codex 비전 구현 포함. 버전 문자열은 release 또는 v3 완료 증명이 아님 |
| home-scale semantic v3 Tasks 1–9 | 구현·독립 리뷰 승인 기록 확인 |
| v3 Task 10 선행 transition/catalog 보완 | 15ca55093cf7bcaed1182bc08af0d1e38d2a6f0e에서 승인 기록 확인 |
| v3 Task 10 결과 확정·중지·재개 | 7파일 미커밋 후보. pause 교정 통과, 독립 누적 리뷰 NEEDS_WORK: 기존 receipt replay의 저장 해시 검증 누락 1건 |
| v3 Tasks 11–16 | 캐시/무효화, full verify/finalize/apply, v2 staging 이전, 프로젝트 심화 분석, CLI·스킬·문서, 대규모 회귀 검증이 남음 |
| 전체 홈 실파일럿·release | 미완료. 전체 구현 및 독립 리뷰 뒤 별도 실행 승인 필요 |

master HEAD는 5f78cd24acfc8c070ac63ef6e17c07c92171105a, 개발본 기준 HEAD는
15ca55093cf7bcaed1182bc08af0d1e38d2a6f0e이다. GitHub 원격 상태를 이번 확인에서
재조회하지 않았으므로 이 표는 로컬 checkout과 보존 리뷰 기록의 상태다.

## 권위 문서와 Task 이름

- [개발 원칙](DEVELOPMENT_PRINCIPLES.md): 제품·승인·검증·release 경계.
- [7월 16일 계획](superpowers/plans/2026-07-16-portfolio-maker-0.2.2.md):
  #5 managed history와 #14 Codex 비전. 이 문서의 Task 10은 해당 기능 통합 검증이다.
- 7월 31일 resumable semantic v2 설계·계획: 기존 복구 계약과 호환/이전 근거.
  v2의 합성 테스트 성공을 전체 홈 분석 또는 v3 완료로 승격하지 않는다.
- 8월 2일 home-scale adaptive semantic frontier v3 설계·계획:
  현재 전체 홈 분석 복구의 권위 문서. 개발 브랜치의
  `docs/superpowers/specs/2026-08-02-portfolio-maker-home-scale-adaptive-semantic-frontier-design.md`와
  `docs/superpowers/plans/2026-08-02-portfolio-maker-home-scale-adaptive-semantic-frontier.md`에서 관리한다.
  아직 master에 병합되지 않았으므로 master에서 경로가 없다고 문서를 재생성하지 않는다.
- [9월 8일 v3 Task 10 한정 교정 계획](superpowers/plans/2026-09-08-semantic-v3-task10-pause-closeout.md):
  이번 중지 기한 결함의 계약, 구현 범위와 재리뷰 완료 조건.

상태 보고와 handoff에는 계획 날짜와 `v3 Task 10`을 함께 적는다. 서로 다른 계획의
Task 번호, 과거 PASS, 기본 0.2.2 후보와 미완성 v3의 검증을 섞지 않는다.

## Task 10 교정 결과와 현재 열린 결함

교정 전에는 pause 요청 100초, deadline 110초, receipt 진입 111초 순서에서도
receipt 1건과 used_units 1건이 확정됐다. 이번 수정은 최초 pause 시각과 기한을 DB에
저장하고 receipt transaction 안에서 검사한다. Planner의 동일 재현에서는 receipt=0,
used_units=0, used_input_bytes=0, used_active_seconds=10, checkpoint=1,
paused_user 및 lock 해제를 확인했다. 독립 Review Team도 이 finding을 CLOSED로 판정했다.

그러나 정상 receipt를 만든 후 SQLite의 receipt_sha256 한 필드만 변조해도
기존 receipt replay가 성공하는 별도 Important 결함 T10-I1이 독립 리뷰와 Planner
재현에서 확인됐다. 저장 receipt의 self-hash와 complete commit binding을 검증하는
경로가 필요하다. 이는 일관된 전체 상태 위조에 대한 보안 보장이 아니라 단일 필드
손상 탐지와 정확한 replay 계약의 누락이다.

최종 개발자 검증은 lifecycle 151 / affected 907 / full 1699 passed,
독립 검증은 lifecycle 151 / affected 514 / full 1699 passed이다.
affected 차이는 실행한 suite 목록 차이이며 실패나 환경 차이로 해석하지 않는다.
과거 48/1690/1686 등의 수치는 이전 후보·실행 기록이다. 이번에도 전체 테스트 통과가
별도 합성 변조에서 발견된 T10-I1을 상쇄하지 않는다.

독립 판정: NEEDS_WORK, Critical 0 / Important 1 / Minor 0.
후보의 seven-file cumulative diff SHA-256:
89c9b9fbff4897ca2ea81cc4cc43fa62b69208d01b133541feb997dae1770d5d.
실제 OS reboot 검증은 수행하지 않았으며 clock epoch 연속성을 보증하지 않는다.
개발 작업트리의 .superpowers/sdd/2026-08-02-portfolio-maker-home-scale-adaptive-semantic-frontier/
아래 task-10-2026-09-08-independent-review.md 및 task-10-2026-09-08-cycle-outcome.md가
이번 판정의 로컬 근거다. ignored 운영 기록을 공개 저장소 링크로 오인하지 않는다.

## 다음 순서와 운영 경계

이번 한정 수정·독립 리뷰 사이클은 NEEDS_WORK로 종료했다. 다음 권장 작업은 T10-I1의
정확한 receipt replay 검증을 보완하는 별도 한정 사이클이며, 아직 실행하지 않았다.
Task 10 독립 PASS → Tasks 11–16 → 별도 승인된 새 실파일럿 → release 판단 순서다.
Task 10 완료는 현재 범위의 구현·검증·독립 리뷰 완료를 뜻하며 이번에는 달성하지 못했다.
커밋, push, merge, tag, plugin 설치·게시, Sites 배포, 실파일럿은 이번 실행 범위에 없다.

개발 에이전트용 `graphify-out/`은 제품의 개인 포트폴리오 의미 그래프와 별개다.
현재 master 작업 폴더만 색인하며 linked worktree의 미병합 구현은 제외한다.
코드 훅은 master의 commit 뒤 AST만 갱신하고 문서 의미 분석은 필요 시 변경분만 수행한다.
따라서 최신 개발 상태는 이 문서, 개발 작업트리, 해당 리뷰를 직접 확인해야 한다.
이번 문서 개정만으로 Graphify의 문서 의미 캐시가 갱신된 것으로 간주하지 않는다.
