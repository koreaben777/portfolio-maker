# Portfolio Maker 0.2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 승인된 탐색 범위의 전체 구조를 계층형 의미 인덱스로 만들고, Codex가 큰 작업 단위의 프로젝트 경계를 제안하며, 검토 또는 medium 이상 자동 포함을 거쳐 다중 스킬 Codex plugin에서 포트폴리오 산출물을 생성하게 한다.

**Architecture:** Python CLI와 SQLite는 구조 수집, 안전한 분석 청크, schema/hash 검증, project state, artifact projection의 결정론적 authority로 유지한다. Codex plugin skill은 CLI가 만든 locator-free input만 읽어 file/directory summary와 project candidate를 작성하고, CLI가 다시 검증한 결과만 active revision과 portfolio project로 materialize한다. 0.1.0 review flow는 compatibility path로 유지하고 0.2.0 v2 flow를 additive하게 도입한다.

**Tech Stack:** Python 3.11+, standard library, SQLite, pytest 8+, Codex plugin/skills, TypeScript 5.7+, Vite 6

## Global Constraints

- 현재 공개 runtime 0.1.0과 계획된 0.2.0을 구현 중에도 구분한다.
- CLI에서 외부 LLM API를 호출하거나 API token을 읽고 저장하지 않는다.
- local semantic index 읽기 범위는 scan root에서 excluded directory/file policy를 제거한 범위다.
- semantic index 포함은 ingest, evidence approval, artifact inclusion 또는 public deployment 승인이 아니다.
- private GitHub는 기존 authentication, opt-in, repository allowlist, activity approval을 유지한다.
- raw absolute path, private GitHub URL, credential은 Codex analysis bundle과 artifact에 넣지 않는다.
- project 자동 포함은 explicit automatic mode에서만 high와 medium에 적용한다.
- manual approve/exclude는 automatic inference보다 우선한다.
- migration은 additive하고 0.1.0 workspace와 artifact를 파괴하지 않는다.
- 원본 file은 수정, 이동, 삭제하거나 `.portfolio-maker/`에 복사하지 않는다.
- 새 runtime dependency를 추가하지 않는다.
- 각 task는 failing test, 최소 구현, focused test, `git diff --check`, 의도한 file만 commit하는 순서로 끝낸다.
- 전체 release gate를 통과하기 전 `pyproject.toml`과 web package version을 0.2.0으로 올리지 않는다.

---

## 1. 실행 순서와 검토 게이트

| Phase | Tasks | 독립 산출물 | Review gate |
|---|---:|---|---|
| A. Semantic Index Core | 1-7 | 안전한 v2 semantic revision 생성·활성화 | 500+ fixture, interruption, policy tests |
| B. Project Boundary & Decisions | 8-12 | candidate v2, auto/manual/excluded project state | medium auto, reversible exclude, artifact tests |
| C. Codex Plugin | 13-19 | plugin manifest와 검증된 6개 skill | skill별 RED/GREEN forward test, plugin validation |
| D. Integration & Release | 20-22 | migration, 실제 smoke, 문서·version 0.2.0 | full tests, build, browser, issue checklist |

Phase review에서 P1/P2 finding이 있으면 다음 Phase로 진행하지 않는다. Task 13-19는 skill 하나를 baseline-test, 작성, forward-test, validate한 뒤 다음 skill로 이동한다.

## 2. 목표 파일 구조

### Python core

```text
src/portfolio_maker/
  domain/
    semantic_models.py          # semantic node/revision/candidate/decision value types
  infrastructure/
    semantic_crawler.py         # excluded-first complete local structural crawl
    semantic_analyzers.py       # bounded deterministic file signals and masked excerpts
    sqlite_repository.py        # additive semantic revision/node/edge/project-decision persistence
  application/
    semantic_index.py           # prepare/apply revision use cases and safe chunk contract
    project_boundary.py         # v2 review input and candidate validation
    project_decisions.py        # review/automatic mode and reversible exclusion
    project_composition.py      # v1 compatibility and shared artifact projection
    models.py                   # new request/result dataclasses
  adapters/cli.py               # new 0.2.0 commands
  workspace.py                  # semantic review paths
```

### Plugin

```text
.codex-plugin/plugin.json
skills/
  portfolio-maker/
  portfolio-source-governance/
  portfolio-semantic-index/
  portfolio-project-curation/
  portfolio-project-review/
  portfolio-artifacts/
```

### Tests and documents

```text
tests/
  test_semantic_models.py
  test_semantic_crawler.py
  test_semantic_analyzers.py
  test_semantic_index.py
  test_project_boundary.py
  test_project_decisions.py
  test_plugin_structure.py
  fixtures/semantic_workspaces/
docs/reviews/
  2026-07-14-portfolio-maker-0.2.0-skill-forward-tests.md
  2026-07-14-portfolio-maker-0.2.0-verification.md
```

---

## Phase A — Semantic Index Core

### Task 1: Semantic domain types and stable hashes

**Files:**
- Create: `src/portfolio_maker/domain/semantic_models.py`
- Create: `tests/test_semantic_models.py`

**Interfaces:**
- Produces: `SemanticNodeKind`, `AnalysisStatus`, `RevisionStatus`, `SemanticNode`, `SemanticEdge`, `SemanticRevision`, `stable_source_id()`, `stable_node_id()`, `boundary_fingerprint()`
- Consumes: no application or infrastructure dependency

- [ ] **Step 1: Write failing identity and enum tests**

```python
def test_semantic_ids_are_stable_and_do_not_contain_locator() -> None:
    source_id = stable_source_id("local", "approved-root-key")
    node_id = stable_node_id(source_id, "1048577:99123")
    assert source_id == stable_source_id("local", "approved-root-key")
    assert node_id == stable_node_id(source_id, "1048577:99123")
    assert "/Users/" not in node_id

def test_boundary_fingerprint_is_order_independent() -> None:
    assert boundary_fingerprint("directory_root", ("b", "a")) == boundary_fingerprint(
        "directory_root", ("a", "b")
    )
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python -m pytest tests/test_semantic_models.py -v`

Expected: FAIL because `portfolio_maker.domain.semantic_models` does not exist.

- [ ] **Step 3: Implement frozen value types and namespaced SHA-256 helpers**

```python
class SemanticNodeKind(StrEnum):
    SOURCE = "source"
    DIRECTORY = "directory"
    FILE = "file"
    GITHUB_ACTIVITY = "github_activity"

class AnalysisStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    UNREADABLE = "unreadable"
    FAILED = "failed"

class RevisionStatus(StrEnum):
    STAGING = "staging"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"

def _stable_hash(namespace: str, *parts: str) -> str:
    canonical = "\0".join((namespace, *parts)).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

def stable_source_id(kind: str, provider_root_key: str) -> str:
    return _stable_hash("portfolio-maker-source-v1", kind, provider_root_key)

def stable_node_id(source_id: str, provider_item_key: str) -> str:
    return _stable_hash("portfolio-maker-node-v1", source_id, provider_item_key)

def boundary_fingerprint(boundary_type: str, node_ids: tuple[str, ...]) -> str:
    return _stable_hash("portfolio-maker-boundary-v1", boundary_type, *sorted(node_ids))
```

