"""Wire tests use independent pack decoding, not a builder/reader round trip."""
from dataclasses import asdict
import hashlib
import json

import pytest

from portfolio_maker.domain.semantic_frontier import PackRecordRef
from portfolio_maker.infrastructure.semantic_pack import PMPackReader, verify_pack
from portfolio_maker.infrastructure import semantic_auth_index as auth

GENERATION = "0" * 32


def entries(count):
    return (auth.AuthEntry(("node", f"{i:06d}"), {"i": i}) for i in range(count))


@pytest.mark.parametrize("count,height", [(0, 0), (1, 0), (17, 1), (257, 2)])
def test_builder_preserves_all_rows_and_authenticated_metadata(tmp_path, count, height):
    path = tmp_path / "auth.pm-pack"
    seal = auth.build_auth_index(path, entries(count), generation_id=GENERATION)
    assert seal.count == count and seal.height == height
    assert verify_pack(path, seal.pack_seal) == seal.pack_seal
    rows = []

    def walk(ref, digest, expected_height):
        value = PMPackReader(path).read(ref)
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(b"portfolio-maker-semantic-auth-v1\0" + raw).hexdigest() == digest
        assert value["format"] == "semantic-auth-index-v1"
        assert value["generation_id"] == GENERATION
        assert value["height"] == expected_height
        assert ref.length <= 65536
        if expected_height == 0:
            assert value["kind"] == "leaf"
            assert value["count"] == len(value["rows"]) <= 16
            rows.extend(value["rows"])
        else:
            assert value["kind"] == "branch"
            assert 1 <= len(value["children"]) <= 16
            assert value["count"] == sum(c["count"] for c in value["children"])
            for child in value["children"]:
                child_ref = PackRecordRef(**child["ref"])
                assert child_ref.offset + child_ref.length + 40 <= ref.offset
                walk(child_ref, child["digest"], expected_height - 1)
    walk(seal.root, seal.root_digest, height)
    assert [r["value"]["i"] for r in rows] == list(range(count))
    assert [r["key"] for r in rows] == [["node", f"{i:06d}"] for i in range(count)]


@pytest.mark.parametrize("keys", [[("a",), ("a",)], [("b",), ("a",)], [("x" * 8193,)], [("a",) * 9]])
def test_builder_rejects_invalid_or_unsorted_keys_without_publication(tmp_path, keys):
    path = tmp_path / "auth.pm-pack"
    with pytest.raises(auth.AuthIndexError):
        auth.build_auth_index(path, (auth.AuthEntry(k, None) for k in keys), generation_id=GENERATION)
    assert list(tmp_path.iterdir()) == []


def test_builder_rejects_oversized_value_without_publication(tmp_path):
    with pytest.raises(auth.AuthIndexError):
        auth.build_auth_index(tmp_path / "auth", [auth.AuthEntry(("a",), "x" * 1048576)], generation_id=GENERATION)
    assert list(tmp_path.iterdir()) == []


def test_large_value_is_separately_authenticated(tmp_path):
    path = tmp_path / "auth"
    seal = auth.build_auth_index(path, [auth.AuthEntry(("a",), "x" * 65536)], generation_id=GENERATION)
    leaf = PMPackReader(path).read(seal.root)
    descriptor = leaf["rows"][0]["value_ref"]
    value = PMPackReader(path).read(PackRecordRef(**descriptor["ref"]))
    assert value == {"format": "semantic-auth-index-v1", "generation_id": GENERATION, "kind": "value", "value": "x" * 65536}
    assert leaf["count"] == 1 and seal.pack_seal.record_count == 2


def test_builder_flushes_before_consuming_whole_generator(tmp_path, monkeypatch):
    appended = 0
    original = auth.PMPackWriter.append
    def observe(self, payload):
        nonlocal appended
        result = original(self, payload)
        appended += 1
        return result
    monkeypatch.setattr(auth.PMPackWriter, "append", observe)
    def source():
        for i in range(500):
            if i == 32:
                assert appended > 0, "builder buffered entire input"
            yield auth.AuthEntry((f"{i:06d}",), i)
    assert auth.build_auth_index(tmp_path / "auth", source(), generation_id=GENERATION).count == 500


def test_generator_failure_preserves_existing_files(tmp_path):
    other = tmp_path / "other"
    other.write_bytes(b"preserve")
    def source():
        yield from entries(20)
        raise RuntimeError("producer failed")
    with pytest.raises(RuntimeError, match="producer failed"):
        auth.build_auth_index(tmp_path / "auth", source(), generation_id=GENERATION)
    assert list(tmp_path.iterdir()) == [other]
    assert other.read_bytes() == b"preserve"


