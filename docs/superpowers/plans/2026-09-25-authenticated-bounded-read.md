<!-- restart-2026-10-11 -->
> **2026-10-11 문서 분류: 이전 설계·계획·인계 기록.** 재개 범위와 다음 작업은 [프로젝트 재개안](../../../../../docs/PROJECT_RESTART.md)을 따른다. 아래의 당시 승인·기술 계약·검증 기록은 보존하며, 미완료 체크박스를 지금의 실행 지시로 해석하지 않는다. 보안·근거·호환 계약을 완화하는 변경이 아니다.
<!-- /restart-2026-10-11 -->

# Authenticated Bounded Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended for this shared-authority change) or superpowers:subagent-driven-development to implement task-by-task. Steps use checkbox syntax. User review of this plan is required before product implementation.

**Goal:** 기존 전체 seal을 유지하면서 새 generation의 일반 next/validate가 인증된 레코드·범위만 읽도록 하고 조기 E의 전수 재검증 실패를 제거한다.

**Architecture:** bounded canonical Merkle tree를 별도 immutable pack으로 작성한다. 전체 catalog/structural seals 및 인증 root를 manifest→proposal→approval에 결합하고, runtime은 승인된 root에서 직접 tree를 탐색한다. 기존 SemanticCatalogReader는 full/legacy 전용으로 보존한다.

**Tech Stack:** Python 3.11+, stdlib hashlib/json/sqlite3/os/struct, 기존 PMPACK1·managed-files·pytest. 새 패키지/서비스 없음.

**Spec:** `docs/superpowers/specs/2026-09-24-authenticated-bounded-read-design.md` — 상세 명세 사용자 승인 2026-09-25.

## Global Constraints

- 부분 검증은 이번 연산에서 소비한 레코드의 값, 승인된 generation 소속, 정렬 및 조회 범위의 완전성을 증명한다.
- 읽지 않은 모든 바이트의 현재 무결성은 부분 검증으로 증명하지 않는다. 미조회 영역의 손상은 이후 접근 또는 full verification에서 발견될 수 있다.
- 부분 검증 결과를 full_verified로 표시하지 않는다. 기본 aggregate status도 그대로 aggregate_bound다.
- 기존 root 및 개발 worktree WIP, HEAD, stash, 기존 active revision을 보존한다.
- 기준 root는 조회 대상 sidecar에서 스스로 가져와 신뢰하지 않는다.
- 기존 seal-only staging과 v1/v2 compatibility는 기존 전수 검증 경로를 유지한다.
- 자동 sidecar 생성 또는 manifest/proposal 재결합을 수행하지 않는다.
- runtime receipt self-hash, canonical accounting, checkpoint, result-pack authority 검사는 별개다. structural/catalog bounded read 도입을 이유로 R1 검사를 제거하지 않는다.
- Git add/commit/stash/reset/push/merge, 설치, 배포, 실자료/홈 스캔은 금지. 계획의 작업 종료는 commit이 아니라 delta/evidence 기록이다.
- 작업 위치: `.worktrees/portfolio-maker-0.2.2-cycle-01`; HEAD 기준 `15ca55093cf7bcaed1182bc08af0d1e38d2a6f0e`; 현재 inherited tracked src/tests diff SHA256 `cd37578e809ac613d3900a993a5c20874806063ebf72ce6a51a0e2115b9b8a0b`. 실행 직전 다시 확인하고 차이가 있으면 보존 기준을 새로 기록한다.
- 이 계획은 원래 Task11 cache 및 Task12 finalize/apply 구현을 포함하지 않는다. 새 full verifier는 Task12가 소비할 선행 API이며 activation API를 추가하지 않는다.

## Review Focus

1. 인증된 row만 남기고 child 하나를 누락한 페이지: 정상 빈 결과/종료로 인정되면 안 됨 — Task2 range tests.
2. 올바른 hash지만 leaf/internal kind, namespace, generation이 다른 record: 승인된 root로 인증되지 않으면 거부 — Tasks1/4.
3. canonical JSON으로 표현 가능한 거대 length/key/proof 및 bool-as-int: allocation 전에 제한 — Tasks1/2/3.
4. full verify 이후 동일 크기 교체·rename·재시작: checked flag/stat cache로 과거 검증을 현재 전체 무결성으로 주장하지 않음 — Tasks3/5/6.
5. 소스 구조가 넓거나 audit가 닫히는 특수 경로: bounded page와 전체 알고리즘 비용을 혼동하지 않음 — Tasks5/6, 별도 phase 계측.

