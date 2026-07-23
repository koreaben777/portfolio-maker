# Portfolio Maker 0.2.2 Managed History and Codex Vision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a recoverable lifecycle for Portfolio Maker-managed history and a user-selected Codex App analysis path for every approved local regular-file format the active Codex session can directly recognize.

**Architecture:** Python owns policy, source identity, persistence, cleanup, and artifact gates. A single portfolio-vision-analysis skill may inspect only the exact source bound to a confirmed request and submits closed-schema JSON to the CLI. Cleanup moves verified managed files to same-filesystem quarantine, commits the corresponding SQLite rows once, then resumes or restores through an fsynced journal.

**Tech Stack:** Python 3.11+, standard library (argparse, contextlib, dataclasses, hashlib, json, os, sqlite3, tarfile), SQLite, pytest 8+, Codex plugin skills.

## Global Constraints

- Use an isolated implementation worktree. Do not use the root workspace's managed state as a test target.
- Preserve all 0.1.0/0.2.1 commands, artifacts, approval contracts, and workspace paths through additive migration.
- Never delete, move, rename, copy, archive, or upload an original user source.
- Vision accepts every approved local regular file without an extension allowlist or local PDF/Office/image conversion. The current Codex App session decides whether it can analyze the selected file.
- Hash vision input in fixed-size descriptor reads. Do not apply text extraction's 2MB decode limit and do not load the whole binary into Python memory.
- CLI code must not invoke an external LLM API or read/store an API token. Managed results must not contain raw source bytes, raw transcript, absolute source path, or credential-like text.
- Every vision request needs --confirm-codex-share. A completed result begins pending; only an explicit included decision can create derived evidence.
- Unsupported, unavailable, cancelled, stale, malformed, policy-blocked, and low-confidence results cannot create claims/evidence and cannot fail text-only workflow.
- Absent history policy retains every unit. No scheduler/background task performs cleanup.
- History cleanup requires a verified immutable plan, export outside the workspace, matching export receipt, exact confirmation text, identity revalidation, same-filesystem quarantine, and a journal.
- Candidate selection never includes original source, approval files, artifacts, semantic-index files, current/referenced snapshots, pending/included analyses, or incomplete requests.
- Reuse descriptor/no-follow, repository lock, database-family, and directory identity safeguards. Reject symlink/path replacement, changed DB sidecar, stale plan, and concurrent state before destructive writes.
- Every task is RED -> minimum implementation -> focused GREEN -> git diff --check -> commit. A fresh reviewer must approve each task before the next task begins.
- Do not set pyproject.toml or .codex-plugin/plugin.json to 0.2.2 until all release validation passes.

---

## Delivery order

| Phase | Tasks | Reviewable deliverable |
|---|---:|---|
| P. Baseline repair | 0 | green 0.2.1 version contract before feature RED tests |
| A. Contracts/persistence | 1-2 | additive model, path, migration, repository contract |
| B. #5 history lifecycle | 3-5 | read-only planning, export, quarantined apply/recovery, CLI |
| C. #14 vision lifecycle | 6-9 | consented request, sanitized result, review, skill/CLI |
| D. Release | 10-11 | cross-boundary verification, documentation, version and release gate |

## File map

~~~text
src/portfolio_maker/
  domain/history_models.py             immutable policy/plan/unit/journal value types
  domain/vision_models.py              request/result/status/review value types
  infrastructure/history_store.py      descriptor-safe plan/export/quarantine files
  infrastructure/vision_store.py       private request + sanitized result JSON
  infrastructure/extractors.py         approved streaming binary identity helper
  infrastructure/sqlite_repository.py  additive records and protected-reference queries
  application/history.py               policy/plan/export/apply/resume/recover use cases
  application/vision_analysis.py       prepare/apply/list/review use cases
  application/build_profile.py         projection of explicitly included analysis
  application/models.py                CLI request/result dataclasses
  adapters/cli.py                      history + vision command parsing
  workspace.py                         new private paths
skills/portfolio-vision-analysis/
  SKILL.md
  agents/openai.yaml
tests/test_history_lifecycle.py
tests/test_vision_analysis.py
tests/test_cli.py
tests/test_plugin_structure.py
tests/test_profile_and_portfolio.py
tests/test_public_portfolio.py
tests/test_sqlite_repository.py
~~~

## Phase P — Baseline repair

### Task 0: Restore a green 0.2.1 version contract

**Files:**
- Modify: tests/test_plugin_structure.py:1-10
- Modify: src/portfolio_maker/__init__.py:1-3

**Interfaces:**
- Produces a single cross-surface release-version contract for Python, plugin, and web metadata.
- Consumes the current 0.2.1 values in pyproject.toml, .codex-plugin/plugin.json, web/portfolio/package.json, and web/portfolio/package-lock.json.

- [ ] **Step 1: Preserve the observed baseline failure**

Run: python -m pytest -q
Expected: exactly one existing failure because tests/test_plugin_structure.py expects 0.2.0 while the plugin manifest is 0.2.1; all other current tests pass.

- [ ] **Step 2: Replace the stale literal with a cross-surface contract**

~~~python
import json
import tomllib
from pathlib import Path

from portfolio_maker import __version__


def test_release_versions_stay_aligned() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    web_package = json.loads(Path("web/portfolio/package.json").read_text(encoding="utf-8"))
    web_lock = json.loads(Path("web/portfolio/package-lock.json").read_text(encoding="utf-8"))
    expected = pyproject["project"]["version"]

    assert {
        plugin["version"],
        web_package["version"],
        web_lock["version"],
        web_lock["packages"][""]["version"],
        __version__,
    } == {expected}
~~~

Keep test_plugin_manifest_names_repository_and_skill_root focused on plugin name and skill-root discovery. Set src/portfolio_maker/__init__.py __version__ to 0.2.1. Do not change any release value to 0.2.2 in this task.

- [ ] **Step 3: Verify the repaired baseline**

Run: python -m pytest tests/test_plugin_structure.py -v
Expected: PASS.

Run: python -m pytest -q
Expected: PASS with no pre-existing failure left for later RED/GREEN tasks.

- [ ] **Step 4: Commit**

~~~bash
git add src/portfolio_maker/__init__.py tests/test_plugin_structure.py
git diff --check
git commit -m "fix: align 0.2.1 release versions"
~~~

## Phase A — Contracts and persistence

### Task 1: Define value types and managed workspace paths

