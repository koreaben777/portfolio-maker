<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Portfolio Maker 목적 중심 복구 Implementation Plan

> **For agentic workers:** 구현을 별도로 승인받은 뒤 executing-plans 또는 subagent-driven-development로 작업별 실행한다. 이 문서 작성은 구현 실행·Git 작업 승인이 아니다.

**Goal:** receipt 신뢰 경계를 복구하고 작은 포트폴리오 수직 검증을 거쳐 v3의 대규모 완료를 증명한다.

**Architecture:** 기존 v3 catalog/pack/ledger를 유지한다. 신규 저장과 replay 검증을 기존 repository 경계에서 통일한다. 기존 Tasks 11–16에 통합·조기 자원 gate를 추가하며 protocol과 승인 경계를 유지한다.

**Tech Stack:** Python 3.11+, 표준 라이브러리, SQLite, pytest, 기존 TypeScript/Vite renderer. 새 의존성 없음.

**Spec:** [목적 중심 설계 개정](../specs/2026-09-24-portfolio-outcome-recovery-design.md). 세부 구현은 개발 브랜치의 2026-08-02 home-scale adaptive semantic frontier 계획을 함께 읽는다.

> 2026-09-24 R1 실행 승인: goose가 구현하고 별도 독립 리뷰 서브에이전트가 누적 후보를 검토한다. 지정 세션 재사용·대체 금지 제한은 해제한다. Git·배포 승인은 포함하지 않는다.

## Global Constraints

- 현재 상태는 NEEDS_WORK이며 아래 체크박스는 모두 미실행이다. 9월 8일 종료 사이클을 다시 열거나 과거 판정을 변경하지 않는다.
- 개발 작업트리 `.worktrees/portfolio-maker-0.2.2-cycle-01`, 기준 HEAD `15ca550` 및 기존 7파일 WIP를 보존한다. 실행 전 실제 HEAD/status/diff를 재확인한다.
- master 보수와 개발본 수정은 별도 변경 단위다. 구현 위치·실행 승인을 확인하기 전 branch/worktree를 새로 만들거나 기존 파일을 덮어쓰지 않는다.
- 원본·보존 pilot·기존 active·수동 결정을 보존한다. 실제 홈/사용자 자료/GitHub 조회와 파일럿은 별도 승인 대상이다.
- source/분석/project/evidence/delivery/배포 승인은 독립적이다. 코드 수정 승인도 commit/push/tag/release를 포함하지 않는다.
- 한 교정 사이클은 한 focused fixback과 독립 누적 리뷰로 닫는다. Important가 남으면 NEEDS_WORK로 멈춘다.
- 테스트 수가 아니라 계약별 결과로 판정한다. Tasks 1–9의 기존 전제, 원래 Task 10 acceptance matrix 전체, Tasks 11–16의 모든 acceptance 조건은 그대로 필수다. E/P는 추가 gate이지 대체·생략 근거가 아니다. 과거 승인 뒤 영향을 받은 계약은 현재 후보에서 재검증한다.

## Review Focus

- 저장 receipt 한 필드 손상 후 application replay: R1에서 거부·무변경 확인.
- 정상 receipt 뒤 다른 unit이 추가된 상태의 과거 replay: R1에서 현재 pack 전체와 과거 prefix를 혼동하지 않음.
- 승인 철회/근거 변경/프로젝트 제외 후 재생성: P gate에서 오래된 claim 제거 또는 stale 차단.
- catalog 규모 증가: E gate에서 status/next/validate의 전수 decode/scan 탐지.
- 알려진 프로젝트 밖의 사례와 실제 reboot: H gate에서 holdout 평가·clock 증거를 별도 보고.

## R0. master 버전 정합성 보수 — 별도 승인 작업

**Files:** `src/portfolio_maker/__init__.py`, `tests/test_plugin_structure.py`. `pyproject.toml`, `.codex-plugin/plugin.json`, `web/portfolio/package.json`, `web/portfolio/package-lock.json`은 비교 대상으로 읽는다.

**Interfaces:** 기존 `__version__`과 배포 metadata 계약 유지. 버전 상승이나 v3 코드 이전 없음.