## 현재 호출과 범위 경계

| 실제 consumer | 현재 authority read | 새 경로 |
|---|---|---|
| `_validate_reopened_authority` | structural verify + full catalog + deferred summary | manifest/proposal/current policy 검증 + authenticated metadata point read |
| `_materialize_bounded_v3_input` | structural verify + full catalog | authenticated node/baseline/children + verified reference read |
| `_validated_frontier_transition` | full catalog + node/runtime_deferred_projection | authenticated node/deferred projection |
| `_final_expansion_audit_freeze` | full catalog + deferred iterator | authenticated deferred pages; **population 순회 자체는 별도 비용** |
| `representative_nodes` | 64행씩 전체 children 순회 | page authenticity 제공; 호출자 전체 loop를 O(1)로 주장하지 않음 |
| prepare/reopen/full verification | full catalog/pack | 기존 full + 새 index 대응 검증 |

**중요:** E의 기존 실패는 매 호출 whole-generation 검증이다. authenticated pages로 교체해도 audit freeze의 전체 deferred population 선택은 O(N)이다. 해당 분기에서도 모든 validate가 bounded라는 최종 주장 또는 E 전체 PASS는 하지 않는다. Task6에서 해당 분기를 측정하고, 원래 E 계약이 그 분기까지 bounded를 요구하면 **NEEDS_WORK로 정지**하여 별도 승인된 audit-selection 선행 작업으로 넘긴다. 이 계획에 audit selector 변경을 몰래 포함하지 않는다.

## 파일 경계

Create:
- `src/portfolio_maker/infrastructure/semantic_auth_index.py`: canonical tree writer/reader, authenticated point/range traversal, bounded observation.
- `src/portfolio_maker/infrastructure/semantic_authenticated_catalog.py`: catalog projection export, typed adapter, full cross-verification; SQL은 build/full 경로에만 존재.
- `tests/test_semantic_auth_index.py`, `tests/test_semantic_authenticated_catalog.py`: 각각 wire/proof와 typed projection의 독립 tests.

Modify narrowly:
- `infrastructure/semantic_pack.py`: optional maximum record length + same-descriptor identity checks; 기존 calls의 full semantics 유지.
- `application/semantic_prepare_v3.py`: new manifest optional-version envelope, index build/full check/publication.
- `application/semantic_lifecycle_v3.py`: manifest-bound reader dispatch, structural whole verification의 new-format 경로 분리.
- `application/semantic_frontier_v3.py`: reader type annotation을 필요한 typed protocol로만 변경; selector algorithm 불변.
- `infrastructure/semantic_catalog.py`: projection용 typed decode/pure validation 최소 재사용; 기존 full-reader constructor 약화 금지.
- `tests/test_semantic_prepare_v3.py`, `tests/test_semantic_lifecycle_v3.py`, `tests/test_semantic_pack.py`, `tests/test_semantic_scale_v3.py`.
- 결과 기록: `docs/reviews/2026-09-25-authenticated-bounded-read-review.md`.

sqlite_repository/domain receipt schema는 이 계획에서 바꾸지 않는다. manifest hash가 기존 proposal/DB authority에 이미 포함되므로 별도 승인 root column은 불필요하다. 필요성이 확인되면 계획 변경으로 기록하고 승인 없이 범위를 확장하지 않는다.

## 고정 wire/API 결정

### Constants

```python
AUTH_FORMAT = "semantic-auth-index-v1"
PROJECTION_VERSION = "semantic-auth-projection-v1"
HASH_DOMAIN = b"portfolio-maker-semantic-auth-v1\0"
MAX_NODE_BYTES = 65_536
MAX_VALUE_BYTES = 1_048_576
MAX_KEY_BYTES = 8_192
MAX_FANOUT = 16
MAX_LEAF_ROWS = 16
MAX_HEIGHT = 32
MAX_PAGE_ROWS = 64
MAX_TREE_READ_BYTES = 16 * 1_048_576  # one point/page operation; fail, not fallback
```

Valueがleaf bytesに収まらない場合、同じ auth pack内の `value` recordへ分離し、leafがそのoffset/length/hashを認証する。1MiBを超えるvalueはprepareで安全に失敗し、切り捨てない。既存fixturesの最大値を実装前に確認し、この上限で正常fixtureが拒否される場合は記録して設計変更を相談する。

### Encoding