**Files:**
- Create: src/portfolio_maker/domain/history_models.py
- Create: src/portfolio_maker/domain/vision_models.py
- Modify: src/portfolio_maker/application/models.py:1-145
- Modify: src/portfolio_maker/workspace.py:7-69
- Create: tests/test_history_models.py
- Create: tests/test_vision_models.py
- Modify: tests/test_sqlite_repository.py:22-43

**Interfaces:**
- Produces HistoryPolicy, HistoryUnit, HistoryPlan, CleanupExecutionState, VisionRequestStatus, VisionReviewStatus, PreparedVisionRequest, VisionAnalysisResult.
- Produces WorkspacePaths.history_policy_path, history_plans_dir, history_journal_dir, history_quarantine_dir, vision_requests_dir, derived_dir, vision_analyses_dir.
- Consumes no new runtime module.

- [ ] **Step 1: Write the failing model and workspace tests**

~~~python
from portfolio_maker.domain.history_models import HistoryPolicy
from portfolio_maker.domain.vision_models import VisionRequestStatus
from portfolio_maker.workspace import WorkspacePaths


def test_default_history_policy_retains_protected_units() -> None:
    policy = HistoryPolicy.default()
    assert policy.minimum_age_days == 30
    assert policy.keep_latest_text_snapshots_per_source == 1
    assert policy.retain_included_vision_analyses is True
    assert policy.retain_pending_review_vision_analyses is True
    assert policy.retain_incomplete_vision_requests is True


def test_workspace_creates_history_and_vision_private_directories(workspace) -> None:
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    assert paths.vision_requests_dir == paths.reviews_dir / "vision-requests"
    assert paths.vision_analyses_dir == paths.root / "derived" / "vision"
    assert paths.history_plans_dir == paths.reviews_dir / "history-plans"
    assert VisionRequestStatus.PENDING.value == "pending"
~~~

- [ ] **Step 2: Verify RED**

Run: python -m pytest tests/test_history_models.py tests/test_vision_models.py tests/test_sqlite_repository.py::test_workspace_paths_create_expected_directories -v
Expected: FAIL because modules and path properties do not exist.

- [ ] **Step 3: Implement exact contracts**

~~~python
# src/portfolio_maker/domain/history_models.py
@dataclass(frozen=True)
class HistoryPolicy:
    minimum_age_days: int
    keep_latest_text_snapshots_per_source: int
    retain_included_vision_analyses: bool
    retain_pending_review_vision_analyses: bool
    retain_incomplete_vision_requests: bool

    @classmethod
    def default(cls) -> "HistoryPolicy":
        return cls(30, 1, True, True, True)


class CleanupExecutionState(StrEnum):
    PLANNED = "planned"
    EXPORTED = "exported"
    QUARANTINED = "quarantined"
    COMMITTED_PENDING_PURGE = "committed_pending_purge"
    PURGED = "purged"
    RESTORED = "restored"
    VACUUM_PENDING = "vacuum_pending"


@dataclass(frozen=True)
class HistoryUnit:
    kind: str
    row_id: str
    relative_path: str
    content_hash: str
    size: int
    device: int
    inode: int
    reason: str


@dataclass(frozen=True)
class HistoryPlan:
    id: str
    policy: HistoryPolicy
    units: tuple[HistoryUnit, ...]
    manifest_hash: str
    confirmation_phrase: str = "DELETE MANAGED HISTORY"

    @classmethod
    def create(cls, policy: HistoryPolicy, units: list[HistoryUnit]) -> "HistoryPlan":
        canonical_units = tuple(sorted(units, key=lambda unit: (unit.kind, unit.row_id)))
        manifest_hash = sha256(_canonical_json(policy, canonical_units)).hexdigest()
        return cls(uuid4().hex, policy, canonical_units, manifest_hash)

    def unit_ids(self) -> tuple[tuple[str, str], ...]:
        return tuple((unit.kind, unit.row_id) for unit in self.units)


