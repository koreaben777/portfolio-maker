# Portfolio Maker Home-Scale Adaptive Semantic Frontier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 승인된 전체 홈 디렉터리를 구조·로컬 기본 분석으로 전수 보존하면서, bounded 적응형 Codex 프런티어와 선택 프로젝트 deepening을 실제 운영 가능한 `codex-semantic-v3`로 구현한다.

**Architecture:** v2 호환 dispatcher는 유지하고, v3는 sealed catalog와 prepare pack, `portfolio.db`의 transactional lifecycle ledger, run별 append-only result pack 및 bounded exchange slot으로 분리한다. prepare/finalize/apply/migrate는 streaming O(N), status는 O(1), next/validate는 한 bounded unit과 최대 64개 dependency만 읽는다. 프로젝트 경계 발견은 전수 로컬 신호 위의 결정론적 adaptive frontier이며 미선택 노드는 deferred truth로 보존한다.

**Tech Stack:** Python 3.11+, 표준 라이브러리(`sqlite3`, `hashlib`, `os`, `resource`, `struct`), pytest 8+, setuptools, 기존 Codex plugin Markdown skills, Node.js LTS/Vite 정적 포트폴리오 회귀 검증

## Global Constraints

- 권위 설계: `docs/superpowers/specs/2026-08-02-portfolio-maker-home-scale-adaptive-semantic-frontier-design.md` (`2d2bc25`, Review Team PASS).
- 승인 범위의 모든 허용 node를 structural catalog와 terminal local-baseline coverage에 보존한다. 전역 file/node top-K 또는 암묵적 제외를 추가하지 않는다.
- prepare gate: 3,600초, managed disk 3 GiB, peak RSS `min(4 GiB, physical RAM의 25%)`.
- 기본 frontier: 512 units / 7,200초 / 128 MiB; orientation 64/16 MiB/15분, expansion 384/96 MiB/90분, audit 64/16 MiB/15분.
- project deepening: project별 256 units / 3,600초 / 64 MiB; 모든 run은 16-unit checkpoint를 사용한다.
- per-unit input/output hard limit는 각각 262,144 bytes, execution hard limit는 300초, dependency와 expansion page는 각각 최대 64개다.
- budget 소진은 `paused_budget`이며 completion, exclusion 또는 자동 증액이 아니다.
- 기본 status median ≤2초/max ≤5초/RSS≤512 MiB, 20회 반복 각 ≤5초/RSS growth≤64 MiB, next/validate p95≤5초/RSS≤512 MiB, full verify≤600초/RSS≤1 GiB, finalize/project review≤600초/RSS≤2 GiB다.
- SHA-256/root chain은 accidental corruption, partial write, stale/mismatched state만 탐지한다. same-user coherent rewrite 방어를 주장하지 않는다.
- source approval, semantic 실행 승인, project 포함, evidence/artifact 선택, delivery, Sites, Git, push/tag/release/deploy 권한을 자동 승격하지 않는다.
- raw locator, private URL, credential/token과 excluded content는 일반 status, safe review, manifest/HTML에 노출하지 않는다. 승인된 private repository 이름 display text만 기존 restricted/public artifact policy에 따라 허용한다.
- 원본 source, 기존 active v1/v2 revision, 보존 pilot, manual project decision/exclusion을 수정·삭제·자동 migration하지 않는다.
- 각 Task는 Developer가 RED→GREEN→focused/full-affected 검증→의도한 파일만 commit하고, Planner가 exact HEAD를 확인한 뒤 독립 Review Team PASS를 받아야 다음 Task로 진행한다. 한 review cycle에서 한 focused fixback만 허용한다.
- 원격 push, tag, release, plugin publish, Sites 배포와 fresh full-home pilot은 전체 구현·독립 review PASS 전 금지한다.

---

## File Structure

### 새 파일

| 파일 | 단일 책임 |
|---|---|
| `src/portfolio_maker/domain/semantic_frontier.py` | v3 protocol enum, budget, coverage, run/action/status dataclass |
| `src/portfolio_maker/infrastructure/semantic_pack.py` | `pm-pack-v1` append/read/seal/root와 committed-prefix recovery |
| `src/portfolio_maker/infrastructure/semantic_catalog.py` | revision catalog와 run-scoped deferred index의 write/query/seal/full streaming root |
| `src/portfolio_maker/infrastructure/semantic_source_reader.py` | no-follow 단일 descriptor full hash와 bounded slice 수집 |
| `src/portfolio_maker/application/semantic_frontier_v3.py` | seed, fairness lane, representative, expansion, audit sample selector |
| `src/portfolio_maker/application/semantic_quality_v3.py` | v3 Codex output schema, grounding, action, dependency validation |
| `src/portfolio_maker/application/semantic_prepare_v3.py` | streaming crawl/baseline sink, manifest/proposal, atomic publication |
| `src/portfolio_maker/application/semantic_lifecycle_v3.py` | approval, bounded status/next/validate, result pack, pause/resume/checkpoint |
| `src/portfolio_maker/application/semantic_finalization_v3.py` | full verify, result-driven finalize, v3 apply/import/activation |
| `src/portfolio_maker/application/semantic_migration_v3.py` | explicit resumable v2-staging→v3 managed-state migration |
| `src/portfolio_maker/application/semantic_deepening_v3.py` | project-bound proposal, derived run, inactive overlay와 적용 gate |
| `src/portfolio_maker/infrastructure/semantic_exchange.py` | workspace mutex 뒤 run-scoped bounded input/output slot 원자 publication |
| `tests/test_semantic_pack_v3.py` | pack framing, root, corruption, orphan tail |
| `tests/test_semantic_catalog_v3.py` | catalog schema, streaming order, seal/query/tamper |
| `tests/test_semantic_frontier_v3.py` | deterministic selection, fairness, actions, audit freeze |
| `tests/test_semantic_prepare_v3.py` | 전수 streaming prepare, publication/failure/resource gate |
| `tests/test_semantic_lifecycle_v3.py` | approval/status/next/validate/result/pause-resume/concurrency |
| `tests/test_semantic_finalization_v3.py` | full verify/finalize/apply/atomic recovery |
| `tests/test_semantic_migration_v3.py` | legacy compatibility와 explicit migration |
| `tests/test_semantic_deepening_v3.py` | project authority, scope, cancel/replay/inactive overlay |
| `tests/test_semantic_scale_v3.py` | env-gated 1M-node/3-GiB-equivalent instrumentation과 latency/RSS gate |

### 수정 파일

| 파일 | 변경 책임 |
|---|---|
| `src/portfolio_maker/domain/semantic_models.py` | legacy revision/node가 v3 protocol metadata를 lossless하게 읽도록 확장 |
| `src/portfolio_maker/domain/semantic_work.py` | v2 type은 보존하고 shared safe identifier helpers만 재사용 |
| `src/portfolio_maker/infrastructure/semantic_crawler.py` | event/sink streaming API 추가, 기존 tuple wrapper 유지 |
| `src/portfolio_maker/infrastructure/semantic_analyzers.py` | coverage와 inspected range/sample identity를 명시 |
| `src/portfolio_maker/infrastructure/sqlite_repository.py` | additive v3 lifecycle schema와 원자 repository methods |
| `src/portfolio_maker/workspace.py` | revision/run/catalog/pack/exchange/failure 경로 helper |
| `src/portfolio_maker/application/models.py` | v3 prepare/status/approval/run/migration/deepening result model |
| `src/portfolio_maker/application/semantic_index.py` | v2 보존, prepare/apply protocol dispatcher |
| `src/portfolio_maker/application/semantic_work.py` | v2 보존, status/next/validate/finalize protocol dispatcher |
| `src/portfolio_maker/application/project_review_v3.py` | active v2/v3 dispatch와 `project_discovery_ready` gate |
| `src/portfolio_maker/application/project_boundary.py` | boundary-critical coverage/counter-signal candidate field 검증 |
| `src/portfolio_maker/application/project_decisions.py` | medium 자동 포함 fail-closed 조건 |
| `src/portfolio_maker/adapters/cli.py` | v3 flags 및 approve/pause/resume/migrate/deepening command |
| `tests/test_sqlite_repository.py` | additive migration, lifecycle atomicity, legacy row 보존 |
| `tests/test_semantic_crawler.py` | streaming event order와 timeout/watchdog 회귀 |
| `tests/test_semantic_analyzers.py` | complete/sampled/unsupported/unreadable/failed coverage |
| `tests/test_semantic_index.py` | v2/v3 prepare/apply dispatch와 active 보존 |
| `tests/test_semantic_work_lifecycle.py` | v2 behavior가 변하지 않는 호환 회귀 |
| `tests/test_project_review_v3.py` | v3 active binding과 discovery-ready gate |
| `tests/test_project_decisions.py` | medium 자동 포함 counter-signal 차단 |
| `tests/test_cli.py` | 전체 새 command/flag, safe output, controlled error |
| `tests/test_plugin_structure.py` | router/semantic skill의 v3 approval·budget·pause 계약 |
| `skills/portfolio-semantic-index/SKILL.md` | v3 prepare→approve→bounded loop→audit→finalize/apply workflow |
| `skills/portfolio-maker/SKILL.md` | safe status와 별도 deepening approval routing |
| `.agents/skills/portfolio-maker/SKILL.md` | repository fallback v3 sequence |
| `README.md` | 현재 사용자 tutorial, migration, pause/resume, deepening, resource gate |
| `docs/DEVELOPMENT_PRINCIPLES.md` | v3 completion/authority/cache/pilot 원칙 |
| `docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md` | 구현 현황, v3 남은 gate와 release/pilot 상태 |
| `pyproject.toml` | `semantic_scale` marker 등록 |

## Shared Interface Contract

이 이름과 type은 Task 사이의 고정 계약이다. 후속 Task에서 다른 이름을 만들지 않는다.

```python
from dataclasses import dataclass
from enum import StrEnum

SEMANTIC_PROTOCOL_V3 = "codex-semantic-v3"
MAX_UNIT_BYTES = 262_144
MAX_DEPENDENCIES = 64
CHECKPOINT_UNITS = 16

class BaselineCoverage(StrEnum):
    COMPLETE = "complete"
    SAMPLED = "sampled"
    UNSUPPORTED = "unsupported"
    UNREADABLE = "unreadable"
    FAILED = "failed"

class FrontierPhase(StrEnum):
    ORIENTATION = "orientation"
    EXPANSION = "expansion"
    AUDIT = "audit"
    DEEPENING = "deepening"

class FrontierAction(StrEnum):
    ACCEPT_BOUNDARY = "accept_boundary"
    EXPAND_CHILDREN = "expand_children"
    STOP_NON_PROJECT = "stop_non_project"
    DEFER_ON_DEMAND = "defer_on_demand"

class SemanticRunState(StrEnum):
    PREPARED = "prepared"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    PAUSED_USER = "paused_user"
    PAUSED_BUDGET = "paused_budget"
    BLOCKED = "blocked"
    CONVERGED = "converged"
    FINALIZING = "finalizing"
    FINALIZED = "finalized"
    FAILED = "failed"

class SemanticLeaseState(StrEnum):
    MATERIALIZING = "materializing"
    ISSUED = "issued"
    EXPIRED = "expired"

class SemanticUnitState(StrEnum):
    READY = "ready"
    LEASED = "leased"
    VALIDATED = "validated"
    DEFERRED = "deferred"
    ERROR = "error"

class DeepeningOverlayState(StrEnum):
    PROPOSED = "proposed"
    INACTIVE_REVIEW_REQUIRED = "inactive_review_required"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"

@dataclass(frozen=True)
class SemanticBudget:
    max_units: int
    max_input_bytes: int
    max_wall_seconds: int
    max_unit_input_bytes: int = MAX_UNIT_BYTES
    max_unit_output_bytes: int = MAX_UNIT_BYTES
    max_unit_wall_seconds: int = 300
    checkpoint_units: int = CHECKPOINT_UNITS

@dataclass(frozen=True)
class SemanticPhaseBinding:
    phase: FrontierPhase
    hard_budget: SemanticBudget
    estimated_units: int
    estimated_input_bytes: int
    estimated_wall_seconds: int

@dataclass(frozen=True)
class SemanticPlannedUnit:
    unit_id: str
    phase: FrontierPhase
    target_node_ids: tuple[str, ...]
    dependency_unit_ids: tuple[str, ...]
    estimated_input_bytes: int
    priority: int
    fairness_lane: str

@dataclass(frozen=True)
class SemanticDeferredNode:
    node_id: str
    reason: str
    node_digest: str
    cursor_json: str

@dataclass(frozen=True)
class SemanticDeferredAuditRow:
    node_id: str
    reason: str
    node_digest: str
    cursor_json: str
    cursor_order: int
    source_id: str
    coverage_stratum: str
    relevance_rank: int
    selector_version: str
    selector_feature_digest: str

@dataclass(frozen=True)
class SemanticRuntimeDeferredRow:
    audit: SemanticDeferredAuditRow
    source_transition_unit_id: str
    created_sequence: int

@dataclass(frozen=True)
class SemanticFrontierPlan:
    run_id: str
    revision_id: str
    policy_sha256: str
    selector_version: str
    target_projection_contract: str
    input_schema_version: str
    output_schema_version: str
    validator_version: str
    purpose: str
    units: tuple[SemanticPlannedUnit, ...]
    deferred_count: int
    deferred_root: str
    deferred_cursor_root: str

@dataclass(frozen=True)
class SemanticDeepeningPlan:
    run_id: str
    revision_id: str
    project_id: str
    authority_root: str
    parent_run_id: str
    parent_proposal_sha256: str
    parent_frontier_checkpoint_root: str
    project_boundary_sha256: str
    source_policy_sha256: str
    selector_version: str
    catalog_root: str
    private_context_scope: str
    evidence_policy_sha256: str
    target_projection_contract: str
    input_schema_version: str
    output_schema_version: str
    validator_version: str
    purpose: str
    units: tuple[SemanticPlannedUnit, ...]
    deferred_count: int
    deferred_root: str
    deferred_cursor_root: str

@dataclass(frozen=True)
class SealedProposalAuthority:
    revision_id: str
    source_approval_sha256: str
    source_policy_sha256: str
    manifest_sha256: str
    catalog_root: str
    safe_source_label: str
    private_context_scope: str
    structural_node_count: int
    baseline_coverage_counts: tuple[tuple[str, int], ...]
    selector_version: str
    lifecycle_genesis_root: str
    target_projection_contract: str
    input_schema_version: str
    output_schema_version: str
    validator_version: str
    root: str

@dataclass(frozen=True)
class DeepeningAuthorityContext:
    base: SealedProposalAuthority
    parent_run_id: str
    parent_proposal_sha256: str
    parent_checkpoint_root: str
    project_id: str
    project_boundary_sha256: str
    evidence_policy_sha256: str
    purpose: str
    root: str

@dataclass(frozen=True)
class PackRecordRef:
    offset: int
    length: int
    sha256: str
```

---

### Task 1: v3 Domain Contract and Safe Workspace Paths

**Files:**
- Create: `src/portfolio_maker/domain/semantic_frontier.py`
- Modify: `src/portfolio_maker/domain/semantic_models.py`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/workspace.py`
- Modify: `tests/test_semantic_models.py`
- Modify: `tests/test_semantic_work_models.py`

**Interfaces:**
- Consumes: existing `stable_source_id`, `stable_node_id`, `RevisionStatus`, `WorkspacePaths`.
- Produces: the Shared Interface Contract plus `FRONTIER_BUDGET`, `DEEPENING_BUDGET`,
  `SemanticCompletion`, `SemanticFrontierPlan`, `SemanticExecutionProposal`,
  `SemanticProposalEnvelope`,
  `SemanticStatusSnapshot`; safe v3 path helpers used by every later Task.

- [ ] **Step 1: Write failing enum, budget, completion, and path tests**

```python
def test_v3_default_budgets_and_completion_states_are_exact(workspace: Path) -> None:
    assert FRONTIER_BUDGET == SemanticBudget(512, 128 * 1024**2, 7_200)
    assert DEEPENING_BUDGET == SemanticBudget(256, 64 * 1024**2, 3_600)
    assert FRONTIER_PHASE_HARD_BUDGETS == (
        (FrontierPhase.ORIENTATION, ORIENTATION_BUDGET),
        (FrontierPhase.EXPANSION, EXPANSION_BUDGET),
        (FrontierPhase.AUDIT, AUDIT_BUDGET),
    )
    completion = SemanticCompletion(
        structural_complete=True,
        local_baseline_complete=True,
        planned_receipts_complete=True,
        frontier_converged=False,
        paused_budget=True,
        project_discovery_ready=False,
        deepening_complete=False,
    )
    assert completion.paused_budget is True
    assert completion.project_discovery_ready is False

def test_v3_workspace_paths_are_revision_and_run_scoped(workspace: Path) -> None:
    paths = WorkspacePaths.from_root(workspace)
    assert paths.semantic_v3_catalog_path("rev-1").name == "catalog.sqlite"
    assert paths.semantic_v3_pack_path("rev-1", "structural").name == "structural.pm-pack"
    assert paths.semantic_v3_result_pack_path("rev-1", "run-1").name == "results.pm-pack"
    assert paths.semantic_v3_deepening_deferred_index_path("rev-1", "run-1").name == "deferred.sqlite"
    temp = paths.semantic_v3_deepening_deferred_temp_path("rev-1", "run-1")
    assert temp.parent == paths.semantic_v3_deepening_deferred_index_path("rev-1", "run-1").parent
    assert temp.name == "deferred.sqlite.preparing"
    with pytest.raises(ValueError):
        paths.semantic_v3_run_dir("rev-1", "../escape")
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python -m pytest -q tests/test_semantic_models.py tests/test_semantic_work_models.py`

Expected: import/attribute failures for the new v3 types and paths; no unrelated failure.

- [ ] **Step 3: Add the exact domain types and constants**

```python
FRONTIER_BUDGET = SemanticBudget(512, 128 * 1024**2, 7_200)
ORIENTATION_BUDGET = SemanticBudget(64, 16 * 1024**2, 15 * 60)
EXPANSION_BUDGET = SemanticBudget(384, 96 * 1024**2, 90 * 60)
AUDIT_BUDGET = SemanticBudget(64, 16 * 1024**2, 15 * 60)
DEEPENING_BUDGET = SemanticBudget(256, 64 * 1024**2, 3_600)
FRONTIER_PHASE_HARD_BUDGETS = (
    (FrontierPhase.ORIENTATION, ORIENTATION_BUDGET),
    (FrontierPhase.EXPANSION, EXPANSION_BUDGET),
    (FrontierPhase.AUDIT, AUDIT_BUDGET),
)

@dataclass(frozen=True)
class SemanticCompletion:
    structural_complete: bool
    local_baseline_complete: bool
    planned_receipts_complete: bool
    frontier_converged: bool
    paused_budget: bool
    project_discovery_ready: bool
    deepening_complete: bool

@dataclass(frozen=True)
class SemanticExecutionProposal:
    run_id: str
    revision_id: str
    approval_kind: str
    source_approval_sha256: str
    manifest_sha256: str
    policy_sha256: str
    selector_version: str
    catalog_root: str
    safe_source_label: str
    private_context_scope: str
    authority_root: str
    plan_sha256: str
    parent_checkpoint_root: str
    target_projection_contract: str
    input_schema_version: str
    output_schema_version: str
    validator_version: str
    purpose: str
    structural_node_count: int
    baseline_coverage_counts: tuple[tuple[str, int], ...]
    phase_bindings: tuple[SemanticPhaseBinding, ...]
    audit_selector_rule: str
    deferred_semantics: str
    budget: SemanticBudget
    project_id: str | None = None
    project_boundary_sha256: str | None = None
    parent_proposal_sha256: str | None = None
    evidence_policy_sha256: str | None = None

@dataclass(frozen=True)
class SemanticProposalEnvelope:
    proposal: SemanticExecutionProposal
    proposal_sha256: str

@dataclass(frozen=True)
class SemanticRunAuthority:
    revision_id: str
    run_id: str
    source_approval_sha256: str
    source_policy_sha256: str
    manifest_sha256: str
    catalog_root: str
    proposal_sha256: str
    approval_receipt_sha256: str
    checkpoint_root: str
    unit_queue_root: str
    dependency_root: str
    budget_binding_sha256: str
    root: str

@dataclass(frozen=True)
class PrepareSemanticIndexRequest:
    workspace: Path
    root: Path
    protocol: str = SEMANTIC_PROTOCOL_V3
    chunk_size: int = 100
    directory_timeout_seconds: float = 30.0
    directory_timeout_budget_seconds: float = 120.0
```

`PrepareSemanticIndexResult`, `SemanticIndexStatusResult`, `NextSemanticWorkResult`,
`ValidateSemanticWorkResult`, and `FinalizeSemanticIndexResult` gain an explicit `protocol`
field while retaining legacy-compatible defaults at v2 dispatch boundaries. The v3 prepare result
also returns the safe `run_id`, `proposal_sha256`, structural/baseline aggregates and phase bindings
needed to display the durable proposal without selecting it by revision alone.

`proposal_sha256` is the SHA-256 of canonical JSON bytes of `SemanticExecutionProposal` only; the
digest is outside those bytes in `SemanticProposalEnvelope`, so no self-referential hash exists. The
proposal digest covers every proposal field above. For the frontier proposal, audit approval binds the
selector rule/version, sealed catalog root, private-context scope and audit phase budget; the exact
ordered 300-node sample/population digests are frozen only after expansion and committed to the
checkpoint as specified in Task 6.
Each `SemanticPhaseBinding` contains both the dynamic estimate and immutable hard budget. Frontier
proposal phase order and hard budgets must equal `FRONTIER_PHASE_HARD_BUDGETS`; a deepening proposal
contains exactly one `DEEPENING` binding with `DEEPENING_BUDGET`. Unused units/bytes/time in one phase
cannot be reassigned to another phase without a new proposal digest and explicit approval.

- [ ] **Step 3a: Add the completion/run/lease truth-table tests**

```python
def test_run_and_lease_states_are_disjoint() -> None:
    assert not ({state.value for state in SemanticRunState} & {state.value for state in SemanticLeaseState})

def test_frontier_converged_and_paused_budget_cannot_both_be_true() -> None:
    with pytest.raises(ValueError, match="converged.*paused_budget"):
        SemanticCompletion(
            structural_complete=True,
            local_baseline_complete=True,
            planned_receipts_complete=True,
            frontier_converged=True,
            paused_budget=True,
            project_discovery_ready=False,
            deepening_complete=False,
        )
```

- [ ] **Step 4: Add safe path helpers without creating directories on read**

```python
def semantic_v3_run_dir(self, revision_id: str, run_id: str) -> Path:
    _validate_revision_id(revision_id)
    _validate_revision_id(run_id)
    return self.semantic_revision_raw_dir(revision_id) / "runs" / run_id

def semantic_v3_result_pack_path(self, revision_id: str, run_id: str) -> Path:
    return self.semantic_v3_run_dir(revision_id, run_id) / "results.pm-pack"