- [ ] 현재 master 전체 테스트와 버전 값을 기록한다. 9월 24일 관찰은 516 passed/1 failed, runtime 0.1.0/package·plugin 0.2.1/test 기대값 0.2.0이었다. 실행 시 재확인한다.
- [ ] 개발본 `test_release_versions_stay_aligned`의 비교 구조를 재사용하여 master 목표값 0.2.1의 모든 버전 표면 일치를 검증하는 RED를 만든다. plugin 이름/skill root 검증은 유지하고 낡은 0.2.0 기대값만 정리한다.
- [ ] runtime `__version__`만 현재 package 값에 맞춘다. 다른 metadata가 다르면 임의 version bump 대신 원인을 확인한다.
- [ ] root에서 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python3 -m pytest -q -p no:cacheprovider`와 `git diff --check`를 실행한다. 독립 리뷰 후 보수 후보로 보고하며 배포는 하지 않는다.

## R1. v3 Task 10 receipt replay 교정 — 다음 개발 사이클

> 2026-09-24 실행 결과: **NEEDS_WORK**. 교정 후보의 full suite 1736 passed 및 독립 리뷰 PASS 이후 최종 accounting probe에서 과거 receipt `input_bytes` 단일 필드 손상 미검출(R1-I1, Important)을 재현했다. R1/Task 10 미승인 상태를 유지하며 추가 교정 사이클은 시작하지 않았다. [최종 기록](../../reviews/2026-09-24-r1-receipt-replay-review.md). 아래 체크리스트는 최초 실행 계약을 보존한다.

> 2026-09-24 R1-I1 후속 승인: 별도 교정 사이클로 canonical envelope accounting 확장(설계 §5.1), first/middle/latest 손상 회귀 및 정상 과거 replay, 전체 테스트와 독립 누적 리뷰를 수행한다. 기존 NEEDS_WORK 기록은 이전 후보의 판정으로 보존한다. 커밋·배포·Task 11 진행 승인은 아니다.

> R1-I1 후속 결과: **한정 범위 PASS**, full1766 및 독립7/7 리뷰 통과. [교정 기록](../../reviews/2026-09-24-r1-i1-accounting-review.md). 이전 후보 NEEDS_WORK는 보존하며 Task 10 전체 승인을 의미하지 않는다.

**Files:** `src/portfolio_maker/infrastructure/sqlite_repository.py`, `src/portfolio_maker/application/semantic_lifecycle_v3.py`, `tests/test_semantic_lifecycle_v3.py`. 필요 시 기존 `tests/test_cli.py`의 safe error 회귀만 추가한다. 기존 나머지 WIP를 제거하지 않는다.

**Interfaces:** `validate_semantic_work_v3(ValidateSemanticWorkV3Request) -> ValidateSemanticWorkV3Result` 유지. 저장 row 조회는 검증된 receipt만 반환하도록 좁히고 신규 commit/replay가 같은 canonical 규칙을 사용한다. 외부 protocol 변경 없음.

- [ ] 현재 7파일 diff를 고정하고 SHA-256을 기록한다. 기준 관찰값은 `89c9b9fbff4897ca2ea81cc4cc43fa62b69208d01b133541feb997dae1770d5d`다. 다르면 과거 리뷰를 현재 승인으로 사용하지 않는다.
- [ ] 기존 테스트 helper를 이용하여 아래 application-level RED를 `tests/test_semantic_lifecycle_v3.py`에 추가한다. 필요한 import는 해당 파일에 이미 존재한다.

```python
def test_application_replay_rejects_corrupt_receipt_hash(workspace: Path) -> None:
    repository, prepared = _prepared_frontier_run(workspace)
    approve_semantic_execution(ApproveSemanticExecutionRequest(
        workspace, prepared.revision_id, prepared.proposal_sha256
    ))
    issued = next_semantic_work_v3(NextSemanticWorkV3Request(
        workspace, prepared.run_id
    ))
    paths = WorkspacePaths.from_root(workspace)
    output_path = paths.semantic_v3_exchange_output_path(
        issued.revision_id, issued.run_id
    )
    _write_valid_v3_exchange_output(issued.input_path, output_path)
    request = ValidateSemanticWorkV3Request(
        workspace=workspace, run_id=issued.run_id,
        unit_id=issued.unit_id, lease_nonce=issued.lease_nonce,
    )
    validate_semantic_work_v3(request)
    pack = paths.semantic_v3_result_pack_path(issued.revision_id, issued.run_id)
    before = pack.read_bytes()
    with sqlite3.connect(paths.db_path) as connection:
        connection.execute(
            "UPDATE semantic_v3_receipts SET receipt_sha256=? "
            "WHERE run_id=? AND unit_id=?",
            ("0" * 64, issued.run_id, issued.unit_id),
        )
    with pytest.raises(SemanticLifecycleV3Error):
        validate_semantic_work_v3(request)
    assert repository.receipt_count(issued.run_id) == 1
    assert pack.read_bytes() == before