def _canonical_json(policy: HistoryPolicy, units: tuple[HistoryUnit, ...]) -> bytes:
    return json.dumps(
        {"policy": asdict(policy), "units": [asdict(unit) for unit in units]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# src/portfolio_maker/domain/vision_models.py
class VisionRequestStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE_SOURCE = "stale_source"


class VisionReviewStatus(StrEnum):
    PENDING = "pending"
    INCLUDED = "included"
    EXCLUDED = "excluded"
~~~

Define immutable dataclasses with these required fields:

~~~python
@dataclass(frozen=True)
class PreparedVisionRequest:
    id: str
    source_id: int
    source_content_hash: str
    source_size: int
    adapter: str
    status: VisionRequestStatus
    confirmation_at: str

    @classmethod
    def new(cls, source_id: int, content_hash: str, size: int) -> "PreparedVisionRequest":
        return cls(uuid4().hex, source_id, content_hash, size, "codex-app-vision-v1", VisionRequestStatus.PENDING, utc_now())

@dataclass(frozen=True)
class VisionAnalysisResult:
    request_id: str
    source_content_hash: str
    adapter: str
    status: VisionRequestStatus
    confidence: str | None
    summary: str | None
    observations: tuple[dict[str, str], ...]
    requires_manual_review: bool


@dataclass(frozen=True)
class AppliedVisionAnalysisResult:
    id: str
    result_hash: str
    analysis_path: Path
    review_status: VisionReviewStatus


@dataclass(frozen=True)
class VisionAnalysisStateResult:
    analysis_id: str
    state: str
~~~

Add exactly these WorkspacePaths values and create their parent directories through ensure_managed_directory:

~~~python
derived_dir = root / "derived"
vision_analyses_dir = derived_dir / "vision"
vision_requests_dir = reviews_dir / "vision-requests"
history_policy_path = reviews_dir / "history-policy.json"
history_plans_dir = reviews_dir / "history-plans"
history_journal_dir = reviews_dir / "history-journal"
history_quarantine_dir = root / "quarantine"
~~~

Add CLI request/result dataclasses with these fields: HistoryPlanRequest(workspace), HistoryExportRequest(workspace, plan_id, backup_dir), HistoryApplyRequest(workspace, plan_id, export_receipt_hash, confirmation), PrepareVisionAnalysisRequest(workspace, source_id, confirm_codex_share), ApplyVisionAnalysisRequest(workspace, result_path), and SetVisionAnalysisStateRequest(workspace, analysis_id, state). All workspace/result/backup values use Path.

- [ ] **Step 4: Verify GREEN**

Run: python -m pytest tests/test_history_models.py tests/test_vision_models.py tests/test_sqlite_repository.py::test_workspace_paths_create_expected_directories -v
Expected: PASS.

Run: python -c 'from portfolio_maker.domain.history_models import HistoryPolicy; assert HistoryPolicy.default().minimum_age_days == 30'
Expected: exit code 0.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/domain/history_models.py src/portfolio_maker/domain/vision_models.py src/portfolio_maker/application/models.py src/portfolio_maker/workspace.py tests/test_history_models.py tests/test_vision_models.py tests/test_sqlite_repository.py
git diff --check
git commit -m "feat: define history and vision contracts"
~~~

### Task 2: Add additive SQLite tables and protected-reference APIs

**Files:**
- Modify: src/portfolio_maker/infrastructure/sqlite_repository.py:33-199,236-335,547-766,1944-2407
- Modify: tests/test_sqlite_repository.py

**Interfaces:**
- Consumes Task 1 models.
- Produces create_vision_request, get_vision_request, finish_vision_request, insert_vision_analysis, get_vision_analysis, list_vision_analyses, mark_vision_analysis_included, exclude_unreferenced_vision_analysis, evidence_id_for_stable_id, history_candidates, create_history_plan, get_history_plan, record_history_journal_entry, delete_planned_history_units, exclusive_workspace_operation.

- [ ] **Step 1: Write failing migration and protected-reference tests**

~~~python
def test_initialize_creates_history_and_vision_tables(workspace) -> None:
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repository.initialize()
    assert {
        "vision_analysis_requests",
        "vision_analyses",
        "history_cleanup_plans",
        "history_cleanup_journal_entries",
    } <= repository.table_names()


def test_history_candidates_exclude_evidence_referenced_snapshot(workspace) -> None:
    repository, snapshot_id = seeded_old_snapshot_with_evidence(workspace)
    candidates = repository.history_candidates(HistoryPolicy.default())
    assert all(unit.row_id != snapshot_id for unit in candidates)
~~~

- [ ] **Step 2: Verify RED**

Run: python -m pytest tests/test_sqlite_repository.py -k 'history or vision' -v
Expected: FAIL because tables and APIs are missing.

- [ ] **Step 3: Add schema and deterministic repository methods**

Add this schema to SCHEMA:

~~~sql
CREATE TABLE IF NOT EXISTS vision_analysis_requests (
    id TEXT PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_content_hash TEXT NOT NULL,
    source_size INTEGER NOT NULL,
    adapter TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','completed','unsupported','unavailable','failed','cancelled','stale_source')),
    confirmation_at TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    completed_at TEXT,
    reason_code TEXT
);

CREATE TABLE IF NOT EXISTS vision_analyses (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE REFERENCES vision_analysis_requests(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_content_hash TEXT NOT NULL,
    result_hash TEXT,
    relative_path TEXT,
    adapter TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT,
    review_status TEXT NOT NULL CHECK(review_status IN ('pending','included','excluded')),
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS history_cleanup_plans (
    id TEXT PRIMARY KEY,
    policy_hash TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    export_receipt_hash TEXT,
    execution_id TEXT,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS history_cleanup_journal_entries (
    execution_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    stage TEXT NOT NULL,
    unit_kind TEXT NOT NULL,
    row_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (execution_id, sequence)
);
~~~

Use static SQL joins to protect units. A source snapshot is ineligible when it is latest for source or referenced by evidence_items.snapshot_id. An analysis is ineligible when policy retains its pending/included review state or a claim/project references its derived evidence. A request is ineligible when unfinished or associated with a retained analysis/evidence.

~~~python
@contextmanager
def exclusive_workspace_operation(self) -> Iterator[None]:
    with self._repository_critical_section():
        yield


def delete_planned_history_units(
    self,
    plan_id: str,
    unit_ids: tuple[tuple[str, str], ...],
) -> None:
    with self._connection() as conn:
        self._assert_history_plan_is_current(conn, plan_id, unit_ids)
        self._assert_no_history_unit_is_now_protected(conn, unit_ids)
        self._delete_history_units(conn, unit_ids)
~~~

The plan never contributes table names/SQL fragments. Additive initialization must remain in initialize() under the existing guarded write transaction.

- [ ] **Step 4: Verify GREEN and legacy safety**

Run: python -m pytest tests/test_sqlite_repository.py -k 'history or vision or legacy' -v
Expected: PASS.

Run: python -m pytest tests/test_sqlite_repository.py -v
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/infrastructure/sqlite_repository.py tests/test_sqlite_repository.py
git diff --check
git commit -m "feat: persist managed history and vision records"
~~~

## Phase B — History lifecycle (#5)

### Task 3: Implement policy parsing and read-only immutable cleanup plans

**Files:**
- Create: src/portfolio_maker/infrastructure/history_store.py
- Create: src/portfolio_maker/application/history.py
- Modify: src/portfolio_maker/infrastructure/managed_files.py:1-118
- Create: tests/test_history_lifecycle.py

**Interfaces:**
- Consumes Task 1 paths/models and Task 2 history_candidates/create_history_plan.
- Produces write_sample_history_policy, load_history_policy, plan_history_cleanup, HistoryStore.write_plan.

- [ ] **Step 1: Write failing plan tests**

~~~python
def test_missing_history_policy_retains_everything(workspace) -> None:
    seed_old_unreferenced_snapshot(workspace)
    result = plan_history_cleanup(HistoryPlanRequest(workspace=workspace))
    assert result.candidate_count == 0


def test_history_plan_is_read_only_and_hides_locator(workspace) -> None:
    state = seed_old_unreferenced_snapshot(workspace)
    write_history_policy(workspace)
    result = plan_history_cleanup(HistoryPlanRequest(workspace=workspace))
    assert state.snapshot_path.exists()
    report = result.report_path.read_text(encoding="utf-8")
    assert "Candidates: 1" in report
    assert str(state.snapshot_path) not in report
    assert ".portfolio-maker/raw/" not in report
~~~

- [ ] **Step 2: Verify RED**

Run: python -m pytest tests/test_history_lifecycle.py -k 'policy or plan' -v
Expected: FAIL because history use cases do not exist.

- [ ] **Step 3: Implement deterministic policy and plan writes**

~~~python
def load_history_policy(paths: WorkspacePaths) -> HistoryPolicy | None:
    if not paths.history_policy_path.exists():
        return None
    payload = json.loads(read_managed_bytes(paths.history_policy_path))
    return HistoryPolicy.from_json(payload)


def plan_history_cleanup(request: HistoryPlanRequest) -> HistoryPlanResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    policy = load_history_policy(paths)
    if policy is None:
        return _write_retain_all_report(paths)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    units = repository.history_candidates(policy)
    plan = HistoryPlan.create(policy, units)
    plan_path = HistoryStore(paths).write_plan(plan)
    repository.create_history_plan(plan, plan_path.relative_to(paths.root))
    return HistoryPlanResult(plan.id, plan_path, len(units), sum(unit.size for unit in units))
~~~

Define class HistoryError(RuntimeError) in this module and raise it for malformed policy, stale plan, wrong confirmation, export receipt mismatch, unsafe managed path, and invalid journal transition.

HistoryStore.write_plan writes a canonical JSON file named plan-<id>-<manifest-hash>.json through write_managed_text. Every unit contains kind, row id, relative managed path, size, device/inode, content hash, and reason. The public report contains only unit counts, total bytes, kinds, reasons, and plan id. Validate every relative path through an opened managed directory descriptor before recording it.

- [ ] **Step 4: Verify GREEN and unsafe-path tests**

Run: python -m pytest tests/test_history_lifecycle.py -k 'policy or plan or unsafe' -v
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/application/history.py src/portfolio_maker/infrastructure/history_store.py src/portfolio_maker/infrastructure/managed_files.py tests/test_history_lifecycle.py
git diff --check
git commit -m "feat: plan managed history cleanup"
~~~

### Task 4: Implement export, quarantine apply, resume, and recovery

**Files:**
- Modify: src/portfolio_maker/application/history.py
- Modify: src/portfolio_maker/infrastructure/history_store.py
- Modify: src/portfolio_maker/infrastructure/sqlite_repository.py
- Modify: tests/test_history_lifecycle.py

**Interfaces:**
- Consumes Task 3 HistoryPlan and Task 2 locked repository APIs.
- Produces export_history_plan, apply_history_plan, resume_history_cleanup, recover_history_cleanup.

- [ ] **Step 1: Write failing transaction/recovery tests**

~~~python
def test_apply_requires_matching_export_receipt_and_confirmation(workspace, tmp_path) -> None:
    plan = seeded_history_plan(workspace)
    with pytest.raises(HistoryError, match="export receipt is required"):
        apply_history_plan(HistoryApplyRequest(workspace, plan.id, None, "DELETE MANAGED HISTORY"))
    exported = export_history_plan(HistoryExportRequest(workspace, plan.id, tmp_path / "backup"))
    result = apply_history_plan(
        HistoryApplyRequest(workspace, plan.id, exported.receipt_hash, "DELETE MANAGED HISTORY")
    )
    assert result.state is CleanupExecutionState.PURGED


def test_database_failure_restores_quarantined_files(workspace, monkeypatch) -> None:
    plan, candidate_path = seeded_history_plan_with_file(workspace)
    monkeypatch.setattr(SQLiteRepository, "delete_planned_history_units", raise_repository_error)
    with pytest.raises(HistoryError, match="managed files were restored"):
        apply_seeded_plan(workspace, plan)
    assert candidate_path.exists()
~~~

- [ ] **Step 2: Verify RED**

Run: python -m pytest tests/test_history_lifecycle.py -k 'export or apply or recover or resume' -v
Expected: FAIL because execution functions are missing.

- [ ] **Step 3: Implement journaled execution**

~~~python
def apply_history_plan(request: HistoryApplyRequest) -> HistoryApplyResult:
    paths = WorkspacePaths.from_root(request.workspace)
    repository = SQLiteRepository(paths.db_path)
    store = HistoryStore(paths)
    with repository.exclusive_workspace_operation():
        plan = store.load_verified_plan(request.plan_id)
        repository.assert_export_receipt(plan.id, request.export_receipt_hash)
        _require_exact_confirmation(request.confirmation, plan.confirmation_phrase)
        store.revalidate_units(plan.units)
        execution_id = uuid4().hex
        store.write_journal(execution_id, CleanupExecutionState.EXPORTED, plan.units)
        moved = store.move_units_to_quarantine(execution_id, plan.units)
        store.write_journal(execution_id, CleanupExecutionState.QUARANTINED, moved)
        try:
            repository.delete_planned_history_units(plan.id, plan.unit_ids())
        except Exception:
            store.restore_quarantine(execution_id, moved)
            store.write_journal(execution_id, CleanupExecutionState.RESTORED, moved)
            raise HistoryError("database update failed; managed files were restored")
        store.write_journal(execution_id, CleanupExecutionState.COMMITTED_PENDING_PURGE, moved)
        store.purge_quarantine(execution_id, moved)
        store.write_journal(execution_id, CleanupExecutionState.PURGED, moved)
        return HistoryApplyResult(execution_id, CleanupExecutionState.PURGED, len(moved))
~~~

history-export rejects an existing/non-empty directory and any destination inside the workspace. It creates a tar archive containing plan JSON, selected managed JSON, and a SQLite backup created by sqlite3.Connection.backup(); then it records the archive SHA-256 receipt. Quarantine uses no-follow directory descriptors, same-filesystem rename, identity checks before/after move, fsync of journal and parent directory. resume only purges a verified committed_pending_purge journal. recover only restores a verified quarantined journal. After purge, run VACUUM under repository lock; if it fails, persist vacuum_pending without undoing a committed cleanup.

- [ ] **Step 4: Verify GREEN and adversarial suite**

Run: python -m pytest tests/test_history_lifecycle.py -k 'export or apply or recover or resume or symlink or replacement or sidecar or stale' -v
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/application/history.py src/portfolio_maker/infrastructure/history_store.py src/portfolio_maker/infrastructure/sqlite_repository.py tests/test_history_lifecycle.py
git diff --check
git commit -m "feat: execute recoverable history cleanup"
~~~

### Task 5: Add history CLI commands with safe output

**Files:**
- Modify: src/portfolio_maker/adapters/cli.py:52-279
- Modify: tests/test_cli.py

**Interfaces:**
- Consumes Task 3 plan and Task 4 export/apply/resume/recover services.
- Produces history-policy, history-plan, history-export, history-apply commands.

- [ ] **Step 1: Write failing CLI test**

~~~python
def test_cli_history_plan_reports_only_summary(workspace, capsys) -> None:
    seed_old_unreferenced_snapshot(workspace)
    assert main(["history-policy", "--workspace", str(workspace), "--write-sample"]) == 0
    assert main(["history-plan", "--workspace", str(workspace)]) == 0
    output = capsys.readouterr().out
    assert "Candidates:" in output
    assert str(workspace) not in output
    assert ".portfolio-maker/raw/" not in output
~~~

- [ ] **Step 2: Verify RED**

Run: python -m pytest tests/test_cli.py -k history -v
Expected: FAIL because parser entries are absent.

- [ ] **Step 3: Implement parser and handler**

~~~python
history_policy = subparsers.add_parser("history-policy")
history_policy.add_argument("--workspace", type=Path, default=Path("."))
history_policy.add_argument("--write-sample", action="store_true")

history_plan = subparsers.add_parser("history-plan")
history_plan.add_argument("--workspace", type=Path, default=Path("."))

history_export = subparsers.add_parser("history-export")
history_export.add_argument("--workspace", type=Path, default=Path("."))
history_export.add_argument("--plan-id", required=True)
history_export.add_argument("--backup-dir", type=Path, required=True)

history_apply = subparsers.add_parser("history-apply")
history_apply.add_argument("--workspace", type=Path, default=Path("."))
history_apply.add_argument("--plan-id")
history_apply.add_argument("--export-receipt")
history_apply.add_argument("--confirm")
history_apply.add_argument("--resume")
history_apply.add_argument("--recover")
~~~

Require exactly one history-apply mode. Add HistoryError to the controlled exception tuple. Print only IDs, state, counts, and bytes; never print an input path, source URI, archive destination, or exception traceback.

- [ ] **Step 4: Verify GREEN**

Run: python -m pytest tests/test_cli.py -k history -v
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/adapters/cli.py tests/test_cli.py
git diff --check
git commit -m "feat: expose managed history controls"
~~~

## Phase C — Codex vision lifecycle (#14)

### Task 6: Create a confirmed request from a safely revalidated binary source

**Files:**
- Modify: src/portfolio_maker/infrastructure/extractors.py:13-95
- Create: src/portfolio_maker/application/vision_analysis.py
- Create: src/portfolio_maker/infrastructure/vision_store.py
- Modify: src/portfolio_maker/application/models.py
- Create: tests/test_vision_analysis.py

**Interfaces:**
- Consumes Task 1 types and Task 2 create_vision_request.
- Produces ApprovedVisionSource, validate_approved_vision_source, prepare_vision_analysis.

- [ ] **Step 1: Write failing source-gate tests**

~~~python
def test_prepare_requires_share_confirmation(workspace, tmp_path) -> None:
    source_id = approve_binary_source(workspace, tmp_path / "resume.pdf", b"%PDF-synthetic")
    with pytest.raises(VisionAnalysisError, match="confirmation is required"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, False))


def test_prepare_streams_large_binary_without_text_limit(workspace, tmp_path) -> None:
    source_id = approve_binary_source(workspace, tmp_path / "large.dat", b"x" * 2_000_001)
    prepared = prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))
    assert prepared.source_size == 2_000_001
    assert prepared.status is VisionRequestStatus.PENDING


def test_prepare_refuses_changed_symlink(workspace, tmp_path) -> None:
    source_id = approve_then_replace_with_symlink(workspace, tmp_path)
    with pytest.raises(VisionAnalysisError, match="approved source is unsafe"):
        prepare_vision_analysis(PrepareVisionAnalysisRequest(workspace, source_id, True))
~~~

- [ ] **Step 2: Verify RED**

Run: python -m pytest tests/test_vision_analysis.py -k prepare -v
Expected: FAIL because vision application service is absent.

- [ ] **Step 3: Implement streaming validation and request receipt**

~~~python
@dataclass(frozen=True)
class ApprovedVisionSource:
    path: Path
    content_hash: str
    byte_size: int
    device: int
    inode: int


def validate_approved_vision_source(uri: str, policy: FilePolicy) -> ApprovedVisionSource:
    path, descriptor = _open_approved_regular_file(uri, policy)
    try:
        stat = os.fstat(descriptor)
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 65_536):
            digest.update(chunk)
            size += len(chunk)
        return ApprovedVisionSource(path, digest.hexdigest(), size, stat.st_dev, stat.st_ino)
    finally:
        os.close(descriptor)
~~~

_open_approved_regular_file must reuse canonical file URI, no-follow component walk, regular-file, approval, and FilePolicy.classify_path checks used by extract_approved_text. It must not call FilePolicy.max_file_size_bytes or decode a chunk.

~~~python
def prepare_vision_analysis(request: PrepareVisionAnalysisRequest) -> PreparedVisionRequest:
    if not request.confirm_codex_share:
        raise VisionAnalysisError("Codex analysis confirmation is required")
    paths, source, policy = _load_approved_local_source(request.workspace, request.source_id)
    checked = validate_approved_vision_source(source.uri, policy)
    prepared = PreparedVisionRequest.new(source.id, checked.content_hash, checked.byte_size)
    SQLiteRepository(paths.db_path).create_vision_request(prepared)
    VisionStore(paths).write_request_receipt(prepared, source.display_name)
    return prepared
~~~

Define class VisionAnalysisError(RuntimeError) in this module. Implement VisionStore.write_request_receipt() here as an atomic managed JSON write under reviews/vision-requests/request-<request-id>.json; Task 7 extends the same class with result persistence.

The private request receipt stores only request id, source id, source hash, source size, adapter name, status, confirmation timestamp, and display name. It does not contain raw bytes or an absolute path.

- [ ] **Step 4: Verify GREEN**

Run: python -m pytest tests/test_vision_analysis.py -k 'prepare or symlink or large or policy' -v
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/infrastructure/extractors.py src/portfolio_maker/application/vision_analysis.py src/portfolio_maker/infrastructure/vision_store.py src/portfolio_maker/application/models.py tests/test_vision_analysis.py
git diff --check
git commit -m "feat: prepare confirmed Codex vision requests"
~~~

### Task 7: Validate and persist only sanitized structured vision results

**Files:**
- Modify: src/portfolio_maker/infrastructure/vision_store.py
- Modify: src/portfolio_maker/application/vision_analysis.py
- Modify: src/portfolio_maker/infrastructure/sqlite_repository.py
- Modify: tests/test_vision_analysis.py

**Interfaces:**
- Consumes Task 6 PreparedVisionRequest and Task 2 vision records.
- Produces parse_vision_result, apply_vision_analysis, VisionStore.write_analysis.

- [ ] **Step 1: Write failing schema/masking tests**

~~~python
def test_apply_completed_result_masks_secret_and_starts_pending(workspace, tmp_path) -> None:
    prepared = prepare_approved_request(workspace, tmp_path)
    result_path = write_result(
        tmp_path, prepared.id, prepared.source_content_hash,
        summary="api_key=synthetic-secret delivered a document",
        confidence="medium",
    )
    result = apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))
    payload = json.loads(result.analysis_path.read_text(encoding="utf-8"))
    assert payload["summary"] == "api_key=[REDACTED]"
    assert result.review_status is VisionReviewStatus.PENDING