```

Add equivalent helpers for `manifest.v3.json`, `catalog.sqlite`, prepare packs, exchange input/output, and private failure reviews. Keep legacy properties unchanged.

- [ ] **Step 5: Run focused and legacy model tests**

Run: `python -m pytest -q tests/test_semantic_models.py tests/test_semantic_work_models.py tests/test_sqlite_repository.py::test_workspace_paths_create_expected_directories`

Expected: PASS.

- [ ] **Step 6: Commit Task 1 only**

```bash
git add src/portfolio_maker/domain/semantic_frontier.py src/portfolio_maker/domain/semantic_models.py src/portfolio_maker/application/models.py src/portfolio_maker/workspace.py tests/test_semantic_models.py tests/test_semantic_work_models.py
git commit -m "feat: define semantic v3 contracts"
```

---

### Task 2: `pm-pack-v1` Framing, Sealing, and Result Recovery

**Files:**
- Create: `src/portfolio_maker/infrastructure/semantic_pack.py`
- Create: `tests/test_semantic_pack_v3.py`

**Interfaces:**
- Consumes: `PackRecordRef`, canonical JSON rules, managed-file no-symlink policy.
- Produces: `PMPackWriter.append()`, `PMPackWriter.seal()`, `PMPackReader.read()`, `verify_pack()`, `recover_committed_prefix()`.

- [ ] **Step 1: Write RED framing and corruption tests**

```python
def test_pack_round_trip_binds_offset_length_digest_and_root(tmp_path: Path) -> None:
    path = tmp_path / "content.pm-pack"
    with PMPackWriter(path) as writer:
        first = writer.append({"node_id": "node-a", "summary": "alpha"})
        second = writer.append({"node_id": "node-b", "summary": "beta"})
        seal = writer.seal()
    reader = PMPackReader(path)
    assert reader.read(first)["node_id"] == "node-a"
    assert reader.read(second)["node_id"] == "node-b"
    assert verify_pack(path, seal) == seal

def test_result_pack_recovery_discards_only_uncommitted_tail(tmp_path: Path) -> None:
    path = tmp_path / "results.pm-pack"
    with PMPackWriter(path, seal_on_close=False) as writer:
        committed = writer.append({"unit_id": "unit-1", "summary": "ok"})
        committed_length = writer.byte_length
        committed_root = writer.record_root
        writer.append({"unit_id": "unit-orphan", "summary": "ignored"})
    recover_committed_prefix(path, committed_length, committed_root)
    assert path.stat().st_size == committed_length
    assert PMPackReader(path).read(committed)["unit_id"] == "unit-1"
```

- [ ] **Step 2: Confirm tests fail because the module does not exist**

Run: `python -m pytest -q tests/test_semantic_pack_v3.py`

Expected: import failure.

- [ ] **Step 3: Implement the exact frame and root contract**

```python
PACK_MAGIC = b"PMPACK1\0"
_LENGTH = struct.Struct(">Q")
_DIGEST_BYTES = 32

def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

def _next_root(current: bytes, payload_sha256: bytes, length: int) -> bytes:
    return hashlib.sha256(
        b"portfolio-maker-pm-pack-v1\0" + current + length.to_bytes(8, "big") + payload_sha256
    ).digest()
```

`append()` must write `length + canonical payload + raw digest`, call `flush()` and `os.fsync()` before returning, and return an exact `PackRecordRef`. `read()` must use offset/length, reject out-of-range/truncated/duplicate-key/noncanonical content, and rehash the record.

- [ ] **Step 4: Implement atomic prepare-pack sealing and committed-prefix recovery**

```python
@dataclass(frozen=True)
class PackSeal:
    byte_length: int
    record_count: int
    record_root: str
    file_sha256: str

def recover_committed_prefix(path: Path, length: int, root: str) -> None:
    observed = verify_pack_prefix(path, length)
    if observed.record_root != root:
        raise SemanticPackError("semantic result pack prefix is invalid")
    with open(path, "r+b", buffering=0) as handle:
        handle.truncate(length)
        os.fsync(handle.fileno())
```

Prepare packs write to a temporary sibling and rename only after `PackSeal` verification. Result packs remain append-only until run finalization; no record rewrite is allowed.

- [ ] **Step 5: Run adversarial pack tests**

Run: `python -m pytest -q tests/test_semantic_pack_v3.py`

Expected: PASS for truncation, bit flip, wrong offset/length/hash/root, append-after-seal, orphan-tail and Unicode canonicalization cases.

- [ ] **Step 6: Commit Task 2 only**

```bash
git add src/portfolio_maker/infrastructure/semantic_pack.py tests/test_semantic_pack_v3.py
git commit -m "feat: add semantic payload packs"
```

---

### Task 3: Immutable Indexed Semantic Catalog

**Files:**
- Create: `src/portfolio_maker/infrastructure/semantic_catalog.py`
- Create: `tests/test_semantic_catalog_v3.py`

**Interfaces:**
- Consumes: prepare `PackRecordRef`s and v3 domain coverage/actions.
- Produces: `SemanticCatalogWriter`, `SemanticCatalogReader`, `CatalogSeal`, indexed structural/local/frontier query API and selector-enriched deferred audit rows.

- [ ] **Step 1: Write RED catalog round-trip and seal tests**

```python
def test_catalog_keeps_every_node_and_queries_children_without_full_load(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    with SemanticCatalogWriter(path) as writer:
        writer.add_node(NodeRecord("root", None, "source", "", "source-1"))
        for index in range(10_000):
            writer.add_node(NodeRecord(f"n-{index:05}", "root", "file", f"f-{index}", "source-1"))
        writer.add_baseline(BaselineRecord("root", BaselineCoverage.COMPLETE, (), (), 0, None))
        seal = writer.seal()
    reader = SemanticCatalogReader(path, seal)
    assert reader.node_count() == 10_001
    assert [row.node_id for row in reader.children_page("root", None, 64)][:2] == ["n-00000", "n-00001"]
    assert len(reader.children_page("root", "n-00063", 64)) == 64

def test_catalog_frontier_deferred_audit_row_round_trips_exact_selector_fields(
    catalog_with_frontier_deferred,
) -> None:
    rows = list(catalog_with_frontier_deferred.reader.iter_frontier_deferred_for_audit(
        selector_version=catalog_with_frontier_deferred.selector_version,
        fetch_size=64,
    ))
    assert rows == [catalog_with_frontier_deferred.expected_audit_row]
    assert rows[0].cursor_json == canonical_json_text({"lane": "source-1", "offset": 64})
    assert rows[0].source_id == "source-1"
    assert rows[0].coverage_stratum == "complete"
    assert rows[0].relevance_rank == 0
    assert rows[0].selector_feature_digest == catalog_with_frontier_deferred.feature_digest
```

- [ ] **Step 2: Run the new catalog tests and confirm RED**

Run: `python -m pytest -q tests/test_semantic_catalog_v3.py`

Expected: import failure.

- [ ] **Step 3: Implement the sealed catalog schema**

```sql
CREATE TABLE nodes (
  node_id TEXT PRIMARY KEY,
  parent_node_id TEXT,
  source_id TEXT NOT NULL,
  node_kind TEXT NOT NULL,
  display_name TEXT NOT NULL,
  relative_hierarchy TEXT NOT NULL,
  structural_ref_json TEXT,
  FOREIGN KEY(parent_node_id) REFERENCES nodes(node_id)
);
CREATE INDEX nodes_parent_order ON nodes(parent_node_id, node_id);
CREATE INDEX nodes_source_order ON nodes(source_id, node_id);
CREATE TABLE children (
  parent_node_id TEXT NOT NULL REFERENCES nodes(node_id),
  child_node_id TEXT NOT NULL REFERENCES nodes(node_id),
  traversal_order INTEGER NOT NULL,
  structural_payload_ref_id TEXT,
  PRIMARY KEY(parent_node_id, child_node_id)
);
CREATE UNIQUE INDEX children_traversal_order ON children(parent_node_id, traversal_order);
CREATE TABLE local_baselines (
  node_id TEXT PRIMARY KEY REFERENCES nodes(node_id),
  coverage TEXT NOT NULL,
  roles_json TEXT NOT NULL,
  topics_json TEXT NOT NULL,
  inspected_bytes INTEGER NOT NULL,
  sampled_ranges_json TEXT NOT NULL,
  sample_sha256 TEXT,
  full_content_sha256 TEXT,
  analyzer_version TEXT NOT NULL,
  payload_ref_json TEXT
);
CREATE INDEX local_baselines_coverage ON local_baselines(coverage, node_id);
CREATE TABLE payload_refs (
  payload_ref_id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL REFERENCES nodes(node_id),
  pack_kind TEXT NOT NULL,
  byte_offset INTEGER NOT NULL,
  byte_length INTEGER NOT NULL,
  payload_sha256 TEXT NOT NULL
);
CREATE TABLE content_identities (
  node_id TEXT PRIMARY KEY REFERENCES nodes(node_id),
  metadata_identity_json TEXT NOT NULL,
  sample_identity_sha256 TEXT,
  full_content_sha256 TEXT
);
CREATE TABLE evidence_links (
  node_id TEXT NOT NULL REFERENCES nodes(node_id),
  evidence_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  PRIMARY KEY(node_id, evidence_id, relation)
);
CREATE TABLE frontier_features (
  node_id TEXT PRIMARY KEY REFERENCES nodes(node_id),
  feature_version TEXT NOT NULL,
  project_like INTEGER NOT NULL,
  ambiguous INTEGER NOT NULL,
  independent_child INTEGER NOT NULL,
  evidence_linked INTEGER NOT NULL,
  uncertainty INTEGER NOT NULL,
  top_level_lane TEXT NOT NULL,
  feature_digest TEXT NOT NULL
);
CREATE INDEX frontier_ready_order ON frontier_features(top_level_lane, project_like DESC, ambiguous DESC, node_id);
CREATE TABLE frontier_order (
  node_id TEXT PRIMARY KEY REFERENCES frontier_features(node_id),
  fair_round INTEGER NOT NULL,
  top_level_lane TEXT NOT NULL,
  project_like INTEGER NOT NULL,
  ambiguous INTEGER NOT NULL,
  independent_child INTEGER NOT NULL,
  evidence_linked INTEGER NOT NULL,
  uncertainty INTEGER NOT NULL
);
CREATE UNIQUE INDEX frontier_order_page ON frontier_order(
  fair_round, top_level_lane, project_like DESC, ambiguous DESC,
  independent_child DESC, evidence_linked DESC, uncertainty DESC, node_id
);
CREATE TABLE frontier_deferred (
  node_id TEXT PRIMARY KEY REFERENCES nodes(node_id),
  reason TEXT NOT NULL,
  node_digest TEXT NOT NULL,
  cursor_json TEXT NOT NULL,
  cursor_order INTEGER NOT NULL UNIQUE,
  source_id TEXT NOT NULL,
  coverage_stratum TEXT NOT NULL,
  relevance_rank INTEGER NOT NULL,
  selector_version TEXT NOT NULL,
  selector_feature_digest TEXT NOT NULL
);
CREATE INDEX frontier_deferred_audit_order ON frontier_deferred(
  relevance_rank, coverage_stratum, source_id, cursor_order, node_id
);
```

Use normalized child edges or the `(parent_node_id, node_id)` index as topology truth; never store a truncated child list as authority.
Build `frontier_order.fair_round` once before seal using SQLite's file-backed sort/window operation
(`temp_store=FILE`) partitioned by `top_level_lane`. Keyset pages over the unique index are the only
frontier proposal iterator; Python never receives all lane members at once.
`frontier_deferred` is the immutable file-backed authority for every eligible-but-unselected node.
Its ordered canonical-row accumulator yields count/root/cursor-root without returning the population
to Python; audit and full verify page it from the sealed catalog. Its row is the same
`SemanticDeferredAuditRow` contract later used by runtime transitions: exact canonical cursor JSON
and order, source/coverage/relevance fields, selector version and the corresponding
`frontier_features.feature_digest` are stored and cross-checked before catalog seal.

- [ ] **Step 4: Implement catalog sealing and dual roots**

```python
@dataclass(frozen=True)
class CatalogSeal:
    byte_length: int
    file_sha256: str
    logical_root: str
    node_count: int

def seal(self) -> CatalogSeal:
    self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    self._connection.execute("VACUUM")
    self._connection.execute("PRAGMA integrity_check")
    self._connection.commit()
    os.fsync(self._descriptor)
    return CatalogSeal(...)
```

Calculate `logical_root` by primary-key ordered canonical rows from every authoritative table with a
domain-separated streaming accumulator. Reject WAL/SHM, schema drift, write attempts after seal, and
catalog/file root mismatch. Reopen the sealed file using SQLite read-only URI mode and verify both
`PRAGMA query_only=ON` and absence of `-wal`/`-shm` sidecars.

- [ ] **Step 5: Run focused catalog tests including tamper and high fan-out**

Run: `python -m pytest -q tests/test_semantic_catalog_v3.py -k 'catalog or read_only or sidecar or high_fanout'`

Expected: PASS without materializing all 10,000 children in the page query.

- [ ] **Step 6: Commit Task 3 only**

```bash
git add src/portfolio_maker/infrastructure/semantic_catalog.py tests/test_semantic_catalog_v3.py
git commit -m "feat: add sealed semantic catalog"
```

---

### Task 4: Additive Transactional Lifecycle Ledger

**Files:**
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `src/portfolio_maker/domain/semantic_frontier.py`
- Modify: `tests/test_sqlite_repository.py`
- Create: `tests/test_semantic_lifecycle_v3.py`

**Interfaces:**
- Consumes: v3 domain types and exact manifest/catalog roots.
- Produces: additive tables plus repository methods used by prepare, status, next, validate, pause/resume, finalize, migration, and deepening.

- [ ] **Step 1: Write RED additive migration and legacy preservation tests**

```python
def test_initialize_adds_v3_lifecycle_without_rewriting_legacy_rows(workspace: Path) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    repository.create_semantic_revision("legacy-v2", "source", "policy", "v2")
    repository.initialize()
    assert repository.get_active_semantic_revision("source") is None
    assert {
        "semantic_v3_revision_bindings", "semantic_v3_runs", "semantic_v3_units",
        "semantic_v3_proposals", "semantic_v3_dependencies", "semantic_v3_leases", "semantic_v3_receipts",
        "semantic_v3_aggregates", "semantic_v3_checkpoints", "semantic_v3_approvals",
        "semantic_v3_deferred", "semantic_v3_runtime_deferred",
        "semantic_v3_expansion_cursors", "semantic_v3_audit_members",
        "semantic_v3_execution_lock",
    }.issubset(repository.table_names())

def test_only_one_workspace_run_can_own_execution_lock(workspace: Path) -> None:
    repository = initialized_repository(workspace)
    repository.acquire_semantic_v3_execution("run-a", "unit-a", "nonce-a")
    with pytest.raises(RepositoryError, match="semantic execution is busy"):
        repository.acquire_semantic_v3_execution("run-b", "unit-b", "nonce-b")

def test_only_one_workspace_run_can_be_operationally_running(workspace: Path) -> None:
    repository = initialized_repository_with_two_approved_prepared_runs(workspace)
    repository.claim_semantic_v3_running_slot("run-a")
    with pytest.raises(RepositoryError, match="semantic execution is busy"):
        repository.claim_semantic_v3_running_slot("run-b")
    assert repository.run_state("run-b") is SemanticRunState.PREPARED
```

- [ ] **Step 2: Run the exact RED tests**

Run: `python -m pytest -q tests/test_sqlite_repository.py::test_initialize_adds_v3_lifecycle_without_rewriting_legacy_rows tests/test_semantic_lifecycle_v3.py::test_only_one_workspace_run_can_own_execution_lock tests/test_semantic_lifecycle_v3.py::test_only_one_workspace_run_can_be_operationally_running`

Expected: missing tables/methods.

- [ ] **Step 3: Add strict v3 tables and indexes inside `initialize()`**

```sql
CREATE TABLE IF NOT EXISTS semantic_v3_revision_bindings (
  revision_id TEXT PRIMARY KEY REFERENCES semantic_index_revisions(id),
  manifest_sha256 TEXT NOT NULL,
  source_policy_sha256 TEXT NOT NULL,
  catalog_file_sha256 TEXT NOT NULL,
  catalog_logical_root TEXT NOT NULL,
  lifecycle_schema_version TEXT NOT NULL,
  lifecycle_genesis_root TEXT NOT NULL,
  current_checkpoint_root TEXT NOT NULL,
  protocol TEXT NOT NULL CHECK(protocol = 'codex-semantic-v3')
);
CREATE TABLE IF NOT EXISTS semantic_v3_proposals (
  run_id TEXT PRIMARY KEY,
  revision_id TEXT NOT NULL REFERENCES semantic_v3_revision_bindings(revision_id),
  approval_kind TEXT NOT NULL CHECK(approval_kind IN ('frontier','project_deepening')),
  proposal_canonical_json BLOB NOT NULL,
  proposal_sha256 TEXT NOT NULL UNIQUE,
  plan_canonical_json BLOB NOT NULL,
  plan_sha256 TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  source_policy_sha256 TEXT NOT NULL,
  catalog_root TEXT NOT NULL,
  selector_version TEXT NOT NULL,
  private_context_scope TEXT NOT NULL,
  authority_root TEXT NOT NULL,
  total_budget_json TEXT NOT NULL,
  phase_bindings_json TEXT NOT NULL,
  budget_binding_sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('proposed','approved','stale')),
  created_at TEXT NOT NULL,
  UNIQUE(run_id, proposal_sha256)
);
CREATE TABLE IF NOT EXISTS semantic_v3_runs (
  run_id TEXT PRIMARY KEY,
  revision_id TEXT NOT NULL REFERENCES semantic_v3_revision_bindings(revision_id),
  approval_kind TEXT NOT NULL CHECK(approval_kind IN ('frontier','project_deepening')),
  project_id TEXT,
  state TEXT NOT NULL CHECK(state IN (
    'prepared','awaiting_approval','running','paused_user','paused_budget',
    'blocked','converged','finalizing','finalized','failed'
  )),
  phase TEXT NOT NULL CHECK(phase IN ('orientation','expansion','audit','deepening')),
  proposal_sha256 TEXT NOT NULL,
  used_units INTEGER NOT NULL DEFAULT 0,
  used_input_bytes INTEGER NOT NULL DEFAULT 0,
  used_active_seconds REAL NOT NULL DEFAULT 0,
  result_pack_length INTEGER NOT NULL DEFAULT 0,
  result_pack_root TEXT NOT NULL,
  FOREIGN KEY(run_id, proposal_sha256)
    REFERENCES semantic_v3_proposals(run_id, proposal_sha256)
);
CREATE UNIQUE INDEX IF NOT EXISTS semantic_v3_one_running_run
  ON semantic_v3_runs(state) WHERE state = 'running';
CREATE TABLE IF NOT EXISTS semantic_v3_execution_lock (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  run_id TEXT NOT NULL REFERENCES semantic_v3_runs(run_id),
  unit_id TEXT NOT NULL,
  lease_nonce TEXT NOT NULL,
  lease_state TEXT NOT NULL CHECK(lease_state IN ('materializing','issued'))
);
```

Add the remaining normalized lifecycle tables without putting catalog-scale payloads in the main DB:

```sql
CREATE TABLE IF NOT EXISTS semantic_v3_units (
  run_id TEXT NOT NULL REFERENCES semantic_v3_runs(run_id),
  unit_id TEXT NOT NULL,
  phase TEXT NOT NULL CHECK(phase IN ('orientation','expansion','audit','deepening')),
  state TEXT NOT NULL CHECK(state IN ('ready','leased','validated','deferred','error')),
  priority INTEGER NOT NULL,
  fairness_lane TEXT NOT NULL,
  target_ref_json TEXT NOT NULL,
  target_ref_sha256 TEXT NOT NULL,
  dependency_root TEXT NOT NULL,
  estimated_input_bytes INTEGER NOT NULL CHECK(estimated_input_bytes >= 0),
  terminal_reason TEXT,
  PRIMARY KEY(run_id, unit_id)
);
CREATE INDEX IF NOT EXISTS semantic_v3_units_ready
  ON semantic_v3_units(run_id, state, phase, priority, fairness_lane, unit_id);

CREATE TABLE IF NOT EXISTS semantic_v3_dependencies (
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  dependency_unit_id TEXT NOT NULL,
  dependency_digest TEXT NOT NULL,
  PRIMARY KEY(run_id, unit_id, dependency_unit_id),
  FOREIGN KEY(run_id, unit_id) REFERENCES semantic_v3_units(run_id, unit_id)
);
CREATE INDEX IF NOT EXISTS semantic_v3_dependencies_reverse
  ON semantic_v3_dependencies(run_id, dependency_unit_id, unit_id);

CREATE TABLE IF NOT EXISTS semantic_v3_leases (
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  lease_nonce TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK(state IN ('materializing','issued','expired')),
  input_sha256 TEXT,
  input_bytes INTEGER,
  lease_started_active_seconds REAL NOT NULL,
  lease_deadline_active_seconds REAL NOT NULL,
  PRIMARY KEY(run_id, unit_id),
  FOREIGN KEY(run_id, unit_id) REFERENCES semantic_v3_units(run_id, unit_id)
);

CREATE TABLE IF NOT EXISTS semantic_v3_receipts (
  run_id TEXT NOT NULL,
  unit_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  lease_nonce TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  output_sha256 TEXT NOT NULL,
  result_offset INTEGER NOT NULL,
  result_length INTEGER NOT NULL,
  result_sha256 TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('accept_boundary','expand_children','stop_non_project','defer_on_demand')),
  validator_version TEXT NOT NULL,
  receipt_sha256 TEXT NOT NULL,
  PRIMARY KEY(run_id, unit_id),
  UNIQUE(run_id, sequence)
);

CREATE TABLE IF NOT EXISTS semantic_v3_aggregates (
  run_id TEXT PRIMARY KEY REFERENCES semantic_v3_runs(run_id),
  ready_count INTEGER NOT NULL,
  leased_count INTEGER NOT NULL,
  validated_count INTEGER NOT NULL,
  deferred_count INTEGER NOT NULL,
  error_count INTEGER NOT NULL,
  used_units INTEGER NOT NULL,
  used_input_bytes INTEGER NOT NULL,
  used_active_seconds REAL NOT NULL,
  phase_usage_json TEXT NOT NULL,
  completion_json TEXT NOT NULL,
  aggregate_root TEXT NOT NULL,
  last_verified_at TEXT
);

CREATE TABLE IF NOT EXISTS semantic_v3_checkpoints (
  run_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  receipt_sequence INTEGER NOT NULL,
  result_pack_length INTEGER NOT NULL,
  result_pack_root TEXT NOT NULL,
  queue_root TEXT NOT NULL,
  aggregate_root TEXT NOT NULL,
  resume_cursor_json TEXT NOT NULL,
  audit_ordered_set_sha256 TEXT,
  audit_population_sha256 TEXT,
  selector_version TEXT NOT NULL,
  previous_checkpoint_root TEXT NOT NULL,
  checkpoint_root TEXT NOT NULL,
  PRIMARY KEY(run_id, sequence),
  UNIQUE(run_id, checkpoint_root)
);