def test_existing_target_is_never_overwritten(tmp_path):
    path = tmp_path / "auth"
    path.write_bytes(b"preserve")
    with pytest.raises((auth.AuthIndexError, OSError, ValueError)):
        auth.build_auth_index(path, entries(1), generation_id=GENERATION)
    assert path.read_bytes() == b"preserve"


def test_seal_failure_does_not_publish(tmp_path, monkeypatch):
    def fail(self):
        raise OSError("seal failure")
    monkeypatch.setattr(auth.PMPackWriter, "seal", fail)
    with pytest.raises(OSError, match="seal failure"):
        auth.build_auth_index(tmp_path / "auth", entries(17), generation_id=GENERATION)
    assert list(tmp_path.iterdir()) == []


def empty_leaf():
    return {"format": "semantic-auth-index-v1", "generation_id": GENERATION, "kind": "leaf", "height": 0, "count": 0, "min": None, "max": None, "rows": []}


@pytest.mark.parametrize("field,value", [("extra", 1), ("kind", "value"), ("generation_id", "1" * 32), ("height", True), ("count", -1)])
def test_codec_rejects_invalid_node_metadata(field, value):
    payload = empty_leaf()
    payload[field] = value
    with pytest.raises(auth.AuthIndexError):
        auth._decode_node(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), generation_id=GENERATION, offset=1000)


def test_codec_rejects_duplicate_json_keys():
    raw = json.dumps(empty_leaf(), sort_keys=True, separators=(",", ":")).encode()
    raw = raw[:-1] + b',"count":0}'
    with pytest.raises(auth.AuthIndexError):
        auth._decode_node(raw, generation_id=GENERATION, offset=1000)


@pytest.mark.parametrize("offset", [True, -1])
def test_codec_rejects_invalid_child_offsets(offset):
    child = {"ref": {"offset": offset, "length": 100, "sha256": "0" * 64}, "digest": "0" * 64, "min": ["a"], "max": ["a"], "count": 1, "height": 0}
    payload = {**empty_leaf(), "kind": "branch", "count": 1, "height": 1, "min": ["a"], "max": ["a"], "children": [child]}
    del payload["rows"]
    with pytest.raises(auth.AuthIndexError):
        auth._decode_node(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), generation_id=GENERATION, offset=1000)


def test_range_is_complete_across_leaf_boundary(tmp_path):
    source = [auth.AuthEntry(("children", "p", f"{i:03d}"), i) for i in range(35)]
    path = tmp_path / "auth"
    seal = auth.build_auth_index(path, source, generation_id=GENERATION)
    with auth.AuthIndexReader(path, seal) as reader:
        page = reader.page(("children", "p"), None, 16)
        assert [e.value for e in page.entries] == list(range(16))
        assert page.next_key == ("children", "p", "015") and not page.complete
        last = reader.page(("children", "p"), page.next_key, 64)
        assert [e.value for e in last.entries] == list(range(16, 35))
        assert last.next_key is None and last.complete
        assert reader.get(("children", "p", "017x")) is None
        assert reader.get(("children", "p", "017")).value == 17
        assert reader.page(("children", "absent"), None, 64) == auth.AuthPage((), None, True)
        assert [e.value for e in reader.page(("children", "p"), ("children", "p", "017x"), 2).entries] == [18, 19]


def test_prefix_unicode_and_empty_boundaries(tmp_path):
    keys = [("a",), ("a", ""), ("a", "é"), ("aa",), ("한",)]
    path = tmp_path / "auth"
    seal = auth.build_auth_index(path, (auth.AuthEntry(k, i) for i,k in enumerate(keys)), generation_id=GENERATION)
    with auth.AuthIndexReader(path, seal) as reader:
        assert [e.value for e in reader.page(("a",), None, 64).entries] == [0, 1, 2]
        assert [e.value for e in reader.page((), None, 64).entries] == [0, 1, 2, 3, 4]
        assert reader.page(("z",), None, 1).complete
    empty = tmp_path / "empty"
    seal = auth.build_auth_index(empty, [], generation_id=GENERATION)
    with auth.AuthIndexReader(empty, seal) as reader:
        assert reader.get(("a",)) is None
        assert reader.page((), None, 1) == auth.AuthPage((), None, True)