key = tuple[str,...]; 各要素のUTF-8 bytesを比較するlexicographic order。SQLite sorting spool用は各要素のhex文字列を `!` で終端して連結しBINARY collationで並べる。`!` はhex alphabetより前なのでprefix順も一致。Unicode正規化を暗黙に行わない。key全体のUTF-8合計8KiB以下、最大8要素。leaf/branchはmin/max/count/heightを持ち、空treeは唯一のempty leaf（min/max=null,count=0,height=0）。非空leafはheight=0、branchはheight>=1で全childのheightが一つ下、count合計とrangeが一致する。

PMPACK1のpayload digestとは別に `sha256(HASH_DOMAIN + canonical_payload)` をsemantic node digestとして使う。parent child descriptorはrefのPMPACK digestとsemantic digestの両方を含む。全payloadは `format,generation_id,kind` を含む。root descriptorも同じmetadataを含み承認済みmanifestに結合する。子のoffsetは親frameより前、recordsのrangeは重ならず親heightと整合する。自己参照・cycle・過大depthを拒否する。

### API (Task1で定義)

```python
@dataclass(frozen=True)
class AuthEntry:
    key: tuple[str, ...]
    value: object

@dataclass(frozen=True)
class AuthTreeSeal:
    generation_id: str
    root: PackRecordRef
    root_digest: str
    height: int
    count: int
    pack_seal: PackSeal

@dataclass(frozen=True)
class AuthPage:
    entries: tuple[AuthEntry, ...]
    next_key: tuple[str, ...] | None
    complete: bool

@dataclass
class AuthReadObserver:
    node_reads: int = 0
    value_reads: int = 0
    explicit_read_bytes: int = 0
    max_retained_rows: int = 0

class AuthIndexError(ValueError):
    pass

def build_auth_index(path: Path, entries: Iterable[AuthEntry], *, generation_id: str) -> AuthTreeSeal:
    # entries must be strictly increasing; duplicate/out-of-order is an error
    ...

class AuthIndexReader:
    def __init__(self, path: Path, seal: AuthTreeSeal, *, observer: AuthReadObserver | None = None): ...
    def __enter__(self) -> AuthIndexReader: ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def get(self, key: tuple[str, ...]) -> AuthEntry | None: ...
    def page(self, prefix: tuple[str, ...], after: tuple[str, ...] | None, limit: int) -> AuthPage: ...
    def close(self) -> None: ...
```

上記ellipsisはsignature notationのみ。実装アルゴリズムとRED testsは各Taskで規定する。afterはprefix内のkeyに限る。`next_key` は続きが存在するときだけ最後の返却key、completeは範囲内の後続が存在しないことを認証したときのみtrue。呼び出し側がSQLで選んだ候補からproofを組み立てるのではなく、trusted rootからrange walkする。

## 共通実行・記録

開発worktreeで環境追加なし:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=src:tests
PY=../../.venv/bin/python3
$PY -m pytest -q -p no:cacheprovider
```

Expected before implementation: 1766 passed,1 skipped（再実行時の実数を記録）。opt-in Eは現状失敗する。保存済みbaselineだけを新runの成功根拠にしない。各TaskはREDログ→実装→GREENログを `.superpowers/sdd/2026-09-25-authenticated-bounded-read/` に残す。未承認Git操作の代わりにdiff hashとファイル一覧を記録する。

---

### Task 1: Canonical tree codecとbounded streaming builder

**Files:** Create `semantic_auth_index.py`, `tests/test_semantic_auth_index.py`.
**Consumes:** canonical JSON, PMPackWriter/Reader, PackRecordRef, PackSeal, managed-files.
**Produces:** 上記types/constants/build_auth_index; Task2が同じwireを読む。

- [ ] **1. RED testsを書く。** 空・1件・17件・多階層、順序違反、duplicate、bool/negative offset、長いkey、過大value、unknown field、duplicate JSON key、generation/kind mismatchの表形式testsを追加する。writerがproducer全体をlist化すると失敗するgenerator observerを置く。

```python
def test_builder_rejects_duplicate_keys(tmp_path):
    entries = [AuthEntry(("nodes", "a"), {"id": "a"}),
               AuthEntry(("nodes", "a"), {"id": "b"})]
    with pytest.raises(AuthIndexError):
        build_auth_index(tmp_path / "auth.pm-pack", entries, generation_id="0" * 32)
    assert not (tmp_path / "auth.pm-pack").exists()
