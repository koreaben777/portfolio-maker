"""Build/full-only SQL exporter and authenticated runtime catalog projections."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from itertools import zip_longest
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from portfolio_maker.domain.semantic_frontier import BaselineCoverage, PackRecordRef, SemanticDeferredAuditRow
from portfolio_maker.infrastructure.semantic_auth_index import (
    AuthEntry, AuthIndexError, AuthIndexReader, AuthTreeSeal, build_auth_index,
    MAX_VALUE_BYTES, _integer, _key, _fields, _digest, _reference,
)
from portfolio_maker.infrastructure.semantic_catalog import (
    BaselineRecord, CatalogSeal, DeferredSummary, NodeRecord, RepresentativeCandidate,
    RuntimeDeferredProjectionObserver, SemanticCatalogReader, _AUTHORITATIVE_TABLES,
    _authoritative_rows, _ADAPTIVE_DEFERRED_REASONS, _UNSAFE_RUNTIME_SOURCE_ID,
)
from portfolio_maker.infrastructure.semantic_pack import PMPackReader, PackSeal, canonical_json_bytes, verify_pack, _decode_canonical_payload, SemanticPackError, _open_existing_file, _require_unchanged

AUTH_FILENAME = "authenticated.pm-pack"
_SELECTOR = "adaptive-frontier-v1"


def _value(record):
    # Normalize tuple/enum values to the exact JSON domain compared by full verify.
    return json.loads(canonical_json_bytes(asdict(record)))


def _structural_record(reader, reference, *, kind, node_id, pack_seal):
    if reference.offset + reference.length + 40 > pack_seal.byte_length:
        raise AuthIndexError("structural reference outside approved pack")
    payload = reader.read(reference, max_record_bytes=MAX_VALUE_BYTES)
    if type(payload) is not dict or payload.get("kind") != kind:
        raise AuthIndexError("structural record kind mismatch")
    field = "child_node_id" if kind == "structural-edge" else "node_id"
    if payload.get(field) != node_id:
        raise AuthIndexError("structural record node binding mismatch")
    return payload


def _export(reader, pack_reader, pack_seal):
    for table, primary_key in _AUTHORITATIVE_TABLES:
        for raw in _authoritative_rows(reader.connection, table, ",".join(primary_key)):
            row = dict(raw)
            yield AuthEntry(("table", table, *(str(row[k]) for k in primary_key)), row)
            if table == "payload_refs":
                reference = PackRecordRef(row["byte_offset"], row["byte_length"], row["payload_sha256"])
                _structural_record(pack_reader, reference, kind=row["pack_kind"], node_id=row["node_id"], pack_seal=pack_seal)
    cursor = None
    while nodes := reader.nodes_page(cursor, 64):
        for node in nodes:
            yield AuthEntry(("node", node.node_id), _value(node))
            if node.structural_ref is not None:
                payload = _structural_record(pack_reader, node.structural_ref, kind="structural-node", node_id=node.node_id, pack_seal=pack_seal)
                for field in ("parent_node_id", "source_id", "node_kind", "relative_hierarchy", "display_name"):
                    if payload.get(field) != getattr(node, field):
                        raise AuthIndexError("structural node projection mismatch")
            baseline = reader.baseline(node.node_id)
            if baseline is not None:
                yield AuthEntry(("baseline", node.node_id), _value(baseline))
                if baseline.payload_ref is not None:
                    payload = _structural_record(pack_reader, baseline.payload_ref, kind="local-baseline", node_id=node.node_id, pack_seal=pack_seal)
                    for field, value in _value(baseline).items():
                        if field != "payload_ref" and payload.get(field) != value:
                            raise AuthIndexError("structural baseline projection mismatch")
            for namespace, method in (("children", reader.children_page), ("representative", reader.representative_page)):
                after = None
                while children := method(node.node_id, after, 64):
                    for child in children:
                        yield AuthEntry((namespace, node.node_id, child.node_id), _value(child))
                    after = children[-1].node_id
        cursor = nodes[-1].node_id
    for selector_row in reader.connection.execute("SELECT selector_version FROM frontier_selector_authorities ORDER BY selector_version"):
        selector = selector_row[0]
        yield AuthEntry(("summary", selector), _value(reader.deferred_summary(selector_version=selector)))
        if selector != _SELECTOR:
            raise AuthIndexError("unsupported authenticated selector")
        reader.runtime_deferred_projection(selector, ())
        cursor = None
        while page := reader.frontier_deferred_page(selector, cursor, 64):
            projected = reader.runtime_deferred_projection(selector, tuple(r.node_id for r in page))
            for row in projected:
                yield AuthEntry(("runtime-deferred", selector, row.node_id), _value(row))
                order = (f"{_integer(row.relevance_rank):016x}", row.coverage_stratum, row.source_id, f"{_integer(row.cursor_order):016x}", row.node_id)
                yield AuthEntry(("audit-deferred", selector, *order), _value(row))
            cursor = reader.frontier_deferred_cursor(page[-1])


@contextmanager
def _sorted_entries(revision, catalog_seal, pack_seal):
    verify_pack(revision / "packs" / "structural.pm-pack", pack_seal)
    with SemanticCatalogReader(revision / "catalog.sqlite", catalog_seal) as reader, tempfile.TemporaryDirectory(prefix="semantic-auth-sort-") as directory:
        connection = sqlite3.connect(str(Path(directory) / "sort.sqlite"))
        try:
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute("PRAGMA cache_size=-2048")
            connection.execute("CREATE TABLE entries (ordering TEXT PRIMARY KEY COLLATE BINARY, key_json TEXT, value_json TEXT) WITHOUT ROWID")
            pack_reader = PMPackReader(revision / "packs" / "structural.pm-pack")
            for entry in _export(reader, pack_reader, pack_seal):
                ordering = "".join(part.hex() + "!" for part in _key(entry.key))
                raw = canonical_json_bytes(entry.value)
                if len(raw) > MAX_VALUE_BYTES:
                    raise AuthIndexError("catalog projection exceeds value limit")
                connection.execute("INSERT INTO entries VALUES (?, ?, ?)", (ordering, canonical_json_bytes(entry.key).decode(), raw.decode()))
            connection.commit()
            yield (AuthEntry(tuple(json.loads(key)), json.loads(value)) for key, value in connection.execute("SELECT key_json, value_json FROM entries ORDER BY ordering COLLATE BINARY"))
        finally:
            connection.close()


def build_authenticated_catalog(revision: Path, catalog_seal: CatalogSeal, pack_seal: PackSeal, generation_id: str) -> AuthTreeSeal:
    revision = Path(revision)
    with _sorted_entries(revision, catalog_seal, pack_seal) as entries:
        return build_auth_index(revision / AUTH_FILENAME, entries, generation_id=generation_id)


def _all_entries(reader):
    cursor = None
    while True:
        page = reader.page((), cursor, 64)
        yield from page.entries
        if page.complete:
            return
        cursor = page.next_key


def verify_authenticated_catalog_full(revision: Path, catalog_seal: CatalogSeal, pack_seal: PackSeal, seal: AuthTreeSeal) -> None:
    revision = Path(revision)
    # Hold identities across all stages, not merely within each individual read.
    with ExitStack() as stack:
        snapshots = []
        for path in (revision / AUTH_FILENAME, revision / "catalog.sqlite", revision / "packs" / "structural.pm-pack"):
            fd = _open_existing_file(path, os.O_RDONLY)
            stack.callback(os.close, fd)
            snapshots.append((path, fd, os.fstat(fd)))
        verify_pack(revision / AUTH_FILENAME, seal.pack_seal)
        missing = object()
        with _sorted_entries(revision, catalog_seal, pack_seal) as expected, AuthIndexReader(revision / AUTH_FILENAME, seal) as reader:
            count = 0
            for left, right in zip_longest(expected, _all_entries(reader), fillvalue=missing):
                if left is missing or right is missing or left != right:
                    raise AuthIndexError("authenticated catalog projection mismatch")
                count += 1
            if count != seal.count:
                raise AuthIndexError("authenticated catalog entry count mismatch")
        for path, fd, before in snapshots:
            try:
                _require_unchanged(path, fd, before)
            except SemanticPackError as error:
                raise AuthIndexError("authenticated correspondence snapshot changed") from error


def _typed_fields(value, record_type):
    _fields(value, " ".join(record_type.__dataclass_fields__))
    return dict(value)


def _text(value, *, empty=False):
    if type(value) is not str or (not empty and not value):
        raise AuthIndexError("invalid authenticated text")
    _key((value,))
    return value


def _texts(value):
    if type(value) is not list:
        raise AuthIndexError("invalid authenticated text list")
    return tuple(_text(item) for item in value)


def _payload_reference(value):
    if value is None:
        return None
    _reference(value, before=(1 << 64) - 1, maximum=MAX_VALUE_BYTES)
    return PackRecordRef(**value)


def _coverage(value):
    if type(value) is not str:
        raise AuthIndexError("invalid authenticated coverage")
    try:
        return BaselineCoverage(value)
    except ValueError as error:
        raise AuthIndexError("invalid authenticated coverage") from error


def _content_digests(value):
    for field in ("sample_sha256", "full_content_sha256"):
        if value[field] is not None:
            _digest(value[field])


def _node(value):
    value = _typed_fields(value, NodeRecord)
    for field in ("node_id", "source_id", "node_kind"):
        _text(value[field])
    _text(value["relative_hierarchy"], empty=True)
    for field in ("parent_node_id", "display_name"):
        if value[field] is not None:
            _text(value[field], empty=field == "display_name")
    value["structural_ref"] = _payload_reference(value["structural_ref"])
    return NodeRecord(**value)


def _baseline(value):
    value = _typed_fields(value, BaselineRecord)
    _text(value["node_id"])
    _text(value["analyzer_version"])
    _integer(value["inspected_bytes"])
    value["coverage"] = _coverage(value["coverage"])
    value["roles"] = _texts(value["roles"])
    value["topics"] = _texts(value["topics"])
    ranges = value["sampled_ranges"]
    if ranges is not None:
        if type(ranges) is not list:
            raise AuthIndexError("invalid authenticated sampled ranges")
        for pair in ranges:
            if type(pair) is not list or len(pair) != 2 or _integer(pair[0]) > _integer(pair[1]):
                raise AuthIndexError("invalid authenticated sampled range")
        value["sampled_ranges"] = tuple(tuple(pair) for pair in ranges)
    _content_digests(value)
    value["payload_ref"] = _payload_reference(value["payload_ref"])
    return BaselineRecord(**value)


def _representative(value):
    value = _typed_fields(value, RepresentativeCandidate)
    _text(value["node_id"])
    value["coverage"] = _coverage(value["coverage"])
    value["roles"] = _texts(value["roles"])
    _content_digests(value)
    for field in ("independent_child", "evidence_linked", "uncertainty"):
        if type(value[field]) is not bool:
            raise AuthIndexError("invalid authenticated representative flag")
    return RepresentativeCandidate(**value)


def _summary(value):
    value = _typed_fields(value, DeferredSummary)
    _integer(value["count"])
    _digest(value["root"])
    _digest(value["cursor_root"])
    return DeferredSummary(**value)


def _deferred(value):
    value = _typed_fields(value, SemanticDeferredAuditRow)
    for field in ("node_id", "reason", "source_id", "selector_version", "cursor_json"):
        _text(value[field])
    for field in ("node_digest", "selector_feature_digest"):
        _digest(value[field])
    _integer(value["cursor_order"])
    _integer(value["relevance_rank"], 31)
    _coverage(value["coverage_stratum"])
    if value["selector_version"] != _SELECTOR or value["reason"] not in _ADAPTIVE_DEFERRED_REASONS or _UNSAFE_RUNTIME_SOURCE_ID.search(value["source_id"]):
        raise AuthIndexError("invalid authenticated deferred authority")
    try:
        cursor = _decode_canonical_payload(value["cursor_json"].encode("utf-8"))
    except (SemanticPackError, RecursionError) as error:
        raise AuthIndexError("invalid authenticated deferred cursor") from error
    _fields(cursor, "depth_band fair_round lane node_id order role_band selector_feature_digest size_band")
    _integer(cursor["fair_round"])
    _integer(cursor["order"])
    for field in ("depth_band", "lane", "role_band", "size_band"):
        _text(cursor[field])
    if any(cursor[field] != value[target] for field, target in (("node_id", "node_id"), ("order", "cursor_order"), ("selector_feature_digest", "selector_feature_digest"))):
        raise AuthIndexError("authenticated deferred cursor binding mismatch")
    return SemanticDeferredAuditRow(**value)


class AuthenticatedCatalogReader:
    def __init__(self, revision: Path, seal: AuthTreeSeal):
        self._reader = AuthIndexReader(Path(revision) / AUTH_FILENAME, seal)

    def __enter__(self):
        self._reader.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._reader.close()

    def close(self):
        self._reader.close()

    def _get(self, key, decode):
        entry = self._reader.get(key)
        if entry is None:
            return None
        result = decode(entry.value)
        if key[0] != "summary" and result.node_id != key[-1]:
            raise AuthIndexError("authenticated projection node binding mismatch")
        return result

    def node(self, node_id: str):
        return self._get(("node", node_id), _node)

    def baseline(self, node_id: str):
        return self._get(("baseline", node_id), _baseline)

    def _page(self, namespace, parent, after, limit, decode):
        prefix = (namespace, parent)
        cursor = None if after is None else (*prefix, after)
        rows = []
        for entry in self._reader.page(prefix, cursor, limit).entries:
            row = decode(entry.value)
            if entry.key != (*prefix, row.node_id):
                raise AuthIndexError("authenticated projection node binding mismatch")
            if namespace == "children" and row.parent_node_id != parent:
                raise AuthIndexError("authenticated child parent binding mismatch")
            rows.append(row)
        return rows

    def children_page(self, parent_node_id, after_node_id, limit):
        return self._page("children", parent_node_id, after_node_id, limit, _node)

    def representative_page(self, parent_node_id, after_node_id, limit):
        return self._page("representative", parent_node_id, after_node_id, limit, _representative)

    def deferred_summary(self, *, selector_version):
        value = self._get(("summary", selector_version), _summary)
        if value is None:
            raise AuthIndexError("authenticated selector summary missing")
        return value

    def runtime_deferred_projection(self, selector_version, node_ids, *, observer=None):
        if selector_version != _SELECTOR or type(node_ids) is not tuple or len(node_ids) > 64 or any(type(n) is not str or not n for n in node_ids) or len(set(node_ids)) != len(node_ids):
            raise AuthIndexError("invalid authenticated deferred request")
        if observer is not None and not isinstance(observer, RuntimeDeferredProjectionObserver):
            raise AuthIndexError("invalid deferred observer")
        self.deferred_summary(selector_version=selector_version)
        rows = []
        for node_id in node_ids:
            row = self._get(("runtime-deferred", selector_version, node_id), _deferred)
            if row is None or row.node_id != node_id or row.selector_version != selector_version:
                raise AuthIndexError("authenticated deferred row missing or mismatched")
            rows.append(row)
        if observer is not None:
            observer.max_python_rows = max(observer.max_python_rows, len(rows))
        return tuple(rows)

    def iter_frontier_deferred_for_audit(self, *, selector_version, fetch_size):
        self.deferred_summary(selector_version=selector_version)
        cursor = None
        while True:
            page = self._reader.page(("audit-deferred", selector_version), cursor, fetch_size)
            for entry in page.entries:
                row = _deferred(entry.value)
                key = ("audit-deferred", selector_version, f"{row.relevance_rank:016x}", row.coverage_stratum, row.source_id, f"{row.cursor_order:016x}", row.node_id)
                if entry.key != key or row.selector_version != selector_version:
                    raise AuthIndexError("authenticated audit order binding mismatch")
                yield row
            if page.complete:
                return
            cursor = page.next_key