```

- 직접 SQLite UPDATE는 합성 fixture의 test-only fault injection이다. context 종료로 손상을 commit하고 정상 application이 새 repository connection으로 다시 읽게 한다. production DB repair/migration을 뜻하지 않는다.
- 필수 결합 필드가 없는 legacy v3 row도 fail-closed 사례로 추가한다. v1/v2의 기존 명시 경로는 유지하되 자동 v3 승격·fallback은 금지한다.

- [ ] 기존 개발 worktree에서 다음 명령으로 RED를 확인한다: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../../.venv/bin/python3 -m pytest -q -p no:cacheprovider tests/test_semantic_lifecycle_v3.py -k application_replay_rejects_corrupt_receipt_hash`. 기대 결과는 손상을 거부하지 않아 발생하는 FAIL이다.
- [ ] 동일 준비 흐름에서 다음 항목을 한 번에 하나씩 바꾸는 독립 parametrized 사례로 추가한다: receipt_sha256, commit_canonical_json, commit_sha256, action, validator_version, validator_input_sha256, 전체 input_sha256, output_sha256, result_sha256, result_offset/length, revision/run/unit/lease 결합, run result_pack_length/root, receipt sequence와 checkpoint 결합. ID 변경으로 existing lookup이 miss하는 경우도 정상 신규 실행으로 fallback하지 않고 거부해야 한다. malformed bytes와 결합 불일치를 구분하여 검사한다. 손상 이후 replay 직전 DB run/counter/checkpoint와 exchange/pack을 캡처하고 거부 뒤 변경되지 않았음을 비교한다. 정상 replay·다음 unit 확정 뒤 과거 replay·orphan-tail 정상 복구도 유지한다.
- [ ] 최소 구현: 기존 row SELECT에 self-hash와 commit binding을 포함하고 typed validation을 수행한다. 기존 writer의 self-hash canonical 구성과 `_v3_receipt_binding`을 재사용한다. `semantic_v3_receipt_for_unit`의 모든 caller를 확인하여 성공 shortcut을 제거한다. 형식상 SHA 길이만 검사하거나 DB 값을 현재 요청으로 다시 쓰는 수정은 금지한다.
- [ ] application에서 검증된 receipt 확보를 mutation/cleanup보다 먼저 수행한다. 현재 run/pack 권위와 과거 prefix 포함 관계를 검증한다. 전체 pack/catalog reload는 추가하지 않는다.
- [ ] 개발 worktree에서 lifecycle suite, affected suite, full suite를 실행한다. 명령 prefix는 위와 같으며 affected 파일은 `tests/test_semantic_lifecycle_v3.py tests/test_semantic_work_lifecycle.py tests/test_semantic_index.py tests/test_sqlite_repository.py tests/test_cli.py`다. 이어 전체 `-m pytest -q -p no:cacheprovider` 및 `git diff --check`를 실행한다.
- [ ] 독립 reviewer가 기존 7파일 누적 후보 전체와 신규 회귀를 검토한다. Critical/Important 0 및 원래 Task 10 acceptance 유지 시 PASS, 아니면 NEEDS_WORK로 종료한다. 새 보고서에 exact HEAD+dirty diff hash와 명령·결과를 남긴다. commit은 별도 승인 전 수행하지 않는다.

## E. 조기 자원 gate — Tasks 11/12 리뷰에 추가

기존 Task 11 cache/invalidation, Task 12 full verify/finalize/apply를 순서대로 수행한다. 파일·인터페이스·상세 RED는 8월 2일 원계획을 유지한다.

- [ ] Task 11 리뷰 전 `tests/test_semantic_scale_v3.py`에 1,000/10,000-node 합성 catalog 비교를 추가한다. 파일 원문이나 홈을 사용하지 않고 deterministic fixture를 생성한다.
- [ ] 같은 bounded unit에 대해 cold/warm status·next·validate의 elapsed/RSS, 읽은 bytes, catalog query/decoded row 수를 기록한다. 전체 catalog/payload 크기에 따라 전수 처리하는 경로가 관찰되면 원인과 indexed/bounded 수정으로 닫기 전 다음 gate로 넘어가지 않는다. 시간 비율 하나만으로 복잡도를 증명하지 않는다.
- [ ] Task 12 리뷰 전 같은 계측을 다시 실행한다. streaming full verify/finalize의 O(N)은 허용하되 steady-state와 분리한다. 최종 1M-node와 기존 절대 자원 한계는 Task 16에서 여전히 필수다.

## P. 작은 포트폴리오 수직 검증 — Task 12 뒤, Task 13 전

**Files:** 새 `tests/test_portfolio_outcome_v3.py`; 기존 `tests/test_semantic_finalization_v3.py`, `tests/test_project_review_v3.py`, `tests/test_public_portfolio.py`, `tests/test_render_html.py`의 fixture와 구현된 application API를 재사용한다. 새로운 제품 mode나 production orchestration을 만들지 않는다.