@pytest.mark.parametrize("limit,after", [(0, None), (65, None), (True, None), (1, ("other",))])
def test_reader_rejects_invalid_range_parameters(tmp_path, limit, after):
    path = tmp_path / "auth"
    seal = auth.build_auth_index(path, entries(1), generation_id=GENERATION)
    with auth.AuthIndexReader(path, seal) as reader, pytest.raises(auth.AuthIndexError):
        reader.page(("node",), after, limit)


@pytest.mark.parametrize("count", [1000, 10000])
def test_point_reads_only_one_path(tmp_path, count):
    path = tmp_path / "auth"
    seal = auth.build_auth_index(path, entries(count), generation_id=GENERATION)
    observer = auth.AuthReadObserver()
    with auth.AuthIndexReader(path, seal, observer=observer) as reader:
        assert reader.get(("node", "000017")).value == {"i": 17}
    assert observer.node_reads == seal.height + 1
    assert observer.explicit_read_bytes <= (seal.height + 1) * (65536 + 40) + 8


def flip(path, offset):
    with path.open("r+b") as stream:
        stream.seek(offset)
        value = stream.read(1)
        stream.seek(offset)
        stream.write(bytes([value[0] ^ 1]))


def test_unread_subtree_corruption_may_pass_partial_but_used_range_fails(tmp_path):
    path = tmp_path / "auth"
    seal = auth.build_auth_index(path, entries(35), generation_id=GENERATION)
    root = PMPackReader(path).read(seal.root)
    flip(path, root["children"][1]["ref"]["offset"] + 8)
    with auth.AuthIndexReader(path, seal) as reader:
        assert reader.get(("node", "000000")).value == {"i": 0}
        with pytest.raises(auth.AuthIndexError):
            reader.page(("node",), None, 35)
    with pytest.raises(ValueError):
        verify_pack(path, seal.pack_seal)


def test_external_value_corruption_and_new_reader_do_not_trust_previous_check(tmp_path):
    path = tmp_path / "auth"
    seal = auth.build_auth_index(path, [auth.AuthEntry(("a",), "x" * 65536)], generation_id=GENERATION)
    with auth.AuthIndexReader(path, seal) as reader:
        assert reader.get(("a",)).value == "x" * 65536
    flip(path, 16)
    with auth.AuthIndexReader(path, seal) as reader, pytest.raises(auth.AuthIndexError):
        reader.get(("a",))


def test_reader_rejects_mismatched_seal_and_truncation(tmp_path):
    from dataclasses import replace
    path = tmp_path / "auth"
    seal = auth.build_auth_index(path, entries(17), generation_id=GENERATION)
    for wrong in (replace(seal, generation_id="1" * 32), replace(seal, count=18), replace(seal, height=True)):
        with pytest.raises(auth.AuthIndexError):
            with auth.AuthIndexReader(path, wrong) as reader:
                reader.get(("node", "000000"))
    with path.open("r+b") as stream:
        stream.truncate(20)
    with pytest.raises(auth.AuthIndexError):
        with auth.AuthIndexReader(path, seal) as reader:
            reader.page((), None, 64)


def test_operation_byte_budget_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "auth"
    seal = auth.build_auth_index(path, entries(35), generation_id=GENERATION)
    monkeypatch.setattr(auth, "MAX_TREE_READ_BYTES", 100)
    with auth.AuthIndexReader(path, seal) as reader, pytest.raises(auth.AuthIndexError):
        reader.page((), None, 64)


@pytest.mark.parametrize("change", ["rename", "inplace", "symlink", "short-read"])
def test_authenticated_operation_rejects_mid_read_changes(tmp_path, monkeypatch, change):
    import os
    path = tmp_path / "auth"
    seal = auth.build_auth_index(path, entries(17), generation_id=GENERATION)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(path.read_bytes())
    original = os.pread
    changed = False

    def race(fd, amount, offset):
        nonlocal changed
        result = original(fd, amount, offset)
        if offset == seal.root.offset and not changed:
            changed = True
            if change == "short-read":
                return result[:-1]
            if change == "rename":
                replacement.replace(path)
            elif change == "symlink":
                path.unlink()
                path.symlink_to(replacement)
            else:
                with path.open("r+b") as stream:
                    stream.seek(seal.root.offset + 8)
                    stream.write(b"!")
        return result

    monkeypatch.setattr(os, "pread", race)
    with auth.AuthIndexReader(path, seal) as reader, pytest.raises(auth.AuthIndexError):
        reader.page(("node",), None, 16)
    assert changed