CREATE TABLE IF NOT EXISTS semantic_v3_approvals (
  run_id TEXT PRIMARY KEY REFERENCES semantic_v3_runs(run_id),
  approval_kind TEXT NOT NULL CHECK(approval_kind IN ('frontier','project_deepening')),
  proposal_sha256 TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  source_policy_sha256 TEXT NOT NULL,
  catalog_root TEXT NOT NULL,
  selector_version TEXT NOT NULL,
  private_context_scope TEXT NOT NULL,
  total_budget_json TEXT NOT NULL,
  phase_bindings_json TEXT NOT NULL,
  budget_binding_sha256 TEXT NOT NULL,
  project_id TEXT,
  user_confirmed INTEGER NOT NULL CHECK(user_confirmed = 1),
  approved_at TEXT NOT NULL,
  FOREIGN KEY(run_id, proposal_sha256)
    REFERENCES semantic_v3_proposals(run_id, proposal_sha256)
);

CREATE TABLE IF NOT EXISTS semantic_v3_deferred (
  run_id TEXT NOT NULL REFERENCES semantic_v3_runs(run_id),
  set_kind TEXT NOT NULL CHECK(set_kind IN ('initial_catalog','runtime_transition')),
  deferred_count INTEGER NOT NULL,
  deferred_root TEXT NOT NULL,
  deferred_cursor_root TEXT NOT NULL,
  current_cursor_json TEXT NOT NULL,
  PRIMARY KEY(run_id, set_kind)
);

CREATE TABLE IF NOT EXISTS semantic_v3_runtime_deferred (
  run_id TEXT NOT NULL REFERENCES semantic_v3_runs(run_id),
  node_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  node_digest TEXT NOT NULL,
  cursor_json TEXT NOT NULL,
  cursor_order INTEGER NOT NULL,
  source_id TEXT NOT NULL,
  coverage_stratum TEXT NOT NULL,
  relevance_rank INTEGER NOT NULL,
  selector_version TEXT NOT NULL,
  selector_feature_digest TEXT NOT NULL,
  source_transition_unit_id TEXT NOT NULL,
  created_sequence INTEGER NOT NULL,
  PRIMARY KEY(run_id, node_id),
  UNIQUE(run_id, cursor_order),
  FOREIGN KEY(run_id, source_transition_unit_id)
    REFERENCES semantic_v3_units(run_id, unit_id)
);
CREATE INDEX IF NOT EXISTS semantic_v3_runtime_deferred_sequence
  ON semantic_v3_runtime_deferred(run_id, created_sequence, cursor_order, node_id);
CREATE INDEX IF NOT EXISTS semantic_v3_runtime_deferred_audit_order
  ON semantic_v3_runtime_deferred(
    run_id, relevance_rank, coverage_stratum, source_id, cursor_order, node_id
  );

CREATE TABLE IF NOT EXISTS semantic_v3_expansion_cursors (
  run_id TEXT NOT NULL,
  lane TEXT NOT NULL,
  last_priority INTEGER NOT NULL,
  last_node_id TEXT NOT NULL,
  population_digest TEXT NOT NULL,
  PRIMARY KEY(run_id, lane)
);

CREATE TABLE IF NOT EXISTS semantic_v3_audit_members (
  run_id TEXT NOT NULL REFERENCES semantic_v3_runs(run_id),
  ordinal INTEGER NOT NULL,
  stratum TEXT NOT NULL CHECK(stratum IN ('relevance','coverage','deterministic_random')),
  node_id TEXT NOT NULL,
  node_digest TEXT NOT NULL,
  audit_unit_id TEXT NOT NULL,
  PRIMARY KEY(run_id, ordinal),
  UNIQUE(run_id, node_id)
);
CREATE INDEX IF NOT EXISTS semantic_v3_audit_unit_members
  ON semantic_v3_audit_members(run_id, audit_unit_id, ordinal);