- [ ] 2개 프로젝트와 build/cache 비프로젝트, 서로 다른 두 종류의 근거를 가진 합성 fixture를 만든다. source/project/artifact 승인을 테스트에서 명시하고 v3 prepare→receipt→full verify→finalize→apply→project 결정→profile/draft/manifest/HTML까지 호출한다. Task 12에서 확정된 API를 사용하며 임의 새 명령을 발명하지 않는다.
- [ ] 결과의 project/evidence/claim ID와 artifact manifest를 비교한다. 무근거 성과·수치가 생성되지 않고, 미지원/미완료 영역은 complete로 표시되지 않아야 한다. 주입한 의미 출력으로 통과한 테스트를 Codex 품질 검증이라 부르지 않는다.
- [ ] source 변경, 승인 철회, project 제외, 재포함을 각각 수행하고 재생성한다. 오래된 claim 제거/stale 차단, 현재 유효한 근거만 복원, restricted/open_public 분리를 assertion으로 남긴다.
- [ ] 임시 생성물에 실제 renderer build와 HTML safety 검증을 수행한다. 실제 build 의존성이 없으면 BLOCKED로 기록하고 설치 승인을 요청한다. mocked subprocess 성공을 실제 build로 보고하지 않는다.
- [ ] independent reviewer가 P를 판정한다. 실패가 가리키는 기존 task의 원인만 교정한다. P PASS 이후에도 full-home 경고와 release 차단을 유지한다.

## H. Tasks 13–16 및 새 파일럿

- [ ] Task 13 명시 v2 staging 이전, Task 14 별도 승인 deepening, Task 15 CLI/skills/package를 기존 순서로 실행·독립 검토한다. P를 migration/deepening 이후 재실행한다.
- [ ] Task 16 품질 fixture에 고정 holdout 4종(한국어 문서 중심, 독립 child, cross-directory, build/cache 비프로젝트)을 추가한다. 기대 경계와 근거를 실행 전에 고정한다. 잘못된 자동 포함·근거 없는 claim·독립 child 소실은 실패다. 실제 Codex 평가와 deterministic fixture 출력 주입을 분리 보고한다.
- [ ] 기존 1M-node·byte-budget·crash·adversarial matrix 및 full suite/web/package 검증을 수행한다. managed reader의 초과 크기와 clock epoch 변경도 점검한다. 실제 reboot를 하지 않았으면 미검증으로 표시하고 reboot-safe 주장은 하지 않는다.
- [ ] 전체 구현·독립 PASS 후 별도 승인된 fresh detached full-home pilot을 실행한다. 과거 staging을 acceptance 입력으로 재사용하지 않는다. 기존 32-receipt pause/resume, 3/3 recall, 300-sample 감사와 자원 수치를 모두 측정한다.
- [ ] 최종 보고에 P/H/릴리스 검증/미검증 항목을 따로 적는다. 릴리스 승인과 Git·게시·배포 실행은 마지막 별도 결정으로 남긴다.

## H acceptance 기록표 — 기존 기준 유지

권위 원문: 개발 브랜치 `docs/superpowers/specs/2026-08-02-portfolio-maker-home-scale-adaptive-semantic-frontier-design.md` §15–18 및 대응 계획 Task 16/마지막 fresh pilot 절. 각 행에 환경, exact candidate, 명령, 관찰값, PASS/NEEDS_WORK/BLOCKED를 기록한다. 미측정·생략은 PASS가 아니다.

| 검증 | 필수 기준 |
|---|---|
| prepare | ≤3,600초, managed disk ≤3 GiB, peak RSS ≤min(4 GiB, physical RAM 25%); 구조와 terminal baseline count 일치 |
| status | median ≤2초, max ≤5초, RSS ≤512 MiB |
| status 20회 | 각 ≤5초, 동일 process RSS growth ≤64 MiB; 별도 CLI 측정 병기 |
| next/validate | p95 ≤5초, RSS ≤512 MiB |
| full verify | ≤600초, RSS ≤1 GiB |
| finalize/project review | 각각 ≤600초, RSS ≤2 GiB |
| 합성 규모 | 기존 1M-node/3-GiB-equivalent 계측; full-state reload/숨은 cap 없음 |
| pause/resume | 32 receipt 뒤 pause, process 종료·resume, 중복/누락/재과금/output drift 없음, 기존 active 보존 |
| 알려진 프로젝트 | 보험 RAG/Portfolio Maker/PlayMCP 3/3 recall, 서로 다른 grounded signal 2종 이상 |
| 미선택 영역 감사 | 정확히 300개: relevance 100/stratified 100/deterministic-random 100; frozen digest; 놓친 high/medium project 1건이면 FAIL |
| 회귀·배포 준비 | 원래 adversarial matrix 전체, full suite, 실제 web build, package/help 검증 및 독립 PASS |
| 실파일럿 | 모든 구현·독립 PASS 후 별도 승인된 fresh detached 실행; 보존 staging 재사용 금지 |

각 gate의 실패는 NEEDS_WORK, 환경·권한 부재로 미실행이면 BLOCKED다. 합성 성능은 실파일럿 수치를 대체하지 않으며 holdout은 기존 3/3·300 감사에 추가한다.
