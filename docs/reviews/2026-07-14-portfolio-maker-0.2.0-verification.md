# Portfolio Maker 0.2.0 검증 보고서

## 범위

- Verification HEAD: `c752c58`.
- Task 21의 smoke는 격리된 임시 workspace에서 사용자가 선택한 저장소의
  `src` 하위 트리만 대상으로 수행했다.
- 저장소 전체와 사용자 홈을 스캔하지 않았다. 보호된 legacy 사용자 데이터와
  기존 runtime workspace는 읽거나 변경하지 않았다.
- 파일 discovery/ingest 수치는 실제 선택된 repository subtree에서 얻었고,
  semantic chunk와 Codex candidate payload는 합성 fixture로 작성했다. 이 보고서에는 원본
  경로, snapshot/database locator, credential, private URL, source text를
  기록하지 않는다.

## 실행 결과

| 단계 | 결과 |
| --- | --- |
| local discovery | 41 discovered, 37 skipped |
| local ingest | 41 ingested, 0 skipped |
| semantic index | 1 safe chunk, 90 indexed nodes, 270 persisted semantic nodes |
| project review input | 34 evidence records, restricted scope |
| candidate / linked evidence | 1 candidate, 30 linked evidence records |
| automatic composition | 1 project, 0 review-required, 0 excluded |
| medium exclusion / re-inclusion | 1 / 1; evidence and semantic-node counts unchanged |
| candidate confidence | 0 high, 1 medium, 0 low |
| project decisions | 1 manually approved, 0 excluded, 0 unassigned |
| final persisted counts | 270 semantic nodes, 36 evidence items, 36 claims, 1 project, 30 project links |
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

The following results are tied to the verification sequence above:

| Command | Result |
| --- | --- |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_semantic_acceptance.py tests/test_semantic_index.py tests/test_project_composition.py tests/test_static_site.py tests/test_render_html.py` | 65 passed in 2.22s |
| `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q` | 515 passed in 12.12s |
| `(cd web/portfolio && npm run build)` | Vite 6.4.3 build passed |
| static output validator plus canonical HTML safety assertions | passed; one inlined stylesheet, no `<link>`, no `fetch(` |
| loopback browser interaction checks | passed; populated project path and 390px mobile path |
| `git diff --check` | exit 0 |