def test_apply_rejects_hash_mismatch_without_managed_output(workspace, tmp_path) -> None:
    prepared = prepare_approved_request(workspace, tmp_path)
    result_path = write_result(tmp_path, prepared.id, "0" * 64)
    with pytest.raises(VisionAnalysisError, match="source hash does not match"):
        apply_vision_analysis(ApplyVisionAnalysisRequest(workspace, result_path))
    assert list(WorkspacePaths.from_root(workspace).vision_analyses_dir.iterdir()) == []
~~~

- [ ] **Step 2: Verify RED**

Run: python -m pytest tests/test_vision_analysis.py -k 'apply or result or mask' -v
Expected: FAIL because parser/store is absent.

- [ ] **Step 3: Implement closed schemas and result store**

~~~python
_COMPLETED_KEYS = {
    "schema_version", "request_id", "source_content_hash", "adapter", "status",
    "confidence", "summary", "observations", "requires_manual_review",
}


def parse_completed_result(payload: object) -> VisionAnalysisResult:
    if not isinstance(payload, dict) or set(payload) != _COMPLETED_KEYS:
        raise VisionAnalysisError("vision result schema is invalid")
    if payload["schema_version"] != "vision-analysis-v1" or payload["status"] != "completed":
        raise VisionAnalysisError("vision result status is invalid")
    return VisionAnalysisResult.from_payload(payload)