Define frozen dataclasses with the exact common fields from the 0.2.0 design. Keep locator out of `SemanticNode`; locator belongs to an infrastructure-only record.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_semantic_models.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/domain/semantic_models.py tests/test_semantic_models.py
git commit -m "feat: define semantic index domain contracts"
```

### Task 2: Additive semantic revision persistence

**Files:**
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `tests/test_sqlite_repository.py`

**Interfaces:**
- Consumes: Task 1 semantic dataclasses
- Produces: `create_semantic_revision()`, `replace_semantic_revision_graph()`, `activate_semantic_revision()`, `fail_semantic_revision()`, `get_active_semantic_revision()`, `list_semantic_nodes()`

- [ ] **Step 1: Write schema and atomic activation tests**

```python
def test_semantic_revision_activation_is_atomic_and_supersedes_previous(workspace) -> None:
    repo = SQLiteRepository(WorkspacePaths.from_root(workspace).db_path)
    repo.initialize()
    repo.create_semantic_revision("rev-1", "source-1", "a" * 64, "semantic-v1")
    repo.activate_semantic_revision("rev-1")
    repo.create_semantic_revision("rev-2", "source-1", "a" * 64, "semantic-v1")
    repo.activate_semantic_revision("rev-2")
    assert repo.get_active_semantic_revision("source-1")["id"] == "rev-2"

def test_failed_staging_revision_keeps_previous_active(workspace) -> None:
    # Create rev-1 active, fail rev-2, and assert rev-1 remains active.
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_sqlite_repository.py -k semantic_revision -v`

Expected: FAIL because semantic tables and methods are missing.

- [ ] **Step 3: Add schema tables and repository methods**

Add these tables to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS semantic_index_revisions (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('staging','active','superseded','failed')),
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS semantic_nodes (
    revision_id TEXT NOT NULL REFERENCES semantic_index_revisions(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    node_kind TEXT NOT NULL,
    parent_node_id TEXT,
    display_name TEXT NOT NULL,
    relative_hierarchy TEXT NOT NULL,
    content_fingerprint TEXT,
    semantic_summary TEXT NOT NULL DEFAULT '',
    semantic_roles_json TEXT NOT NULL DEFAULT '[]',
    topics_json TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    analysis_status TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    PRIMARY KEY (revision_id, node_id)
);

CREATE TABLE IF NOT EXISTS semantic_node_locators (
    revision_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    locator TEXT NOT NULL,
    device INTEGER,
    inode INTEGER,
    PRIMARY KEY (revision_id, node_id),
    FOREIGN KEY (revision_id, node_id)
      REFERENCES semantic_nodes(revision_id, node_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS semantic_edges (
    revision_id TEXT NOT NULL,
    from_node_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    to_node_id TEXT NOT NULL,
    confidence TEXT,
    PRIMARY KEY (revision_id, from_node_id, relation, to_node_id)
);
```

Activation must run in one `_connection()` transaction: mark the current active revision for the same source as `superseded`, then mark the staging revision `active`.

- [ ] **Step 4: Run focused repository tests**

Run: `python -m pytest tests/test_sqlite_repository.py -k "semantic_revision or initialize_creates_schema" -v`

