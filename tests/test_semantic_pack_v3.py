from __future__ import annotations

import hashlib
from stat import S_ISDIR
from dataclasses import replace
from pathlib import Path

import pytest

import portfolio_maker.infrastructure.semantic_pack as semantic_pack
from portfolio_maker.domain.semantic_frontier import PackRecordRef
from portfolio_maker.infrastructure.semantic_pack import (
    PACK_MAGIC,
    PMPackReader,
    PMPackWriter,
    PackSeal,
    SemanticPackError,
    canonical_json_bytes,
    recover_committed_prefix,
    verify_pack,
)


def _record_frame(payload: object) -> bytes:
    canonical = canonical_json_bytes(payload)
    return len(canonical).to_bytes(8, "big") + canonical + hashlib.sha256(canonical).digest()


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
    assert first.offset == len(PACK_MAGIC)
    assert first.length == len(canonical_json_bytes({"node_id": "node-a", "summary": "alpha"}))
    assert first.sha256 == hashlib.sha256(
        canonical_json_bytes({"node_id": "node-a", "summary": "alpha"})
    ).hexdigest()


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


def test_prepare_pack_is_published_only_after_verified_seal(tmp_path: Path) -> None:
    path = tmp_path / "content.pm-pack"

    with PMPackWriter(path) as writer:
        writer.append({"node_id": "node-a", "summary": "alpha"})
        assert not path.exists()
        assert list(tmp_path.glob(".content.pm-pack.*.tmp"))
        seal = writer.seal()

    assert verify_pack(path, seal) == seal
    assert not list(tmp_path.glob(".content.pm-pack.*.tmp"))


def test_pack_reader_rejects_wrong_offset_length_or_digest(tmp_path: Path) -> None:
    path = tmp_path / "content.pm-pack"
    with PMPackWriter(path) as writer:
        record = writer.append({"node_id": "node-a", "summary": "alpha"})
        writer.seal()

    reader = PMPackReader(path)
    invalid_references = (
        replace(record, offset=record.offset + 1),
        replace(record, length=record.length - 1),
        replace(record, sha256="0" * 64),
        replace(record, offset=1 << 100),
    )
    for reference in invalid_references:
        with pytest.raises(SemanticPackError):
            reader.read(reference)


def test_verify_pack_rejects_truncation_bit_flip_and_wrong_root(tmp_path: Path) -> None:
    path = tmp_path / "content.pm-pack"
    with PMPackWriter(path) as writer:
        writer.append({"node_id": "node-a", "summary": "alpha"})
        seal = writer.seal()

    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(SemanticPackError, match="truncated"):
        verify_pack(path, seal)

    with PMPackWriter(path.with_name("bit-flip.pm-pack")) as writer:
        record = writer.append({"node_id": "node-a", "summary": "alpha"})
        flipped_seal = writer.seal()
    flipped_path = path.with_name("bit-flip.pm-pack")
    damaged = bytearray(flipped_path.read_bytes())
    damaged[record.offset + 8] ^= 1
    flipped_path.write_bytes(damaged)
    with pytest.raises(SemanticPackError, match="digest"):
        verify_pack(flipped_path, flipped_seal)

    with PMPackWriter(path.with_name("wrong-root.pm-pack")) as writer:
        writer.append({"node_id": "node-a", "summary": "alpha"})
        root_seal = writer.seal()
    wrong_root = replace(root_seal, record_root="0" * 64)
    with pytest.raises(SemanticPackError, match="seal"):
        verify_pack(path.with_name("wrong-root.pm-pack"), wrong_root)


@pytest.mark.parametrize(
    "tail",
    (
        b"raw-unframed-tail",
        _record_frame({"node_id": "node-b", "summary": "valid extra record"}),
    ),
    ids=("raw", "valid-record"),
)
def test_verify_pack_rejects_any_tail_after_a_seal(tmp_path: Path, tail: bytes) -> None:
    path = tmp_path / "content.pm-pack"
    with PMPackWriter(path) as writer:
        writer.append({"node_id": "node-a", "summary": "alpha"})
        seal = writer.seal()

    with path.open("ab") as handle:
        handle.write(tail)

    with pytest.raises(SemanticPackError, match="seal"):
        verify_pack(path, seal)


def test_reader_rejects_duplicate_key_and_noncanonical_payloads(tmp_path: Path) -> None:
    duplicate = b'{"node_id":"node-a","node_id":"node-b"}'
    noncanonical = b'{"summary": "alpha","node_id":"node-a"}'
    for name, payload in (("duplicate.pm-pack", duplicate), ("noncanonical.pm-pack", noncanonical)):
        path = tmp_path / name
        digest = hashlib.sha256(payload).digest()
        path.write_bytes(PACK_MAGIC + len(payload).to_bytes(8, "big") + payload + digest)
        reference = PackRecordRef(
            offset=len(PACK_MAGIC),
            length=len(payload),
            sha256=digest.hex(),
        )

        with pytest.raises(SemanticPackError):
            PMPackReader(path).read(reference)
        with pytest.raises(SemanticPackError):
            verify_pack(path, _single_record_seal(path, payload))