def sanitize_result(result: VisionAnalysisResult) -> VisionAnalysisResult:
    return replace(
        result,
        summary=mask_secrets(result.summary or ""),
        observations=tuple(
            {**observation, "text": mask_secrets(observation["text"])}
            for observation in result.observations
        ),
        requires_manual_review=True,
    )
~~~

Accept a separate status-only schema for unsupported, unavailable, failed, and cancelled: schema_version, request_id, source_content_hash, adapter, status, reason_code. It cannot contain summary or observations and cannot create evidence. Rehash the original source with validate_approved_vision_source before writing. Write sanitized canonical JSON only to derived/vision/analysis-<request-id>-<result-hash>.json with write_managed_text, and persist its path relative to the managed root.

- [ ] **Step 4: Verify GREEN**

Run: python -m pytest tests/test_vision_analysis.py -k 'apply or result or mask or malformed or unsupported' -v
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/infrastructure/vision_store.py src/portfolio_maker/application/vision_analysis.py src/portfolio_maker/infrastructure/sqlite_repository.py tests/test_vision_analysis.py
git diff --check
git commit -m "feat: store sanitized Codex vision analyses"
~~~

### Task 8: Require review before deriving evidence and project it safely

**Files:**
- Modify: src/portfolio_maker/application/vision_analysis.py
- Modify: src/portfolio_maker/application/build_profile.py:28-250
- Modify: src/portfolio_maker/infrastructure/sqlite_repository.py:1944-2208
- Modify: tests/test_vision_analysis.py
- Modify: tests/test_profile_and_portfolio.py
- Modify: tests/test_public_portfolio.py

