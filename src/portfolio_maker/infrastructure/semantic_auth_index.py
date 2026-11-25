"""Versioned, disk-built authenticated sorted index for immutable generations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import struct
import tempfile
from typing import Iterable

from portfolio_maker.domain.semantic_frontier import PackRecordRef
from portfolio_maker.infrastructure.semantic_pack import (
    PMPackWriter, PackSeal, SemanticPackError, canonical_json_bytes,
    _decode_canonical_payload,
)

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
MAX_TREE_READ_BYTES = 16 * 1_048_576
_U64 = (1 << 64) - 1
_SPOOL_HEADER = struct.Struct(">QQ")  # subtree count, canonical descriptor length


class AuthIndexError(ValueError):
    pass


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


def _integer(value, maximum=_U64):
    if type(value) is not int or not 0 <= value <= maximum:
        raise AuthIndexError("invalid authenticated integer")
    return value


def _key(value):
    if not isinstance(value, (list, tuple)) or len(value) > 8:
        raise AuthIndexError("invalid authenticated key")
    try:
        if any(type(part) is not str for part in value):
            raise AuthIndexError("invalid authenticated key component")
        encoded = tuple(part.encode("utf-8") for part in value)
    except UnicodeError as error:
        raise AuthIndexError("invalid authenticated key encoding") from error
    if sum(map(len, encoded)) > MAX_KEY_BYTES:
        raise AuthIndexError("authenticated key exceeds byte limit")
    return encoded


def _fields(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise AuthIndexError("invalid authenticated record fields")


def _digest(value):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise AuthIndexError("invalid authenticated digest")


def _canonical(value):
    try:
        return canonical_json_bytes(value)
    except (SemanticPackError, UnicodeError, RecursionError) as error:
        raise AuthIndexError("invalid authenticated canonical value") from error


def _semantic_digest(raw):
    return hashlib.sha256(HASH_DOMAIN + raw).hexdigest()


def _reference(value, *, before, maximum):
    _fields(value, "offset length sha256")
    offset = _integer(value["offset"])
    length = _integer(value["length"], maximum)
    _digest(value["sha256"])
    if offset < 8 or offset + length + 40 > before:
        raise AuthIndexError("invalid authenticated reference bounds")
    return offset, offset + length + 40


def _descriptor(value, *, before):
    _fields(value, "ref digest min max count height")
    interval = _reference(value["ref"], before=before, maximum=MAX_NODE_BYTES)
    _digest(value["digest"])
    _integer(value["height"], MAX_HEIGHT)
    if not _integer(value["count"]):
        raise AuthIndexError("empty authenticated child")
    if _key(value["min"]) > _key(value["max"]):
        raise AuthIndexError("invalid authenticated child range")
    return interval


def _decode_node(raw: bytes, *, generation_id: str, offset: int):
    if len(raw) > MAX_NODE_BYTES:
        raise AuthIndexError("authenticated node exceeds byte limit")
    try:
        node = _decode_canonical_payload(raw)
    except (SemanticPackError, RecursionError) as error:
        raise AuthIndexError("invalid authenticated node encoding") from error
    if type(node) is not dict or node.get("kind") not in ("leaf", "branch"):
        raise AuthIndexError("invalid authenticated node kind")
    kind = node["kind"]
    _fields(node, "format generation_id kind min max count height " + ("rows" if kind == "leaf" else "children"))
    if node["format"] != AUTH_FORMAT or node["generation_id"] != generation_id:
        raise AuthIndexError("authenticated node generation/format mismatch")
    height = _integer(node["height"], MAX_HEIGHT)
    count = _integer(node["count"])
    items = node["rows" if kind == "leaf" else "children"]
    if type(items) is not list or len(items) > 16:
        raise AuthIndexError("authenticated node fanout exceeded")
    intervals = []
    if kind == "leaf":
        if height != 0 or count != len(items):
            raise AuthIndexError("invalid authenticated leaf metadata")
        previous = None
        for row in items:
            if type(row) is not dict:
                raise AuthIndexError("invalid authenticated row")
            _fields(row, "key value_ref" if "value_ref" in row else "key value")
            key = _key(row["key"])
            if previous is not None and previous >= key:
                raise AuthIndexError("authenticated leaf order violation")
            previous = key
            if "value_ref" in row:
                value = row["value_ref"]
                _fields(value, "ref digest")
                intervals.append(_reference(value["ref"], before=offset, maximum=MAX_VALUE_BYTES))
                _digest(value["digest"])
        low, high = (items[0]["key"], items[-1]["key"]) if items else (None, None)
    else:
        if not items or height == 0:
            raise AuthIndexError("invalid authenticated branch")
        previous = None
        total = 0
        for child in items:
            intervals.append(_descriptor(child, before=offset))
            if child["height"] != height - 1:
                raise AuthIndexError("authenticated height mismatch")
            if previous is not None and previous >= _key(child["min"]):
                raise AuthIndexError("authenticated child ranges overlap")
            previous = _key(child["max"])
            total += child["count"]
        if total != count:
            raise AuthIndexError("authenticated count mismatch")
        low, high = items[0]["min"], items[-1]["max"]
    if node["min"] != low or node["max"] != high:
        raise AuthIndexError("authenticated node range mismatch")
    intervals.sort()
    if any(a[1] > b[0] for a, b in zip(intervals, intervals[1:])):
        raise AuthIndexError("authenticated record ranges overlap")
    return node


def _node(generation_id, *, rows=None, children=None):
    leaf = rows is not None
    items = rows if leaf else children
    return {
        "format": AUTH_FORMAT, "generation_id": generation_id,
        "kind": "leaf" if leaf else "branch",
        "height": 0 if leaf else items[0]["height"] + 1,
        "count": len(items) if leaf else sum(c["count"] for c in items),
        "min": (items[0]["key" if leaf else "min"] if items else None),
        "max": (items[-1]["key" if leaf else "max"] if items else None),
        "rows" if leaf else "children": items,
    }


def _append_node(writer, payload, generation_id):
    raw = _canonical(payload)
    _decode_node(raw, generation_id=generation_id, offset=writer.byte_length)
    ref = writer.append(payload)
    return {"ref": asdict(ref), "digest": _semantic_digest(raw),
            **{name: payload[name] for name in ("min", "max", "count", "height")}}


def _write_descriptor(stream, descriptor):
    raw = _canonical(descriptor)
    if len(raw) > MAX_NODE_BYTES:
        raise AuthIndexError("descriptor spool limit exceeded")
    stream.write(_SPOOL_HEADER.pack(descriptor["count"], len(raw)))
    stream.write(raw)


def _read_descriptors(stream):
    stream.seek(0)
    while header := stream.read(_SPOOL_HEADER.size):
        if len(header) != _SPOOL_HEADER.size:
            raise AuthIndexError("truncated descriptor spool")
        count, length = _SPOOL_HEADER.unpack(header)
        if length > MAX_NODE_BYTES:
            raise AuthIndexError("descriptor spool limit exceeded")
        raw = stream.read(length)
        if len(raw) != length:
            raise AuthIndexError("truncated descriptor spool")
        descriptor = _decode_canonical_payload(raw)
        if descriptor["count"] != count:
            raise AuthIndexError("descriptor spool count mismatch")
        yield descriptor


def build_auth_index(path: Path, entries: Iterable[AuthEntry], *, generation_id: str) -> AuthTreeSeal:
    if type(generation_id) is not str or not generation_id or len(generation_id.encode("utf-8")) > 128:
        raise AuthIndexError("invalid authenticated generation")
    # TemporaryFile unlinks only its own spool; PMPackWriter owns its temporary pack.
    with PMPackWriter(path) as writer, tempfile.TemporaryFile() as first_level:
        rows = []
        previous = None
        descriptor_count = 0
        for entry in entries:
            if not isinstance(entry, AuthEntry) or type(entry.key) is not tuple:
                raise AuthIndexError("invalid authenticated entry")
            key = _key(entry.key)
            if previous is not None and previous >= key:
                raise AuthIndexError("authenticated entries must be strictly increasing")
            previous = key
            value_payload = {"format": AUTH_FORMAT, "generation_id": generation_id, "kind": "value", "value": entry.value}
            value_raw = _canonical(value_payload)
            if len(value_raw) > MAX_VALUE_BYTES:
                raise AuthIndexError("authenticated value exceeds byte limit")
            row = {"key": list(entry.key), "value": entry.value}
            if len(_canonical(_node(generation_id, rows=[row]))) > MAX_NODE_BYTES:
                ref = writer.append(value_payload)
                row = {"key": list(entry.key), "value_ref": {"ref": asdict(ref), "digest": _semantic_digest(value_raw)}}
            if rows and (len(rows) == MAX_LEAF_ROWS or len(_canonical(_node(generation_id, rows=rows + [row]))) > MAX_NODE_BYTES):
                _write_descriptor(first_level, _append_node(writer, _node(generation_id, rows=rows), generation_id))
                descriptor_count += 1
                rows = []
            rows.append(row)
        if rows or descriptor_count == 0:
            _write_descriptor(first_level, _append_node(writer, _node(generation_id, rows=rows), generation_id))
            descriptor_count += 1
        level = first_level
        try:
            while descriptor_count > 1:
                next_level = tempfile.TemporaryFile()
                try:
                    children = []
                    next_count = 0
                    for child in _read_descriptors(level):
                        trial = _node(generation_id, children=children + [child])
                        if children and (len(children) == MAX_FANOUT or len(_canonical(trial)) > MAX_NODE_BYTES):
                            _write_descriptor(next_level, _append_node(writer, _node(generation_id, children=children), generation_id))
                            next_count += 1
                            children = []
                        children.append(child)
                    if children:
                        _write_descriptor(next_level, _append_node(writer, _node(generation_id, children=children), generation_id))
                        next_count += 1
                    if next_count >= descriptor_count:
                        raise AuthIndexError("authenticated keys prevent bounded tree fanout")
                except BaseException:
                    next_level.close()
                    raise
                if level is not first_level:
                    level.close()
                level = next_level
                descriptor_count = next_count
            root = next(_read_descriptors(level))
            seal = writer.seal()
            return AuthTreeSeal(generation_id, PackRecordRef(**root["ref"]), root["digest"], root["height"], root["count"], seal)
        finally:
            if level is not first_level:
                level.close()


class AuthIndexReader:
    """No persistent verification cache: every operation starts at the trusted root."""

    def __init__(self, path: Path, seal: AuthTreeSeal, *, observer: AuthReadObserver | None = None):
        self.path = Path(path)
        self.seal = seal
        self.observer = observer if observer is not None else AuthReadObserver()
        self._closed = False
        if not isinstance(seal, AuthTreeSeal) or not isinstance(seal.pack_seal, PackSeal):
            raise AuthIndexError("invalid authenticated tree seal")
        _integer(seal.height, MAX_HEIGHT)
        _integer(seal.count)
        _integer(seal.pack_seal.byte_length)
        _digest(seal.root_digest)
        _reference(asdict(seal.root), before=seal.pack_seal.byte_length, maximum=MAX_NODE_BYTES)
        if type(seal.generation_id) is not str or not seal.generation_id:
            raise AuthIndexError("invalid authenticated generation")

    def __enter__(self):
        if self._closed:
            raise AuthIndexError("authenticated reader is closed")
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        self._closed = True

    def _record(self, fd, descriptor, *, value=False):
        import os
        maximum = MAX_VALUE_BYTES if value else MAX_NODE_BYTES
        ref = descriptor["ref"]
        _reference(ref, before=self.seal.pack_seal.byte_length, maximum=maximum)
        amount = ref["length"] + 40
        if self._operation_bytes + amount > MAX_TREE_READ_BYTES:
            raise AuthIndexError("authenticated read budget exceeded")
        self._operation_bytes += amount
        self.observer.explicit_read_bytes += amount
        frame = os.pread(fd, amount, ref["offset"])
        if len(frame) != amount or struct.unpack(">Q", frame[:8])[0] != ref["length"]:
            raise AuthIndexError("invalid authenticated frame length")
        raw = frame[8:-32]
        if hashlib.sha256(raw).hexdigest() != ref["sha256"] or frame[-32:].hex() != ref["sha256"] or _semantic_digest(raw) != descriptor["digest"]:
            raise AuthIndexError("authenticated record digest mismatch")
        if value:
            self.observer.value_reads += 1
            try:
                payload = _decode_canonical_payload(raw)
            except (SemanticPackError, RecursionError) as error:
                raise AuthIndexError("invalid authenticated value encoding") from error
            _fields(payload, "format generation_id kind value")
            if payload["format"] != AUTH_FORMAT or payload["generation_id"] != self.seal.generation_id or payload["kind"] != "value":
                raise AuthIndexError("authenticated value binding mismatch")
            return payload["value"]
        self.observer.node_reads += 1
        node = _decode_node(raw, generation_id=self.seal.generation_id, offset=ref["offset"])
        for field in ("min", "max", "height", "count"):
            if field in descriptor and node[field] != descriptor[field]:
                raise AuthIndexError("authenticated child descriptor mismatch")
        return node

    def _walk(self, fd, descriptor, intersects, accepts):
        node = self._record(fd, descriptor)
        if node["kind"] == "branch":
            for child in node["children"]:
                if intersects(_key(child["min"]), _key(child["max"])):
                    yield from self._walk(fd, child, intersects, accepts)
        else:
            for row in node["rows"]:
                if accepts(_key(row["key"])):
                    value = self._record(fd, row["value_ref"], value=True) if "value_ref" in row else row["value"]
                    yield AuthEntry(tuple(row["key"]), value)

    def _query(self, intersects, accepts, limit):
        import os
        from portfolio_maker.infrastructure.semantic_pack import _open_existing_file, PACK_MAGIC
        if self._closed:
            raise AuthIndexError("authenticated reader is closed")
        try:
            fd = _open_existing_file(self.path, os.O_RDONLY)
            try:
                before = os.fstat(fd)
                identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                if before.st_size != self.seal.pack_seal.byte_length:
                    raise AuthIndexError("authenticated pack length mismatch")
                self._operation_bytes = 8
                self.observer.explicit_read_bytes += 8
                if os.pread(fd, 8, 0) != PACK_MAGIC:
                    raise AuthIndexError("authenticated pack magic mismatch")
                root = {"ref": asdict(self.seal.root), "digest": self.seal.root_digest,
                        "height": self.seal.height, "count": self.seal.count}
                result = []
                for entry in self._walk(fd, root, intersects, accepts):
                    result.append(entry)
                    self.observer.max_retained_rows = max(self.observer.max_retained_rows, len(result))
                    if len(result) == limit:
                        break
                # Check the descriptor AND the current no-symlink path after reading.
                check_fd = _open_existing_file(self.path, os.O_RDONLY)
                try:
                    if identity(before) != identity(os.fstat(fd)) or identity(before) != identity(os.fstat(check_fd)):
                        raise AuthIndexError("authenticated pack changed during read")
                finally:
                    os.close(check_fd)
                return result
            finally:
                os.close(fd)
        except (OSError, SemanticPackError) as error:
            raise AuthIndexError("authenticated pack read failed") from error

    def get(self, key: tuple[str, ...]) -> AuthEntry | None:
        target = _key(key)
        rows = self._query(lambda low, high: low <= target <= high, lambda k: k == target, 1)
        return rows[0] if rows else None

    def page(self, prefix: tuple[str, ...], after: tuple[str, ...] | None, limit: int) -> AuthPage:
        p = _key(prefix)
        if not _integer(limit, MAX_PAGE_ROWS):
            raise AuthIndexError("authenticated page limit must be positive")
        cursor = _key(after) if after is not None else None
        if cursor is not None and cursor[:len(p)] != p:
            raise AuthIndexError("authenticated cursor outside prefix")
        def intersects(low, high):
            return high >= p and low[:len(p)] <= p and (cursor is None or high > cursor)
        def accepts(key):
            return key[:len(p)] == p and (cursor is None or key > cursor)
        rows = self._query(intersects, accepts, limit + 1)
        if len(rows) > limit:
            return AuthPage(tuple(rows[:limit]), rows[limit - 1].key, False)
        return AuthPage(tuple(rows), None, True)