```

- [ ] **2. Run:** `$PY -m pytest -q -p no:cacheprovider tests/test_semantic_auth_index.py`. Expected RED: import/missing builder, then behavior failures; typo/environment errorsをfeature REDとしない。
- [ ] **3. 最小codec/builderを実装。** 葉は16件または64KiB境界でflushしdescriptorをdisk spoolへ出す。spoolを16件ずつ読んでparentをappendし次level spoolへ出す。全level descriptorをPython listに保持しない。単一rootまで繰り返し、fsync/seal後にreturn。例外時は自分のtemporaryのみabort、既存pathを上書きしない。

```python
# Internal builder loop; level input/output are file-backed descriptor streams.
while descriptor_count > 1:
    next_count = 0
    for children in bounded_groups(level_stream, MAX_FANOUT):
        parent = encode_branch(children, generation_id)
        write_descriptor(next_stream, append_checked_node(parent))
        next_count += 1
    descriptor_count = next_count
    level_stream = reopen_next_level()
```

`bounded_groups/encode_branch/write_descriptor/append_checked_node/reopen_next_level`はこのmoduleのprivate helpersとしてこのTaskで定義する。固定長count+length prefixed canonical descriptor spoolを用い、読み取り長上限も64KiB。最終単独childはpromotionして無意味な1-child chainを作らない。
- [ ] **4. Run:** 同じtest command。Expected GREEN。writer終了後failure、file already exists、generator failureでもfinalが現れないことを確認。
- [ ] **5. Task1 diff/logを記録。** commitしない。

### Task 2: Root-driven point/range verifier

**Files:** Modify `semantic_auth_index.py`; test same file.
**Consumes:** Task1 AuthTreeSeal/wire。
**Produces:** AuthIndexReader.get/page, AuthPage, observer。

- [ ] **1. RED tests追加。** 手書きのsorted fixtureで境界と非存在をassertする。単純な真のrow membershipだけでpage終了と認める実装を落とす。

```python
def test_range_is_complete_across_leaf_boundary(tmp_path):
    entries = [AuthEntry(("children", "p", f"{i:03d}"), i) for i in range(35)]
    seal = build_auth_index(tmp_path / "auth.pm-pack", entries, generation_id="0" * 32)
    with AuthIndexReader(tmp_path / "auth.pm-pack", seal) as reader:
        a = reader.page(("children", "p"), None, 16)
        assert [e.value for e in a.entries] == list(range(16))
        assert a.next_key == ("children", "p", "015") and not a.complete
        b = reader.page(("children", "p"), a.next_key, 64)
        assert [e.value for e in b.entries] == list(range(16, 35))
        assert b.next_key is None and b.complete
        assert reader.get(("children", "p", "017x")) is None
        assert reader.page(("children", "absent"), None, 64).entries == ()
```

追加ケース: prefixより前後にkey、Unicode/prefix key、既存でないcursor、空tree、limit=0/65/bool、別prefix cursor、middle leaf truncation、別generation root、external value corruption、branch descriptor mismatch。tree bytesを改変してdigest不一致で止まる検査と、承認済みdescriptor下の未読subtreeを省略できない探索検査を区別する。
- [ ] **2. Run:** `$PY -m pytest -q -p no:cacheprovider tests/test_semantic_auth_index.py`. Expected RED: reader missing/incorrect boundary。
- [ ] **3. verifier実装。** root refからbounded pread、canonical/hash/domain/generation確認、root sealのmin/max/count/height整合確認。node内key strict order、child ranges disjoint、counts sum、offset/length boundsを確認してからbranchを選択。range traversalはprefix/cursorと交差するchildだけdepth-firstで読む。戻り先stackの未訪問兄弟から後続存在を判定し、limit+1件まで探索してnext/completeを決める。未訪問childのdescriptorによる境界判定は認証済みparentだけに基づく。SQLやuntrusted next pointerを信用しない。

```python
rows = []
for entry in walk_authenticated_range(prefix, after):
    rows.append(entry)
    if len(rows) == limit + 1:
        return AuthPage(tuple(rows[:limit]), rows[limit - 1].key, False)