```

The execution mutex is a singleton row. Receipt indexes are already unique on `(run_id, unit_id)`
and `(run_id, sequence)`; checkpoint lookup is indexed by run/sequence and root. All enum checks
reject unknown legacy text. Task 11 adds cache tables, Task 13 migration runs and Task 14 deepening
lineage after their own contracts exist.

- [ ] **Step 4: Add transaction-scoped repository methods**

```python
def bind_semantic_v3_revision(self, binding: SemanticRevisionBinding) -> None: ...
def create_semantic_v3_proposal(self, envelope: SemanticProposalEnvelope, canonical_bytes: bytes, plan_bytes: bytes) -> None: ...
def get_semantic_v3_proposal_by_digest(self, revision_id: str, proposal_sha256: str) -> tuple[SemanticProposalEnvelope, bytes, bytes]: ...
def get_semantic_v3_proposal_for_run(self, run_id: str) -> tuple[SemanticProposalEnvelope, bytes, bytes]: ...
def create_semantic_v3_run(self, envelope: SemanticProposalEnvelope) -> None: ...
def approve_semantic_v3_run(self, run_id: str, proposal_sha256: str) -> None: ...
def semantic_v3_status(self, run_id: str | None) -> SemanticStatusSnapshot: ...
def claim_semantic_v3_running_slot(self, run_id: str) -> None: ...
def acquire_semantic_v3_execution(self, run_id: str, unit_id: str, lease_nonce: str) -> None: ...
def issue_semantic_v3_input(self, run_id: str, lease_nonce: str, input_sha256: str, byte_count: int) -> None: ...
def commit_semantic_v3_receipt(self, receipt: SemanticReceiptCommit) -> None: ...
def iter_semantic_v3_runtime_deferred(self, run_id: str, fetch_size: int = 256) -> Iterator[SemanticRuntimeDeferredRow]: ...
def iter_semantic_v3_runtime_deferred_audit(self, run_id: str, fetch_size: int = 256) -> Iterator[SemanticDeferredAuditRow]: ...
def checkpoint_semantic_v3_run(self, checkpoint: SemanticCheckpoint) -> None: ...
def commit_semantic_v3_audit_freeze(self, freeze: SemanticAuditFreeze) -> None: ...
def pause_semantic_v3_run(self, run_id: str, reason: str) -> None: ...
```

`create_semantic_v3_proposal()` rejects noncanonical proposal/plan bytes or digest mismatch, requires
`envelope.proposal.plan_sha256` to match the canonical plan bytes, and preserves both exact bodies as
the cross-process approval authority. `commit_semantic_v3_receipt()` must validate the
exact lock/nonce/input/current authority, insert the receipt, update unit/dependents/aggregate/result
root/checkpoint, insert every bounded runtime `defer_on_demand` row, recompute the
`runtime_transition` count/root/cursor binding, and release the singleton lock in one transaction.
Each runtime canonical row stores the exact cursor JSON plus its validated numeric order, source,
coverage stratum, relevance rank, selector version and sealed-catalog feature digest; the transaction
rejects a cursor/order or selector/feature mismatch. `SemanticRuntimeDeferredRow` additionally
preserves the source transition unit and receipt sequence. The `runtime_transition` ledger root
hashes the audit row and both lineage fields; its cursor root hashes only ordered audit cursors.
`iter_semantic_v3_runtime_deferred_audit()` is a streaming projection used by the audit selector,
while full verification always consumes the complete runtime row.
The runtime table is bounded by approved work: at most 64 transitioned node IDs per unit and at most
512 frontier units (32,768 rows), or the separately approved 256-unit deepening budget. Duplicate exact receipt is
success before any pack append; different replay is conflict. `commit_semantic_v3_audit_freeze()`
atomically inserts the ordered membership, its audit units, population/ordered-set digests and the
new checkpoint after the last expansion receipt. Its population authority is the streaming canonical
merge of selector-enriched sealed-catalog `frontier_deferred` rows and repository
`semantic_v3_runtime_deferred` rows,
ordered by `(set_kind_rank, cursor_order, node_id)` with `initial_catalog` before
`runtime_transition`; its domain-separated canonical row includes every field of
`SemanticDeferredAuditRow`, and the checkpoint stores the exact combined population digest. Receipt
lineage is intentionally outside this audit-population projection but remains hash-bound by the
runtime ledger root and is independently receipt-joined during full verification.

- [ ] **Step 5: Run lifecycle atomicity and DB safety tests**

Run: `python -m pytest -q tests/test_semantic_lifecycle_v3.py tests/test_sqlite_repository.py -k 'semantic_v3 or only_one_workspace_run or legacy_active_semantic or semantic_revision_activation or repository_'`

Expected: PASS; existing database family/sidecar safety tests remain green.

- [ ] **Step 6: Commit Task 4 only**

```bash
git add src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/domain/semantic_frontier.py tests/test_sqlite_repository.py tests/test_semantic_lifecycle_v3.py
git commit -m "feat: add semantic lifecycle ledger"
```

---

### Task 5: Streaming Crawl Events, Exhaustive Local Baseline, and Verified Source Read

**Files:**
- Modify: `src/portfolio_maker/infrastructure/semantic_crawler.py`
- Modify: `src/portfolio_maker/infrastructure/semantic_analyzers.py`
- Create: `src/portfolio_maker/infrastructure/semantic_source_reader.py`
- Modify: `tests/test_semantic_crawler.py`
- Modify: `tests/test_semantic_analyzers.py`
- Create: `tests/test_semantic_prepare_v3.py`

**Interfaces:**
- Consumes: existing watchdog/policy worker and v3 coverage/catalog writer API.
- Produces: `iter_local_structure_events()`, `analyze_local_baseline()`, `read_verified_representative()`; legacy `crawl_local_structure()` remains behavior-compatible.

- [ ] **Step 1: Write RED streaming order and coverage tests**

```python
def test_stream_emits_every_node_then_closes_directories_bottom_up(tmp_path: Path, approval) -> None:
    (tmp_path / "project" / "src").mkdir(parents=True)
    (tmp_path / "project" / "README.md").write_text("purpose", encoding="utf-8")
    (tmp_path / "project" / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
    events = list(iter_local_structure_events(tmp_path, approval))
    seen = [event for event in events if event.kind == StructuralEventKind.NODE_SEEN]
    closed = [event.node.relative_hierarchy for event in events if event.kind == StructuralEventKind.DIRECTORY_CLOSED]
    assert len(seen) == 5
    assert closed.index("project/src") < closed.index("project") < closed.index("")

def test_prefix_analysis_is_sampled_not_full_identity(tmp_path: Path, structural_entry) -> None:
    structural_entry.absolute_path.write_bytes(b"a" * 131_072 + b"tail")
    baseline = analyze_local_baseline(structural_entry)
    assert baseline.coverage is BaselineCoverage.SAMPLED
    assert baseline.sample_sha256 is not None
    assert baseline.full_content_sha256 is None

def test_high_fanout_worker_streams_without_directory_tuple(high_fanout_root, approval, crawl_observer) -> None:
    count = sum(1 for _ in iter_local_structure_events(
        high_fanout_root, approval, worker_queue_size=256, observer=crawl_observer
    ))
    assert count >= 10_002
    assert crawl_observer.max_buffered_events <= 256
    assert crawl_observer.directory_tuple_materializations == 0
```

- [ ] **Step 2: Write the no-follow one-pass TOCTOU RED test**

```python
def test_verified_representative_uses_same_descriptor_for_hash_and_slice(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.md"
    source.write_text("alpha\nbeta\ngamma", encoding="utf-8")
    observed = read_verified_representative(source, ranges=((0, 5),), max_materialized_bytes=64)
    assert observed.full_content_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert observed.slices == (b"alpha",)
    assert observed.pre_identity == observed.post_identity
```

Add fault injection that rewrites the tail on the same inode before post-`fstat`; the function must raise `SemanticSourceChangedError` and publish no exchange input.

- [ ] **Step 3: Run crawler/analyzer/source-reader tests and confirm RED**

Run: `python -m pytest -q tests/test_semantic_crawler.py tests/test_semantic_analyzers.py tests/test_semantic_prepare_v3.py -k 'stream or baseline or representative'`

Expected: missing event/baseline/source-reader interfaces.

- [ ] **Step 4: Add the event generator while preserving the v2 wrapper**

```python
class StructuralEventKind(StrEnum):
    NODE_SEEN = "node_seen"
    FILE_ANALYZED = "file_analyzed"
    DIRECTORY_CLOSED = "directory_closed"
    SOURCE_CLOSED = "source_closed"

@dataclass(frozen=True)
class StructuralEvent:
    kind: StructuralEventKind
    node: StructuralEntry
    baseline: LocalBaseline | None = None

def crawl_local_structure(...):
    entries = tuple(
        event.node for event in iter_local_structure_events(...)
        if event.kind is StructuralEventKind.NODE_SEEN
    )
    return StructuralCrawl(entries=entries, ...)
```

The directory worker sends events through a bounded queue (`maxsize=256`) instead of returning a
per-directory tuple. The v3 sink consumes each event immediately and retains only
`O(depth + bounded batch)` state; only the explicit legacy wrapper materializes for v2 callers.
Preserve existing timeout review counts and cancellable worker behavior exactly. A 10,000-child
directory and the later 1M-node scale fixture must report zero directory-tuple materializations.
For each `NODE_SEEN`, the v3 sink appends one structural node record and, when a parent exists, one
`(parent_node_id, child_node_id, traversal_order)` structural edge record to the structural pack,
then inserts the catalog node/child rows with those exact payload references. `DIRECTORY_CLOSED`
stores only bounded aggregates and never serializes a full child list.

- [ ] **Step 5: Implement explicit local coverage and single-descriptor representative reads**

```python
@dataclass(frozen=True)
class LocalBaseline:
    node_id: str
    coverage: BaselineCoverage
    roles: tuple[str, ...]
    topics: tuple[str, ...]
    inspected_bytes: int
    sampled_ranges: tuple[tuple[int, int], ...]
    sample_sha256: str | None
    full_content_sha256: str | None
    masked_excerpt: str

def read_verified_representative(path: Path, ranges: tuple[tuple[int, int], ...], max_materialized_bytes: int) -> VerifiedRepresentative:
    descriptor = _open_regular_file(path)
    try:
        before = os.fstat(descriptor)
        digest, slices = _hash_and_collect_ranges(descriptor, ranges, max_materialized_bytes)
        after = os.fstat(descriptor)
        if file_identity(before) != file_identity(after):
            raise SemanticSourceChangedError("semantic source changed during verified read")
        return VerifiedRepresentative(f"sha256:{digest.hexdigest()}", tuple(slices), file_identity(before), file_identity(after))
    finally:
        os.close(descriptor)
```

Mask selected slices before returning any managed payload. A sampled cache hit stays sampled.

- [ ] **Step 6: Run focused plus existing watchdog regression tests**

Run: `python -m pytest -q tests/test_semantic_crawler.py tests/test_semantic_analyzers.py tests/test_semantic_prepare_v3.py -k 'stream or baseline or representative or timeout or worker'`

Expected: PASS with no implicit `Library`/`Containers` exclusion.

- [ ] **Step 7: Commit Task 5 only**

```bash
git add src/portfolio_maker/infrastructure/semantic_crawler.py src/portfolio_maker/infrastructure/semantic_analyzers.py src/portfolio_maker/infrastructure/semantic_source_reader.py tests/test_semantic_crawler.py tests/test_semantic_analyzers.py tests/test_semantic_prepare_v3.py
git commit -m "feat: stream semantic local baseline"
```

---

### Task 6: Deterministic Adaptive Frontier, Fairness, and Audit Selection

**Files:**
- Create: `src/portfolio_maker/application/semantic_frontier_v3.py`
- Create: `src/portfolio_maker/application/semantic_quality_v3.py`
- Modify: `src/portfolio_maker/infrastructure/semantic_catalog.py`
- Create: `tests/test_semantic_frontier_v3.py`
- Modify: `tests/test_semantic_quality.py`

**Interfaces:**
- Consumes: indexed pre-seal/sealed catalog queries, v3 budgets/actions, existing semantic presentation safety and v2 quality principles.
- Produces: `build_frontier_plan()`, `representative_nodes()`, `transition_frontier()`, `freeze_audit_sample()`, `validate_v3_semantic_output()`. Task 7 binds the plan to the sealed manifest/catalog and creates the durable execution proposal.

- [ ] **Step 1: Write RED deterministic seed, ancestor closure, and fairness tests**

```python
def test_selector_is_deterministic_ancestor_closed_and_fair(catalog_fixture) -> None:
    first = build_frontier_plan(catalog_fixture.fresh_planning_session(), revision_id="rev", policy_hash="p" * 64)
    second = build_frontier_plan(catalog_fixture.fresh_planning_session(), revision_id="rev", policy_hash="p" * 64)
    assert first == second
    assert first.units[:3] == (
        unit_for("source-a/root"),
        unit_for("source-b/root"),
        unit_for("source-c/root"),
    )
    selected = {node_id for unit in first.units for node_id in unit.target_node_ids}
    assert ancestors_of_selected(catalog_fixture, selected).issubset(selected)

def test_decoy_lane_cannot_starve_small_korean_project(catalog_fixture) -> None:
    catalog_fixture.add_decoy_subtree("source-a", count=100_000)
    catalog_fixture.add_project("source-b/공모전", roles=("documentation", "code", "test"))
    plan = build_frontier_plan(catalog_fixture.fresh_planning_session(), "rev", "p" * 64)
    assert "source-b/공모전" in plan.orientation_target_ids

def test_selector_keyset_pages_without_materializing_lane_members(million_feature_catalog, query_observer) -> None:
    plan = build_frontier_plan(million_feature_catalog.planning_session(), "rev", "p" * 64)
    assert plan.unit_count <= FRONTIER_BUDGET.max_units
    assert not hasattr(plan, "deferred")
    assert plan.deferred_count == million_feature_catalog.deferred_row_count()
    assert plan.deferred_root == million_feature_catalog.deferred_logical_root()
    assert query_observer.max_python_rows <= 64
    assert query_observer.sequence_sort_calls == 0
```

- [ ] **Step 2: Write RED representative and closed-action tests**

```python
def test_attestation_first_64_is_not_semantic_representative_set(catalog_fixture) -> None:
    catalog_fixture.add_high_fanout_parent(10_000, project_child_index=9_000)
    representatives = representative_nodes(catalog_fixture.reader(), "parent")
    assert "child-09000" in representatives
    assert representatives != tuple(f"child-{index:05}" for index in range(64))

@pytest.mark.parametrize("action", tuple(FrontierAction))
def test_v3_quality_accepts_only_closed_grounded_actions(action, valid_output) -> None:
    output = {**valid_output, "action": action.value}
    assert validate_v3_semantic_output(output, valid_input()).action is action
```

- [ ] **Step 3: Confirm the frontier tests are RED**

Run: `python -m pytest -q tests/test_semantic_frontier_v3.py tests/test_semantic_quality.py -k 'v3 or selector or representative'`

Expected: missing v3 selector/validator.

- [ ] **Step 4: Implement versioned feature tuples, eligibility, and representative ordering**

```python
FRONTIER_SELECTOR_VERSION = "adaptive-frontier-v1"

def seed_sort_key(feature: FrontierFeature) -> tuple[object, ...]:
    return (
        -int(feature.project_like),
        -int(feature.ambiguous),
        -int(feature.independent_child),
        -int(feature.evidence_linked),
        -int(feature.uncertainty),
        feature.node_id,
    )

REPRESENTATIVE_ROLE_ORDER = (
    "project-description", "entrypoint", "package-manifest",
    "test", "deployment", "documentation", "data", "generated-output",
)

def deferred_audit_row(seed: FrontierSeed, cursor: FrontierCursor) -> SemanticDeferredAuditRow:
    return SemanticDeferredAuditRow(
        node_id=seed.node_id,
        reason=seed.deferred_reason,
        node_digest=seed.node_digest,
        cursor_json=canonical_json_text(cursor.as_canonical_dict()),
        cursor_order=cursor.order,
        source_id=seed.source_id,
        coverage_stratum=seed.coverage_stratum,
        relevance_rank=seed.relevance_rank,
        selector_version=FRONTIER_SELECTOR_VERSION,
        selector_feature_digest=seed.feature_digest,
    )
```

A node is eligible when multiple complementary role/purpose signals, ambiguity, independent-child, approved-evidence, uncertainty, or explicit on-demand authority is present. One filename/README/manifest or file count alone must never set `project_like`.

- [ ] **Step 5: Implement file-backed breadth-before-depth order and bounded keyset pages**

```python
def iter_fair_seeds(reader: SemanticCatalogReader) -> Iterator[FrontierSeed]:
    cursor: FrontierOrderKey | None = None
    while page := reader.frontier_order_page(after=cursor, limit=64):
        for seed in page:
            yield seed
        cursor = page[-1].order_key

def build_frontier_plan(session: SemanticCatalogPlanningSession, revision_id: str, policy_hash: str) -> SemanticFrontierPlan:
    units: list[SemanticPlannedUnit] = []
    for page in session.iter_fair_seed_pages(limit=64):
        for seed in page:
            unit = maybe_build_bounded_unit(seed, session)
            if unit is not None and within_phase_estimates(units, unit):
                units.append(unit)
            else:
                session.append_deferred(
                    deferred_audit_row(seed, session.allocate_deferred_cursor(seed))
                )
    deferred = session.seal_deferred_rows()
    return SemanticFrontierPlan(
        run_id=stable_run_id(revision_id, policy_hash),
        revision_id=revision_id,
        policy_sha256=policy_hash,
        selector_version=FRONTIER_SELECTOR_VERSION,
        target_projection_contract=TARGET_PROJECTION_CONTRACT,
        input_schema_version=SEMANTIC_INPUT_SCHEMA_V3,
        output_schema_version=SEMANTIC_OUTPUT_SCHEMA_V3,
        validator_version=SEMANTIC_VALIDATOR_V3,
        purpose="project_discovery",
        units=tuple(units),
        deferred_count=deferred.record_count,
        deferred_root=deferred.logical_root,
        deferred_cursor_root=deferred.cursor_root,
    )
```

`frontier_order_page()` uses Task 3's indexed catalog during the pre-seal planning session; the
one-time fair-round build uses SQLite file-backed temporary storage. After deferred rows and their
roots are final, Task 7 seals the catalog. Do not accept a Python `Sequence` of lane members,
call `sorted()` on all candidates, or retain more than one 64-row page. Build
orientation/expansion/audit phase budgets exactly from Task 1. Batch small directory targets only
while canonical input estimate stays ≤262,144 bytes and dependency count ≤64. `units` is bounded by
the 512-unit approval cap; the unbounded deferred population is written row-by-row to the catalog
sink and represented in the plan only by count/root/cursor-root. Store each bounded
unit's canonical target-node IDs and dependency IDs/digests in `semantic_v3_units.target_ref_json`;
all eligible-but-unselected records receive the complete `SemanticDeferredAuditRow` in
`frontier_deferred`; cursor JSON is canonical and its numeric order, selector version and feature
digest are validated before the catalog can seal.

- [ ] **Step 6: Implement action transition and post-expansion audit freeze**

```python
def freeze_audit_sample(
    *,
    catalog_deferred: Iterator[SemanticDeferredAuditRow],
    runtime_deferred: Iterator[SemanticDeferredAuditRow],
    pending_runtime_deferred: Sequence[SemanticDeferredAuditRow],
    checkpoint: ExpansionCheckpoint,
) -> AuditSample:
    selector = BoundedAuditSelector(per_stratum_reserve=300)
    population = DeferredPopulationAccumulator(domain="semantic-v3-deferred-population")
    for set_kind, row in stream_canonical_deferred_merge(
        catalog_deferred,
        runtime_deferred,
        pending_runtime_deferred,
    ):
        validate_deferred_audit_row(row, checkpoint.selector_version)
        population.update(set_kind, row)
        selector.consider(
            row,
            random_key=sha256_hex(
                f"{checkpoint.revision_id}\0{checkpoint.policy_sha256}\0{row.node_id}".encode()
            ),
        )
    relevance, coverage, random = selector.finalize_unique(100, 100, 100)
    ordered = tuple((*relevance, *coverage, *random))
    members = tuple(
        AuditMember(index, stratum_for(index), node.node_id, node.node_digest)
        for index, node in enumerate(ordered)
    )
    return AuditSample(
        members,
        digest_audit_members(members),
        population.digest,
        checkpoint.selector_version,
    )
```

`freeze_audit_sample()` returns data only; Task 10 is the sole authority that may atomically store
its ordered membership, create membership-bound audit units and publish the audit checkpoint.
The selector retains at most 300 candidates for each of the three strata and therefore never holds
the population. `stream_canonical_deferred_merge()` rejects duplicate node IDs, noncanonical cursors
and selector/feature mismatch. `transition_frontier()` accepts only the four actions, validates requested child IDs against catalog
pages, and never turns budget exhaustion into `stop_non_project` or convergence.

- [ ] **Step 7: Run frontier, quality, high-fanout, and audit tests**

Run: `python -m pytest -q tests/test_semantic_frontier_v3.py tests/test_semantic_quality.py tests/test_semantic_work_plan.py -k 'frontier or quality or high_fanout or projection'`

Expected: PASS including different valid action sequences producing different checkpoint-bound audit digests.

- [ ] **Step 8: Commit Task 6 only**

```bash
git add src/portfolio_maker/application/semantic_frontier_v3.py src/portfolio_maker/application/semantic_quality_v3.py src/portfolio_maker/infrastructure/semantic_catalog.py tests/test_semantic_frontier_v3.py tests/test_semantic_quality.py
git commit -m "feat: plan adaptive semantic frontier"
```

---

### Task 7: Streaming v3 Prepare, Manifest, Proposal, and Atomic Publication

**Files:**
- Create: `src/portfolio_maker/application/semantic_prepare_v3.py`
- Modify: `src/portfolio_maker/application/semantic_index.py:prepare_semantic_index`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `tests/test_semantic_prepare_v3.py`
- Modify: `tests/test_semantic_index.py`

**Interfaces:**
- Consumes: streaming events, local baseline, pack/catalog writers, frontier proposal, repository binding.
- Produces: default v3 `prepare_semantic_index()`, canonical `manifest.v3.json`, safe proposal aggregate, staging/lifecycle genesis. Existing body becomes `_prepare_v2_semantic_index()` for explicit internal compatibility tests only.

- [ ] **Step 1: Write RED end-to-end v3 prepare tests**

```python
def test_prepare_defaults_to_v3_and_publishes_manifest_last(workspace: Path, approved_root: Path) -> None:
    result = prepare_semantic_index(PrepareSemanticIndexRequest(workspace, approved_root))
    manifest = read_canonical_json(result.manifest_path)
    assert manifest["schema_version"] == "codex-semantic-v3"
    assert result.protocol == "codex-semantic-v3"
    assert result.structural_node_count == result.local_baseline_terminal_count
    assert result.exchange_input_created is False
    assert repository(workspace).semantic_v3_binding(result.revision_id).manifest_sha256 == manifest["manifest_sha256"]
    envelope, _, _ = repository(workspace).get_semantic_v3_proposal_by_digest(result.revision_id, result.proposal_sha256)
    authority = repository(workspace).sealed_proposal_authority(result.run_id)
    assert envelope.proposal.manifest_sha256 == authority.manifest_sha256
    assert envelope.proposal.catalog_root == authority.catalog_root
    assert envelope.proposal.policy_sha256 == authority.source_policy_sha256
    assert envelope.proposal.source_approval_sha256 == authority.source_approval_sha256
    assert envelope.proposal.safe_source_label == authority.safe_source_label
    assert envelope.proposal.private_context_scope == authority.private_context_scope
    assert envelope.proposal.structural_node_count == authority.structural_node_count
    assert envelope.proposal.baseline_coverage_counts == authority.baseline_coverage_counts
    assert tuple((item.phase, item.hard_budget) for item in envelope.proposal.phase_bindings) == FRONTIER_PHASE_HARD_BUDGETS
    assert all(item.estimated_units <= item.hard_budget.max_units for item in envelope.proposal.phase_bindings)
    assert all(item.estimated_input_bytes <= item.hard_budget.max_input_bytes for item in envelope.proposal.phase_bindings)
    assert all(item.estimated_wall_seconds <= item.hard_budget.max_wall_seconds for item in envelope.proposal.phase_bindings)

def test_prepare_failure_leaves_previous_active_and_staging_binding_unchanged(workspace, approved_root, monkeypatch) -> None:
    before = snapshot_semantic_bindings(workspace)
    monkeypatch.setattr(SemanticCatalogWriter, "seal", raise_io_error)
    with pytest.raises(SemanticIndexError, match="failed safely"):
        prepare_semantic_index(PrepareSemanticIndexRequest(workspace, approved_root))
    assert snapshot_semantic_bindings(workspace) == before

def test_prepare_persists_canonical_proposal_and_bounded_unit_targets_for_new_process(workspace, approved_root) -> None:
    prepared = prepare_semantic_index(PrepareSemanticIndexRequest(workspace, approved_root))
    repository = reopen_repository_in_new_process(workspace)
    envelope, canonical, plan_bytes = repository.get_semantic_v3_proposal_bytes_by_digest(
        prepared.revision_id, prepared.proposal_sha256
    )
    assert hashlib.sha256(canonical).hexdigest() == envelope.proposal_sha256
    assert hashlib.sha256(plan_bytes).hexdigest() == envelope.proposal.plan_sha256
    assert repository.semantic_v3_unit_count(envelope.proposal.run_id) <= FRONTIER_BUDGET.max_units
    assert all(repository.verify_unit_target_ref(row) for row in repository.iter_semantic_v3_units(envelope.proposal.run_id))
```

- [ ] **Step 2: Write RED resource and no-implicit-exclusion tests**

```python
def test_prepare_stops_without_truncation_when_resource_gate_is_exceeded(workspace, approved_root, fake_budget) -> None:
    fake_budget.managed_bytes = 3 * 1024**3 + 1
    with pytest.raises(SemanticIndexError, match="resource gate"):
        prepare_semantic_index(PrepareSemanticIndexRequest(workspace, approved_root))
    assert no_v3_binding(workspace)

def test_prepare_does_not_implicitly_exclude_library_or_containers(workspace, approved_home) -> None:
    result = prepare_with_small_fixture_named_library_and_containers(workspace, approved_home)
    assert result.structural_node_count == expected_allowed_count(approved_home)
```

- [ ] **Step 3: Run prepare/index tests and confirm RED**

Run: `python -m pytest -q tests/test_semantic_prepare_v3.py tests/test_semantic_index.py -k 'v3 or defaults or resource_gate or implicitly_exclude'`

Expected: current v2 manifest or missing v3 service.

- [ ] **Step 4: Implement an event-driven prepare sink**

```python
class SemanticPrepareSink:
    def on_event(self, event: StructuralEvent) -> None:
        if event.kind is StructuralEventKind.NODE_SEEN:
            node_ref = self.structural_pack.append(structural_node_payload(event.node))
            self.catalog.add_node(node_record(event.node, node_ref))
            if event.node.parent_node_id is not None:
                edge_ref = self.structural_pack.append(structural_edge_payload(event.node))
                self.catalog.add_child(child_record(event.node, edge_ref))
        elif event.kind is StructuralEventKind.FILE_ANALYZED:
            self.catalog.add_baseline(baseline_record(event, self.content_pack))
        elif event.kind is StructuralEventKind.DIRECTORY_CLOSED:
            self.catalog.close_directory(event.node.node_id)
        elif event.kind is StructuralEventKind.SOURCE_CLOSED:
            self.structural_complete = True
```

Maintain only directory depth state, one file payload, and one bounded DB batch. Every node gets exactly one terminal baseline row.

- [ ] **Step 5: Implement manifest/proposal and publication order**

```python
temporary = paths.semantic_revision_raw_dir(f".{revision_id}.tmp")
sink.consume(iter_local_structure_events(request.root, approval))
pack_seals = sink.seal_packs()
frontier_plan = build_frontier_plan(
    sink.catalog.planning_session(), revision_id, policy_hash
)
catalog_seal = sink.seal_catalog()
frontier_plan_bytes = canonical_json_bytes(frontier_plan)
frontier_plan_sha256 = sha256_hex(frontier_plan_bytes)
manifest_bytes = build_canonical_v3_manifest_bytes(
    pack_seals, catalog_seal, frontier_plan_sha256, frontier_plan, resource_observation
)
manifest_sha256 = sha256_hex(manifest_bytes)
sealed_authority = build_sealed_proposal_authority(
    approval=approval,
    manifest_bytes=manifest_bytes,
    catalog_seal=catalog_seal,
    safe_source_label=safe_source_label(request.root),
    private_context_scope=approved_private_context_scope(approval),
    structural_node_count=sink.structural_node_count,
    baseline_coverage_counts=sink.baseline_coverage_counts,
    selector_version=frontier_plan.selector_version,
    lifecycle_genesis_root=lifecycle_genesis_root(frontier_plan),
    target_projection_contract=frontier_plan.target_projection_contract,
    input_schema_version=frontier_plan.input_schema_version,
    output_schema_version=frontier_plan.output_schema_version,
    validator_version=frontier_plan.validator_version,
)
proposal = build_execution_proposal_from_frontier(
    frontier_plan,
    authority=sealed_authority,
    plan_sha256=frontier_plan_sha256,
    phase_bindings=build_frontier_phase_bindings(
        frontier_plan, hard_budgets=FRONTIER_PHASE_HARD_BUDGETS
    ),
)
proposal_bytes = canonical_json_bytes(proposal)
proposal_envelope = SemanticProposalEnvelope(proposal, sha256_hex(proposal_bytes))
write_canonical_fsync(temporary / "manifest.v3.json", manifest_bytes)
atomic_publish_directory(temporary, paths.semantic_revision_raw_dir(revision_id))
repository.bind_semantic_v3_revision_genesis_proposal_and_units(
    binding=binding_from_manifest_bytes(manifest_bytes, proposal_envelope),
    proposal_bytes=proposal_bytes,
    proposal_envelope=proposal_envelope,
    frontier_plan_bytes=frontier_plan_bytes,
    units=frontier_plan.units,
    deferred_binding=(
        frontier_plan.deferred_count,
        frontier_plan.deferred_root,
        frontier_plan.deferred_cursor_root,
    ),
)
```

The manifest binds the frontier plan digest/estimates but not the later proposal digest, avoiding a
manifest/proposal hash cycle. The proposal binds the already sealed manifest hash and catalog root.
`build_sealed_proposal_authority()` parses and rehashes the manifest, current source approval/policy,
catalog seal, safe source label/privacy scope and exact structural/baseline aggregates; its `root`
binds every field. The proposal builder accepts this authority object rather than reconstructing
dynamic values from caller arguments, and verifies all plan projection/schema/selector fields match.
The last method is one DB transaction and verifies that canonical plan/proposal bytes, proposal and
manifest frontier-plan digests,
unit target/dependency roots and lifecycle genesis agree before inserting the revision binding,
canonical proposal row, run, bounded units, dependencies, aggregates and the catalog deferred
count/root/cursor binding. It uses `INSERT ... SELECT`/root verification against the sealed
file-backed catalog and never passes deferred rows through Python. An
unbound published directory is crash residue, not current staging. Failure review is bounded/private
and does not copy locator or payload.
The newly bound run state is `awaiting_approval`; only exact approval changes it to approved
`prepared`, and only Task 9 `next` may claim `running`.

- [ ] **Step 6: Convert public prepare/apply functions to protocol dispatchers**

```python
def prepare_semantic_index(request: PrepareSemanticIndexRequest) -> PrepareSemanticIndexResult:
    if request.protocol == "codex-semantic-v2":
        return _prepare_v2_semantic_index(request)
    return prepare_v3_semantic_index(request)

def apply_semantic_index(request: ApplySemanticIndexRequest) -> ApplySemanticIndexResult:
    protocol = detect_final_semantic_protocol(WorkspacePaths.from_root(request.workspace))
    if protocol == "codex-semantic-v3":
        return apply_v3_semantic_index(request)
    return _apply_v2_semantic_index(...)
```

CLI never exposes a downgrade flag; its request uses the v3 default. Existing v2 fixtures specify protocol explicitly.

- [ ] **Step 7: Run prepare, publication, timeout, and v2 compatibility tests**

Run: `python -m pytest -q tests/test_semantic_prepare_v3.py tests/test_semantic_index.py tests/test_semantic_crawler.py tests/test_semantic_analyzers.py`

Expected: PASS; existing timeout review atomic restore tests stay green.

- [ ] **Step 8: Commit Task 7 only**

```bash
git add src/portfolio_maker/application/semantic_prepare_v3.py src/portfolio_maker/application/semantic_index.py src/portfolio_maker/application/models.py src/portfolio_maker/infrastructure/sqlite_repository.py tests/test_semantic_prepare_v3.py tests/test_semantic_index.py
git commit -m "feat: stream semantic v3 preparation"
```

---

### Task 8: Exact Execution Approval and O(1) Aggregate Status

**Files:**
- Create: `src/portfolio_maker/application/semantic_lifecycle_v3.py`
- Modify: `src/portfolio_maker/application/semantic_work.py:semantic_index_status`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `src/portfolio_maker/adapters/cli.py`
- Modify: `tests/test_semantic_lifecycle_v3.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: proposal row, manifest/policy/catalog bindings and lifecycle aggregate.
- Produces: `approve_semantic_execution()`, `semantic_index_status_v3()`, dispatcher-backed default status and safe CLI approval command.

- [ ] **Step 1: Write RED exact-approval tests**

```python
def test_approval_binds_exact_proposal_catalog_policy_selector_scope_and_budget(prepared_v3) -> None:
    envelope = prepared_v3.proposal_envelope
    proposal = envelope.proposal
    result = approve_semantic_execution(ApproveSemanticExecutionRequest(
        workspace=prepared_v3.workspace,
        revision_id=proposal.revision_id,
        proposal_sha256=envelope.proposal_sha256,
    ))
    assert result.run_state is SemanticRunState.PREPARED
    for changed in (
        "proposal", "catalog", "policy", "selector", "scope", "total_budget",
        "orientation_budget", "expansion_budget", "audit_budget",
    ):
        with pytest.raises(SemanticWorkError, match="stale"):
            prepared_v3.approve_with_drift(changed)

def test_two_exact_approvals_remain_prepared_until_one_next_claims_running_slot(prepared_two_runs) -> None:
    first = approve_semantic_execution(prepared_two_runs.approval_request(0))
    second = approve_semantic_execution(prepared_two_runs.approval_request(1))
    assert first.run_state is second.run_state is SemanticRunState.PREPARED
    assert prepared_two_runs.repository.running_run_count() == 0

def test_separate_process_approval_rehashes_durable_canonical_proposal(prepared_v3) -> None:
    completed = run_cli_in_new_process(
        prepared_v3.workspace,
        "approve-semantic-execution",
        "--revision-id", prepared_v3.revision_id,
        "--proposal-digest", prepared_v3.proposal_envelope.proposal_sha256,
    )
    assert completed.returncode == 0
    assert prepared_v3.repository.approval(prepared_v3.run_id).proposal_sha256 == prepared_v3.proposal_envelope.proposal_sha256
```

- [ ] **Step 2: Write RED bounded status tests with a repository spy**

```python
def test_status_reads_only_binding_run_aggregate_and_lock(prepared_million_node_v3, monkeypatch) -> None:
    calls = QueryCounter.install(monkeypatch)
    status = semantic_index_status_v3(SemanticIndexStatusRequest(prepared_million_node_v3.workspace))
    assert status.integrity_level == "aggregate_bound"
    assert status.completion.structural_complete is True
    assert calls.catalog_rows == 0
    assert calls.receipt_rows == 0
    assert calls.total_selects <= 4
```

- [ ] **Step 3: Confirm approval/status/CLI tests are RED**

Run: `python -m pytest -q tests/test_semantic_lifecycle_v3.py tests/test_cli.py -k 'approval or v3_status'`

Expected: missing approval use case and v3 aggregate result fields.

- [ ] **Step 4: Implement exact proposal approval**

```python
def approve_semantic_execution(request: ApproveSemanticExecutionRequest) -> ApproveSemanticExecutionResult:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = initialized_repository(paths)
    envelope, canonical_bytes, plan_bytes = repository.get_semantic_v3_proposal_bytes_by_digest(
        request.revision_id, request.proposal_sha256
    )
    if sha256_hex(canonical_bytes) != envelope.proposal_sha256:
        raise SemanticWorkError("semantic proposal authority is corrupt")
    if sha256_hex(plan_bytes) != envelope.proposal.plan_sha256:
        raise SemanticWorkError("semantic plan authority is corrupt")
    validate_current_proposal_binding(paths, envelope, request.proposal_sha256)
    validate_sealed_or_deepening_authority_root(repository, envelope.proposal)
    validate_exact_phase_bindings(envelope.proposal.phase_bindings, envelope.proposal.budget)
    repository.approve_semantic_v3_run(envelope.proposal.run_id, envelope.proposal_sha256)
    return ApproveSemanticExecutionResult(envelope.proposal.run_id, SemanticRunState.PREPARED)
```

Do not reconstruct proposal authority from a digest or caller values and do not accept caller-supplied
budget overrides. A CLI invocation is only valid after the skill has shown the exact durable proposal
and the user has confirmed it. Approval changes `awaiting_approval` to approved `prepared`; it never
claims execution. Multiple approved/prepared runs may coexist. Task 9's first `next` transaction
claims the partial-unique `running` row and singleton mutex together; another run stays `prepared`
and receives `run_busy` until the running run is paused/finalized/failed.
`validate_exact_phase_bindings()` requires orientation/expansion/audit hard budgets to equal Task 1's
three constants and sum exactly to `FRONTIER_BUDGET`; deepening requires exactly one deepening binding.
The approval receipt stores canonical total budget, all phase bindings and their digest. No runtime
method accepts a phase transfer/reallocation parameter.

- [ ] **Step 5: Implement aggregate-only status and explicit completion fields**

```python
def semantic_index_status_v3(request: SemanticIndexStatusRequest) -> SemanticIndexStatusResult:
    snapshot = repository_for(request.workspace).semantic_v3_status(request.run_id)
    return SemanticIndexStatusResult(
        protocol=SEMANTIC_PROTOCOL_V3,
        revision_id=snapshot.revision_id,
        run_id=snapshot.run_id,
        run_state=snapshot.run_state,
        completion=snapshot.completion,
        phase_usage=snapshot.phase_usage,
        ready_count=snapshot.ready_count,
        leased_count=snapshot.leased_count,
        validated_count=snapshot.validated_count,
        receipted_count=snapshot.receipted_count,
        deferred_count=snapshot.deferred_count,
        error_count=snapshot.error_count,
        last_checkpoint_root=snapshot.last_checkpoint_root,
        last_verified_at=snapshot.last_verified_at,
        remaining_budget=snapshot.remaining_budget,
        integrity_level="aggregate_bound",
        resumable=snapshot.resumable,
    )
```

Keep v2 status mapping explicit; never map v2 `complete` to v3 `project_discovery_ready`.

- [ ] **Step 6: Add the safe approval CLI surface**

```python
approve_semantic = subparsers.add_parser("approve-semantic-execution")
approve_semantic.add_argument("--workspace", type=Path, default=Path("."))
approve_semantic.add_argument("--revision-id", required=True)
approve_semantic.add_argument("--proposal-digest", required=True)
```

Print only safe revision/run IDs, proposal digest and state; reject raw path/private content in errors.

- [ ] **Step 7: Run approval/status/CLI and legacy status tests**

Run: `python -m pytest -q tests/test_semantic_lifecycle_v3.py tests/test_cli.py tests/test_semantic_work_lifecycle.py -k 'approval or status or legacy'`

Expected: PASS; v2 safe identifier output remains unchanged.

- [ ] **Step 8: Commit Task 8 only**

```bash
git add src/portfolio_maker/application/semantic_lifecycle_v3.py src/portfolio_maker/application/semantic_work.py src/portfolio_maker/application/models.py src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/adapters/cli.py tests/test_semantic_lifecycle_v3.py tests/test_cli.py
git commit -m "feat: approve semantic frontier runs"
```

---

### Task 9: Bounded `next`, Workspace Mutex, and Run-Scoped Exchange

**Files:**
- Create: `src/portfolio_maker/infrastructure/semantic_exchange.py`
- Modify: `src/portfolio_maker/application/semantic_lifecycle_v3.py`
- Modify: `src/portfolio_maker/application/semantic_work.py:next_semantic_work`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `src/portfolio_maker/adapters/cli.py`
- Modify: `tests/test_semantic_lifecycle_v3.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: approved run, ready-unit index, catalog/packs, `read_verified_representative()`.
- Produces: `next_semantic_work_v3()` with one run-scoped exchange slot, DB-first mutex/lease and byte-identical replay.

- [ ] **Step 1: Write RED concurrency and idempotency tests**

```python
def test_two_concurrent_next_calls_issue_exactly_one_workspace_lease(approved_two_runs) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(call_next, approved_two_runs.run_ids))
    issued = [result for result in results if isinstance(result, NextSemanticWorkResult)]
    busy = [result for result in results if isinstance(result, SemanticRunBusy)]
    assert len(issued) == 1
    assert len(busy) == 1
    assert busy[0].safe_code == "run_busy"
    assert approved_two_runs.repository.running_run_count() == 1
    assert approved_two_runs.repository.run_state(busy[0].run_id) is SemanticRunState.PREPARED

def test_same_active_lease_replays_byte_identical_input(approved_run) -> None:
    first = next_semantic_work_v3(NextSemanticWorkRequest(approved_run.workspace, approved_run.run_id))
    second = next_semantic_work_v3(NextSemanticWorkRequest(approved_run.workspace, approved_run.run_id))
    assert first.lease_nonce == second.lease_nonce
    assert first.input_sha256 == second.input_sha256
    assert first.input_path.read_bytes() == second.input_path.read_bytes()
```

- [ ] **Step 2: Write RED bounded-read and TOCTOU tests**

```python
def test_next_reads_one_unit_and_at_most_64_dependencies(million_node_run, observer) -> None:
    result = next_semantic_work_v3(NextSemanticWorkRequest(million_node_run.workspace, million_node_run.run_id))
    assert result.input_byte_count <= 262_144
    assert observer.catalog_rows <= 1 + 64
    assert observer.pack_records <= 1 + 64

def test_next_tail_rewrite_publishes_no_input(approved_run, inject_same_inode_rewrite) -> None:
    with pytest.raises(SemanticWorkError, match="source changed"):
        next_semantic_work_v3(NextSemanticWorkRequest(approved_run.workspace, approved_run.run_id))
    assert not approved_run.exchange_input.exists()

def test_active_time_is_monotonic_restart_safe_and_excludes_explicit_pause(approved_run, monotonic_clock) -> None:
    approved_run.run_one_unit(monotonic_clock.advance(12))
    approved_run.pause(monotonic_clock.advance(120))
    approved_run.restart_process(monotonic_clock)
    approved_run.resume_and_run_one_unit(monotonic_clock.advance(8))
    assert approved_run.status().used_active_seconds == pytest.approx(20)

def test_unit_over_300_active_seconds_is_rejected_without_receipt(issued_unit, monotonic_clock) -> None:
    monotonic_clock.advance(301)
    with pytest.raises(SemanticWorkError, match="unit time budget"):
        validate_semantic_work_v3(issued_unit.request())
    assert issued_unit.repository.receipt_count() == 0

@pytest.mark.parametrize("drift", ("source_approval", "policy", "manifest", "catalog", "proposal", "approval", "checkpoint"))
def test_next_revalidates_current_authority_before_lease_or_source_read(approved_run, drift) -> None:
    approved_run.apply_authority_drift(drift)
    with pytest.raises(SemanticWorkError, match="stale"):
        next_semantic_work_v3(NextSemanticWorkRequest(approved_run.workspace, approved_run.run_id))
    assert approved_run.repository.active_lease_count() == 0
    assert approved_run.source_read_count == 0
    assert not approved_run.exchange_input.exists()

def test_batched_unit_uses_ledger_target_refs_not_unit_id_as_catalog_node(approved_batched_run) -> None:
    result = next_semantic_work_v3(approved_batched_run.request())
    assert result.unit_id != approved_batched_run.target_node_ids[0]
    assert tuple(result.target_node_ids) == approved_batched_run.target_node_ids

@pytest.mark.parametrize("axis", ("units", "input_bytes", "active_seconds"))
def test_phase_budget_exhaustion_cannot_borrow_from_other_phase(approved_run, axis) -> None:
    approved_run.exhaust_phase_budget(FrontierPhase.ORIENTATION, axis)
    approved_run.leave_spare_budget(FrontierPhase.EXPANSION)
    with pytest.raises(SemanticWorkPaused, match="paused_budget"):
        next_semantic_work_v3(approved_run.request())
    assert approved_run.status().phase_usage[FrontierPhase.EXPANSION].used_units == 0
```

- [ ] **Step 3: Confirm bounded-next tests are RED**

Run: `python -m pytest -q tests/test_semantic_lifecycle_v3.py tests/test_cli.py -k 'concurrent_next or active_lease or next_reads or tail_rewrite or phase_budget'`

Expected: current full-state v2 behavior or missing v3 next.

- [ ] **Step 4: Implement atomic run-scoped exchange**

```python
class SemanticExchange:
    def publish_input(self, run_id: str, lease_nonce: str, payload: dict[str, object]) -> ExchangeBinding:
        content = canonical_json_bytes(payload)
        if len(content) > MAX_UNIT_BYTES:
            raise SemanticWorkError("semantic work input exceeds byte budget")
        atomic_write_fsync(self.input_path, content)
        return ExchangeBinding(run_id, lease_nonce, sha256_hex(content), len(content))
```

Exchange output must be a regular managed file in the same run directory. Never create per-unit input/output files.

- [ ] **Step 5: Implement DB-first lease and bounded materialization**

```python
def next_semantic_work_v3(request: NextSemanticWorkRequest) -> NextSemanticWorkResult:
    authority = repository.load_bounded_semantic_v3_authority(request.run_id)
    validate_current_run_authority(request.workspace, authority)
    lease = repository.reserve_next_semantic_v3_unit(
        request.run_id, new_nonce(), expected_authority_root=authority.root,
        claim_single_running_slot=True,
    )
    if lease.replay_binding is not None:
        return replay_existing_input(lease.replay_binding)
    target_ref = verify_canonical_target_ref(
        lease.target_ref_json, lease.target_ref_sha256, max_target_nodes=64
    )
    dependencies = repository.read_validated_dependency_receipts(
        lease.run_id, target_ref.dependency_unit_ids, max_dependencies=64
    )
    target = catalog.read_unit_envelope(
        node_ids=target_ref.target_node_ids,
        dependency_digests=dependencies,
        expected_dependency_root=lease.dependency_root,
    )
    representatives = tuple(read_verified_representative_from_envelope(item) for item in target.representatives)
    validate_current_run_authority(request.workspace, authority)
    binding = exchange.publish_input(lease.run_id, lease.nonce, materialize_v3_input(target, representatives))
    repository.issue_semantic_v3_input(
        lease.run_id, lease.nonce, binding.sha256, binding.byte_count,
        expected_authority_root=authority.root,
    )
    return next_result(lease, binding)
```

`validate_current_run_authority()` rehashes the current source approval/policy, durable canonical
proposal, approval receipt, manifest and sealed catalog binding and checks the latest checkpoint/run
budget. It runs before mutex/lease/source/pack/exchange access and again after source materialization;
the reserve transaction atomically changes this approved `prepared` run to `running`, enforces the
partial unique running index, and takes the singleton execution lock before materialization. A
different running run returns `run_busy` without changing the requested run or exchange. The
reserve/issue transactions compare the same authority root. Check unit/input/phase-specific/
monotonic active-time budgets before reserving. Persist accumulated active seconds
and the current process monotonic interval boundary in the ledger so restart resumes from the stored
accumulator; explicit paused/approval-wait time is excluded. If any budget is exhausted, atomically
set `paused_budget`, preserve deferred cursor, and return no unit. Validate also rejects a lease whose
per-unit active execution exceeds 300 seconds.

- [ ] **Step 6: Add optional `--run-id` to next and safe `run_busy` handling**

```python
next_semantic.add_argument("--run-id")
```

If omitted, select only when exactly one runnable v3 run exists; otherwise fail closed. v2 keeps its no-run behavior.

- [ ] **Step 7: Run next/exchange/concurrency and v2 compatibility tests**

Run: `python -m pytest -q tests/test_semantic_lifecycle_v3.py tests/test_cli.py tests/test_semantic_work_lifecycle.py -k 'next or lease or exchange or ready_order or phase_budget or running_slot'`

Expected: PASS with no full catalog/plan load in v3.

- [ ] **Step 8: Commit Task 9 only**

```bash
git add src/portfolio_maker/infrastructure/semantic_exchange.py src/portfolio_maker/application/semantic_lifecycle_v3.py src/portfolio_maker/application/semantic_work.py src/portfolio_maker/application/models.py src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/adapters/cli.py tests/test_semantic_lifecycle_v3.py tests/test_cli.py
git commit -m "feat: issue bounded semantic work"
```

---

### Task 10: Validated Result Pack, Atomic Receipt, Pause, and Resume

**Files:**
- Modify: `src/portfolio_maker/application/semantic_lifecycle_v3.py`
- Modify: `src/portfolio_maker/application/semantic_work.py:validate_semantic_work`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `src/portfolio_maker/infrastructure/semantic_pack.py`
- Modify: `src/portfolio_maker/infrastructure/semantic_exchange.py`
- Modify: `src/portfolio_maker/adapters/cli.py`
- Modify: `tests/test_semantic_lifecycle_v3.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: issued lease/input, v3 quality validator, action transition, result pack and repository transaction.
- Produces: `validate_semantic_work_v3()`, `pause_semantic_work()`, `resume_semantic_work()` and durable checkpoint chain.

- [ ] **Step 1: Write RED result durability and conflicting replay tests**

```python
def test_validate_persists_canonical_output_before_clearing_exchange(issued_unit) -> None:
    issued_unit.write_valid_output(summary="grounded")
    result = validate_semantic_work_v3(issued_unit.request())
    issued_unit.exchange_output.unlink(missing_ok=True)
    stored = issued_unit.result_reader.read(result.result_ref)
    assert stored["semantic_summary"] == "grounded"
    assert result.receipt_sha256 == issued_unit.repository.receipt(result.unit_id).receipt_sha256

def test_validate_is_exactly_idempotent_and_conflicting_output_fails(issued_unit) -> None:
    issued_unit.write_valid_output(summary="first")
    first = validate_semantic_work_v3(issued_unit.request())
    second = validate_semantic_work_v3(issued_unit.request())
    assert first == second
    issued_unit.write_valid_output(summary="different")
    with pytest.raises(SemanticWorkError, match="conflict"):
        validate_semantic_work_v3(issued_unit.request())
```

- [ ] **Step 2: Write RED crash, orphan-tail, pause/resume tests**

```python
@pytest.mark.parametrize("failure_point", ("after_append", "after_fsync", "after_receipt_commit", "before_exchange_cleanup"))
def test_validate_fault_recovery_has_one_receipt_and_one_committed_result(issued_unit, failure_point) -> None:
    issued_unit.inject_failure(failure_point)
    issued_unit.run_validate_and_restart()
    assert issued_unit.repository.receipt_count() == 1
    assert issued_unit.result_reader.committed_record_count() == 1

def test_pause_after_32_receipts_resumes_without_double_charge(approved_run) -> None:
    approved_run.complete_units(32)
    pause_semantic_work(PauseSemanticWorkRequest(approved_run.workspace, approved_run.run_id))
    before = approved_run.status()
    resume_semantic_work(ResumeSemanticWorkRequest(approved_run.workspace, approved_run.run_id))
    after = approved_run.status()
    assert before.receipted_count == after.receipted_count == 32
    assert before.used_input_bytes == after.used_input_bytes

def test_pause_after_non_interval_receipt_publishes_checkpoint_within_ten_seconds(approved_run, monotonic_clock) -> None:
    approved_run.complete_units(7)
    requested_at = monotonic_clock.now()
    paused = pause_semantic_work(PauseSemanticWorkRequest(approved_run.workspace, approved_run.run_id))
    assert paused.checkpoint_receipt_sequence == 7
    assert paused.published_monotonic - requested_at <= 10

def test_restart_recovers_materializing_and_expired_lease_without_double_charge(approved_run) -> None:
    lease = approved_run.reserve_and_crash_in_materializing()
    recovered = approved_run.restart_and_next()
    assert recovered.lease_nonce == lease.lease_nonce
    approved_run.expire_current_lease()
    replay = approved_run.restart_and_next()
    assert replay.unit_id == lease.unit_id
    assert approved_run.status().used_units == 0

def test_failure_review_is_bounded_private_and_non_disclosing(issued_unit) -> None:
    review = issued_unit.force_controlled_failure()
    assert review.stat().st_size <= 64 * 1024
    payload = json.loads(review.read_text(encoding="utf-8"))
    assert set(payload) == {
        "revision_id", "phase", "safe_error_code", "last_checkpoint_root",
        "affected_node_id", "retryable", "policy_sha256", "manifest_sha256",
    }
    assert issued_unit.raw_locator not in review.read_text(encoding="utf-8")

def test_exact_validate_replay_does_not_append_result_pack(issued_unit) -> None:
    issued_unit.write_valid_output(summary="same")
    first = validate_semantic_work_v3(issued_unit.request())
    length = issued_unit.result_reader.byte_length
    second = validate_semantic_work_v3(issued_unit.request())
    assert second == first
    assert issued_unit.result_reader.byte_length == length

@pytest.mark.parametrize("drift", ("source_approval", "policy", "catalog", "proposal", "approval", "checkpoint", "dependency"))
def test_validate_authority_drift_commits_neither_pack_record_nor_receipt(issued_unit, drift) -> None:
    issued_unit.write_valid_output(summary="grounded")
    issued_unit.apply_authority_drift(drift)
    before = issued_unit.result_reader.byte_length
    with pytest.raises(SemanticWorkError, match="stale"):
        validate_semantic_work_v3(issued_unit.request())
    assert issued_unit.result_reader.byte_length == before
    assert issued_unit.repository.receipt_count() == 0

def test_last_expansion_receipt_atomically_freezes_ordered_audit_members_and_units(expansion_run) -> None:
    result = expansion_run.validate_last_expansion_output()
    freeze = expansion_run.repository.audit_freeze(result.run_id)
    assert len(freeze.members) == 300
    assert [member.ordinal for member in freeze.members] == list(range(300))
    assert freeze.ordered_set_sha256 == digest_audit_members(freeze.members)
    assert freeze.checkpoint_root == expansion_run.repository.current_checkpoint_root(result.run_id)
    assert expansion_run.repository.audit_unit_targets(result.run_id) == freeze.members_by_unit()

@pytest.mark.parametrize("drift", ("selector", "catalog", "privacy_scope", "audit_budget"))
def test_audit_freeze_or_read_rejects_approval_binding_drift(expansion_run, drift) -> None:
    expansion_run.apply_audit_binding_drift(drift)
    with pytest.raises(SemanticWorkError, match="new approval"):
        expansion_run.validate_last_expansion_output()
    assert expansion_run.repository.audit_member_count(expansion_run.run_id) == 0

def test_runtime_deferred_transition_is_atomic_restartable_and_row_exact(expansion_run) -> None:
    transition = expansion_run.validate_defer_on_demand_output(
        node_id="node-runtime-deferred",
        reason="bounded_frontier_budget",
    )
    expansion_run.restart_process()
    rows = list(expansion_run.repository.iter_semantic_v3_runtime_deferred(expansion_run.run_id))
    assert [
        (
            row.audit.node_id, row.audit.reason, row.audit.node_digest,
            row.audit.cursor_json, row.audit.cursor_order, row.audit.source_id,
            row.audit.coverage_stratum, row.audit.relevance_rank,
            row.audit.selector_version, row.audit.selector_feature_digest,
        )
        for row in rows
    ] == [
        (
            row.node_id, row.reason, row.node_digest, row.cursor_json, row.cursor_order,
            row.source_id, row.coverage_stratum, row.relevance_rank,
            row.selector_version, row.selector_feature_digest,
        )
        for row in transition.deferred
    ]
    assert [
        (row.source_transition_unit_id, row.created_sequence) for row in rows
    ] == [
        (transition.source_unit_id, transition.receipt_sequence)
        for _ in transition.deferred
    ]
    assert expansion_run.repository.deferred_binding(expansion_run.run_id, "runtime_transition") == (
        len(transition.deferred),
        digest_runtime_deferred_ledger(rows),
        digest_runtime_deferred_cursors(row.audit for row in rows),
    )

@pytest.mark.parametrize("failure_point", ("before_receipt_insert", "after_runtime_deferred_insert", "before_commit"))
def test_runtime_deferred_receipt_crash_leaves_neither_row_nor_aggregate_drift(expansion_run, failure_point) -> None:
    before = expansion_run.repository.snapshot_runtime_deferred_state(expansion_run.run_id)
    expansion_run.inject_receipt_failure(failure_point)
    with pytest.raises(SemanticWorkError):
        expansion_run.validate_defer_on_demand_output(node_id="node-rollback", reason="budget")
    assert expansion_run.repository.snapshot_runtime_deferred_state(expansion_run.run_id) == before

def test_audit_freeze_can_select_runtime_deferred_member_and_binds_combined_population(expansion_run) -> None:
    runtime = expansion_run.add_runtime_deferred_counter_signal(relevance_rank=0)
    expansion_run.validate_last_expansion_output()
    freeze = expansion_run.repository.audit_freeze(expansion_run.run_id)
    assert runtime.node_id in {member.node_id for member in freeze.members}
    assert freeze.population_sha256 == digest_combined_deferred_population(
        expansion_run.catalog.iter_frontier_deferred_for_audit(
            selector_version=expansion_run.selector_version,
            fetch_size=64,
        ),
        expansion_run.repository.iter_semantic_v3_runtime_deferred_audit(
            expansion_run.run_id, fetch_size=64
        ),
    )
```

- [ ] **Step 3: Confirm lifecycle validation tests are RED**

Run: `python -m pytest -q tests/test_semantic_lifecycle_v3.py tests/test_cli.py -k 'validate or result or orphan or pause or resume or runtime_deferred'`

Expected: missing v3 validation/pause/resume.

- [ ] **Step 4: Preflight exact replay/current authority, then append/fsync one result record**

```python
validated = validate_v3_semantic_output(output_payload, issued_input, dependency_receipts)
authority = repository.load_bounded_semantic_v3_authority(run_id)
validate_current_run_authority(workspace, authority, lease=issued_lease, dependencies=dependency_receipts)
existing = repository.get_semantic_v3_receipt(run_id, unit_id)
if existing is not None:
    return resolve_exact_receipt_replay_or_conflict(existing, validated, issued_lease)
committed_prefix = repository.semantic_v3_result_prefix(run_id)
result_ref = result_writer.append(validated.canonical_payload)
result_writer.fsync()
receipt = SemanticReceiptCommit(
    run_id=run_id,
    unit_id=unit_id,
    lease_nonce=lease.nonce,
    input_sha256=lease.input_sha256,
    output_sha256=validated.output_sha256,
    result_ref=result_ref,
    result_pack_length=result_writer.byte_length,
    result_pack_root=result_writer.record_root,
    action=validated.action,
    expected_authority_root=authority.root,
)
```

Validate current source approval/policy, manifest/catalog/proposal/approval/checkpoint, exact
unit/lease/input, dependencies and budgets before any result append; the receipt transaction rechecks
the same authority root. Exact existing receipt/output returns before append. Recovery validates the
DB-committed prefix and truncates only undeclared tail. A committed receipt must always reconstruct
canonical output after process restart.

- [ ] **Step 5: Commit receipt, action, aggregate, dependency, budget, audit freeze, and checkpoint atomically**

```python
try:
    raw_transition = transition_frontier(catalog, unit, validated)
    transition = replace(
        raw_transition,
        deferred=catalog.enrich_runtime_deferred_for_audit(
            raw_transition.deferred,
            selector_version=authority.selector_version,
            max_rows=64,
        ),
    )
    audit_freeze = None
    if transition.ends_expansion:
        audit_freeze = freeze_audit_sample(
            catalog_deferred=catalog.iter_frontier_deferred_for_audit(
                selector_version=authority.selector_version,
                fetch_size=64,
            ),
            runtime_deferred=repository.iter_semantic_v3_runtime_deferred_audit(
                run_id, fetch_size=64
            ),
            pending_runtime_deferred=transition.deferred,
            checkpoint=repository.expansion_checkpoint(
                run_id, expected_root=authority.checkpoint_root
            ),
        )
    committed = repository.commit_semantic_v3_receipt(
        receipt,
        opened_units=transition.opened_units,
        deferred=transition.deferred,
        next_cursor=transition.next_cursor,
        checkpoint_due=(receipt.sequence % CHECKPOINT_UNITS == 0),
        audit_freeze=audit_freeze,
        expected_authority_root=authority.root,
    )
except BaseException:
    observed = repository.get_semantic_v3_receipt(run_id, unit_id)
    if observed is not None and observed.receipt_sha256 == receipt.receipt_sha256:
        committed = observed
    else:
        result_writer.close()
        recover_committed_prefix(
            result_pack_path, committed_prefix.byte_length, committed_prefix.record_root
        )
        raise
exchange.clear_output_if_matches(lease.nonce, validated.output_sha256)
return validation_result(committed)
```

The transition first yields at most 64 `SemanticDeferredNode` values. One bounded sealed-catalog
lookup enriches them into `SemanticDeferredAuditRow` values and verifies each node/source/coverage/
feature digest against the approved selector before result receipt commit. No later audit depends on
an unrecorded lookup. The receipt transaction inserts those exact rows into
`semantic_v3_runtime_deferred`, rejects any node/cursor conflict, and updates the
`runtime_transition` aggregate binding and checkpoint from those exact rows; receipt failure rolls
all of those writes back together. When `audit_freeze` is present, the same DB transaction first
accounts for the pending runtime rows, inserts all 300 ordered member rows, creates
only membership-bound audit units, records ordered/population digests and selector version in the
checkpoint, and advances the binding's current checkpoint root. `next` rejects any audit target not
found under the same run/unit in `semantic_v3_audit_members`. Resume/full verify streams those rows;
it never recomputes or substitutes the set. No source/Codex output may alter budgets, selector
version, completion, approval or policy binding. The receipt charges units/input/active seconds to
its exact phase binding; a phase cannot consume another phase's unused allowance and exhaustion
sets `paused_budget` with that phase/cursor intact.

- [ ] **Step 6: Implement durable pause/resume and CLI commands**

```python
pause = subparsers.add_parser("pause-semantic-work")
pause.add_argument("--workspace", type=Path, default=Path("."))
pause.add_argument("--run-id", required=True)
resume = subparsers.add_parser("resume-semantic-work")
resume.add_argument("--workspace", type=Path, default=Path("."))
resume.add_argument("--run-id", required=True)
```

Pause stops new lease issuance after the current atomic unit and publishes a durable checkpoint within
10 seconds even when the receipt sequence is not a multiple of 16. Resume validates the result-pack
committed prefix, recovers `materializing`/expired leases, then revalidates
proposal/policy/catalog/approval/budget/checkpoint and changes `paused_user` to approved `prepared`;
the following `next` must claim the one running slot again. Pause/finalize/failure releases the
running slot and mutex in its state transaction. Resume returns to the exact cursor. Controlled failure
writes only the ≤64-KiB private safe review fields tested above; an absolute locator is a separate
private reference only when indispensable and is never copied into default review text/public output.

- [ ] **Step 7: Run validation, crash, pause/resume, quality, and CLI tests**

Run: `python -m pytest -q tests/test_semantic_lifecycle_v3.py tests/test_semantic_quality.py tests/test_cli.py -k 'validate or receipt or result_pack or pause or resume or runtime_deferred or semantic_quality'`

Expected: PASS.

- [ ] **Step 8: Commit Task 10 only**

```bash
git add src/portfolio_maker/application/semantic_lifecycle_v3.py src/portfolio_maker/application/semantic_work.py src/portfolio_maker/application/models.py src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/infrastructure/semantic_pack.py src/portfolio_maker/infrastructure/semantic_exchange.py src/portfolio_maker/adapters/cli.py tests/test_semantic_lifecycle_v3.py tests/test_cli.py
git commit -m "feat: checkpoint semantic results"
```

---

### Task 11: Three-Layer Cache and Exact Invalidation

**Files:**
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `src/portfolio_maker/infrastructure/semantic_analyzers.py`
- Modify: `src/portfolio_maker/application/semantic_frontier_v3.py`
- Modify: `src/portfolio_maker/application/semantic_lifecycle_v3.py`
- Modify: `tests/test_sqlite_repository.py`
- Modify: `tests/test_semantic_analyzers.py`
- Modify: `tests/test_semantic_frontier_v3.py`
- Modify: `tests/test_semantic_lifecycle_v3.py`

**Interfaces:**
- Consumes: verified source identity, analyzer/selector/validator contracts, parent/dependency digests, project-boundary digest.
- Produces: `SemanticCacheKey`, three namespaced cache tables, exact hit/miss decision, stale preservation and dependency invalidation.

- [ ] **Step 1: Write RED local-baseline cache coverage tests**

```python
def test_sampled_baseline_cache_never_becomes_complete(tmp_path: Path) -> None:
    source = tmp_path / "large.txt"
    source.write_bytes(b"a" * 200_000)
    first = analyze_with_cache(source, sample_ranges=((0, 4096),))
    second = analyze_with_cache(source, sample_ranges=((0, 4096),))
    assert first.coverage is BaselineCoverage.SAMPLED
    assert second.coverage is BaselineCoverage.SAMPLED
    assert second.cache_hit is True

def test_same_prefix_different_tail_is_not_semantic_result_cache_hit(cache_fixture) -> None:
    before = b"p" * 131_072 + b"old-tail"
    after = b"p" * 131_072 + b"new-tail"
    cache_fixture.store_validated_result(content=before)
    assert cache_fixture.lookup_validated_result(content=after) is None
```

- [ ] **Step 2: Write RED cross-context invalidation tests**

```python
@pytest.mark.parametrize(
    "changed",
    (
        "source_policy_sha256", "masking_policy_version", "analyzer_contract",
        "selector_version", "target_projection_contract", "parent_context_sha256",
        "dependency_root", "input_schema_version", "output_schema_version",
        "validator_version", "project_boundary_sha256",
    ),
)
def test_validated_cache_misses_when_any_binding_changes(cache_fixture, changed) -> None:
    key = cache_fixture.valid_key()
    cache_fixture.store(key)
    assert cache_fixture.lookup(replace_one_binding(key, changed)) is None

def test_ancestor_context_change_invalidates_candidate_closure(cache_fixture) -> None:
    cache_fixture.store_candidate("child", parent_context_sha256="a" * 64)
    cache_fixture.invalidate_from_changed_node("parent")
    assert cache_fixture.candidate("child") is None
```

- [ ] **Step 3: Confirm cache tests are RED**

Run: `python -m pytest -q tests/test_semantic_analyzers.py tests/test_semantic_frontier_v3.py tests/test_semantic_lifecycle_v3.py tests/test_sqlite_repository.py -k 'cache or invalidate or same_prefix'`

Expected: missing cache schema/key/invalidation behavior.

- [ ] **Step 4: Add namespaced cache schema and canonical key**

```python
@dataclass(frozen=True)
class SemanticCacheKey:
    namespace: Literal["local_baseline", "frontier_feature", "validated_result"]
    full_content_sha256: str | None
    sample_identity_sha256: str | None
    source_policy_sha256: str
    masking_policy_version: str
    analyzer_contract: str
    selector_version: str
    target_projection_contract: str
    parent_context_sha256: str
    dependency_root: str
    input_schema_version: str
    output_schema_version: str
    validator_version: str
    project_boundary_sha256: str | None

    @property
    def digest(self) -> str:
        return canonical_sha256(asdict(self))
```

Add `semantic_v3_baseline_cache`, `semantic_v3_frontier_cache`, and
`semantic_v3_result_cache`. Cache rows contain canonical key bytes, safe payload/result
references, coverage and creation contract. Validated-result lookup requires
`full_content_sha256`; sample identity is never substituted.

- [ ] **Step 5: Implement stale preservation and dependency invalidation**

```python
def invalidate_semantic_v3_dependents(
    repository: SQLiteRepository,
    catalog: SemanticCatalogReader,
    revision_id: str,
    changed_node_id: str,
) -> int:
    topology = catalog.stream_ancestor_and_cross_directory_closure(changed_node_id, page_size=256)
    affected = merge_sorted_unique_streams(
        topology,
        repository.iter_reverse_dependency_closure(revision_id, changed_node_id, fetch_size=256),
    )
    return repository.delete_semantic_cache_keys_in_batches(affected, batch_size=256)
```

Unreadable transitions set the new node state to stale/unreadable and retain the prior node and
project record for explicit review. A rename changes hierarchy/parent context even if bytes match.
Selector, masking, analyzer, projection, validator or schema version drift produces a miss.
The sealed catalog is the ancestor/topology authority; the lifecycle DB is the unit/dependency
authority. Each streamed node is bound to the same verified revision/catalog root before deletion,
and the merge retains at most two 256-row pages.

- [ ] **Step 6: Run cache, poisoning, rename, and existing lifecycle tests**

Run: `python -m pytest -q tests/test_semantic_analyzers.py tests/test_semantic_frontier_v3.py tests/test_semantic_lifecycle_v3.py tests/test_sqlite_repository.py -k 'cache or invalidate or stale or semantic_v3'`

Expected: PASS, including a corrupted cached result reference being rejected rather than recomputed as a silent hit.

- [ ] **Step 7: Commit Task 11 only**

```bash
git add src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/infrastructure/semantic_analyzers.py src/portfolio_maker/application/semantic_frontier_v3.py src/portfolio_maker/application/semantic_lifecycle_v3.py tests/test_sqlite_repository.py tests/test_semantic_analyzers.py tests/test_semantic_frontier_v3.py tests/test_semantic_lifecycle_v3.py
git commit -m "feat: invalidate semantic caches exactly"
```

---

### Task 12: Full Verification, Result-Driven Finalization, and Atomic v3 Apply

**Files:**
- Create: `src/portfolio_maker/application/semantic_finalization_v3.py`
- Modify: `src/portfolio_maker/application/semantic_work.py`
- Modify: `src/portfolio_maker/application/semantic_index.py`
- Modify: `src/portfolio_maker/application/project_review_v3.py`
- Modify: `src/portfolio_maker/application/project_boundary.py`
- Modify: `src/portfolio_maker/application/project_decisions.py`
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Create: `tests/test_semantic_finalization_v3.py`
- Modify: `tests/test_semantic_work_lifecycle.py`
- Modify: `tests/test_semantic_index.py`
- Modify: `tests/test_project_review_v3.py`
- Modify: `tests/test_project_decisions.py`

**Interfaces:**
- Consumes: sealed manifest/catalog/packs, receipt/checkpoint chain, result-pack canonical bytes, v3 completion truth table.
- Produces: `verify_semantic_v3_full()`, `finalize_semantic_v3()`, resumable `apply_semantic_v3()`, protocol dispatch and `project_discovery_ready` gate.

- [ ] **Step 1: Write RED full-integrity and false-completion tests**

```python
def test_default_status_is_aggregate_bound_but_full_verify_streams_every_authority(v3_run) -> None:
    assert semantic_index_status(v3_run.status_request()).integrity_level == "aggregate_bound"
    verified = semantic_index_status(v3_run.status_request(verify="full"))
    assert verified.integrity_level == "full_verified"
    assert verified.verified_receipts == v3_run.receipt_count

def test_forged_aggregate_cannot_make_finalize_succeed(v3_run) -> None:
    v3_run.repository.force_aggregate_for_test(frontier_converged=True, validated_count=999)
    with pytest.raises(SemanticWorkError, match="full verification"):
        finalize_semantic_index(v3_run.finalize_request())

@pytest.mark.parametrize("counter_signal", ("deferred", "sampled", "unreadable", "failed"))
def test_material_counter_signal_blocks_project_discovery_ready(v3_run, counter_signal) -> None:
    v3_run.add_material_counter_signal(counter_signal)
    assert finalize_semantic_index(v3_run.finalize_request()).project_discovery_ready is False

@pytest.mark.parametrize("coverage", ("sampled", "unsupported", "unreadable", "failed"))
def test_medium_candidate_with_material_counter_signal_is_review_only(v3_candidate, coverage) -> None:
    v3_candidate.boundary_coverage = replace(v3_candidate.boundary_coverage, material_state=coverage)
    assert decide_project(v3_candidate, mode="automatic").state == "review_required"

def test_manual_exclusion_and_split_merge_lineage_are_not_auto_propagated(v3_candidate) -> None:
    changed = v3_candidate.with_changed_boundary_identity()
    assert decide_project(changed, mode="automatic").state == "review_required"
    assert changed.inherited_manual_decision is None

@pytest.mark.parametrize(
    "failed_condition",
    (
        "representative_full_content", "boundary_conflict", "evidence_ownership_conflict",
        "policy_binding", "analyzer_binding", "selector_binding", "receipt_root_binding",
    ),
)
def test_medium_candidate_requires_every_current_authority_binding(v3_candidate, current_authority, failed_condition) -> None:
    v3_candidate.break_medium_condition(failed_condition)
    assert decide_project(v3_candidate, mode="automatic", authority=current_authority).state == "review_required"
```

- [ ] **Step 2: Write RED result recovery and atomic publication failure tests**

```python
def test_finalize_recovers_from_result_pack_after_exchange_is_deleted(v3_run) -> None:
    v3_run.complete_and_delete_exchange()
    first = finalize_semantic_index(v3_run.finalize_request())
    v3_run.restart_process()
    second = finalize_semantic_index(v3_run.finalize_request())
    assert first.final_manifest_sha256 == second.final_manifest_sha256

@pytest.mark.parametrize("failure_point", ("after_preflight", "after_manifest_write", "after_binding_update"))
def test_finalize_failure_restores_only_small_previous_binding_and_manifest(v3_run, failure_point) -> None:
    before = v3_run.snapshot_small_publication_state()
    v3_run.inject_finalize_failure(failure_point)
    with pytest.raises(SemanticWorkError):
        finalize_semantic_index(v3_run.finalize_request())
    assert v3_run.snapshot_small_publication_state() == before
```

- [ ] **Step 3: Write RED resumable apply and active preservation tests**

```python
def test_v3_apply_resumes_from_last_import_receipt_and_switches_once(finalized_v3, legacy_active) -> None:
    finalized_v3.interrupt_apply_after_chunks(2)
    assert finalized_v3.repository.active_revision_id() == legacy_active.revision_id
    result = apply_semantic_index(finalized_v3.apply_request())
    assert result.revision_id == finalized_v3.revision_id
    assert finalized_v3.repository.active_revision_id() == finalized_v3.revision_id
    assert finalized_v3.repository.imported_node_count() == finalized_v3.final_node_count

def test_policy_drift_during_apply_preserves_previous_active(finalized_v3, legacy_active) -> None:
    finalized_v3.change_policy_binding()
    with pytest.raises(SemanticIndexError, match="policy"):
        apply_semantic_index(finalized_v3.apply_request())
    assert finalized_v3.repository.active_revision_id() == legacy_active.revision_id

def test_structural_pack_and_catalog_topology_or_order_mismatch_fails_full_verify(v3_run) -> None:
    v3_run.mutate_catalog_child_order_while_resealing_component_for_test()
    with pytest.raises(SemanticWorkError, match="topology"):
        verify_semantic_v3_full(v3_run.context())

def test_full_verify_streams_initial_and_runtime_deferred_and_binds_combined_root(v3_run) -> None:
    v3_run.add_runtime_deferred_counter_signal(relevance_rank=0)
    v3_run.complete()
    verification = verify_semantic_v3_full(v3_run.context())
    assert verification.runtime_deferred_count == 1
    assert verification.combined_deferred_population_sha256 == (
        v3_run.repository.current_checkpoint(v3_run.run_id).audit_population_sha256
    )

@pytest.mark.parametrize(
    "field",
    (
        "node_digest", "cursor_json", "cursor_order", "source_id", "coverage_stratum",
        "relevance_rank", "selector_version", "selector_feature_digest",
        "source_transition_unit_id", "created_sequence",
    ),
)
def test_runtime_deferred_row_field_tamper_fails_full_verify(v3_run, field) -> None:
    runtime = v3_run.add_runtime_deferred_counter_signal(relevance_rank=0)
    v3_run.repository.mutate_runtime_deferred_field_for_test(
        v3_run.run_id, runtime.node_id, field
    )
    with pytest.raises(SemanticWorkError, match="runtime deferred"):
        verify_semantic_v3_full(v3_run.context())
```

- [ ] **Step 4: Confirm finalization/apply/project tests are RED**

Run: `python -m pytest -q tests/test_semantic_finalization_v3.py tests/test_semantic_work_lifecycle.py tests/test_semantic_index.py tests/test_project_review_v3.py -k 'full_verify or runtime_deferred or finalize or v3_apply or discovery_ready'`

Expected: missing v3 full verifier/finalizer/apply dispatch.

- [ ] **Step 5: Implement streaming full verifier**

```python
def verify_semantic_v3_full(context: SemanticV3Context) -> FullVerification:
    authority = context.repository.load_bounded_semantic_v3_authority(context.run_id)
    validate_current_run_authority(context.workspace, authority)
    manifest = verify_manifest_self_hash(context.manifest_path)
    catalog = verify_catalog_streaming(context.catalog_path, manifest.catalog_seal)
    packs = {kind: verify_pack_streaming(path, seal) for kind, path, seal in context.prepare_packs()}
    topology = verify_structural_pack_catalog_equivalence_streaming(
        packs["structural"], catalog, fetch_size=256
    )
    lifecycle = verify_lifecycle_streaming(
        proposal=context.repository.get_semantic_v3_proposal_bytes_for_run(context.run_id),
        approval=context.repository.approval(context.run_id),
        units=context.repository.iter_semantic_v3_units(context.run_id, fetch_size=256),
        dependencies=context.repository.iter_semantic_v3_dependencies(context.run_id, fetch_size=256),
        deferred=context.repository.iter_semantic_v3_deferred(context.run_id, fetch_size=256),
        catalog_deferred=catalog.iter_frontier_deferred_for_audit(
            selector_version=authority.selector_version,
            fetch_size=256,
        ),
        runtime_deferred=context.repository.iter_semantic_v3_runtime_deferred(
            context.run_id, fetch_size=256
        ),
        deepening_deferred=None,
        audit_members=context.repository.iter_semantic_v3_audit_members(context.run_id, fetch_size=256),
        receipts=context.repository.iter_receipts(context.run_id, fetch_size=256),
        result_pack_path=context.result_pack_path,
        checkpoints=context.repository.iter_checkpoints(context.run_id, fetch_size=64),
        expected_authority=authority,
    )
    return evaluate_verified_completion(manifest, catalog, packs, topology, lifecycle)
```

Verify manifest self-hash, catalog file/logical roots, all pack record roots, receipt sequences,
canonical proposal/approval, every unit target and dependency root, deferred/cursor state,
input/output/dependency hashes, committed result offsets/lengths/hashes, checkpoint result roots,
exact ordered audit membership-to-unit mapping, selector convergence and current source
approval/policy/content binding. Cross-verify every ordered structural edge and payload reference
between the structural pack and catalog. Read in bounded batches; never materialize the catalog,
pack, unit, dependency, audit member or receipt set. Independently recompute the sealed catalog
initial root, repository runtime root and canonical combined deferred population root; each must match
its aggregate/checkpoint binding. A runtime row whose reason, node digest, exact cursor JSON/order,
source, coverage stratum, relevance rank, selector version/feature digest, source unit or creation
sequence does not follow its receipt transition and sealed-catalog feature row fails verification.
The verifier recomputes the runtime ledger root from `SemanticRuntimeDeferredRow` including
`source_transition_unit_id` and `created_sequence`, joins those two values to the exact receipt's
unit/sequence, and only then projects `.audit` into the combined deferred population root.

- [ ] **Step 6: Implement result-driven finalization and recoverable publication**

```python
with FinalPackWriter(temp_final_pack) as writer:
    for node in stream_structural_nodes_from_pack(verification.packs["structural"]):
        verification.catalog.assert_same_node_and_ordered_edges(node)
        semantic = verification.result_for(node.node_id)
        writer.append(merge_structural_truth(node, semantic))
    final_seal = writer.seal()
preflight_publication(final_seal, final_manifest_bytes)
publish_final_generation_and_small_binding(
    previous=repository.snapshot_semantic_binding(revision_id),
    manifest_bytes=final_manifest_bytes,
    final_seal=final_seal,
)
```

Full ordered topology always comes from the structural pack and must be byte/digest-equivalent to
the catalog's normalized `children` rows; `node_seen` emits one bounded structural-node record and
one edge record, never a whole child list. Semantic results can annotate but cannot create
nodes/children/locators. Preflight canonical bytes and root before publication. On any post-write
failure restore and verify the previous small DB binding/pointer and manifest bytes/absence; do not
copy a multi-GiB generation.

- [ ] **Step 7: Implement bounded idempotent v3 apply and project review dispatch**

```python
def apply_semantic_v3(request: ApplySemanticIndexRequest) -> ApplySemanticIndexResult:
    verified = verify_final_binding(request.workspace)
    for chunk in stream_final_import_chunks(verified, after=repository.last_import_receipt(verified.revision_id), size=100):
        repository.import_semantic_v3_chunk(verified.revision_id, chunk)
    repository.verify_graph_and_activate_semantic_v3(verified.revision_id, verified.policy_sha256)
    return ApplySemanticIndexResult(verified.revision_id, protocol=SEMANTIC_PROTOCOL_V3)
```

`project_review_v3` dispatches from active manifest protocol. It may generate project candidates
only when `structural_complete`, `local_baseline_complete`, all planned frontier/audit receipts,
convergence and material counter-signal conditions make `project_discovery_ready=True`.

```python
def can_auto_include_medium(candidate: ProjectCandidate, authority: CandidateAuthority) -> bool:
    return (
        candidate.confidence == "medium"
        and candidate.boundary_coverage.complete
        and len(set(candidate.grounded_signal_types)) >= 2
        and candidate.all_representatives_full_content_bound
        and not candidate.has_material_deferred_counter_signal
        and not candidate.has_partial_sampled_unreadable_failed_signal
        and not candidate.has_boundary_conflict
        and not candidate.has_evidence_ownership_conflict
        and candidate.binding.matches(
            source_policy_sha256=authority.source_policy_sha256,
            analyzer_contract=authority.analyzer_contract,
            selector_version=authority.selector_version,
            receipt_root=authority.receipt_root,
        )
    )
```

Until these conditions hold, medium is `review_required`. Changed v3 boundary identity never
inherits an old manual exclusion, split or merge decision automatically; it produces an explicit
review item.

- [ ] **Step 8: Run full-verifier, crash, apply, legacy, and project-review tests**

Run: `python -m pytest -q tests/test_semantic_finalization_v3.py tests/test_semantic_work_lifecycle.py tests/test_semantic_index.py tests/test_project_review_v3.py tests/test_project_decisions.py tests/test_sqlite_repository.py -k 'semantic or runtime_deferred or deferred_index or revision or project_discovery_ready or medium or lineage'`

Expected: PASS; legacy v1/v2 active remains readable and unchanged until a fully verified v3 activation transaction succeeds.

- [ ] **Step 9: Commit Task 12 only**

```bash
git add src/portfolio_maker/application/semantic_finalization_v3.py src/portfolio_maker/application/semantic_work.py src/portfolio_maker/application/semantic_index.py src/portfolio_maker/application/project_review_v3.py src/portfolio_maker/application/project_boundary.py src/portfolio_maker/application/project_decisions.py src/portfolio_maker/infrastructure/sqlite_repository.py tests/test_semantic_finalization_v3.py tests/test_semantic_work_lifecycle.py tests/test_semantic_index.py tests/test_project_review_v3.py tests/test_project_decisions.py
git commit -m "feat: verify and activate semantic v3"
```

---

### Task 13: Explicit Resumable v2-Staging Migration

**Files:**
- Create: `src/portfolio_maker/application/semantic_migration_v3.py`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `src/portfolio_maker/adapters/cli.py`
- Create: `tests/test_semantic_migration_v3.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: named v2 staging manifest/capsule/structural/locator/plan managed state only.
- Produces: explicit `migrate_semantic_staging()`, v3 `derived_from`, private bounded migration report and resumable migration checkpoint; never promotes v2 receipts.

- [ ] **Step 1: Write RED source-isolation and provenance tests**

```python
def test_migration_never_reads_raw_source(v2_staging, monkeypatch) -> None:
    monkeypatch.setattr(Path, "read_bytes", reject_reads_outside(v2_staging.managed_dir))
    result = migrate_semantic_staging(v2_staging.request())
    assert result.derived_from_revision_id == v2_staging.revision_id
    assert result.protocol == SEMANTIC_PROTOCOL_V3
    assert result.promoted_receipt_count == 0

def test_migration_requires_current_source_approval(v2_staging) -> None:
    v2_staging.remove_current_approval()
    with pytest.raises(SemanticMigrationError, match="source approval"):
        migrate_semantic_staging(v2_staging.request())
```

- [ ] **Step 2: Write RED fail-closed compatibility and rollback tests**

```python
def test_pre_attestation_plan_requires_fresh_prepare(v2_pre_attestation) -> None:
    with pytest.raises(SemanticMigrationError, match="fresh prepare"):
        migrate_semantic_staging(v2_pre_attestation.request())

def test_failed_or_interrupted_migration_preserves_all_v2_state(v2_staging) -> None:
    before = v2_staging.snapshot_all_v2_bytes_and_rows()
    v2_staging.inject_migration_failure(after_nodes=100)
    with pytest.raises(SemanticMigrationError):
        migrate_semantic_staging(v2_staging.request())
    assert v2_staging.snapshot_all_v2_bytes_and_rows() == before
```

- [ ] **Step 3: Confirm migration/CLI tests are RED**

Run: `python -m pytest -q tests/test_semantic_migration_v3.py tests/test_cli.py -k 'migrate_semantic_staging or migration'`

Expected: command/use case/schema are absent.

- [ ] **Step 4: Add migration-run checkpoint schema and exact request**

```python
@dataclass(frozen=True)
class MigrateSemanticStagingRequest:
    workspace: Path
    revision_id: str

CREATE TABLE IF NOT EXISTS semantic_v3_migration_runs (
  migration_id TEXT PRIMARY KEY,
  source_revision_id TEXT NOT NULL,
  target_revision_id TEXT NOT NULL,
  source_manifest_sha256 TEXT NOT NULL,
  source_policy_sha256 TEXT NOT NULL,
  projection_contract TEXT NOT NULL,
  last_record_kind TEXT,
  last_record_offset INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL,
  report_sha256 TEXT
);
```

The checkpoint is bound to immutable v2 managed inputs and resumes byte-idempotently. A changed
manifest/policy/projection fails closed and does not reset the run.

- [ ] **Step 5: Implement streaming conversion and bounded private report**

```python
def migrate_semantic_staging(request: MigrateSemanticStagingRequest) -> SemanticMigrationResult:
    legacy = verify_migratable_v2_staging(request)
    run = repository.get_or_create_migration(legacy.binding)
    with resumed_v3_sink(run) as sink:
        for record in stream_v2_managed_records(legacy, after=run.cursor):
            sink.append(convert_v2_record(record))
            repository.checkpoint_migration(run.id, record.kind, record.next_offset)
    return seal_migrated_v3_staging(sink, derived_from=legacy.revision_id, promoted_receipts=0)
```

Validate source ID, policy hash, node count, topology and every payload digest. Write only a safe
bounded private migration report. Preserve v2 files/rows/status exactly; deletion is out of scope.

- [ ] **Step 6: Add CLI command and safe output**

```python
migrate = subparsers.add_parser("migrate-semantic-staging")
migrate.add_argument("--workspace", type=Path, default=Path("."))
migrate.add_argument("--revision-id", required=True)
```

Output revision IDs, protocol, counts, digests, state and private report reference only. Do not
print source locators, private URLs, credentials or managed payload content.

- [ ] **Step 7: Run migration, interruption, CLI, and legacy dispatch tests**

Run: `python -m pytest -q tests/test_semantic_migration_v3.py tests/test_cli.py tests/test_semantic_index.py tests/test_semantic_work_lifecycle.py -k 'migration or migrate_semantic_staging or legacy or protocol'`

Expected: PASS and no automatic migration in prepare/status/next/apply.

- [ ] **Step 8: Commit Task 13 only**

```bash
git add src/portfolio_maker/application/semantic_migration_v3.py src/portfolio_maker/application/models.py src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/adapters/cli.py tests/test_semantic_migration_v3.py tests/test_cli.py
git commit -m "feat: migrate semantic staging explicitly"
```

---

### Task 14: Project-Bound Deepening and Inactive Review Overlay

**Files:**
- Create: `src/portfolio_maker/application/semantic_deepening_v3.py`
- Modify: `src/portfolio_maker/application/semantic_finalization_v3.py`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/application/project_boundary.py`
- Modify: `src/portfolio_maker/application/project_decisions.py`
- Modify: `src/portfolio_maker/application/project_review_v3.py`
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `src/portfolio_maker/infrastructure/semantic_catalog.py`
- Modify: `src/portfolio_maker/workspace.py`
- Modify: `src/portfolio_maker/adapters/cli.py`
- Create: `tests/test_semantic_deepening_v3.py`
- Modify: `tests/test_project_review_v3.py`
- Modify: `tests/test_project_decisions.py`
- Modify: `tests/test_semantic_catalog_v3.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: current opaque project ID, active v3 boundary digest, verified frontier receipts and a separately approved deepening proposal.
- Produces: `prepare_semantic_deepening()`, derived deepening run, inactive semantic overlay, cancellation/invalidation, and fail-closed medium automatic inclusion.
- Extends: `verify_semantic_v3_full()` so a deepening run cannot complete without streaming its exact run-scoped deferred index.

- [ ] **Step 1: Write RED authority and scope tests**

```python
def test_prepare_deepening_accepts_only_current_opaque_project_id(active_v3_projects) -> None:
    result = prepare_semantic_deepening(PrepareSemanticDeepeningRequest(
        workspace=active_v3_projects.workspace,
        project_id=active_v3_projects.project_id,
    ))
    assert result.approval_kind == "project_deepening"
    assert result.budget == DEEPENING_BUDGET
    assert result.project_boundary_sha256 == active_v3_projects.boundary_sha256
    plan, plan_bytes = active_v3_projects.repository.deepening_plan_bytes(result.run_id)
    envelope, proposal_bytes, stored_plan_bytes = active_v3_projects.repository.get_semantic_v3_proposal_bytes_for_run(result.run_id)
    assert stored_plan_bytes == plan_bytes
    authority = active_v3_projects.repository.deepening_authority_context(result.run_id)
    assert sha256_hex(plan_bytes) == envelope.proposal.plan_sha256
    assert plan.authority_root == envelope.proposal.authority_root == authority.root
    assert plan.parent_proposal_sha256 == envelope.proposal.parent_proposal_sha256 == authority.parent_proposal_sha256
    assert envelope.proposal.parent_checkpoint_root == active_v3_projects.frontier_checkpoint_root
    assert plan.source_policy_sha256 == envelope.proposal.policy_sha256 == authority.base.source_policy_sha256
    assert plan.catalog_root == envelope.proposal.catalog_root == authority.base.catalog_root
    assert plan.private_context_scope == envelope.proposal.private_context_scope == authority.base.private_context_scope
    assert plan.evidence_policy_sha256 == envelope.proposal.evidence_policy_sha256 == authority.evidence_policy_sha256
    assert envelope.proposal.target_projection_contract == plan.target_projection_contract
    assert envelope.proposal.phase_bindings == (
        SemanticPhaseBinding(FrontierPhase.DEEPENING, DEEPENING_BUDGET, result.estimated_units, result.estimated_input_bytes, result.estimated_wall_seconds),
    )
    assert not hasattr(envelope.proposal, "units")
    for locator in ("../project", "/Users/example/project", "https://github.com/private/repo"):
        with pytest.raises(SemanticDeepeningError):
            prepare_semantic_deepening(PrepareSemanticDeepeningRequest(active_v3_projects.workspace, locator))

def test_deepening_requires_separate_exact_approval(active_v3_projects) -> None:
    proposal = prepare_semantic_deepening(active_v3_projects.request())
    with pytest.raises(SemanticWorkError, match="approval"):
        next_semantic_work(NextSemanticWorkRequest(active_v3_projects.workspace, proposal.run_id))

def test_deepening_phase_budget_drift_requires_new_proposal(active_v3_projects) -> None:
    proposal = prepare_semantic_deepening(active_v3_projects.request())
    active_v3_projects.mutate_deepening_hard_budget(proposal.run_id, max_units=257)
    with pytest.raises(SemanticWorkError, match="stale"):
        approve_semantic_execution(active_v3_projects.approval_request(proposal))
```

- [ ] **Step 2: Write RED non-authority, invalidation, and idempotency tests**

```python
def test_deepening_result_stays_inactive_and_cannot_mutate_downstream_authority(deepening_run) -> None:
    deepening_run.complete()
    assert deepening_run.repository.active_project_rows_unchanged()
    assert deepening_run.repository.evidence_rows_unchanged()
    assert deepening_run.repository.artifact_rows_unchanged()
    assert deepening_run.overlay_state == "inactive_review_required"

@pytest.mark.parametrize("change", ("split", "merge", "exclude", "policy", "boundary"))
def test_project_change_cancels_or_invalidates_deepening_without_deleting_receipts(deepening_run, change) -> None:
    receipt_bytes = deepening_run.receipt_bytes()
    deepening_run.apply_project_change(change)
    assert deepening_run.run_state in {SemanticRunState.PAUSED_USER, SemanticRunState.BLOCKED}
    assert deepening_run.overlay_state in {DeepeningOverlayState.CANCELLED, DeepeningOverlayState.INVALIDATED}
    assert deepening_run.receipt_bytes() == receipt_bytes

def test_retry_and_resume_are_byte_idempotent(deepening_run) -> None:
    first = deepening_run.pause_and_resume()
    second = deepening_run.pause_and_resume()
    assert first.proposal_bytes == second.proposal_bytes
    assert first.receipt_root == second.receipt_root
```

- [ ] **Step 3: Write RED downstream-authority and deferred-index publication tests**

```python
def test_deepening_overlay_cannot_upgrade_review_required_medium_candidate(deepening_run, candidate) -> None:
    before = decide_project(candidate, mode="automatic")
    deepening_run.complete_with_high_confidence_overlay(candidate.project_id)
    after = decide_project(candidate, mode="automatic")
    assert before.state == after.state == "review_required"

def test_deepening_deferred_index_paths_are_run_isolated_for_same_project(active_v3_projects) -> None:
    first = prepare_semantic_deepening(active_v3_projects.request(purpose="portfolio-boundary"))
    second = prepare_semantic_deepening(active_v3_projects.request(purpose="counter-signal-audit"))
    assert first.project_id == second.project_id
    assert first.run_id != second.run_id
    assert first.deferred_index_path_relative != second.deferred_index_path_relative

def test_deepening_deferred_index_reopens_with_exact_final_path_and_seal(active_v3_projects) -> None:
    proposal = prepare_semantic_deepening(active_v3_projects.request())
    active_v3_projects.restart_process()
    binding = active_v3_projects.repository.deepening_deferred_binding(proposal.run_id)
    opened = verify_deepening_deferred_index_read_only(
        active_v3_projects.workspace / binding.path_relative,
        binding.seal,
    )
    assert opened.logical_root == proposal.deferred_root
    assert opened.count == proposal.deferred_count
    assert opened.cursor_root == proposal.deferred_cursor_root

@pytest.mark.parametrize("failure_point", ("after_temp_fsync", "after_atomic_publish", "before_db_binding"))
def test_deepening_deferred_index_crash_recovers_same_intent_byte_idempotently(active_v3_projects, failure_point) -> None:
    active_v3_projects.inject_prepare_failure(failure_point)
    with pytest.raises(SemanticDeepeningError):
        prepare_semantic_deepening(active_v3_projects.request())
    before = active_v3_projects.repository.only_deepening_prepare_intent()
    proposal = prepare_semantic_deepening(active_v3_projects.request())
    after = active_v3_projects.repository.deepening_prepare_intent(before.intent_id)
    assert proposal.run_id == before.run_id == after.run_id
    assert after.state == "bound"
    assert after.deferred_file_sha256 == proposal.deferred_file_sha256

@pytest.mark.parametrize("tamper", ("path", "file", "missing_file", "logical_root", "count", "cursor_root"))
def test_deepening_deferred_index_tamper_or_binding_mismatch_fails(active_v3_projects, tamper) -> None:
    proposal = prepare_semantic_deepening(active_v3_projects.request())
    active_v3_projects.tamper_deepening_deferred_binding(proposal.run_id, tamper)
    with pytest.raises(SemanticWorkError, match="deferred index"):
        verify_semantic_v3_full(active_v3_projects.context(proposal.run_id))

def test_deepening_full_verify_opens_and_streams_run_deferred_index(active_v3_projects) -> None:
    proposal = prepare_semantic_deepening(active_v3_projects.request())
    active_v3_projects.approve_and_complete(proposal.run_id)
    verified = verify_semantic_v3_full(active_v3_projects.context(proposal.run_id))
    assert verified.deepening_deferred_count == proposal.deferred_count
    assert verified.deepening_deferred_root == proposal.deferred_root

def test_no_bound_deepening_run_points_at_missing_final_index(active_v3_projects) -> None:
    active_v3_projects.inject_prepare_failure("before_db_binding")
    with pytest.raises(SemanticDeepeningError):
        prepare_semantic_deepening(active_v3_projects.request())
    assert active_v3_projects.repository.bound_deepening_run_count() == 0
    assert active_v3_projects.only_unbound_final_index().is_file()

def test_unbound_mismatched_deferred_residue_is_not_adopted_deleted_or_overwritten(active_v3_projects) -> None:
    residue = active_v3_projects.write_unbound_mismatched_final_index()
    before = residue.read_bytes()
    with pytest.raises(SemanticDeepeningError, match="unbound"):
        prepare_semantic_deepening(active_v3_projects.request())
    assert residue.read_bytes() == before
```

- [ ] **Step 4: Confirm deepening/project tests are RED**

Run: `python -m pytest -q tests/test_semantic_deepening_v3.py tests/test_project_review_v3.py tests/test_project_decisions.py tests/test_semantic_catalog_v3.py tests/test_cli.py -k 'deepening or deferred_index or overlay or authority'`

Expected: missing deepening planner/schema/CLI and inactive-overlay authority.

- [ ] **Step 5: Add deepening run lineage and bounded proposal**

The run-scoped `deferred.sqlite` uses this complete file contract in
`semantic_catalog.py`; it is separate from the main lifecycle database:

```python
@dataclass(frozen=True)
class DeferredIndexSeal:
    byte_length: int
    file_sha256: str
    logical_root: str
    deferred_count: int
    cursor_root: str
```

```sql
CREATE TABLE deferred_nodes (
  node_id TEXT PRIMARY KEY,
  reason TEXT NOT NULL,
  node_digest TEXT NOT NULL,
  cursor_json TEXT NOT NULL,
  cursor_order INTEGER NOT NULL UNIQUE,
  source_id TEXT NOT NULL,
  coverage_stratum TEXT NOT NULL,
  relevance_rank INTEGER NOT NULL,
  selector_version TEXT NOT NULL,
  selector_feature_digest TEXT NOT NULL
);
CREATE INDEX deferred_nodes_cursor
  ON deferred_nodes(cursor_order, node_id);
```

`SemanticDeferredIndexWriter` inserts `SemanticDeferredAuditRow` canonical rows in bounded pages,
cross-checks their selector feature fields against the sealed source catalog, checkpoints and fsyncs the
temporary file, rejects WAL/SHM sidecars, and derives `logical_root`, `deferred_count` and
`cursor_root` by streaming `(cursor_order, node_id)` rows. `seal()` runs integrity check, compacts,
fsyncs, closes the writer, hashes the final bytes and reopens the file read-only before returning the
seal. The reader independently recomputes every field rather than trusting file metadata.

```sql
CREATE TABLE IF NOT EXISTS semantic_v3_deepening_runs (
  run_id TEXT PRIMARY KEY REFERENCES semantic_v3_runs(run_id),
  intent_id TEXT NOT NULL UNIQUE REFERENCES semantic_v3_deepening_prepare_intents(intent_id),
  project_id TEXT NOT NULL,
  parent_revision_id TEXT NOT NULL,
  authority_root TEXT NOT NULL,
  parent_proposal_sha256 TEXT NOT NULL,
  parent_frontier_checkpoint_root TEXT NOT NULL,
  project_boundary_sha256 TEXT NOT NULL,
  evidence_policy_sha256 TEXT NOT NULL,
  deepening_plan_sha256 TEXT NOT NULL,
  unit_queue_root TEXT NOT NULL,
  deferred_index_path_relative TEXT NOT NULL,
  deferred_file_sha256 TEXT NOT NULL,
  deferred_file_bytes INTEGER NOT NULL,
  deferred_logical_root TEXT NOT NULL,
  deferred_count INTEGER NOT NULL,
  deferred_cursor_root TEXT NOT NULL,
  proposal_sha256 TEXT NOT NULL,
  overlay_state TEXT NOT NULL CHECK(overlay_state IN ('proposed','inactive_review_required','cancelled','invalidated')),
  invalidated_reason TEXT,
  FOREIGN KEY(run_id, proposal_sha256)
    REFERENCES semantic_v3_proposals(run_id, proposal_sha256)
);
CREATE INDEX IF NOT EXISTS semantic_v3_deepening_project
  ON semantic_v3_deepening_runs(project_id, parent_revision_id, overlay_state);

CREATE TABLE IF NOT EXISTS semantic_v3_deepening_prepare_intents (
  intent_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  revision_id TEXT NOT NULL REFERENCES semantic_v3_revision_bindings(revision_id),
  project_id TEXT NOT NULL,
  authority_root TEXT NOT NULL,
  purpose TEXT NOT NULL,
  temp_path_relative TEXT NOT NULL,
  final_path_relative TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('preparing','published','bound','failed')),
  expected_file_sha256 TEXT,
  expected_file_bytes INTEGER,
  expected_logical_root TEXT,
  expected_deferred_count INTEGER,
  expected_cursor_root TEXT,
  safe_error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id, authority_root, purpose)
);
CREATE INDEX IF NOT EXISTS semantic_v3_deepening_intent_state
  ON semantic_v3_deepening_prepare_intents(state, revision_id, run_id);
