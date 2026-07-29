# Portfolio Maker 0.2.2 Task 10 Integration Verification

## Scope

- Candidate parent: `395d2a1b23d76fb1109e27d68a9e3851683f730b`
- Candidate range: `395d2a1b23d76fb1109e27d68a9e3851683f730b..HEAD`
- Exact candidate commit is recorded by the Task 10 implementation report after
  the bounded verification commit is created.
- This record verifies managed-history and Codex-native vision isolation. It
  does not declare the 0.2.2 release complete or close a tracking issue.

Two independent read-only audits preceded the test changes:

- The cross-boundary audit found no production blocker and identified missing
  integration assertions for the exact history candidate set, artifact scopes,
  noneligible vision states, and text-only capability-unavailable output.
- The current-session capability audit found a direct image-inspection surface.
  It found no directly exercisable PDF, document, spreadsheet, or presentation
  understanding surface within the allowed offline boundary.

## Permanent Verification

The new integration assertions were GREEN on first meaningful execution after
one test-fixture setup correction. No production source change was required.

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest \
  -p no:cacheprovider \
  tests/test_history_lifecycle.py::test_history_plan_selects_only_expired_unreferenced_managed_history \
  tests/test_public_portfolio.py::test_included_vision_respects_restricted_and_open_public_artifact_scopes \
  tests/test_public_portfolio.py::test_status_only_vision_request_keeps_text_only_artifacts_self_contained \
  -q
6 passed
```

The focused checks prove:

- history planning selects only an expired unreferenced snapshot, an excluded
  finished analysis, and the corresponding eligible finished requests;
- original sources, approval/policy files, generated artifacts, semantic-index
  material, current/referenced snapshots, incomplete requests, and
  pending/included analyses remain outside the plan;
- an explicitly included, medium-confidence, still-bound vision summary reaches
  the restricted master profile, while its exact derived evidence ID reaches
  draft, manifest, and HTML only through an approved semantic project and safe
  local evidence presentation;
- the same local vision project is absent from `open_public` manifest and HTML;
- `unsupported`, `unavailable`, `failed`, and `cancelled` status-only requests
  leave no vision analysis or evidence while text-only artifacts still build;
- request/result private metadata, source locators, managed paths, hashes,
  adapters, raw observations, database names, and runtime fetch expressions do
  not appear in generated output.

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest \
  -p no:cacheprovider tests/test_history_lifecycle.py \
  tests/test_vision_analysis.py tests/test_profile_and_portfolio.py \
  tests/test_public_portfolio.py -q
292 passed
```

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest \
  -p no:cacheprovider tests/test_history_lifecycle.py \
  tests/test_vision_analysis.py tests/test_cli.py tests/test_plugin_structure.py -q
275 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest \
  -p no:cacheprovider -q