return AuthPage(tuple(rows), None, True)
```

`walk_authenticated_range`はこのTaskでreader private methodとして定義。1-call総bytes16MiB、height32、key/row上限を守り、over budgetはAuthIndexError。任意pathへのproof参照禁止。読み終わりのdescriptor identityも確認。
- [ ] **4. Run:** 同test command。Expected GREEN、1k/10k tree point accessのnode readsはheight+value loadに制限、rootから全leaf walkしない。file内容をmutableにした後の新readerが以前のchecked状態を引き継がない。
- [ ] **5. Task2 diff/log記録。** 未使用subtree corruptionでpartialが成功してよいという仕様もtest名に明記。

### Task 3: Typed catalog projectionとfull cross-verifier

**Files:** Create `semantic_authenticated_catalog.py`, `tests/test_semantic_authenticated_catalog.py`; modify `semantic_catalog.py`, `semantic_pack.py`及び既存pack tests。
**Consumes:** Task1/2 tree APIs、既存NodeRecord/BaselineRecord/DeferredSummary等。
**Produces:**

```python
def build_authenticated_catalog(revision: Path, catalog_seal: CatalogSeal,
                                pack_seal: PackSeal, generation_id: str) -> AuthTreeSeal: ...
def verify_authenticated_catalog_full(revision: Path, catalog_seal: CatalogSeal,
                                      pack_seal: PackSeal, seal: AuthTreeSeal) -> None: ...
class AuthenticatedCatalogReader:
    # context manager; no runtime SQLite connection
    def __init__(self, revision: Path, seal: AuthTreeSeal): ...
    def node(self, node_id: str) -> NodeRecord | None: ...
    def baseline(self, node_id: str) -> BaselineRecord | None: ...
    def children_page(self, parent_node_id: str, after_node_id: str | None, limit: int) -> list[NodeRecord]: ...
    def representative_page(self, parent_node_id: str, after_node_id: str | None, limit: int) -> list[RepresentativeCandidate]: ...
    def runtime_deferred_projection(self, selector_version: str, node_ids: tuple[str, ...], *, observer=None) -> tuple[SemanticDeferredAuditRow, ...]: ...
    def deferred_summary(self, *, selector_version: str) -> DeferredSummary: ...
    def iter_frontier_deferred_for_audit(self, *, selector_version: str, fetch_size: int) -> Iterator[SemanticDeferredAuditRow]: ...
```

- [ ] **1. RED:** existing catalog fixturesでfull readerと新typed adapterの値を照合する。順序/抜けに関してはfixture由来のliteralもassert。検証済みcatalogから生成した後、runtimeではsqlite3.connectを失敗させてもtyped pageが読めるtestを追加。古いreaderはtamper時に従来どおり失敗することを保つ。

```python
def test_typed_children_keep_exact_order(authenticated_three_children):
    revision, seal = authenticated_three_children
    with AuthenticatedCatalogReader(revision, seal) as reader:
        assert [n.node_id for n in reader.children_page("parent", None, 2)] == ["a", "b"]
        assert [n.node_id for n in reader.children_page("parent", "b", 2)] == ["c"]
```

`authenticated_three_children`はこのtest fileで定義するfixture。既存SemanticCatalogWriterを使いparent/sourceとa/b/c file, baselineを作成しseal、empty structural packをseal（payload refs無し）、build_authenticated_catalogを呼ぶ。これはTask3専用test utilityでproduction helperにしない。
- [ ] **2. Run:** `$PY -m pytest -q -p no:cacheprovider tests/test_semantic_authenticated_catalog.py tests/test_semantic_pack.py tests/test_semantic_catalog_v3.py`. Expected REDは新adapter不足。
- [ ] **3. exporter実装。** authoritative raw tablesは `("table",table_name,*primary_key)` に保存。projectionは `("node",id)`, `("baseline",id)`, `("children",parent,child)`, `("representative",parent,child)`, `("runtime-deferred",selector,id)`, `("audit-deferred",selector,*audit_order_key)`, `("summary",selector)` とする。raw rowsとprojection双方をdisk SQLite spoolへINSERTしbinary ordered keyでstream。audit integer order keysは非負64bit範囲を確認後16桁hex encoding。既存`frontier_deferred_cursor`と同一並びをfixture比較する。

runtime-deferredは既存`runtime_deferred_projection`を同一full-readerで64件ずつ呼びtyped rowsを保存し、欠損やselected rowを勝手にdeferred化しない。empty requestでもselector authorityを認証する。representativeは元のSQL JOINと同じ対象集合で作り、filtered subsetのcompleteをtreeで証明する。summaryは既存ロジックのcount/root/cursor_rootを保存してmanifest planと照合する。

- [ ] **4. full verifier実装。** 既存CatalogReader/verify_packで全seal確認→auth pack全seal確認→全tree nodeの構造制約確認→同じversioned export streamとtree全entryをzip_longestで比較し欠落/余分/差分で失敗。全root descriptorのgeneration/bindings確認。各payload refのkind/offset/length/digestとstructural recordを検査。disk spoolは失敗時に所有ファイルだけ回収。独立literal/known fixture testsでexporterとverifierの共通bugを補う。

```python
missing = object()
for expected, observed in zip_longest(expected_entries, actual_entries, fillvalue=missing):
    if expected is missing or observed is missing or expected != observed:
        raise AuthIndexError("authenticated catalog projection mismatch")