```

```python
authority = load_verified_deepening_authority_context(
    workspace=request.workspace,
    project_id=request.project_id,
)
intent = repository.get_or_create_deepening_prepare_intent(
    project_id=authority.project_id,
    authority_root=authority.root,
    purpose=authority.purpose,
    revision_id=authority.base.revision_id,
)
temp_path = paths.semantic_v3_deepening_deferred_temp_path(
    authority.base.revision_id, intent.run_id
)
final_path = paths.semantic_v3_deepening_deferred_index_path(
    authority.base.revision_id, intent.run_id
)
deferred_writer = SemanticDeferredIndexWriter.resume_or_create(
    temp_path,
    expected=intent.expected_seal,
)
deepening_draft = ProjectDeepeningPlanner(catalog, active_projects).prepare(
    authority=authority,
    run_id=intent.run_id,
    project_id=request.project_id,
    budget=DEEPENING_BUDGET,
    max_dependencies=MAX_DEPENDENCIES,
    max_page_size=64,
    deferred_sink=deferred_writer,
)
deferred_seal = deferred_writer.seal()
repository.record_deepening_prepare_seal(intent.intent_id, deferred_seal)
publish_deepening_deferred_index_atomically(
    temp_path=temp_path,
    final_path=final_path,
    expected_seal=deferred_seal,
)
verify_deepening_deferred_index_read_only(final_path, deferred_seal)
repository.mark_deepening_prepare_published(intent.intent_id, deferred_seal)
deepening_plan = finalize_deepening_plan(deepening_draft, deferred_seal)
plan_bytes = canonical_json_bytes(deepening_plan)
plan_sha256 = sha256_hex(plan_bytes)
proposal = build_deepening_execution_proposal(
    deepening_plan=deepening_plan,
    authority=authority,
    plan_sha256=plan_sha256,
    phase_bindings=(SemanticPhaseBinding(
        FrontierPhase.DEEPENING,
        DEEPENING_BUDGET,
        len(deepening_plan.units),
        sum(unit.estimated_input_bytes for unit in deepening_plan.units),
        estimate_deepening_wall_seconds(deepening_plan),
    ),),
)
proposal_bytes = canonical_json_bytes(proposal)
envelope = SemanticProposalEnvelope(proposal, sha256_hex(proposal_bytes))
repository.create_semantic_v3_deepening_proposal_run_and_units(
    envelope=envelope,
    canonical_bytes=proposal_bytes,
    deepening_plan=deepening_plan,
    deepening_plan_bytes=plan_bytes,
    units=deepening_plan.units,
    intent_id=intent.intent_id,
    deferred_index_path_relative=paths.workspace_relative(final_path),
    deferred_index_seal=deferred_seal,
    deferred_binding=(
        deepening_plan.deferred_count,
        deepening_plan.deferred_root,
        deepening_plan.deferred_cursor_root,
    ),
)
```

Extend the Task 12 verifier with the run-kind-specific file authority; frontier runs keep `None`,
while deepening runs must supply an opened, verified stream:

```python
def verify_deepening_deferred_authority(
    context: SemanticV3Context,
    authority: BoundedSemanticV3Authority,
) -> VerifiedDeferredIndex | None:
    if authority.approval_kind != "project_deepening":
        return None
    binding = context.repository.deepening_deferred_binding(context.run_id)
    path = context.paths.resolve_safe_workspace_relative(binding.path_relative)
    opened = verify_deepening_deferred_index_read_only(path, binding.seal)
    verify_deepening_deferred_binding_equivalence(
        opened=opened,
        binding=binding,
        proposal=authority.proposal,
        plan=authority.plan,
        intent=context.repository.bound_deepening_prepare_intent(context.run_id),
    )
    return opened