**Interfaces:**
- Consumes Task 7 analysis rows/files.
- Produces set_vision_analysis_state, list_vision_analyses, stable evidence id vision-analysis:<id>:<hash>.

- [ ] **Step 1: Write failing consent/projection tests**

~~~python
def test_only_included_analysis_creates_derived_evidence(workspace, tmp_path) -> None:
    analysis = apply_completed_analysis(workspace, tmp_path)
    repository = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    assert repository.evidence_id_for_stable_id(
        f"vision-analysis:{analysis.id}:{analysis.result_hash}"
    ) is None
    set_vision_analysis_state(SetVisionAnalysisStateRequest(workspace, analysis.id, "included"))
    assert repository.evidence_id_for_stable_id(
        f"vision-analysis:{analysis.id}:{analysis.result_hash}"
    ) is not None


def test_pending_or_excluded_analysis_cannot_appear_in_public_output(workspace, tmp_path) -> None:
    apply_completed_analysis(workspace, tmp_path)
    build_profile(BuildProfileRequest(workspace=workspace))
    render_html(RenderHtmlRequest(workspace=workspace))
    html = WorkspacePaths.from_root(workspace).portfolio_html_path.read_text(encoding="utf-8")
    assert "vision-requests" not in html
    assert "derived/vision" not in html
~~~

- [ ] **Step 2: Verify RED**

Run: python -m pytest tests/test_vision_analysis.py -k 'included or excluded or pending' tests/test_profile_and_portfolio.py -k vision tests/test_public_portfolio.py -k vision -v
Expected: FAIL because review/projection is absent.

- [ ] **Step 3: Implement second consent and artifact-policy projection**

~~~python
def set_vision_analysis_state(request: SetVisionAnalysisStateRequest) -> VisionAnalysisStateResult:
    repository = SQLiteRepository(WorkspacePaths.from_root(request.workspace).db_path)
    analysis = repository.get_vision_analysis(request.analysis_id)
    if analysis["status"] != "completed":
        raise VisionAnalysisError("only completed analyses can be reviewed")
    if request.state == "included":
        evidence_id = repository.upsert_evidence_item(
            source_id=analysis["source_id"],
            snapshot_id=None,
            github_activity_id=None,
            locator=f"derived:vision-analysis:{analysis['id']}",
            stable_id=f"vision-analysis:{analysis['id']}:{analysis['result_hash']}",
            content_hash=analysis["result_hash"],
            public_safe=False,
            origin_type="local",
            origin_visibility="private",
        )
        repository.mark_vision_analysis_included(analysis["id"], evidence_id)
    else:
        repository.exclude_unreferenced_vision_analysis(analysis["id"])
    return VisionAnalysisStateResult(analysis["id"], request.state)
~~~

Extend build_profile after its existing local-source loop. Load only included sanitized analyses, create/update project local:<source_id>, create a direct claim from safe_local_public_label(mask_public_value(summary)), link it to derived evidence, and use the existing EvidenceSelectionService decision for source/artifact policy. Do not project request id, source URI, analysis path, or raw observations. Exclusion must reject a result referenced by claim_evidence or portfolio_project_evidence instead of silently deleting its evidence.

- [ ] **Step 4: Verify GREEN**

Run: python -m pytest tests/test_vision_analysis.py -k 'included or excluded or pending' tests/test_profile_and_portfolio.py -k vision tests/test_public_portfolio.py -k vision -v
Expected: PASS.

