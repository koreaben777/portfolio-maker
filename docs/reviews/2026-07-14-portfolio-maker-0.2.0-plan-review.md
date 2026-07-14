# Portfolio Maker 0.2.0 구현 계획 자체 검토

검토일: 2026-07-14
검토 범위: 승인된 0.2.0 설계, 22개 작업 단위 구현 계획, README, Phase roadmap, GitHub Issue #13

## 결론

0.2.0 전체 개발 목표를 독립적으로 구현·검증·커밋할 수 있는 22개 작업으로 구체화했다. 이번
변경은 문서 전용이며 0.2.0 기능 구현이나 release 완료를 주장하지 않는다.

## 계획 구조

| 단계 | 작업 | 완료 산출물 | 주요 gate |
|---|---:|---|---|
| A. Semantic Index Core | 1-7 | schema, crawler, analyzer, prepare/apply revision | 정책·hash·coverage·원자성 테스트 |
| B. Project Boundary & Decisions | 8-12 | boundary, candidate v2, review/automatic, exclusion | decision matrix·artifact projection 테스트 |
| C. Codex Plugin & Skills | 13-19 | plugin manifest, 6개 skill, router | plugin validation·skill별 RED/GREEN forward test |
| D. Integration & Release | 20-22 | migration, 실제 scope smoke, 문서·version | 전체 test·build·browser·Issue checklist |

## 설계 대비 확인

- 계층형 구조와 의미 요약은 전역 500개 후보 상한을 사용하지 않는다.
- CLI는 LLM API를 호출하지 않고 safe chunk를 준비·검증하며 Codex skill이 의미 분석을 담당한다.
- `prepare -> Codex -> apply` 중 실패하거나 stale output이면 기존 active revision을 유지한다.
- 상위 맥락, 독립 하위 제품, cross-directory cluster를 서로 다른 boundary type으로 검증한다.
- automatic mode는 high와 medium을 포함하지만 evidence·delivery·deployment 승인을 대체하지 않는다.
- 자동 포함 프로젝트의 제외와 재포함은 원본·evidence·index 삭제 없이 가역적으로 수행한다.
- plugin은 6개 책임 skill과 대표 router로 나누며 MCP/App은 이번 release에 포함하지 않는다.
- 개인 근거 지식 그래프와 Google Drive는 공통 node/provenance 기반의 후속 범위로 유지한다.

## 검증 기록

계획 작성 후 다음 정적 검사를 수행한다.

```bash
rg -n 'TBD|TODO|<smoke|<workspace|<root|<project' \
  docs/superpowers/plans/2026-07-14-portfolio-maker-0.2.0.md
git diff --check
```

기대 결과는 placeholder 검색 결과 없음과 `git diff --check` 성공이다. 문서 전용 변경이므로 Python
test suite와 Vite build는 이번 단계에서 다시 실행하지 않는다. 해당 검증은 구현 계획의 Task 20-22에
필수 gate로 배치했다.

## 남은 위험과 실행 조건

- 구현은 아직 시작되지 않았으며 현재 공개 runtime은 계속 0.1.0이다.
- 각 skill의 RED/GREEN forward test는 구현 단계에서 깨끗한 agent context로 수행하고 결과를 별도
  review 문서에 남겨야 한다.
- 실제 사용자 범위 smoke test는 source 내용을 repository에 저장하지 않고 `/private/tmp` workspace와
  safe output만 사용해야 한다.
- Issue #13은 모든 completion item과 실제 scope smoke가 통과하기 전 닫지 않는다.