```

- [ ] **5. pack boundary RED→GREEN。** `PMPackReader.read(reference, *, max_record_bytes: int | None = None)`を追加し、payload_lengthが上限を超えるとpayload pread前に拒否。既存default維持、auth readerは必ず上限指定。同fdのpre/post fstatと読んだbytes hash検証。testsは巨大declared lengthでpayload allocation/readが無いこと、原子rename・同inode変更・symlink・短いreadを検査。
- [ ] **6. Run:** Step2 command、Expected GREEN。unused catalog/pack bytesを損傷しpartialが成功しfullが拒否するtest、auth packへの未参照tail追加をfullで拒否するtestを含む。
- [ ] **7. Task3 diff/log記録。** full verifierはread-only、manifest/DB更新しない。

### Task 4: Prepare publicationとversioned manifest authority

**Files:** Modify `semantic_prepare_v3.py`, `tests/test_semantic_prepare_v3.py`, typed catalog tests。
**Consumes:** build/full APIs from Task3。
**Produces:** 新prepareの `authenticated_index` manifest field（optional for legacy only）。

manifest extension exact keys:

```json
{"format":"semantic-auth-index-v1","projection":"semantic-auth-projection-v1","filename":"authenticated.pm-pack","generation_id":"<revision_id>","tree_seal":{"generation_id":"<revision_id>","root":{"offset":8,"length":1,"sha256":"<digest>"},"root_digest":"<digest>","height":0,"count":0,"pack_seal":{"byte_length":1,"file_sha256":"<digest>","record_count":1,"record_root":"<digest>"}}}
```

上記数値はshape例で有効test vectorではない。filenameは固定、自由path禁止。parserはlegacy exact keysetまたはlegacy+authenticated_indexだけを受け入れる。field有りでunknown version/missing child/invalid integerはlegacyへfallbackしない。元schema_version/protocolはv3のまま、extension自身をversion化し古いstrict parserは新manifestを拒否する。

- [ ] **1. RED:** new prepare manifestとproposal digest結合、legacy fixed fixture受理、unknown extension拒否、root入替後self-hashのみ更新しても既存proposalが拒否するtests。

```python
def test_new_prepare_binds_index_to_approved_manifest(workspace):
    repository, prepared = _prepared_frontier_run(workspace)
    paths = WorkspacePaths.from_root(workspace)
    manifest = json.loads(paths.semantic_v3_manifest_path(prepared.revision_id).read_text())
    assert manifest["authenticated_index"]["generation_id"] == prepared.revision_id
    assert manifest["authenticated_index"]["projection"] == "semantic-auth-projection-v1"
    envelope, _, _ = repository.get_semantic_v3_proposal_for_run(prepared.run_id)
    assert envelope.proposal.manifest_sha256 == manifest["manifest_sha256"]