Run: python -m pytest tests/test_evidence_selection.py tests/test_render_html.py -v
Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/application/vision_analysis.py src/portfolio_maker/application/build_profile.py src/portfolio_maker/infrastructure/sqlite_repository.py tests/test_vision_analysis.py tests/test_profile_and_portfolio.py tests/test_public_portfolio.py
git diff --check
git commit -m "feat: require review for vision evidence"
~~~

### Task 9: Expose vision CLI and the only raw-source-capable skill

**Files:**
- Modify: src/portfolio_maker/adapters/cli.py:52-279
- Create: skills/portfolio-vision-analysis/SKILL.md
- Create: skills/portfolio-vision-analysis/agents/openai.yaml
- Modify: skills/portfolio-maker/SKILL.md
- Modify: skills/portfolio-artifacts/SKILL.md
- Modify: tests/test_cli.py
- Modify: tests/test_plugin_structure.py

**Interfaces:**
- Consumes Tasks 6-8.
- Produces prepare-vision-analysis, apply-vision-analysis, list-vision-analyses, set-vision-analysis-state and the privileged child-skill boundary.

- [ ] **Step 1: Write failing CLI and skill-boundary tests**

~~~python
def test_cli_prepare_vision_requires_confirmation(workspace, tmp_path, capsys) -> None:
    source_id = approve_binary_source(workspace, tmp_path / "slide.pptx", b"PK\x03\x04")
    exit_code = main([
        "prepare-vision-analysis", "--workspace", str(workspace),
        "--source-id", str(source_id),
    ])
    assert exit_code == 1
    assert "confirmation" in capsys.readouterr().err


def test_only_vision_skill_has_raw_selected_source_permission() -> None:
    vision = Path("skills/portfolio-vision-analysis/SKILL.md").read_text(encoding="utf-8")
    assert "only raw selected source" in vision
    assert "do not read raw" in Path("skills/portfolio-maker/SKILL.md").read_text(encoding="utf-8")
    assert "do not read raw" in Path("skills/portfolio-artifacts/SKILL.md").read_text(encoding="utf-8")
~~~

- [ ] **Step 2: Verify RED**

Run: python -m pytest tests/test_cli.py -k vision tests/test_plugin_structure.py -v
Expected: FAIL because commands and skill do not exist.

- [ ] **Step 3: Implement CLI and skill flow**

~~~python
prepare_vision = subparsers.add_parser("prepare-vision-analysis")
prepare_vision.add_argument("--workspace", type=Path, default=Path("."))
prepare_vision.add_argument("--source-id", type=int, required=True)
prepare_vision.add_argument("--confirm-codex-share", action="store_true")

apply_vision = subparsers.add_parser("apply-vision-analysis")
apply_vision.add_argument("--workspace", type=Path, default=Path("."))
apply_vision.add_argument("--result", type=Path, required=True)

list_vision = subparsers.add_parser("list-vision-analyses")
list_vision.add_argument("--workspace", type=Path, default=Path("."))

set_vision = subparsers.add_parser("set-vision-analysis-state")
set_vision.add_argument("--workspace", type=Path, default=Path("."))
set_vision.add_argument("--analysis-id", required=True)
set_vision.add_argument("--state", choices=("included", "excluded"), required=True)
~~~

Write the new skill with this exact order:

~~~text
1. Run prepare-vision-analysis with --confirm-codex-share only after the user confirms the selected source may be sent to Codex.
2. Read only the pending request and the exact approved source bound to its request ID.
3. Use the current Codex App native capability. Do not install or invoke an OCR package, converter, external API, or fallback uploader.
4. Return closed-schema completed JSON or status-only unsupported/unavailable/cancelled JSON.
5. Run apply-vision-analysis and explain that pending results need an explicit included/excluded decision.
~~~

The router may hand a request id to this skill but cannot read its raw source/request/result. Artifact skill continues to consume existing evidence policy only and never raw request/result material. CLI messages include only request/analysis IDs, status, confidence, and count.

- [ ] **Step 4: Verify GREEN and capability-unavailable smoke**

Run: python -m pytest tests/test_cli.py -k vision tests/test_plugin_structure.py -v
Expected: PASS.

Manual smoke in an isolated fixture workspace: prepare a confirmed request, submit a status-only unavailable result, run build-profile, draft-portfolio, render-html. Expected: all commands succeed and no new claim/evidence exists.

- [ ] **Step 5: Commit**

~~~bash
git add src/portfolio_maker/adapters/cli.py skills/portfolio-vision-analysis/SKILL.md skills/portfolio-vision-analysis/agents/openai.yaml skills/portfolio-maker/SKILL.md skills/portfolio-artifacts/SKILL.md tests/test_cli.py tests/test_plugin_structure.py
git diff --check
git commit -m "feat: add consented Codex vision skill"
~~~

## Phase D — Integration and release

### Task 10: Verify cross-boundary isolation and record the validation

**Files:**
- Modify: tests/test_history_lifecycle.py
- Modify: tests/test_vision_analysis.py
- Modify: tests/test_profile_and_portfolio.py
- Modify: tests/test_public_portfolio.py
- Create: docs/reviews/2026-07-16-portfolio-maker-0.2.2-verification.md

**Interfaces:**
- Consumes Tasks 1-9.
- Produces release-grade evidence for issue completion/review.

- [ ] **Step 1: Write failing isolation tests**

~~~python
def test_history_plan_never_selects_approvals_artifacts_or_semantic_index(workspace) -> None:
    paths = seed_workspace_with_every_managed_category(workspace)
    plan = plan_history_cleanup(HistoryPlanRequest(workspace=workspace))
    selected = {unit.relative_path for unit in plan.units}
    assert paths.approval_path.relative_to(paths.root).as_posix() not in selected
    assert paths.portfolio_html_path.relative_to(paths.root).as_posix() not in selected
    assert all(not path.startswith("reviews/semantic-index/") for path in selected)


def test_public_outputs_never_emit_vision_request_or_locator(workspace, tmp_path) -> None:
    include_completed_analysis(workspace, tmp_path)
    render_html(RenderHtmlRequest(workspace=workspace))
    output = WorkspacePaths.from_root(workspace).portfolio_html_path.read_text(encoding="utf-8")
    assert "vision-requests" not in output
    assert "derived/vision" not in output
    assert str(workspace) not in output
~~~

- [ ] **Step 2: Verify RED**

Run: python -m pytest tests/test_history_lifecycle.py -k isolation tests/test_vision_analysis.py -k public tests/test_profile_and_portfolio.py -k vision -v
Expected: FAIL until all boundaries are enforced.

