# Portfolio Maker 0.2.0 검증 보고서

## 범위

- Task 21의 smoke는 격리된 임시 workspace에서 사용자가 선택한 저장소의
  `src` 하위 트리만 대상으로 수행했다.
- 저장소 전체와 사용자 홈을 스캔하지 않았다. 보호된 legacy 사용자 데이터와
  기존 runtime workspace는 읽거나 변경하지 않았다.
- 모든 검증 값과 후보 내용은 합성 fixture에서 얻었으며, 이 보고서에는 원본
  경로, snapshot/database locator, credential, private URL, source text를
  기록하지 않는다.

## 실행 결과

| 단계 | 결과 |
| --- | --- |
| local discovery | 41 discovered, 37 skipped |
| local ingest | 41 ingested, 0 skipped |
| semantic index | 1 safe chunk applied |
| project review input | 35 evidence records |
| candidate / linked evidence | 1 candidate, 31 linked evidence records |
| automatic composition | 1 project, 0 review-required, 0 excluded |
| medium exclusion / re-inclusion | 1 / 1; evidence and semantic-node counts unchanged |
| final persisted counts | 270 semantic nodes, 36 evidence items, 36 claims, 1 project, 31 project links |
| master profile | 1 approved project, 34 claims |
| draft | 1 approved project |
| HTML / public manifest | generated successfully with restricted delivery scope |

The project was temporarily excluded and then included again. The final
projection contained the approved project and retained the evidence pool.
No semantic project was inferred from a source file without the explicit
candidate and approval boundary.

## Static and browser verification

- The copied tracked Sites project passed its normal Vite build.
- The static output validator passed; canonical HTML contained one inlined
  stylesheet, no external stylesheet link, and no runtime fetch.
- Through loopback HTTP, the page rendered the expected heading, project
  filter, project detail, and 30 timeline records.
- The project filter became selected on click and the project detail control
  opened with Enter while focused.
- At a 390px viewport, document width equaled the viewport width with no
  horizontal overflow.
- A `prefers-reduced-motion` rule was present in the inlined stylesheet.
- The rendered HTML contained none of the checked internal locator, database,
  credential, private URL, or runtime-fetch markers.

## Safety and residual risk

- This is a repository-source smoke, not a full user-home smoke. It does not
  establish behavior for other user directories, large projects, or live
  GitHub responses.
- No Sites hosting or public deployment was attempted.
- Browser checks covered the populated project path; the empty-manifest path
  remains covered by the focused test suite rather than this smoke workspace.

## Verification commands

The focused and full Python suites, Sites build, static validator, browser
checks, and whitespace checks were run before this report was committed.