853 passed
```

## Current-Session Synthetic Format Matrix

All fixtures were harmless generated files in a disposable private temporary
workspace. Every case used a separate explicit confirmation and one selected
approved local regular file. The matrix recorded only safe fields.

| Format class | Bytes | Prepared | Applied | Native outcome | Initial review | Decision |
| --- | ---: | --- | --- | --- | --- | --- |
| ordinary image | 1,513 | pending | completed | supported | pending | included |
| multi-page text PDF | 1,800 | pending | unavailable | unavailable | none | none |
| multi-page scanned PDF | 8,365 | pending | unavailable | unavailable | none | none |
| document | 36,635 | pending | unavailable | unavailable | none | none |
| spreadsheet | 4,861 | pending | unavailable | unavailable | none | none |
| presentation | 28,263 | pending | unavailable | unavailable | none | none |
| deliberately unsupported regular file | 25 | pending | unsupported | unsupported | none | none |

The ordinary image was directly inspected in the current Codex App session.
Its completed result began in `pending`, and an explicit `included` decision
was exercised. Only one format had a supported completed result, so a second
supported-result exclusion was not available. The other formats were not
treated as understood merely because fixture-creation libraries existed.

After the matrix, restricted profile, draft, manifest, and self-contained HTML
all built with the included synthetic image project. A separate status-only
`unavailable` text-source smoke also built all four artifacts with no vision
analysis, evidence, or private request/result material.

## Safety And Residuals

- No user data, external API, network service, upload, OCR package, PDF/Office
  converter, manual archive parsing, hosting, deployment, or remote mutation
  was used.
- Public and restricted artifact policies remained distinct. Local vision
  evidence was not promoted into `open_public`.
- The shared managed-file reader can read a tampered managed file to EOF before
  hash rejection. The file is still rejected and produces no claim. This is the
  accepted Task 8 residual.
- Injected untyped programming errors are not converted into the fixed Vision
  CLI error. Concrete typed production failures remain controlled. This is the
  accepted Task 9 operational limitation.
- Cycle 12U independently closed database-confidence tampering and malformed
  direct included insertion with atomic fail-closed behavior.
- Live user documents, live private repositories, native PDF/Office
  interpretation, hosting, and deployment remain untested here.

## Task 11 Local Release Candidate Preparation

This branch prepares `0.2.2` as a local release candidate. It has not been
pushed, tagged, published, deployed, or used to mutate a GitHub issue.

All six version surfaces report `0.2.2`:

- Python project metadata;
- Python runtime `__version__`;
- Codex plugin manifest;
- web package metadata;
- package-lock top level;
- package-lock root package.

The release-preparation checks produced the following results:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest \
  -p no:cacheprovider tests/test_cli.py tests/test_plugin_structure.py -q
64 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/bin/python -m pytest \
  -p no:cacheprovider -q
854 passed

(cd web/portfolio && npm ci && npm run build)
Vite production build passed

/opt/anaconda3/bin/python -m build
source distribution and wheel built successfully

wheel METADATA
Name: portfolio-maker
Version: 0.2.2

fresh disposable venv wheel installation
installed metadata: 0.2.2
installed runtime: 0.2.2
installed CLI help: history and vision command groups present
```

The installed help includes `history-policy`, `history-plan`,
`history-export`, `history-apply`, `prepare-vision-analysis`,
`apply-vision-analysis`, `list-vision-analyses`, and
`set-vision-analysis-state`.

The Python build emitted the existing setuptools deprecation warning for the
TOML table form of `project.license`. Dependency installation for the static
renderer also reported two high-severity npm audit advisories. Neither warning
changed the candidate lockfile or runtime behavior; both remain release
maintenance risks rather than evidence of a successful remote release.

## Factual GitHub Completion-Note Drafts

These notes are drafts only. They were not posted and do not close remote
issues.

### Issue #5 Draft

> Portfolio Maker 0.2.2의 local release candidate에 managed history
> lifecycle을 구현했습니다. 정책이 없으면 모든 이력을 보존하며, immutable dry-run
> plan, workspace 밖의 비어 있는 backup directory로의 export와 exact receipt,
> private plan의 정확한 확인 문구, same-filesystem quarantine, SQLite transaction,
> rollback, resume/recover, `vacuum_pending` 재개를 검증했습니다. 대상은
> Portfolio Maker가 관리하는 snapshot·SQLite 이력뿐이며 원본 사용자 source는
> 이동·복사·삭제하지 않습니다. Task 10 통합 검증과 Task 11 후보 검증은 통과했지만,
> 이 메모는 아직 push·tag·publish 또는 원격 Issue 종료를 뜻하지 않습니다.

### Issue #14 Draft

> Portfolio Maker 0.2.2의 local release candidate에 선택형 Codex-native
> vision workflow를 구현했습니다. 사용자가 승인된 local source 하나를 선택하고
> Codex 공유를 명시 확인한 뒤에만 private request를 만들며, closed-schema 결과는
> `pending` 검토에서 시작합니다. 명시적으로 포함된 medium/high 결과만 기존
> source·artifact policy를 통과해 derived evidence가 될 수 있고, status-only
> unsupported/unavailable/failed/cancelled 및 low-confidence 결과는 evidence가
> 되지 않습니다. Task 10의 current-session 합성 검증에서는 일반 이미지 한 건만
> supported였고 PDF·문서·스프레드시트·프레젠테이션은 unavailable였습니다.
> 원본 locator와 private request/result metadata는 artifact에 포함하지 않았습니다.
> 이 메모는 아직 push·tag·publish 또는 원격 Issue 종료를 뜻하지 않습니다.

Issue #8 remains open for an independently deployable OCR, renderer, and
extractor surface. The current Codex-native adapter does not claim native
PDF/Office support, a local OCR engine, bulk source reading, fallback upload,
or a format-independent extraction runtime.