deepening_deferred = verify_deepening_deferred_authority(context, authority)
catalog_deferred = (
    None if deepening_deferred is not None
    else catalog.iter_frontier_deferred_for_audit(
        selector_version=authority.selector_version,
        fetch_size=256,
    )
)
lifecycle = verify_lifecycle_streaming(
    proposal=context.repository.get_semantic_v3_proposal_bytes_for_run(context.run_id),
    approval=context.repository.approval(context.run_id),
    units=context.repository.iter_semantic_v3_units(context.run_id, fetch_size=256),
    dependencies=context.repository.iter_semantic_v3_dependencies(context.run_id, fetch_size=256),
    deferred=context.repository.iter_semantic_v3_deferred(context.run_id, fetch_size=256),
    catalog_deferred=catalog_deferred,
    runtime_deferred=context.repository.iter_semantic_v3_runtime_deferred(
        context.run_id, fetch_size=256
    ),
    deepening_deferred=deepening_deferred.iter_rows(fetch_size=256)
        if deepening_deferred is not None else None,
    audit_members=context.repository.iter_semantic_v3_audit_members(
        context.run_id, fetch_size=256
    ),
    receipts=context.repository.iter_receipts(context.run_id, fetch_size=256),
    result_pack_path=context.result_pack_path,
    checkpoints=context.repository.iter_checkpoints(context.run_id, fetch_size=64),
    expected_authority=authority,
)
```

Bind project ID, parent revision/checkpoint, boundary digest, selector/projection/schema versions,
policy/masking scope and the exact 256-unit/64-MiB/3,600-second budget. This is a new durable
`semantic_v3_proposals` row addressed by revision plus digest; do not select it by revision alone and
do not copy approval from the frontier run. The one repository call is a transaction that rehashes
both canonical bodies, requires proposal `plan_sha256` and parent/projection/schema/validator/purpose
bindings to match the deepening plan, derives `unit_queue_root` from `deepening_plan.units`, and then
stores proposal, plan, run, units, dependencies and aggregate together.
`load_verified_deepening_authority_context()` follows the active opaque project provenance to its
exact parent run/proposal digest (never revision alone), rehashes that canonical parent envelope,
full-verified checkpoint, sealed manifest/catalog, current source/evidence policy, privacy scope and
project boundary, and returns one root-bound context. Planner and proposal builder both accept that
same object and reject any plan field mismatch before publication.
The planner pages the sealed source catalog 64 rows at a time, retains at most 256 selected units,
and streams all unselected project-scope nodes to a run-scoped temporary `deferred.sqlite`. It seals
that index before proposal construction. The durable intent allocates the run ID before any path is
opened, so both temporary and final paths are revision/run scoped and never project scoped. After
seal/integrity/fsync/read-only verification, publication is one same-directory atomic rename. An
existing exact final file is an idempotent success; a missing intent or mismatched temp/final file is
private unbound residue and fails closed without adoption, deletion or overwrite.

The final repository transaction receives the safe workspace-relative final path and the complete
`DeferredIndexSeal`. It reopens that exact file read-only, verifies file hash/bytes, logical root,
count and cursor root, stores proposal/plan/run/units/dependencies/aggregate/deepening binding, and
changes the same intent from `published` to `bound`. A bound run may never reference a missing file.
Crash recovery reuses `(project_id, authority_root, purpose)` and its existing run ID: before publish
it verifies/resumes or safely rewrites only that intent's temp; after rename but before DB binding it
verifies the exact final seal and completes the same binding. Full verification of a deepening run
must reopen and stream this run-scoped index and match its file/logical/count/cursor bindings to the
proposal, plan, intent and `semantic_v3_deepening_runs`. For a frontier run, the initial population is
the sealed catalog index; for a deepening run it is only the run-scoped deferred index. In either
case, full verification merges that initial authority with the same run's bounded runtime deferred
rows and verifies the exact combined checkpoint root; it never double-counts the parent frontier
catalog. No deepening deferred row tuple enters the plan or Python heap.

- [ ] **Step 6: Implement inactive overlay and cancel/replay**

Deepening output is stored under its run and exposed only as an inactive review overlay. Applying a
split/merge/exclusion, source-policy drift or boundary change invalidates the overlay and any cache
key without deleting frontier/deepening receipts. Re-approval is required for a changed proposal.

- [ ] **Step 7: Add safe deepening CLI command**

```python
deepening = subparsers.add_parser("prepare-semantic-deepening")
deepening.add_argument("--workspace", type=Path, default=Path("."))
deepening.add_argument("--project-id", required=True)
```

The command prints opaque project/run IDs, proposal digest, bounded counts, budget and safe review
state only. It does not accept a path/URL and does not approve or start execution.

- [ ] **Step 8: Run deepening, decisions, review, lifecycle, and CLI tests**

Run: `python -m pytest -q tests/test_semantic_deepening_v3.py tests/test_project_review_v3.py tests/test_project_decisions.py tests/test_semantic_catalog_v3.py tests/test_semantic_lifecycle_v3.py tests/test_semantic_finalization_v3.py tests/test_cli.py -k 'deepening or deepening_deferred_index or deferred_index or overlay or project or semantic_v3'`

Expected: PASS; no deepening action changes project/evidence/artifact/delivery authority.

- [ ] **Step 9: Commit Task 14 only**

```bash
git add src/portfolio_maker/application/semantic_deepening_v3.py src/portfolio_maker/application/semantic_finalization_v3.py src/portfolio_maker/application/models.py src/portfolio_maker/application/project_boundary.py src/portfolio_maker/application/project_decisions.py src/portfolio_maker/application/project_review_v3.py src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/infrastructure/semantic_catalog.py src/portfolio_maker/workspace.py src/portfolio_maker/adapters/cli.py tests/test_semantic_deepening_v3.py tests/test_project_review_v3.py tests/test_project_decisions.py tests/test_semantic_catalog_v3.py tests/test_cli.py
git commit -m "feat: deepen selected semantic projects"
```

---

### Task 15: CLI, Plugin Skills, User Documentation, and Package Surface

**Files:**
- Modify: `src/portfolio_maker/adapters/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_plugin_structure.py`
- Modify: `skills/portfolio-semantic-index/SKILL.md`
- Modify: `skills/portfolio-maker/SKILL.md`
- Modify: `skills/portfolio-source-governance/SKILL.md`
- Modify: `skills/portfolio-project-curation/SKILL.md`
- Modify: `skills/portfolio-project-review/SKILL.md`
- Modify: `.agents/skills/portfolio-maker/SKILL.md`
- Modify: `README.md`
- Modify: `docs/DEVELOPMENT_PRINCIPLES.md`
- Modify: `docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md`
- Modify: `pyproject.toml`
- Create: `docs/reviews/2026-08-02-semantic-frontier-v3-implementation-verification.md`

**Interfaces:**
- Consumes: runtime behavior proven in Tasks 1–14.
- Produces: complete safe CLI surface, approval-gated Codex skill workflow, current Korean tutorial/development rules/roadmap and installable package metadata. No new child skill and no version bump.

- [ ] **Step 1: Write RED parser/dispatch/help/safe-output tests for the complete surface**

```python
@pytest.mark.parametrize(
    "argv",
    (
        ("prepare-semantic-index", "--workspace", "w", "--root", "r"),
        ("semantic-index-status", "--workspace", "w", "--verify", "full"),
        ("next-semantic-work", "--workspace", "w", "--run-id", "run"),
        ("validate-semantic-work", "--workspace", "w", "--run-id", "run", "--unit-id", "unit"),
        ("approve-semantic-execution", "--workspace", "w", "--revision-id", "rev", "--proposal-digest", "a" * 64),
        ("pause-semantic-work", "--workspace", "w", "--run-id", "run"),
        ("resume-semantic-work", "--workspace", "w", "--run-id", "run"),
        ("migrate-semantic-staging", "--workspace", "w", "--revision-id", "rev"),
        ("prepare-semantic-deepening", "--workspace", "w", "--project-id", "project"),
    ),
)
def test_semantic_v3_cli_surface_parses(argv) -> None:
    assert build_parser().parse_args(argv).command == argv[0]