```

既存test helper `_prepared_frontier_run`をtest moduleからimport可能か確認し、不可能なら同じ合成prepare fixtureをtests utilityへ抽出する。repository return shapeは実装前に既存signatureを確認し、このsnippetと異なるならテストのみ正しいshapeへ合わせる（public APIをtestに合わせて変更しない）。
- [ ] **2. Run:** `$PY -m pytest -q -p no:cacheprovider tests/test_semantic_prepare_v3.py tests/test_semantic_authenticated_catalog.py`. Expected RED missing extension。
- [ ] **3. prepare順序変更。** sink.seal→build index→full correspondence verification→resource cap再計測→current approval再確認→manifest hash→publish→reopen full correspondence→DB bind。index bytesも既存3GiB/時間/RSS resource capへ含める。既存reopenの小binding rollback契約を変えない。
- [ ] **4. fault tests RED→GREEN。** index build、fsync、manifest write、publication、post-publication full verify、DB bindの各失敗でactive不変・成功proposal無し。自分のtmpのみcleanup、published不正stagingを正常扱いしない。cross-device fallbackも新indexを含む。
- [ ] **5. Run:** Step2 commandと`tests/test_semantic_lifecycle_v3.py`。Expected GREEN。旧testの「任意catalog byte損傷ならpartial必ず失敗」はnew形式に流用せず、legacy/full対象かaccessed-record corruptionへ明確化し弱いassertへ削除しない。
- [ ] **6. Task4 diff/log記録。** 既存stagingを更新しない。

### Task 5: Lifecycle bounded dispatch（full/legacyは保持）

**Files:** Modify `semantic_lifecycle_v3.py`, `semantic_frontier_v3.py` annotations, `tests/test_semantic_lifecycle_v3.py`.
**Consumes:** manifest parser from4、typed reader from3。
**Produces:** new-format通常next/validateはwhole catalog/structural verifyを呼ばない；legacyは現行経路。

- [ ] **1. RED tests。** new-formatでprepare/approve完了後full catalog logical hash/structural verify呼び出しを観測し、next、issued replay、validate、receipt replayそれぞれ0をassert。DB snapshotとexchange bytesを保存してused-record corruption・stale proposalで成功receiptやcleanup無しをassert。R1の正常古いreceipt replayとaccounting fault injectionをそのまま実行する。

```python
def test_new_format_next_avoids_full_generation_verification(workspace, monkeypatch):
    repository, prepared = _prepared_frontier_run(workspace)
    approve_semantic_execution(ApproveSemanticExecutionRequest(
        workspace, prepared.revision_id, prepared.proposal_sha256))
    def forbidden(*args, **kwargs):
        raise AssertionError("steady-state whole-generation verification")
    monkeypatch.setattr(semantic_catalog_module, "_logical_root", forbidden)
    monkeypatch.setattr(lifecycle_module, "verify_pack", forbidden)
    issued = next_semantic_work_v3(NextSemanticWorkV3Request(workspace, prepared.run_id))
    assert issued.run_id == prepared.run_id