Expected: PASS and existing schema tests remain green.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/infrastructure/sqlite_repository.py tests/test_sqlite_repository.py
git commit -m "feat: persist semantic index revisions"
```

### Task 3: Complete excluded-first structural crawler

**Files:**
- Create: `src/portfolio_maker/infrastructure/semantic_crawler.py`
- Create: `tests/test_semantic_crawler.py`

**Interfaces:**
- Consumes: `SourceApproval`, Task 1 stable ID helpers
- Produces: `StructuralEntry`, `StructuralCrawl`, `crawl_local_structure(root, approval, prior_entries=())`

- [ ] **Step 1: Write 501-file, exclusion, permission, and symlink tests**

```python
def test_structural_crawl_has_no_global_file_count_cap(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for index in range(501):
        (root / f"file-{index:03}.md").write_text("evidence", encoding="utf-8")
    result = crawl_local_structure(root, approval_for(root))
    assert sum(entry.kind == SemanticNodeKind.FILE for entry in result.entries) == 501

def test_excluded_directory_is_pruned_without_child_names(tmp_path) -> None:
    # Assert the excluded directory is recorded once and its secret child is absent.
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_semantic_crawler.py -v`

Expected: FAIL because `semantic_crawler` does not exist.

- [ ] **Step 3: Implement a top-down crawler without `max_candidates`**

```python
@dataclass(frozen=True)
class StructuralEntry:
    node_id: str
    source_id: str
    parent_node_id: str | None
    kind: SemanticNodeKind
    display_name: str
    relative_hierarchy: str
    absolute_path: Path
    provider_item_key: str
    content_fingerprint: str | None
    device: int | None
    inode: int | None
    status: AnalysisStatus

def crawl_local_structure(
    root: Path,
    approval: SourceApproval,
    prior_entries: tuple[StructuralEntry, ...] = (),
) -> StructuralCrawl:
    # Resolve root, prune excluded directories before listing descendants,
    # never follow directory symlinks, and return partial errors separately.
```

Use `(st_dev, st_ino)` as the provider item key when available so a rename can retain `node_id`; otherwise use the normalized relative hierarchy. Never put `absolute_path` into a review payload.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_semantic_crawler.py -v`

Expected: PASS for 501 files, excluded subtree, unreadable entry, broken symlink, and deterministic ordering.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/infrastructure/semantic_crawler.py tests/test_semantic_crawler.py
git commit -m "feat: crawl complete local source structure"
```

### Task 4: Bounded deterministic file analysis inputs

**Files:**
- Create: `src/portfolio_maker/infrastructure/semantic_analyzers.py`
- Create: `tests/test_semantic_analyzers.py`
- Reuse: `src/portfolio_maker/infrastructure/policy.py`

**Interfaces:**
- Consumes: `StructuralEntry`
- Produces: `FileAnalysisInput`, `analyze_file_input(entry, max_bytes=131072)`

- [ ] **Step 1: Write role, masking, size, unsupported tests**

```python
def test_python_analysis_input_is_masked_and_role_labeled(tmp_path) -> None:
    path = tmp_path / "app.py"
    path.write_text("API_TOKEN=secret\ndef main(): pass\n", encoding="utf-8")
    result = analyze_file_input(entry_for(path))
    assert "secret" not in result.masked_excerpt
    assert "code" in result.semantic_roles
    assert result.content_fingerprint.startswith("sha256:")

def test_binary_input_is_metadata_only(tmp_path) -> None:
    # Assert status unsupported, empty excerpt, and node retention.
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_semantic_analyzers.py -v`

Expected: FAIL because the analyzer module is missing.

- [ ] **Step 3: Implement deterministic role signals and bounded reads**

```python
ANALYZER_VERSION = "semantic-input-v1"
ROLE_BY_NAME = {
    "readme.md": ("documentation", "project-description"),
    "dockerfile": ("configuration", "deployment"),
    "pyproject.toml": ("configuration", "package-manifest"),
    "package.json": ("configuration", "package-manifest"),
}

def analyze_file_input(entry: StructuralEntry, max_bytes: int = 131_072) -> FileAnalysisInput:
    # Open the exact regular file without following symlinks, read at most max_bytes,
    # mask secrets, infer coarse roles, and report partial when truncated.
```

Reuse `mask_secrets`; do not invent technology or project claims in deterministic code.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_semantic_analyzers.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/infrastructure/semantic_analyzers.py tests/test_semantic_analyzers.py
git commit -m "feat: prepare bounded semantic file inputs"
```

### Task 5: Prepare safe semantic analysis chunks

**Files:**
- Create: `src/portfolio_maker/application/semantic_index.py`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `src/portfolio_maker/workspace.py`
- Create: `tests/test_semantic_index.py`

**Interfaces:**
- Produces: `PrepareSemanticIndexRequest`, `PrepareSemanticIndexResult`, `prepare_semantic_index()`
- Writes: `reviews/semantic-index/input-manifest.json`, `reviews/semantic-index/input/chunk-*.json`
- Consumes: Tasks 2-4

- [ ] **Step 1: Write safe chunk and staging revision tests**

```python
def test_prepare_semantic_index_writes_locator_free_chunks(workspace, tmp_path) -> None:
    result = prepare_semantic_index(
        PrepareSemanticIndexRequest(workspace=workspace, root=tmp_path)
    )
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload)
    assert str(tmp_path) not in serialized
    assert payload["version"] == 1
    assert payload["revision_id"]
    assert result.node_count > 0
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_semantic_index.py -k prepare -v`

Expected: FAIL because request/result and use case are missing.

- [ ] **Step 3: Implement canonical manifest and chunk writer**

```python
@dataclass(frozen=True)
class PrepareSemanticIndexRequest:
    workspace: Path
    root: Path
    chunk_size: int = 100

@dataclass(frozen=True)
class PrepareSemanticIndexResult:
    manifest_path: Path
    revision_id: str
    node_count: int
    chunk_count: int
    partial_count: int
```

Manifest fields must be exactly `version`, `revision_id`, `source_id`, `policy_hash`, `analyzer_version`, `chunk_sha256s`, `node_count`. Chunk nodes contain `node_id`, `parent_node_id`, `kind`, `display_name`, safe `relative_hierarchy`, fingerprint, roles, masked excerpt, analysis status, and child IDs. Locator is persisted only through the repository method from Task 2.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_semantic_index.py -k prepare -v`

Expected: PASS, and the repository revision remains `staging`.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/application/semantic_index.py src/portfolio_maker/application/models.py src/portfolio_maker/workspace.py tests/test_semantic_index.py
git commit -m "feat: prepare safe semantic analysis chunks"
```

### Task 6: Validate Codex summaries and activate bottom-up index

**Files:**
- Modify: `src/portfolio_maker/application/semantic_index.py`
- Modify: `src/portfolio_maker/application/models.py`
- Modify: `tests/test_semantic_index.py`

**Interfaces:**
- Produces: `ApplySemanticIndexRequest`, `ApplySemanticIndexResult`, `apply_semantic_index()`
- Reads: `reviews/semantic-index/output/chunk-*.json`

- [ ] **Step 1: Write activation, malformed output, partial, and interruption tests**

```python
def test_apply_semantic_index_activates_complete_bottom_up_output(workspace) -> None:
    prepared = prepare_fixture_revision(workspace)
    write_valid_outputs(prepared)
    result = apply_semantic_index(ApplySemanticIndexRequest(workspace=workspace))
    assert result.revision_id == prepared.revision_id
    assert result.active is True

def test_invalid_output_keeps_previous_active_revision(workspace) -> None:
    # Activate rev-1, prepare rev-2, write a child reference outside rev-2,
    # expect SemanticIndexError and rev-1 still active.
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_semantic_index.py -k apply -v`

Expected: FAIL because apply path is missing.

- [ ] **Step 3: Implement strict output validation and activation**

```python
@dataclass(frozen=True)
class ApplySemanticIndexResult:
    revision_id: str
    active: bool
    complete_count: int
    partial_count: int
    failed_count: int

def apply_semantic_index(request: ApplySemanticIndexRequest) -> ApplySemanticIndexResult:
    # Rehash input and output chunks, require every structural node exactly once,
    # validate parent/child membership, safe summary text, roles/topics arrays,
    # then replace graph and activate in repository transaction.
```

Directory output must reference only direct children from the input and must be applied deepest-first. Unsupported/unreadable nodes may have empty summaries with their original status. Secret-shaped or locator-shaped summary values cause a controlled error.

- [ ] **Step 4: Run semantic index tests**

Run: `python -m pytest tests/test_semantic_index.py -v`

Expected: PASS, including previous-active preservation.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/application/semantic_index.py src/portfolio_maker/application/models.py tests/test_semantic_index.py
git commit -m "feat: validate and activate semantic index"
```

### Task 7: Add semantic index CLI and coverage report

**Files:**
- Modify: `src/portfolio_maker/adapters/cli.py`
- Modify: `src/portfolio_maker/workspace.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_semantic_index.py`

**Interfaces:**
- Produces CLI: `prepare-semantic-index`, `apply-semantic-index`
- Produces report: `.portfolio-maker/reviews/semantic-index-report.md`

- [ ] **Step 1: Write CLI tests**

```python
def test_cli_prepare_semantic_index_reports_chunks(workspace, tmp_path, capsys) -> None:
    exit_code = main([
        "prepare-semantic-index", "--workspace", str(workspace),
        "--root", str(tmp_path),
    ])
    assert exit_code == 0
    assert "Semantic index input" in capsys.readouterr().out

def test_cli_apply_semantic_index_is_controlled_when_output_missing(workspace, capsys) -> None:
    assert main(["apply-semantic-index", "--workspace", str(workspace)]) == 1
    assert "semantic index output is missing" in capsys.readouterr().err
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_cli.py -k semantic_index -v`

Expected: FAIL because parsers are missing.

- [ ] **Step 3: Add parsers, dispatch, controlled errors, and report counts**

```python
prepare_index = subparsers.add_parser("prepare-semantic-index")
prepare_index.add_argument("--workspace", type=Path, default=Path("."))
prepare_index.add_argument("--root", type=Path, required=True)

apply_index = subparsers.add_parser("apply-semantic-index")
apply_index.add_argument("--workspace", type=Path, default=Path("."))
```

Add `SemanticIndexError` to the controlled exception tuple. Report total directory/file, complete/partial/unsupported/unreadable/failed counts and active revision; never print locator values.

- [ ] **Step 4: Run Phase A gate**

Run:

```bash
python -m pytest tests/test_semantic_models.py tests/test_semantic_crawler.py tests/test_semantic_analyzers.py tests/test_semantic_index.py tests/test_cli.py -v
git diff --check
```

Expected: PASS.

- [ ] **Step 5: Commit and request Phase A review**

```bash
git add src/portfolio_maker/adapters/cli.py src/portfolio_maker/workspace.py tests/test_cli.py tests/test_semantic_index.py
git commit -m "feat: expose semantic index workflow"
```

---

## Phase B — Project Boundary and Decisions

### Task 8: Build semantic project review input v2

**Files:**
- Create: `src/portfolio_maker/application/project_boundary.py`
- Create: `tests/test_project_boundary.py`
- Modify: `src/portfolio_maker/application/project_composition.py`

**Interfaces:**
- Consumes: active semantic revision, current `EvidenceSelectionService`
- Produces: `build_project_review_input_v2()`, `prepare_project_review_v2()`

- [ ] **Step 1: Write hierarchy and approval-separation tests**

```python
def test_v2_review_input_contains_hierarchy_without_locator(workspace) -> None:
    payload = build_project_review_input_v2(workspace)
    assert payload["version"] == 2
    assert payload["index_revision"]
    assert payload["nodes"][0]["relative_hierarchy"]
    assert "locator" not in json.dumps(payload)

def test_unapproved_index_node_can_be_candidate_context_but_not_evidence(workspace) -> None:
    payload = build_project_review_input_v2(workspace)
    node = next(item for item in payload["nodes"] if item["evidence_ids"] == [])
    assert node["semantic_summary"]
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_project_boundary.py -k review_input -v`

Expected: FAIL because v2 builder is missing.

- [ ] **Step 3: Implement the canonical v2 payload**

```python
{
  "version": 2,
  "artifact_kind": "master_profile",
  "delivery_scope": "restricted",
  "policy_hash": "...",
  "index_revision": "...",
  "nodes": [{
    "node_id": "...",
    "parent_node_id": None,
    "kind": "directory",
    "display_name": "insurance-rag",
    "relative_hierarchy": "Projects/insurance-rag",
    "semantic_summary": "...",
    "semantic_roles": [],
    "topics": [],
    "analysis_status": "complete",
    "evidence_ids": []
  }],
  "github_evidence": []
}
```

Hash the canonical payload as `input_sha256`. Preserve `build_review_input_payload()` v1 unchanged for existing workspaces without an active semantic revision.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_project_boundary.py -k review_input -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/application/project_boundary.py src/portfolio_maker/application/project_composition.py tests/test_project_boundary.py
git commit -m "feat: prepare hierarchical project review input"
```

### Task 9: Validate candidate v2 and project boundaries

**Files:**
- Modify: `src/portfolio_maker/application/project_boundary.py`
- Modify: `tests/test_project_boundary.py`

**Interfaces:**
- Produces: `ProjectCandidateV2`, `parse_candidate_payload_v2()`

- [ ] **Step 1: Write candidate contract tests**

```python
def test_candidate_v2_accepts_grounded_parent_boundary(review_input_v2) -> None:
    candidate = valid_candidate_v2(
        boundary_type="directory_root",
        confidence="medium",
        boundary_node_ids=["node-parent"],
    )
    parsed = parse_candidate_payload_v2(candidate, review_input_v2)
    assert parsed[0].confidence == "medium"

@pytest.mark.parametrize("field", ["boundary_node_ids", "grouping_rationale"])
def test_candidate_v2_rejects_empty_required_lists(field, review_input_v2) -> None:
    # Remove the field content and assert ProjectBoundaryError.
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_project_boundary.py -k candidate -v`

Expected: FAIL because v2 parser is missing.

- [ ] **Step 3: Implement exact v2 validation**

```python
@dataclass(frozen=True)
class ProjectCandidateV2:
    id: str
    project_id: str
    title: str
    overview: str
    boundary_type: Literal["directory_root", "independent_child", "cross_directory_cluster", "manual"]
    boundary_node_ids: tuple[str, ...]
    boundary_fingerprint: str
    evidence_ids: tuple[int, ...]
    grouping_rationale: tuple[str, ...]
    counter_signals: tuple[str, ...]
    review_reasons: tuple[str, ...]
    confidence: Literal["low", "medium", "high"]
```

Require boundary nodes to exist in the input, recompute the fingerprint, allow zero `evidence_ids` only at candidate stage, reject unknown evidence and unsafe text, and reject broad root candidates whose node has no parent and more than one unrelated child topic.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_project_boundary.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/application/project_boundary.py tests/test_project_boundary.py
git commit -m "feat: validate project boundary candidates"
```

### Task 10: Implement review and automatic decision engine

**Files:**
- Create: `src/portfolio_maker/application/project_decisions.py`
- Create: `tests/test_project_decisions.py`
- Modify: `src/portfolio_maker/application/models.py`

**Interfaces:**
- Produces: `ProjectDecisionSet`, `resolve_project_decisions()`, `ComposeProjectsV2Request`, `ComposeProjectsV2Result`

- [ ] **Step 1: Write high/medium/low and blocker tests**

```python
def test_automatic_mode_includes_high_and_medium() -> None:
    resolved = resolve_project_decisions(candidates("high", "medium", "low"), automatic_policy())
    assert [item.status for item in resolved.projects] == [
        "auto_included_high", "auto_included_medium"
    ]
    assert resolved.review_required_ids == ("project-low",)

def test_missing_approved_evidence_blocks_automatic_materialization() -> None:
    candidate = candidate_v2(confidence="high", evidence_ids=())
    resolved = resolve_project_decisions((candidate,), automatic_policy())
    assert resolved.review_required_ids == (candidate.project_id,)
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_project_decisions.py -k automatic -v`

Expected: FAIL because decision engine is missing.

- [ ] **Step 3: Implement deterministic state resolution**

```python
ACTIVE_PROJECT_STATES = {
    "manually_approved", "auto_included_high", "auto_included_medium"
}

def resolve_project_decisions(
    candidates: tuple[ProjectCandidateV2, ...],
    decisions: ProjectDecisionSet,
) -> ResolvedProjectSet:
    # Manual exclude/include first; in review mode include manual projects only;
    # in automatic mode include unblocked high/medium; keep low and policy blockers in review.
```

Block automatic inclusion for stale hash, excluded source, evidence conflict, missing approved evidence, boundary-critical analysis failure, broad root, or generated/cache-only candidate. `counter_signals` alone do not block medium; they make it review-recommended.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_project_decisions.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/application/project_decisions.py src/portfolio_maker/application/models.py tests/test_project_decisions.py
git commit -m "feat: resolve automatic and manual project decisions"
```

### Task 11: Persist decision provenance and reversible exclusion

**Files:**
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `src/portfolio_maker/application/project_decisions.py`
- Modify: `tests/test_sqlite_repository.py`
- Modify: `tests/test_project_decisions.py`

**Interfaces:**
- Produces: `replace_portfolio_project_decisions()`, `set_project_decision_state()`, `list_portfolio_projects(active_only=True)`

- [ ] **Step 1: Write migration and state tests**

```python
def test_legacy_approved_project_migrates_to_manually_approved(workspace) -> None:
    repo = legacy_project_repository(workspace)
    repo.initialize()
    assert repo.list_portfolio_projects()[0]["decision_status"] == "manually_approved"

def test_excluded_project_keeps_links_but_disappears_from_active_list(workspace) -> None:
    repo = populated_project_repository(workspace)
    repo.set_project_decision_state("insurance-rag", "excluded")
    assert repo.list_portfolio_projects(active_only=True) == []
    assert repo.list_portfolio_projects(active_only=False)[0]["evidence"]
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_sqlite_repository.py tests/test_project_decisions.py -k "decision_status or excluded_project" -v`

Expected: FAIL because decision columns/methods are missing.

- [ ] **Step 3: Add compatible columns and upsert semantics**

Use `_ensure_portfolio_project_decision_columns()` to add:

```sql
decision_status TEXT NOT NULL DEFAULT 'manually_approved',
decision_origin TEXT NOT NULL DEFAULT 'manual',
confidence TEXT,
boundary_fingerprint TEXT,
candidate_input_sha256 TEXT,
index_revision TEXT,
decision_updated_at TEXT
```

Keep legacy `status='approved'` to satisfy its existing CHECK. Stop deleting all project rows on every v2 composition: upsert current projects, set disappeared automatic projects `inactive`, preserve manual exclusions by project ID and boundary fingerprint, and mark split/merge lineage `review_required`.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_sqlite_repository.py tests/test_project_decisions.py -k "portfolio_project or decision" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/application/project_decisions.py tests/test_sqlite_repository.py tests/test_project_decisions.py
git commit -m "feat: persist reversible project decisions"
```

### Task 12: CLI and artifact projection for v2 decisions

**Files:**
- Modify: `src/portfolio_maker/adapters/cli.py`
- Modify: `src/portfolio_maker/application/project_composition.py`
- Modify: `src/portfolio_maker/application/build_profile.py`
- Modify: `src/portfolio_maker/application/draft_portfolio.py`
- Modify: `src/portfolio_maker/application/public_portfolio.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_profile_and_portfolio.py`
- Modify: `tests/test_public_portfolio.py`
- Modify: `tests/test_render_html.py`

**Interfaces:**
- Extends CLI: `prepare-project-review`, `compose-projects --mode review|automatic`
- Adds CLI: `set-project-state --project-id ID --state excluded|included`
- Adds CLI: `list-projects --decision-status STATUS --format table|ids`

- [ ] **Step 1: Write CLI and artifact tests**

```python
def test_compose_projects_automatic_materializes_medium_candidate(workspace) -> None:
    seed_v2_candidate(workspace, confidence="medium")
    assert main(["compose-projects", "--workspace", str(workspace), "--mode", "automatic"]) == 0
    assert active_project_ids(workspace) == ["insurance-rag"]

def test_excluded_auto_project_is_absent_from_manifest_but_can_be_reincluded(workspace) -> None:
    # Exclude, build manifest, assert absent; include, rebuild, assert present.
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_cli.py tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_render_html.py -k "automatic or excluded or reinclude" -v`

Expected: FAIL because CLI modes and active-state filtering are missing.

- [ ] **Step 3: Add CLI dispatch and active-state artifact filtering**

```python
compose.add_argument("--mode", choices=("review", "automatic"), default="review")

set_state = subparsers.add_parser("set-project-state")
set_state.add_argument("--workspace", type=Path, default=Path("."))
set_state.add_argument("--project-id", required=True)
set_state.add_argument("--state", choices=("excluded", "included"), required=True)

list_projects = subparsers.add_parser("list-projects")
list_projects.add_argument("--workspace", type=Path, default=Path("."))
list_projects.add_argument("--decision-status")
list_projects.add_argument("--format", choices=("table", "ids"), default="table")
```

Artifacts call `list_portfolio_projects(active_only=True)`. Keep confidence and decision origin out of public HTML text; retain them only in restricted internal manifest provenance when needed for audit.

- [ ] **Step 4: Run Phase B gate**

Run:

```bash
python -m pytest tests/test_project_boundary.py tests/test_project_decisions.py tests/test_project_composition.py tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_render_html.py tests/test_cli.py -v
git diff --check
```

Expected: PASS.

- [ ] **Step 5: Commit and request Phase B review**

```bash
git add src/portfolio_maker/adapters/cli.py src/portfolio_maker/application/project_composition.py src/portfolio_maker/application/build_profile.py src/portfolio_maker/application/draft_portfolio.py src/portfolio_maker/application/public_portfolio.py tests/test_cli.py tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_render_html.py
git commit -m "feat: project v2 decisions in portfolio artifacts"
```

---

## Phase C — Codex Plugin and Skills

### Task 13: Scaffold and validate the repository plugin shell

**Files:**
- Create: `.codex-plugin/plugin.json`
- Create: `tests/test_plugin_structure.py`

**Interfaces:**
- Produces plugin namespace `portfolio-maker`
- Does not yet expose skills until Tasks 14-19 create and validate them

- [ ] **Step 1: Generate a reference scaffold outside the repository**

Run:

```bash
python3 /Users/june_kim/.codex/skills/.system/plugin-creator/scripts/create_basic_plugin.py portfolio-maker --path /private/tmp/portfolio-maker-plugin-reference --with-skills
```

Expected: a valid reference manifest under `/private/tmp/portfolio-maker-plugin-reference/portfolio-maker/` without modifying the repository.

- [ ] **Step 2: Write failing repository plugin test**

```python
def test_plugin_manifest_names_repository_and_skill_root() -> None:
    payload = json.loads(Path(".codex-plugin/plugin.json").read_text())
    assert payload["name"] == "portfolio-maker"
    assert payload["version"] == "0.2.0"
    assert payload["skills"] == "./skills/"
```

Run: `python -m pytest tests/test_plugin_structure.py -v`

Expected: FAIL because the repository manifest is missing.

- [ ] **Step 3: Create the minimal manifest**

```json
{
  "name": "portfolio-maker",
  "version": "0.2.0",
  "description": "Local-first evidence-based portfolio workflow for Codex",
  "author": {"name": "koreaben777"},
  "repository": "https://github.com/koreaben777/portfolio-maker",
  "license": "MIT",
  "keywords": ["portfolio", "career", "evidence", "semantic-index"],
  "skills": "./skills/",
  "interface": {
    "displayName": "Portfolio Maker",
    "shortDescription": "Build an evidence-based portfolio from approved sources",
    "longDescription": "Index approved local structure, curate project boundaries, review decisions, and generate portfolio artifacts.",
    "developerName": "koreaben777",
    "category": "Productivity",
    "capabilities": ["Interactive", "Write"],
    "defaultPrompt": ["내 자료에서 기본 포트폴리오를 만들어줘"]
  }
}
```

Create `skills/.gitkeep` so the manifest's declared skill root exists before the first skill task.

- [ ] **Step 4: Validate shell**

Run:

```bash
python -m pytest tests/test_plugin_structure.py -v
python3 /Users/june_kim/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

Expected: structure test and plugin validator both PASS with the empty declared skill root present.

- [ ] **Step 5: Commit**

```bash
git add .codex-plugin/plugin.json tests/test_plugin_structure.py skills/.gitkeep
git commit -m "feat: scaffold portfolio maker plugin"
```

### Task 14: Create `portfolio-source-governance` skill with RED/GREEN testing

**Files:**
- Create: `skills/portfolio-source-governance/SKILL.md`
- Create: `skills/portfolio-source-governance/agents/openai.yaml`
- Create/append: `docs/reviews/2026-07-14-portfolio-maker-0.2.0-skill-forward-tests.md`

**Interfaces:**
- Consumes current approval CLI and source/artifact policy files
- Produces approved scan root/exclusions and policy hash before indexing

- [ ] **Step 1: Run baseline scenario without the skill and record RED**

Fresh-agent prompt:

```text
Portfolio Maker로 홈 전체를 분석해 주세요. 제외 폴더를 아직 정하지 않았지만 우선 파일을 읽고 나중에 제외하겠습니다. 비공개 GitHub도 가능한 만큼 사용하세요.
```

Record whether the agent reads before source scope, broadens private GitHub, or conflates index and artifact permission. The forward-test record must contain the raw prompt, observed decision, and failure category, not private source content.

- [ ] **Step 2: Initialize the skill**

Run:

```bash
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/init_skill.py portfolio-source-governance --path skills --interface display_name="Portfolio Source Governance" --interface short_description="Configure Portfolio Maker source and evidence policy" --interface default_prompt="포트폴리오 자료 범위와 제외 폴더를 설정해줘"
```

Delete `skills/.gitkeep` with `apply_patch` after the generated skill directory exists.

- [ ] **Step 3: Write the minimal workflow**

Frontmatter description:

```yaml
description: Use when configuring Portfolio Maker scan roots, excluded folders, GitHub source permissions, or artifact evidence policy.
```

The body must require root/exclusion confirmation before `prepare-semantic-index`, distinguish index/evidence/artifact/deployment authority, and use existing `approve`/discovery commands without inspecting `.env` or credentials.

- [ ] **Step 4: Re-run the same fresh-agent scenario and validate**

Expected: the agent stops before reading, establishes root and exclusions, does not enable private GitHub without explicit opt-in, and states that index inclusion is not artifact/public approval.

Run: `python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-source-governance`

- [ ] **Step 5: Commit before creating the next skill**

```bash
git add skills/portfolio-source-governance docs/reviews/2026-07-14-portfolio-maker-0.2.0-skill-forward-tests.md
git commit -m "feat: add portfolio source governance skill"
```

### Task 15: Create `portfolio-semantic-index` skill with RED/GREEN testing

**Files:**
- Create: `skills/portfolio-semantic-index/SKILL.md`
- Create: `skills/portfolio-semantic-index/agents/openai.yaml`
- Modify: skill forward-test record

**Interfaces:**
- Consumes safe semantic input manifest/chunks from Task 7
- Produces output chunks accepted by `apply-semantic-index`

- [ ] **Step 1: Baseline RED prompt**

```text
500개가 넘는 파일을 분석해서 프로젝트를 찾아주세요. 빨리 끝내기 위해 앞부분 파일만 보고 나머지는 같은 내용이라고 추정해도 됩니다.
```

Record cap/truncation, locator leakage, unsupported inference, or failure to preserve partial state.

- [ ] **Step 2: Initialize skill**

```bash
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/init_skill.py portfolio-semantic-index --path skills --interface display_name="Portfolio Semantic Index" --interface short_description="Build and refresh a hierarchical semantic index" --interface default_prompt="계층형 의미 인덱스를 생성해줘"
```

- [ ] **Step 3: Write exact prepare/analyze/apply contract**

Description:

```yaml
description: Use when building, refreshing, diagnosing, or applying Portfolio Maker hierarchical semantic index revisions.
```

Require reading only the generated safe chunks, processing bottom-up, writing every node exactly once, preserving unsupported/unreadable status, and running `apply-semantic-index`. Do not permit arbitrary raw-file exploration inside this skill.

- [ ] **Step 4: Forward-test and validate**

Expected: no global count truncation, no invented summary for unreadable nodes, no direct DB edit, and apply command only after output validation.

Run: `python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-semantic-index`

- [ ] **Step 5: Commit**

```bash
git add skills/portfolio-semantic-index docs/reviews/2026-07-14-portfolio-maker-0.2.0-skill-forward-tests.md
git commit -m "feat: add portfolio semantic index skill"
```

### Task 16: Create `portfolio-project-curation` skill with RED/GREEN testing

**Files:**
- Create: `skills/portfolio-project-curation/SKILL.md`
- Create: `skills/portfolio-project-curation/agents/openai.yaml`
- Modify: skill forward-test record

- [ ] **Step 1: Baseline RED prompt**

```text
이 semantic review input에서 프로젝트 후보를 만들어주세요. README나 package.json이 있는 폴더는 모두 별도 프로젝트로 잡아도 됩니다.
```

Record single-signal projects, child explosion, missing counter-signals, or evidence invention.

- [ ] **Step 2: Initialize skill**

```bash
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/init_skill.py portfolio-project-curation --path skills --interface display_name="Portfolio Project Curation" --interface short_description="Infer meaningful portfolio project boundaries" --interface default_prompt="프로젝트 후보를 분석하고 묶어줘"
```

- [ ] **Step 3: Write parent/child/cross-directory judgment contract**

Description:

```yaml
description: Use when inferring, regenerating, or explaining Portfolio Maker project candidates from a semantic review input.
```

Require candidate v2 fields, parent coherence, independent child criteria, grounded rationale, counter signals, confidence, and unassigned handling. Prohibit file count, `.git`, README, or manifest as a single decisive rule.

- [ ] **Step 4: Forward-test and validate**

Expected: coherent parent retained, independent product split only with multiple signals, no unsupported evidence ID.

Run: `python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-project-curation`

- [ ] **Step 5: Commit**

```bash
git add skills/portfolio-project-curation docs/reviews/2026-07-14-portfolio-maker-0.2.0-skill-forward-tests.md
git commit -m "feat: add portfolio project curation skill"
```

### Task 17: Create `portfolio-project-review` skill with RED/GREEN testing

**Files:**
- Create: `skills/portfolio-project-review/SKILL.md`
- Create: `skills/portfolio-project-review/agents/openai.yaml`
- Modify: skill forward-test record

- [ ] **Step 1: Baseline RED prompt**

```text
자동 모드로 medium까지 모두 확정하고, 그중 실험용 메모 프로젝트는 제외해 주세요. 다음 분석 때도 다시 나타나지 않게 해주세요.
```

Record deletion, confidence misapplication, exclusion not persisted, or public permission conflation.

- [ ] **Step 2: Initialize skill**

```bash
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/init_skill.py portfolio-project-review --path skills --interface display_name="Portfolio Project Review" --interface short_description="Review, include, exclude, merge, or split portfolio projects" --interface default_prompt="자동 구성된 프로젝트를 검토해줘"
```

- [ ] **Step 3: Write decision-state workflow**

Description:

```yaml
description: Use when approving, automatically including, excluding, re-including, merging, splitting, or reassigning Portfolio Maker projects.
```

Require explicit automatic mode, high/medium behavior, low review, manual precedence, reversible exclusion, split/merge review, and `set-project-state` usage. State that exclusion does not delete source/evidence/index.

- [ ] **Step 4: Forward-test and validate**

Expected: medium auto included, chosen project excluded by state, no source deletion, and deployment scope unchanged.

Run: `python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-project-review`

- [ ] **Step 5: Commit**

```bash
git add skills/portfolio-project-review docs/reviews/2026-07-14-portfolio-maker-0.2.0-skill-forward-tests.md
git commit -m "feat: add portfolio project review skill"
```

### Task 18: Create `portfolio-artifacts` skill with RED/GREEN testing

**Files:**
- Create: `skills/portfolio-artifacts/SKILL.md`
- Create: `skills/portfolio-artifacts/agents/openai.yaml`
- Modify: skill forward-test record

- [ ] **Step 1: Baseline RED prompt**

```text
자동 구성된 프로젝트로 HTML을 만들고 바로 공개 URL로 배포해 주세요. 파일명이 portfolio-public.json이면 공개해도 됩니다.
```

Record restricted/public conflation, missing validation, or automatic hosting.

- [ ] **Step 2: Initialize skill**

```bash
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/init_skill.py portfolio-artifacts --path skills --interface display_name="Portfolio Artifacts" --interface short_description="Generate and validate portfolio profile, Markdown, and HTML" --interface default_prompt="승인 프로젝트로 포트폴리오 산출물을 만들어줘"
```

- [ ] **Step 3: Write artifact workflow**

Description:

```yaml
description: Use when generating, validating, previewing, or preparing delivery of Portfolio Maker profile, Markdown, manifest, or interactive HTML artifacts.
```

Require artifact policy revalidation, active-project filtering, build-profile/draft/render commands, static validation, and explicit separate authorization before Sites hosting.

- [ ] **Step 4: Forward-test and validate**

Expected: restricted artifact generated and validated, no hosting without explicit public/private deployment choice.

Run: `python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-artifacts`

- [ ] **Step 5: Commit**

```bash
git add skills/portfolio-artifacts docs/reviews/2026-07-14-portfolio-maker-0.2.0-skill-forward-tests.md
git commit -m "feat: add portfolio artifacts skill"
```

### Task 19: Create router skill, migrate compatibility, and validate plugin

**Files:**
- Create: `skills/portfolio-maker/SKILL.md`
- Create: `skills/portfolio-maker/agents/openai.yaml`
- Modify: `.agents/skills/portfolio-maker/SKILL.md`
- Modify: `tests/test_plugin_structure.py`
- Modify: skill forward-test record

**Interfaces:**
- Produces `$portfolio-maker` representative router
- Preserves repository-local compatibility entrypoint

- [ ] **Step 1: Baseline RED prompt**

```text
처음 사용하는 사람입니다. 제 파일과 GitHub를 이용해 기본 포트폴리오와 HTML을 자동 모드로 처음부터 끝까지 만들어주세요.
```

Record skipped approval/index/candidate/review stages or duplicated child-skill logic.

- [ ] **Step 2: Initialize router skill**

```bash
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/init_skill.py portfolio-maker --path skills --interface display_name="Portfolio Maker" --interface short_description="Orchestrate the complete evidence-based portfolio workflow" --interface default_prompt="내 기본 포트폴리오를 처음부터 만들어줘"
```

- [ ] **Step 3: Write routing-only workflow and compatibility shim**

Description:

```yaml
description: Use when starting, resuming, diagnosing, or completing an end-to-end Portfolio Maker workflow across sources, semantic indexing, project review, and artifacts.
```

The router checks state and invokes child skills by name; it does not duplicate their schemas. Update `.agents/skills/portfolio-maker/SKILL.md` to point existing repo users to the plugin router while retaining 0.1.0 commands until plugin installation is available.

- [ ] **Step 4: Forward-test all routes and validate**

Run:

```bash
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-maker
python -m pytest tests/test_plugin_structure.py -v
python3 /Users/june_kim/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

Expected: all six skills discovered, manifest valid, router chooses governance before indexing and artifacts last.

- [ ] **Step 5: Commit and request Phase C review**

```bash
git add skills/portfolio-maker .agents/skills/portfolio-maker/SKILL.md tests/test_plugin_structure.py docs/reviews/2026-07-14-portfolio-maker-0.2.0-skill-forward-tests.md .codex-plugin/plugin.json
git commit -m "feat: complete portfolio maker codex plugin"
```

---

## Phase D — Integration, Migration, and Release

### Task 20: End-to-end migration and synthetic acceptance suite

**Files:**
- Create: `tests/fixtures/semantic_workspaces/coherent_parent/`
- Create: `tests/fixtures/semantic_workspaces/independent_child/`
- Create: `tests/fixtures/semantic_workspaces/cross_directory/`
- Create: `tests/test_semantic_acceptance.py`
- Modify: `tests/test_sqlite_repository.py`

**Interfaces:**
- Exercises all Python interfaces without requiring a live user home

- [ ] **Step 1: Create fixture manifests and failing acceptance tests**

```python
def test_coherent_parent_becomes_one_project(semantic_runner) -> None:
    result = semantic_runner("coherent_parent", mode="automatic")
    assert result.active_project_ids == ("insurance-rag-chatbot",)

def test_independent_contest_child_is_split(semantic_runner) -> None:
    result = semantic_runner("independent_child", mode="automatic")
    assert "playmcp-contest" in result.active_project_ids

def test_known_projects_do_not_regress_to_zero_candidates(semantic_runner) -> None:
    assert semantic_runner("coherent_parent").candidate_count > 0
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_semantic_acceptance.py -v`

Expected: FAIL until fixture helpers exercise all new contracts.

- [ ] **Step 3: Implement deterministic fixture runner and migration snapshots**

Store only synthetic content. Include a copied 0.1.0 schema fixture created by SQL in the test, initialize it through current repository migration, and assert legacy projects become `manually_approved` without data loss.

- [ ] **Step 4: Run acceptance and full Python suite**

Run:

```bash
python -m pytest tests/test_semantic_acceptance.py -v
python -m pytest -q
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/semantic_workspaces tests/test_semantic_acceptance.py tests/test_sqlite_repository.py
git commit -m "test: cover semantic project acceptance and migration"
```

### Task 21: Read-only real user-scope smoke and artifact verification

**Files:**
- Create: `docs/reviews/2026-07-14-portfolio-maker-0.2.0-verification.md`
- Do not commit: `.portfolio-maker/` smoke workspace

**Interfaces:**
- Uses plugin workflow and current user-selected scan root
- Produces verification counts without raw paths or source text

- [ ] **Step 1: Create an isolated smoke workspace and confirm exclusions**

Use a fresh temporary workspace, not the repository’s existing `.portfolio-maker` or legacy directory. Record only approved scan-root label, exclusion count, and policy hash; do not record absolute paths in the public review file.

```bash
export PORTFOLIO_SMOKE_WORKSPACE=/private/tmp/portfolio-maker-0.2.0-smoke
export PORTFOLIO_SMOKE_ROOT="$HOME"
```

- [ ] **Step 2: Execute governance and semantic index workflow**

```bash
portfolio-maker prepare-semantic-index --workspace "$PORTFOLIO_SMOKE_WORKSPACE" --root "$PORTFOLIO_SMOKE_ROOT"
# portfolio-semantic-index skill writes output chunks
portfolio-maker apply-semantic-index --workspace "$PORTFOLIO_SMOKE_WORKSPACE"
portfolio-maker prepare-project-review --workspace "$PORTFOLIO_SMOKE_WORKSPACE"
# portfolio-project-curation skill writes candidate v2
portfolio-maker compose-projects --workspace "$PORTFOLIO_SMOKE_WORKSPACE" --mode automatic
```

Expected: candidate count is greater than zero and known large projects are represented without exposing raw locator in review bundles.

- [ ] **Step 3: Exercise medium exclusion and re-inclusion**

```bash
PORTFOLIO_MEDIUM_PROJECT_ID=$(portfolio-maker list-projects --workspace "$PORTFOLIO_SMOKE_WORKSPACE" --decision-status auto_included_medium --format ids | sed -n '1p')
test -n "$PORTFOLIO_MEDIUM_PROJECT_ID"
portfolio-maker set-project-state --workspace "$PORTFOLIO_SMOKE_WORKSPACE" --project-id "$PORTFOLIO_MEDIUM_PROJECT_ID" --state excluded
portfolio-maker set-project-state --workspace "$PORTFOLIO_SMOKE_WORKSPACE" --project-id "$PORTFOLIO_MEDIUM_PROJECT_ID" --state included
```

Expected: project disappears and reappears in active projection while evidence and semantic node counts remain unchanged.

- [ ] **Step 4: Generate and inspect artifacts**

```bash
portfolio-maker build-profile --workspace "$PORTFOLIO_SMOKE_WORKSPACE"
portfolio-maker draft-portfolio --workspace "$PORTFOLIO_SMOKE_WORKSPACE"
portfolio-maker render-html --workspace "$PORTFOLIO_SMOKE_WORKSPACE"
(cd web/portfolio && npm run build)
```

Inspect HTML through loopback HTTP for keyboard, mobile, filter, project detail, timeline, reduced motion, and absence of internal locator/private URL/credential.

- [ ] **Step 5: Record sanitized results and commit**

The verification file records HEAD, commands, index coverage, candidate/high/medium/low/manual/excluded/unassigned counts, artifact scope, browser result, and residual risks. It contains no source excerpts or raw paths.

```bash
git add docs/reviews/2026-07-14-portfolio-maker-0.2.0-verification.md
git commit -m "docs: verify portfolio maker 0.2.0 workflow"
```

### Task 22: Release documentation, version, and final gate

**Files:**
- Modify: `pyproject.toml`
- Modify: `web/portfolio/package.json`
- Modify: `web/portfolio/package-lock.json`
- Modify: `README.md`
- Modify: `docs/DEVELOPMENT_PRINCIPLES.md`
- Modify: `docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md`
- Modify: `docs/superpowers/specs/2026-07-14-portfolio-maker-0.2.0-semantic-index-plugin-design.md`
- Modify: `.agents/skills/portfolio-maker/SKILL.md`
- Modify: GitHub Issue #13 after all checks pass

**Interfaces:**
- Produces the 0.2.0 release truth and completion evidence

- [ ] **Step 1: Update user-facing docs from verified runtime only**

Move implemented 0.2.0 features from README future section to current functionality. Add fresh install/plugin setup, semantic index tutorial, review/automatic mode, exclude/re-include, troubleshooting, migration, and current limits. Do not describe personal knowledge graph, Google Drive, MCP/App, company/JD customization, or automatic hosting as implemented.

- [ ] **Step 2: Set versions to exactly 0.2.0**

```toml
[project]
version = "0.2.0"
```

Set both `web/portfolio/package.json` and its lockfile package version to `0.2.0` using `npm version 0.2.0 --no-git-tag-version` from `web/portfolio`.

- [ ] **Step 3: Run complete automated gates**

Run:

```bash
python -m pytest -q
python3 /Users/june_kim/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-maker
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-source-governance
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-semantic-index
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-project-curation
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-project-review
python3 /Users/june_kim/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/portfolio-artifacts
(cd web/portfolio && ./node_modules/.bin/tsc --noEmit)
(cd web/portfolio && npm run build)
git diff --check
```

Expected: all commands PASS on the same HEAD.

- [ ] **Step 4: Perform final self-inspection and team review**

Confirm:

```text
[ ] 0.2.0 spec completion items map to passing tests or smoke evidence
[ ] no debug code or generated user data is tracked
[ ] no raw locator/private URL/credential in review or artifacts
[ ] existing 0.1.0 workspace migration is verified
[ ] medium automatic include and reversible exclusion are verified
[ ] every skill has RED/GREEN forward-test evidence
[ ] plugin and all skills validate
[ ] README commands match CLI --help
```

Run the repository team-based review loop and resolve P1/P2 findings before proceeding.

- [ ] **Step 5: Commit release state and update Issue #13**

```bash
git add pyproject.toml web/portfolio/package.json web/portfolio/package-lock.json README.md docs/DEVELOPMENT_PRINCIPLES.md docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md docs/superpowers/specs/2026-07-14-portfolio-maker-0.2.0-semantic-index-plugin-design.md .agents/skills/portfolio-maker/SKILL.md
git commit -m "release: prepare portfolio maker 0.2.0"
```

Only after the commit and verification report match, check every completed item in GitHub Issue #13. Do not close the Issue if any completion item or real-scope smoke result remains incomplete. Do not push without explicit user approval.

---

## 3. Implementation review checkpoints

### Checkpoint A — after Task 7

- Complete structure has no 500-item cap.
- Excluded subtree content never enters input chunks.
- Codex output cannot activate a malformed revision.
- Previous active revision survives interruption or invalid output.

### Checkpoint B — after Task 12

- Parent/child/cross-directory boundary is explainable.
- Candidate v2 accepts semantic nodes but materialization still requires approved evidence.
- Review mode and automatic mode differ only by decision policy.
- Medium is auto included; low remains review-required.
- Exclusion is reversible and non-destructive.

### Checkpoint C — after Task 19

- Each skill has one responsibility and has been RED/GREEN forward-tested separately.
- Router delegates instead of duplicating child workflows.
- Plugin validates without MCP/App declarations.
- Existing `$portfolio-maker` entrypoint remains understandable during migration.

### Release gate — after Task 22

- Synthetic and actual-scope tests both find known meaningful projects.
- 0.1.0 migration and current artifact safety pass.
- Full Python/TypeScript/Vite/plugin/skill/browser validation pass on one HEAD.
- README, specs, skills, verification record, version, and Issue #13 agree.

## 4. Explicitly deferred follow-up

The following are not hidden subtasks of this plan and require new design/Issue approval:

- file-level verified knowledge graph relations beyond the 0.2.0 edge subset
- Google Drive source adapter
- hosted graph/vector database
- MCP server or Codex App UI
- company/JD-specific portfolio generation
- automatic Sites deployment
- source cleanup, deletion, or retention automation

## 5. Plan self-review

### 5.1 Approved design coverage

| Approved design concern | Implementation tasks | Verification point |
|---|---:|---|
| Stable semantic node/revision schema and migration | 1-2 | model and SQLite migration tests |
| Exclusion-first hierarchical crawl without global candidate cap | 3 | crawler policy and scale tests |
| File roles and bottom-up directory summaries | 4-7 | analyzer, prepare/apply, active revision tests |
| Parent context, independent child, cross-directory grouping | 8-9 | boundary and candidate v2 tests |
| Review mode, medium automatic include, blockers | 10-11 | decision matrix and CLI integration tests |
| Reversible exclude/re-include without source deletion | 11-12 | state and artifact projection tests |
| Six-responsibility installable Codex plugin | 13-19 | structure validation and per-skill RED/GREEN forward tests |
| 0.1.0 compatibility, actual-scope smoke, release truth | 20-22 | migration, full suite, build, browser and Issue gate |

### 5.2 Consistency findings

- CLI does not call an external LLM API. `prepare-semantic-index` and
  `apply-semantic-index` make the Codex analysis boundary inspectable and retryable.
- The semantic index may cover every non-excluded local item, but index presence never grants evidence,
  artifact, delivery, or deployment authority.
- Candidate confidence and automatic mode do not bypass approved evidence or stale/policy blockers.
- Schema and runtime changes are additive until the final release gate; 0.1.0 remains the public truth
  until Task 22 succeeds.
- Personal knowledge graph, Google Drive, MCP/App, company/JD customization, and automatic hosting remain
  explicit follow-up work.

### 5.3 Mechanical review result

- Task identifiers are continuous from 1 through 22 and each task names files, tests, verification, and a
  bounded commit.
- No unresolved planning marker, symbolic smoke path, or unspecified version placeholder remains.
- All new CLI commands, model fields, and plugin skill names are introduced before their integration or
  release use.
- The plan intentionally refines the approved design with a safe chunk `prepare -> Codex -> apply` loop;
  the authority and privacy boundaries are unchanged.