def test_safe_status_and_error_output_never_contains_private_material(capsys, private_fixture) -> None:
    private_fixture.run_status_and_controlled_failure()
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert private_fixture.absolute_root not in output
    assert private_fixture.private_url not in output
    assert private_fixture.token not in output
```

- [ ] **Step 2: Write RED plugin contract and tutorial assertions**

```python
def test_semantic_skill_requires_proposal_confirmation_before_approval() -> None:
    text = read_skill("skills/portfolio-semantic-index/SKILL.md")
    assert "approve-semantic-execution" in text
    assert "proposal digest" in text.lower()
    assert text.index("proposal digest") < text.index("approve-semantic-execution")

def test_router_keeps_deepening_separate_from_frontier_and_delivery() -> None:
    router = read_skill("skills/portfolio-maker/SKILL.md")
    assert "prepare-semantic-deepening" in router
    assert "별도 승인" in router
    assert "delivery" in router.lower()
```

- [ ] **Step 3: Confirm CLI/plugin/docs tests are RED**

Run: `python -m pytest -q tests/test_cli.py tests/test_plugin_structure.py -k 'semantic or skill or router'`

Expected: missing flags/workflow text.

- [ ] **Step 4: Complete CLI dispatch and fail-closed run selection**

```python
status.add_argument("--verify", choices=("aggregate", "full"), default="aggregate")
next_work.add_argument("--run-id")
validate_work.add_argument("--run-id")
validate_work.add_argument("--unit-id", required=True)
```

When `--run-id` is absent, `next`/`validate` select only if exactly one runnable run exists;
otherwise return a controlled `run_required`/`run_ambiguous` error. All commands emit protocol,
opaque IDs, counts, state, digests, approved/used budget and safe failure code. Never emit raw
locator, source content, private URL, credential/token or excluded material.

- [ ] **Step 5: Update plugin skills to the executable approval-gated loop**

The semantic skill sequence is exact:

1. verify current source approval without editing it;
2. `prepare-semantic-index` once;
3. show safe proposal, scope, phase budgets and proposal digest;
4. wait for explicit user confirmation;
5. call `approve-semantic-execution` with the exact digest;
6. bounded `next` → Codex analysis → `validate` loop;
7. handle `paused_user`, `paused_budget`, controlled failure and exact resume;
8. require `semantic-index-status --verify full` before finalize;
9. finalize/apply only through runtime commands;
10. route selected project deepening through a new proposal and separate approval;
11. route project/evidence/artifact/delivery approvals separately.

`portfolio-source-governance` must preserve the explicit-only exclusion rule, including no implicit
`Library`/`Containers` additions. Project curation/review must explain medium automatic inclusion
and later manual exclusion. The repository fallback shim points at the canonical router and does not
duplicate pack/SQL/hash algorithms.

- [ ] **Step 6: Update Korean tutorial, principles, roadmap and verification record**

README documents prerequisites, installation, approval setup, v3 walkthrough, pause/resume,
recovery, migration alternative, selected-project deepening, privacy boundaries, resource gates,
expected outputs and another-user setup. Development principles make completion/authority/cache/
fresh-pilot rules normative. Roadmap marks implemented behavior only after the runtime tests are
green and leaves actual fresh-pilot/release acceptance pending. The in-repository verification record
lists the implementation base HEAD, exact staged file scope, test commands/results, measured limits
and known residual risk without claiming push/tag/publish/deploy. Because a commit cannot contain its
own hash, the Planner records the exact post-commit HEAD/tree in the immutable general-review-loop
ledger before independent review.

- [ ] **Step 7: Register scale marker and run CLI/plugin/document integrity tests**

```toml
markers = [
  "semantic_scale: resource-intensive synthetic semantic v3 acceptance tests",
]
```

Run: `python -m pytest -q tests/test_cli.py tests/test_plugin_structure.py`

Expected: PASS, including front matter, canonical skill routing and safe-output assertions.

- [ ] **Step 8: Build package from a disposable copy and verify every help surface**

```bash
tmpdir="$(mktemp -d /private/tmp/portfolio-maker-package-XXXXXX)"
git add src/portfolio_maker/adapters/cli.py tests/test_cli.py tests/test_plugin_structure.py skills/portfolio-semantic-index/SKILL.md skills/portfolio-maker/SKILL.md skills/portfolio-source-governance/SKILL.md skills/portfolio-project-curation/SKILL.md skills/portfolio-project-review/SKILL.md .agents/skills/portfolio-maker/SKILL.md README.md docs/DEVELOPMENT_PRINCIPLES.md docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md pyproject.toml docs/reviews/2026-08-02-semantic-frontier-v3-implementation-verification.md
git diff --cached --check
git diff --cached --name-status
mkdir -p "$tmpdir/source"
git checkout-index --all --prefix="$tmpdir/source/"
python -m build --outdir "$tmpdir/dist" "$tmpdir/source"
python -m venv "$tmpdir/venv"
"$tmpdir/venv/bin/python" -m pip install "$tmpdir"/dist/*.whl
"$tmpdir/venv/bin/portfolio-maker" --help
"$tmpdir/venv/bin/portfolio-maker" semantic-index-status --help
"$tmpdir/venv/bin/portfolio-maker" approve-semantic-execution --help
"$tmpdir/venv/bin/portfolio-maker" prepare-semantic-deepening --help
```

`git checkout-index` snapshots the exact intended staged Task 15 tree, including its uncommitted
changes; `git archive HEAD` is forbidden here because it would test Task 14. Inspect the cached name
list and `git status --short` after the build; no unrelated staged file, wheel, egg-info, cache, DB or
generated pilot artifact may appear in the candidate worktree.

- [ ] **Step 9: Commit Task 15 only**

```bash
git add src/portfolio_maker/adapters/cli.py tests/test_cli.py tests/test_plugin_structure.py skills/portfolio-semantic-index/SKILL.md skills/portfolio-maker/SKILL.md skills/portfolio-source-governance/SKILL.md skills/portfolio-project-curation/SKILL.md skills/portfolio-project-review/SKILL.md .agents/skills/portfolio-maker/SKILL.md README.md docs/DEVELOPMENT_PRINCIPLES.md docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md pyproject.toml docs/reviews/2026-08-02-semantic-frontier-v3-implementation-verification.md
git commit -m "docs: publish semantic v3 workflow"
```

---

### Task 16: Synthetic Scale, Adversarial Acceptance, and Full Regression

**Files:**
- Create: `tests/test_semantic_scale_v3.py`
- Modify: `tests/test_semantic_pack_v3.py`
- Modify: `tests/test_semantic_catalog_v3.py`
- Modify: `tests/test_semantic_prepare_v3.py`
- Modify: `tests/test_semantic_frontier_v3.py`
- Modify: `tests/test_semantic_lifecycle_v3.py`
- Modify: `tests/test_semantic_finalization_v3.py`
- Modify: `tests/test_semantic_migration_v3.py`
- Modify: `tests/test_semantic_deepening_v3.py`
- Modify: `docs/reviews/2026-08-02-semantic-frontier-v3-implementation-verification.md`

**Interfaces:**
- Consumes: the complete Tasks 1–15 candidate.
- Produces: reproducible 1M-node synthetic instrumentation, adversarial evidence, full regression/build evidence and an implementation-review candidate. No full-home source access.

- [ ] **Step 1: Add env-gated deterministic scale fixtures**

```python
pytestmark = pytest.mark.semantic_scale

@pytest.fixture(scope="module")
def million_node_source(tmp_path_factory):
    return build_deterministic_synthetic_source(
        tmp_path_factory.mktemp("semantic-scale"),
        node_count=1_000_000,
        high_fanout=(3_584, 10_000),
        project_fixtures=("insurance-rag", "portfolio-maker", "playmcp"),
        seed="semantic-v3-acceptance-v1",
    )
```

The test skips unless `PORTFOLIO_MAKER_RUN_SEMANTIC_SCALE=1`; it records OS, Python, physical RAM,
warm/cold cache state, wall time, peak RSS, managed bytes and command. Fixture generation is outside
measured prepare time but included in report provenance.

- [ ] **Step 2: Add RED scale/resource/latency acceptance**

```python
def test_million_node_pipeline_meets_bounded_gates(million_node_source, measured_workspace) -> None:
    prepared = measured_workspace.prepare(million_node_source)
    assert prepared.wall_seconds <= 3_600
    assert prepared.managed_bytes <= 3 * 1024**3
    assert prepared.peak_rss_bytes <= min(4 * 1024**3, measured_workspace.physical_ram // 4)
    assert prepared.structural_node_count == prepared.local_baseline_terminal_count
    assert prepared.implicit_exclusion_count == 0

    status_samples = measured_workspace.measure_status(repetitions=20, same_process=True)
    assert median(sample.wall_seconds for sample in status_samples) <= 2
    assert max(sample.wall_seconds for sample in status_samples) <= 5
    assert max(sample.rss_bytes for sample in status_samples) <= 512 * 1024**2
    assert status_samples[-1].rss_bytes - status_samples[0].rss_bytes <= 64 * 1024**2
```

- [ ] **Step 3: Add bounded operation and pause/resume acceptance**

```python
def test_next_validate_verify_finalize_and_review_meet_gates(scale_run) -> None:
    assert percentile(scale_run.next_times, 95) <= 5
    assert percentile(scale_run.validate_times, 95) <= 5
    assert scale_run.max_next_validate_rss <= 512 * 1024**2
    assert scale_run.full_verify.wall_seconds <= 600
    assert scale_run.full_verify.peak_rss_bytes <= 1024**3
    assert scale_run.finalize.wall_seconds <= 600
    assert scale_run.project_review.wall_seconds <= 600
    assert max(scale_run.finalize.peak_rss_bytes, scale_run.project_review.peak_rss_bytes) <= 2 * 1024**3

def test_pause_after_32_survives_new_process_without_drift(scale_run) -> None:
    scale_run.validate_exactly(32)
    before = scale_run.pause_and_shutdown()
    after = scale_run.resume_in_new_process()
    assert after.receipt_root == before.receipt_root
    assert after.used_units == before.used_units == 32
    assert after.duplicate_receipts == after.missing_receipts == 0
```

- [ ] **Step 4: Add project-quality and exact 300-sample audit acceptance**

```python
def test_three_reference_projects_have_recall_and_grounded_boundaries(scale_run) -> None:
    candidates = scale_run.project_candidates()
    assert {"insurance-rag", "portfolio-maker", "playmcp"}.issubset(candidates.reference_keys())
    assert all(len(set(item.grounded_signal_types)) >= 2 for item in candidates.reference_items())
    assert candidates.has_no_build_cache_temp_promotions()
    assert candidates.parent_child_boundary_cases_pass()

def test_frozen_three_hundred_sample_has_no_high_or_medium_miss(scale_run, independent_audit_labels) -> None:
    sample = scale_run.frozen_audit_sample()
    assert len(sample.members) == 300
    assert sample.ordered_set_sha256 == digest_audit_members(sample.members)
    assert sample.population_digest == scale_run.deferred_population_digest()
    assert scale_run.checkpoint_members() == sample.members
    assert independent_audit_labels.high_or_medium_miss_count == 0
```

The automated synthetic labels are a deterministic release test, not a substitute for the later
fresh private pilot reviewer. Verify relevance 100, coverage-stratified 100 and stable-random 100
with duplicate replacement, exact checkpoint binding and audit budget exhaustion behavior.

- [ ] **Step 5: Add complete adversarial matrix**

Cover at minimum:

- pack/catalog partial and coherent component tamper against manifest roots;
- durable proposal canonical-byte/digest corruption and caller-reconstructed proposal substitution;
- batched unit target-ref/dependency-root substitution and audit target outside stored membership;
- aggregate-only completion forgery;
- expired/wrong-unit lease replay and conflicting duplicate output;
- exact duplicate validate replay leaving result-pack length/root unchanged;
- forced completion after unit/byte/time exhaustion;
- phase hard-budget drift and attempted orientation/expansion/audit/deepening budget reallocation;
- nonexistent child/evidence/full-topology Codex output;
- unreadable subtree misclassification attempt;
- audit node replacement and selector/catalog/policy/privacy/budget drift;
- two valid action sequences with distinct but correctly checkpoint-bound audit populations;
- same-prefix/different-tail cache poisoning, rename, parent, dependency, schema and validator drift;
- same-FD post-hash tail rewrite/identity change TOCTOU;
- current source approval/policy/manifest/catalog/proposal/approval/checkpoint drift before next,
  before exchange publication, before result append and inside receipt/full-verify gates;
- two concurrent runs competing for the singleton workspace exchange;
- two simultaneous exact approvals staying prepared, then one atomic `next` winning the unique running slot;
- result recovery after exchange deletion/restart;
- v2 active preservation, migration interruption and pre-attestation migration rejection;
- deepening split/merge/exclude/policy/boundary invalidation;
- deepening canonical plan/proposal hash mismatch, parent checkpoint/projection/schema drift and
  attempted use of proposal body as the unit plan;
- private locator/URL/token/excluded content non-disclosure in status, failure review and artifacts.

- [ ] **Step 6: Run the semantic scale suite and save measurements**

Run: `PORTFOLIO_MAKER_RUN_SEMANTIC_SCALE=1 python -m pytest -p no:cacheprovider -q -m semantic_scale tests/test_semantic_scale_v3.py`

Expected: PASS with every numeric gate recorded in the verification document. A skipped test, OOM,
timeout or unmeasured gate is `NEEDS_WORK`, not PASS.

- [ ] **Step 7: Run all focused semantic/adversarial tests**

Run: `python -m pytest -p no:cacheprovider -q tests/test_semantic_pack_v3.py tests/test_semantic_catalog_v3.py tests/test_semantic_crawler.py tests/test_semantic_analyzers.py tests/test_semantic_prepare_v3.py tests/test_semantic_frontier_v3.py tests/test_semantic_quality.py tests/test_semantic_lifecycle_v3.py tests/test_semantic_finalization_v3.py tests/test_semantic_migration_v3.py tests/test_semantic_deepening_v3.py tests/test_semantic_index.py tests/test_semantic_work_lifecycle.py tests/test_project_review_v3.py tests/test_project_decisions.py tests/test_sqlite_repository.py tests/test_cli.py tests/test_plugin_structure.py`

Expected: PASS.

- [ ] **Step 8: Run full suite/web build and complete the implementation verification record**

Run: `python -m pytest -p no:cacheprovider -q`

Run: `(cd web/portfolio && npm ci && npm run build)`

Run: `git diff --check && git status --short`

Record the Task 15 implementation base HEAD, exact intended Task 16 file list, commands, pass counts,
timing/RSS/disk measurements, platform, all tested failure injections, known threat-model limit and
remaining fresh-pilot gate. Do not invent the not-yet-created Task 16 commit ID and do not say
`released`, `published`, `deployed` or `0.2.2 complete`. The Planner records exact post-commit
HEAD/tree in the immutable review ledger.

- [ ] **Step 9: Stage the exact Task 16 tree and run disposable package/help checks**

```bash
git add tests/test_semantic_scale_v3.py tests/test_semantic_pack_v3.py tests/test_semantic_catalog_v3.py tests/test_semantic_prepare_v3.py tests/test_semantic_frontier_v3.py tests/test_semantic_lifecycle_v3.py tests/test_semantic_finalization_v3.py tests/test_semantic_migration_v3.py tests/test_semantic_deepening_v3.py docs/reviews/2026-08-02-semantic-frontier-v3-implementation-verification.md
git diff --cached --check
git diff --cached --name-status
tmpdir="$(mktemp -d /private/tmp/portfolio-maker-final-package-XXXXXX)"
mkdir -p "$tmpdir/source"
git checkout-index --all --prefix="$tmpdir/source/"
python -m build --outdir "$tmpdir/dist" "$tmpdir/source"
python -m venv "$tmpdir/venv"
"$tmpdir/venv/bin/python" -m pip install "$tmpdir"/dist/*.whl
"$tmpdir/venv/bin/portfolio-maker" --help
"$tmpdir/venv/bin/portfolio-maker" semantic-index-status --help
"$tmpdir/venv/bin/portfolio-maker" next-semantic-work --help
"$tmpdir/venv/bin/portfolio-maker" validate-semantic-work --help
"$tmpdir/venv/bin/portfolio-maker" approve-semantic-execution --help
"$tmpdir/venv/bin/portfolio-maker" pause-semantic-work --help
"$tmpdir/venv/bin/portfolio-maker" resume-semantic-work --help
"$tmpdir/venv/bin/portfolio-maker" migrate-semantic-staging --help
"$tmpdir/venv/bin/portfolio-maker" prepare-semantic-deepening --help
```

This snapshot includes the intended uncommitted Task 16 changes. `git archive HEAD` is forbidden
because it would silently retest Task 15. Expected: all PASS; cached scope is exact and the candidate
worktree contains no cache, DB, pack, exchange, pilot or build residue.

- [ ] **Step 10: Commit Task 16 only**

```bash
git add tests/test_semantic_scale_v3.py tests/test_semantic_pack_v3.py tests/test_semantic_catalog_v3.py tests/test_semantic_prepare_v3.py tests/test_semantic_frontier_v3.py tests/test_semantic_lifecycle_v3.py tests/test_semantic_finalization_v3.py tests/test_semantic_migration_v3.py tests/test_semantic_deepening_v3.py docs/reviews/2026-08-02-semantic-frontier-v3-implementation-verification.md
git commit -m "test: verify semantic v3 at scale"
```

---

## Fresh Detached Full-Home Pilot and Release Gate (Not an Implementation Task)

Run only after every Task 1–16 Developer commit has its exact HEAD/tree captured and an independent
Review Team verdict of `PASS`. This gate requires a fresh detached workspace at that exact candidate
HEAD, the user's still-current source approval, and a separate explicit user go/no-go authorizing
this fresh full-home pilot before prepare. Plan approval, implementation approval, prior pilot
approval or Review Team PASS is not pilot execution authority. It never reuses preserved pilot state
and never changes `source-approval.json` or exclusions implicitly.

1. Obtain and record the explicit fresh-pilot go/no-go, then record candidate HEAD/tree, plugin source root, installed-plugin path/version, macOS/Python/RAM,
   approved roots/exclusions and policy digest. Do not expose raw locators in the public report.
2. Prepare the approved home directory exactly once with no hidden global cap or implicit
   `Library`/`Containers` exclusion. Stop on resource gate/failure; do not auto-retry or expand policy.
3. Confirm structural node count equals terminal local-baseline count and measure the prepare
   3,600-second/3-GiB/RSS gates.
4. Show the exact frontier proposal/digest, private-context scope, selector version and phase budgets;
   obtain a new explicit execution approval before running Codex units.
5. Execute bounded `next`/Codex/`validate`; intentionally pause immediately after 32 receipts, end the
   process, resume from checkpoint and verify no duplicate/missing receipt, double charge or drift.
6. Complete expansion and the approval-bound audit phase. Verify the frozen ordered 300-node digest,
   population digest, selector version and 64-unit/16-MiB/15-minute audit cap.
7. Run aggregate status, 20-call status measurement and `--verify full`; then finalize, apply and
   project review only if all completion gates pass.
8. Independently verify 3/3 recall for 보험 RAG, Portfolio Maker and PlayMCP; grounded two-signal
   minimum; parent/child boundary quality; no build/cache/temp promotion; no high/medium miss in the
   300 sample; and private repository display-name versus URL/locator privacy.
9. If the user selects a project, prepare its deepening proposal, show the exact digest/budget, obtain
   separate approval, execute it, and confirm its result remains an inactive review overlay.
10. Save only bounded private pilot/review evidence. Do not commit pilot data, delete preserved state,
    push, tag, create a release, publish the plugin, deploy Sites or alter delivery authority.

Any numeric-gate miss, skipped audit, project recall/boundary failure, privacy leak, state drift,
unbounded operation or independent review finding is `NEEDS_WORK`/`BLOCKED`. Only after a focused
fixback, exact-scope re-review and a fresh rerun may the release gate become `PASS`.