def test_pack_preserves_unicode_canonical_payload_bytes(tmp_path: Path) -> None:
    path = tmp_path / "unicode.pm-pack"
    payload = {"summary": "한글", "node_id": "node-a"}
    with PMPackWriter(path) as writer:
        reference = writer.append(payload)
        writer.seal()

    raw = path.read_bytes()[reference.offset + 8 : reference.offset + 8 + reference.length]
    assert raw == b'{"node_id":"node-a","summary":"\xed\x95\x9c\xea\xb8\x80"}'
    assert PMPackReader(path).read(reference) == {"node_id": "node-a", "summary": "한글"}


def test_writer_rejects_append_after_seal_and_unsafe_target_symlink(tmp_path: Path) -> None:
    path = tmp_path / "content.pm-pack"
    writer = PMPackWriter(path)
    writer.append({"node_id": "node-a", "summary": "alpha"})
    writer.seal()
    with pytest.raises(SemanticPackError, match="sealed"):
        writer.append({"node_id": "node-b", "summary": "beta"})
    writer.close()

    external = tmp_path / "external.pm-pack"
    external.write_bytes(b"preserve")
    unsafe = tmp_path / "unsafe.pm-pack"
    unsafe.symlink_to(external)
    with pytest.raises(OSError, match="regular file"):
        PMPackWriter(unsafe)
    assert external.read_bytes() == b"preserve"


def test_result_pack_cannot_be_reopened_for_append_after_seal(tmp_path: Path) -> None:
    path = tmp_path / "results.pm-pack"
    with PMPackWriter(path, seal_on_close=False) as writer:
        writer.append({"unit_id": "unit-1", "summary": "ok"})
        writer.seal()

    with pytest.raises(SemanticPackError, match="sealed"):
        PMPackWriter(path, seal_on_close=False)


def test_new_result_pack_fsyncs_file_before_parent_directory(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    original_fsync = semantic_pack.os.fsync

    def record_fsync(descriptor: int) -> None:
        mode = semantic_pack.os.fstat(descriptor).st_mode
        calls.append("directory" if S_ISDIR(mode) else "file")
        original_fsync(descriptor)

    monkeypatch.setattr(semantic_pack.os, "fsync", record_fsync)
    writer = PMPackWriter(tmp_path / "results.pm-pack", seal_on_close=False)
    try:
        assert calls[:2] == ["file", "directory"]
    finally:
        writer.close()


def test_result_writer_exposes_idempotent_public_fsync(tmp_path: Path) -> None:
    with PMPackWriter(tmp_path / "results.pm-pack", seal_on_close=False) as writer:
        writer.append({"unit_id": "unit-1", "summary": "ok"})
        writer.fsync()
        writer.fsync()


def test_recovery_rejects_mismatched_committed_root_without_truncating(tmp_path: Path) -> None:
    path = tmp_path / "results.pm-pack"
    with PMPackWriter(path, seal_on_close=False) as writer:
        writer.append({"unit_id": "unit-1", "summary": "ok"})
        committed_length = writer.byte_length
        writer.append({"unit_id": "unit-orphan", "summary": "ignored"})
    original = path.read_bytes()
    original_size = path.stat().st_size

    with pytest.raises(SemanticPackError, match="prefix is invalid"):
        recover_committed_prefix(path, committed_length, "0" * 64)

    assert path.read_bytes() == original
    assert path.stat().st_size == original_size


def test_recovery_rejects_wrong_length_without_truncating(tmp_path: Path) -> None:
    path = tmp_path / "results.pm-pack"
    with PMPackWriter(path, seal_on_close=False) as writer:
        writer.append({"unit_id": "unit-1", "summary": "ok"})
        committed_length = writer.byte_length
        committed_root = writer.record_root
        writer.append({"unit_id": "unit-orphan", "summary": "ignored"})
    original = path.read_bytes()
    original_size = path.stat().st_size

    with pytest.raises(SemanticPackError):
        recover_committed_prefix(path, committed_length - 1, committed_root)

    assert path.read_bytes() == original
    assert path.stat().st_size == original_size


def _single_record_seal(path: Path, payload: bytes) -> PackSeal:
    digest = hashlib.sha256(payload).digest()
    root = hashlib.sha256(
        b"portfolio-maker-pm-pack-v1\0" + bytes(32) + len(payload).to_bytes(8, "big") + digest
    ).hexdigest()
    return PackSeal(
        byte_length=path.stat().st_size,
        record_count=1,
        record_root=root,
        file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