```

aliases `semantic_catalog_module/lifecycle_module`はtest file top-levelで実moduleをimport。これはauthority decision mockではなく禁止call検出。実bytes corruption testsを別に保つ。
- [ ] **2. Run:** `$PY -m pytest -q -p no:cacheprovider tests/test_semantic_lifecycle_v3.py`. Expected RED forbidden scan。
- [ ] **3. `_open_execution_catalog(paths, authority, manifest)` context managerをlifecycleに定義。** manifest digest/proposal/current policy一致を呼出し側で確認した後にnew extensionのrootを使用する。extensionなしだけSemanticCatalogReader。newはAuthenticatedCatalogReader。typed protocolはNode/Baseline/children/runtime-deferred/summary/audit iteratorのこの計画のmethodsのみ。新readerにconnectionプロパティやunchecked SQLを追加しない。

`_validate_reopened_authority`ではnew形式はsummaryとindex root metadataを認証し、catalog/structural whole verifyをしない。元manifest/approval/plan/root比較は残す。`_materialize_bounded_v3_input`は認証済みreferenceからstructural payloadと原本sourceを現行の同fd手順で再検証する。new形式のstructural length/magic等bounded sanity checksは残す。transition/auditも同dispatchを使う。
- [ ] **4. legacy/new split tests。** legacy fixtureは承認前からextension無しの旧build fixtureとして作る。承認済みnew manifestからfieldを削除してlegacyと偽装しない。new root入替、proof same-size変更、source drift、replay nonce衝突、停止/再開で失敗順序を確認。
- [ ] **5. Run:** Step2 command + `tests/test_semantic_work_lifecycle.py tests/test_cli.py tests/test_semantic_index.py tests/test_sqlite_repository.py`. Expected GREEN。既存canonical receipt/DB schema不変をdiffでも確認。
- [ ] **6. audit freeze boundary test。** expansionが最後に閉じてaudit sampleを作るfixtureを用意し結果sample/digestがlegacyに一致することと、deferred iteratorが母集団を読む事実を記録。一般next/validateの0-scan主張にこの全母集団列挙を混ぜて隠さない。
- [ ] **7. Task5 diff/log記録。** R1/legacy回帰failureはこのTaskのGREENを阻止する。

### Task 6: Resource E、失敗回復、独立レビューと引継ぎ

**Files:** Modify `tests/test_semantic_scale_v3.py`; test additions in auth/prepare/lifecycle; create review report。
**Consumes:** Tasks1–5 implemented candidate。
**Produces:** measured gate verdictと原Task11/12へのhandoff。失敗時に完了扱いしない。

- [ ] **1. E拡張を先にRED。** 新形式1k/10kのprocess-cold→issued replay→receipt replay→次の新unitを区別する。既存single-root fixtureで次unitが無い場合はtwo independent project lanesの同node数fixtureを用意し、issued.unit_idが異なることをassertする。proof observerを集計する。

```python
assert first_issued.unit_id != second_issued.unit_id
assert sample["catalog_authority_rows"] == 0
assert sample["structural_verification_calls"] == 0
assert sample["max_page_rows"] <= 64
assert sample["max_auth_operation_bytes"] <= 16 * 1_048_576
```

sample fieldsはtestの計測collectorでTask2 observerから生成。monkeypatchで数値を偽装せず実reader callsを記録する。statusは20回計測してp95を記録。next/validate elapsedは単発をp95と呼ばない。
- [ ] **2. Run:** `PM_SEMANTIC_EARLY_SCALE=1 $PY -m pytest -q -s -p no:cacheprovider tests/test_semantic_scale_v3.py`. Expected、未統合部分があればRED。既にGREENなら新観測が既存実装を確認していると記録しREDを捏造しない。必要なintegration correctionだけ行って再実行。
- [ ] **3. 追加acceptance。** subprocess restart、open/read前後のrename・in-place tamper、publication失敗後の再起動、未読subtree損傷時partial可/full拒否、fullverify失敗時小binding/DB不変。巨大key/value/proofではcontrolled errorかつ上限以上readなし。明示I/OとSQLite pager未観測・RSS high-water・OS cache条件を併記。
- [ ] **4. full suite。** `$PY -m pytest -q -p no:cacheprovider`。Expected all regression GREEN（opt-in Eのskipは別実行の結果と並べる）。full verifierでindexのtemp/spoolがmemory全載せされないobserverと1k/10k peak RSSを記録。prepareの追加bytes/timeも記録。
- [ ] **5. E gate判定。** ordinary pathsのproof readsがN倍の全scanに戻らず、whole verification0であることを確認。audit population scanや既存runtime receipt-prefix scanは別欄で報告し、元Eの受入条件に抵触するならNEEDS_WORK。wall time <5sのみでPASSにしない。1M/full-home/rebootの未実行を明記。
- [ ] **6. fresh-context独立review。** baseline以降のこの計画の差分と、既存7-file WIPを含む現在candidateのauthority関連箇所を渡す。exact diff/file hashes、承認spec、計画、全RED/GREEN logsを固定。reviewerはread-only、synthetic検査可、Git/実資料不可。root binding/range completeness/legacy/R1/lifecycle failure ordering/Eを確認させる。
- [ ] **7. one focused fix pass。** Critical/Importantは実際に失敗するtest→修正→suite/E再実行。architecture変更や新gateが必要なら停止して承認を求める。未解決findingはNEEDS_WORK、Minorはreportに記録。独立reviewの未検証範囲を自動PASSにしない。
- [ ] **8. preservation。** baselineファイルhash/HEAD/stash、own new files、`git diff --check`、production API/schema diffを確認。review reportとledgerに結果を残し、証拠workspaceを削除しない。必要条件が全部GREENでも原Task11/12は別作業で未完了と明記する。

## Self-review / spec traceability

| spec | owning tasks |
|---|---|
| §1–2 scope/partial vs full | Global Constraints, Tasks3/5/6 |
| §3–4 bounded tree/types/encoding | Tasks1/2、固定wire section |
| §5 publication/approval | Task4 |
| §6 nonexistence/range completeness | Task2/3 |
| §7 bounded pack/same fd | Tasks3/5 |
| §8 runtime/legacy compatibility | Tasks4/5 |
| §9 full correspondence/API for Task12 | Task3/6、Task12自体は除外 |
| §10 corruption/resource acceptance | Tasks1–6 |
| §11 WIP/scope/no Git | Global Constraints、Task6 |

Interface namesはこの計画内で定義しTask間で共通化した。snippet中のellipsisはpublic signatureだけであり製品実装のstubではない。full/legacy authorityを削除するstepは存在しない。audit全母集団選択はこの先行計画では解決せず、E最終承認の障害になり得ることを明記した。

## Execution handoff

推奨: **gooseが同じセッションで順次実装し、最後に別の独立reviewerが検証する方式**。Task間のwire/projection/authority結合が強いため、同じsourceをparallel implementersに書かせない。ユーザーの計画レビュー・実行方式確認まで実装しない。別方式を希望する場合でも同じファイルの並行書き込みは禁止する。