- [ ] **Step 3: Fix only the responsible layer**

Fix candidate filtering in history selection, result-only loading in VisionStore/build_profile, or renderer projection. Do not weaken source approval, artifact policy, secret masking, or static HTML path checks.

- [ ] **Step 4: Run full validation and write factual review record**

Run: python -m pytest -q
Expected: PASS.

Run: python -m pytest tests/test_history_lifecycle.py tests/test_vision_analysis.py tests/test_cli.py tests/test_plugin_structure.py -v
Expected: PASS.

Write the exact commands, pass counts, isolated-workspace smoke result, capability-unavailable result, and untested live format boundaries to the verification record. Do not claim a format succeeded unless the current Codex session actually exercised it.

- [ ] **Step 5: Exercise the current Codex capability with synthetic files**

In a disposable workspace containing no user data, run the real portfolio-vision-analysis skill against:

- one ordinary image;
- one multi-page text PDF;
- one multi-page scanned PDF;
- one document, spreadsheet, and presentation format that the current Codex App session offers directly;
- one deliberately unsupported regular file.

For each case, record only format class, byte size, request/result status, capability outcome, and whether explicit inclusion was exercised. Do not record raw text, absolute paths, or source bytes. A format may safely return unsupported or unavailable; the acceptance condition is that every selected source follows the same confirmed request protocol, successful results begin pending, and no failed/unsupported result becomes evidence. Verify that text-only profile, draft, manifest, and HTML generation still succeed after the matrix.

- [ ] **Step 6: Commit**

~~~bash
git add tests/test_history_lifecycle.py tests/test_vision_analysis.py tests/test_profile_and_portfolio.py tests/test_public_portfolio.py docs/reviews/2026-07-16-portfolio-maker-0.2.2-verification.md
git diff --check
git commit -m "test: verify 0.2.2 history and vision boundaries"
~~~

### Task 11: Update tutorial, release metadata, and issue completion only after PASS

**Files:**
- Modify: README.md
- Modify: docs/DEVELOPMENT_PRINCIPLES.md
- Modify: docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md
- Modify: .codex-plugin/plugin.json
- Modify: pyproject.toml
- Modify: src/portfolio_maker/__init__.py
- Modify: web/portfolio/package.json
- Modify: web/portfolio/package-lock.json
- Modify: tests/test_cli.py
- Modify: tests/test_plugin_structure.py

**Interfaces:**
- Consumes Task 10 verification record.
- Produces accurate 0.2.2 public documentation/version and issue closure criteria.

- [ ] **Step 1: Write failing/help-gate test**

~~~python
def test_cli_help_lists_history_and_vision_commands(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["--help"])
    assert error.value.code == 0
    assert "history-plan" in capsys.readouterr().out
    assert "prepare-vision-analysis" in capsys.readouterr().out
~~~

- [ ] **Step 2: Run documentation/help gate**

Run: python -m pytest tests/test_cli.py::test_cli_help_lists_history_and_vision_commands -v
Expected: PASS; this confirms the final tutorial names active commands.

- [ ] **Step 3: Write final documentation and version values**

Set versions exactly:

~~~json
{"version": "0.2.2"}
~~~

~~~toml
version = "0.2.2"
~~~

Set src/portfolio_maker/__init__.py, web/portfolio/package.json, both root package version fields in web/portfolio/package-lock.json, pyproject.toml, and .codex-plugin/plugin.json to 0.2.2. The Task 0 cross-surface test must pass without weakening or removing a version surface.

README tutorial must show: create policy sample; inspect history-plan; export to an outside-workspace directory; apply only after reading the plan's confirmation phrase; resume/recover an execution; choose a source; run prepare-vision-analysis --confirm-codex-share; let portfolio-vision-analysis submit a result; inspect pending status; explicitly include/exclude it. State that format availability is current-session dependent and that #8 remains open for independent OCR/renderer support.

Change roadmap/development-principles language from planned 0.2.2 to current 0.2.2 only after Task 10 passes. Prepare the factual #5 and #14 completion updates from the verification record, but do not mutate or close the remote issues until the reviewed release commit has been pushed with explicit user authorization. Keep #8 open.

- [ ] **Step 4: Run final release checks**

Run: python -m pytest -q
Expected: PASS.

Run: python -m build
Expected: PASS and distribution metadata reports version 0.2.2.

Run: python -m venv /tmp/portfolio-maker-0.2.2-venv && /tmp/portfolio-maker-0.2.2-venv/bin/pip install dist/portfolio_maker-0.2.2-py3-none-any.whl && /tmp/portfolio-maker-0.2.2-venv/bin/portfolio-maker --help
Expected: help lists history and vision commands.

Run: git diff --check && git status --short
Expected: only intended release files are staged; no historical local review artifact is staged.

- [ ] **Step 5: Commit and hand off for release review**

~~~bash
git add README.md docs/DEVELOPMENT_PRINCIPLES.md docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md .codex-plugin/plugin.json pyproject.toml src/portfolio_maker/__init__.py web/portfolio/package.json web/portfolio/package-lock.json tests/test_cli.py tests/test_plugin_structure.py docs/reviews/2026-07-16-portfolio-maker-0.2.2-verification.md
git diff --check
git commit -m "release: prepare portfolio maker 0.2.2"
~~~

Do not push, tag, publish, or close remote issues until the user authorizes publication after review-team PASS.

---

## Plan self-review

| Approved design requirement | Tasks |
|---|---|
| green current-release baseline and cross-surface version contract | 0, 11 |
| #5 default policy, candidate planning, protected units | 1-3, 5 |
| export, confirmation, quarantine, transaction, resume/recovery, vacuum | 2, 4, 5, 10 |
| path/symlink/sidecar/concurrency protection | 2-4, 10 |
| no original source/raw persistence | 1, 3-4, 6-10 |
| all Codex-recognized approved local formats with no converter/allowlist | 6, 9-11 |
| analysis consent separate from artifact consent | 6, 8-9 |
| sanitization, failure state, artifact/public-output isolation | 7-10 |
| additive migration and 0.2.1 compatibility | 0-2, 10-11 |
| Korean tutorial, version, release/Issue gate | 10-11 |

Placeholder scan: complete. Type consistency: HistoryPolicy, HistoryPlan, PreparedVisionRequest, VisionAnalysisResult, HistoryApplyRequest, and SetVisionAnalysisStateRequest are defined in Task 1 before later tasks use them. Scope is split into #5 then #14; #8 OCR/renderer implementation is excluded from this release.
