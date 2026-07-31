from __future__ import annotations

import json
from pathlib import Path

from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.infrastructure.semantic_analyzers import FileAnalysisInput
from portfolio_maker.infrastructure.semantic_capsules import (
    MAX_SIGNAL_CHARS,
    TRUNCATION_MARKER,
    build_feature_capsules,
)
from portfolio_maker.infrastructure.semantic_crawler import StructuralEntry


def _entry(
    root: Path,
    node_id: str,
    *,
    parent_node_id: str | None,
    kind: SemanticNodeKind,
    relative_hierarchy: str,
    status: AnalysisStatus = AnalysisStatus.PENDING,
) -> StructuralEntry:
    return StructuralEntry(
        node_id=node_id,
        source_id="source-1",
        parent_node_id=parent_node_id,
        kind=kind,
        display_name=Path(relative_hierarchy).name if relative_hierarchy != "." else "root",
        relative_hierarchy=relative_hierarchy,
        absolute_path=root / relative_hierarchy,
        provider_item_key=f"provider-{node_id}",
        content_fingerprint=None,
        device=101,
        inode=1000 + len(node_id),
        status=status,
    )


def _analysis(
    node_id: str,
    text: str,
    *,
    status: AnalysisStatus = AnalysisStatus.COMPLETE,
    roles: tuple[str, ...] = ("documentation",),
) -> FileAnalysisInput:
    return FileAnalysisInput(
        node_id=node_id,
        content_fingerprint=f"sha256:{node_id:0<64}"[:71],
        masked_excerpt=text,
        semantic_roles=roles,
        status=status,
    )


def test_feature_capsules_account_for_every_node_without_locators_or_conclusions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "synthetic-root"
    entries = (
        _entry(
            root,
            "root",
            parent_node_id=None,
            kind=SemanticNodeKind.SOURCE,
            relative_hierarchy=".",
        ),
        _entry(
            root,
            "docs",
            parent_node_id="root",
            kind=SemanticNodeKind.DIRECTORY,
            relative_hierarchy="docs",
        ),
        _entry(
            root,
            "readme",
            parent_node_id="docs",
            kind=SemanticNodeKind.FILE,
            relative_hierarchy="docs/README.md",
        ),
        _entry(
            root,
            "binary",
            parent_node_id="root",
            kind=SemanticNodeKind.FILE,
            relative_hierarchy="archive.bin",
            status=AnalysisStatus.UNSUPPORTED,
        ),
        _entry(
            root,
            "unreadable",
            parent_node_id="root",
            kind=SemanticNodeKind.FILE,
            relative_hierarchy="blocked.txt",
            status=AnalysisStatus.UNREADABLE,
        ),
        _entry(
            root,
            "empty",
            parent_node_id="root",
            kind=SemanticNodeKind.FILE,
            relative_hierarchy="empty.md",
        ),
    )
    unsafe_text = (
        "OPENAI_API_KEY=sk-synthetic-placeholder\n"
        f"SOURCE_PATH={root}/private.txt\n"
        "See https://example.invalid/private\n"
        "# Safe heading\nSafe evidence"
    )
    analyses = {
        "readme": _analysis("readme", unsafe_text),
        "empty": _analysis("empty", ""),
    }

    capsules = build_feature_capsules(
        entries,
        analyses,
        revision_id="revision-1",
    )

    assert {capsule["node_id"] for capsule in capsules} == {
        entry.node_id for entry in entries
    }
    by_id = {capsule["node_id"]: capsule for capsule in capsules}
    assert by_id["readme"]["semantic_disposition"] == "analysis_required"
    assert by_id["binary"]["semantic_disposition"] == "unsupported"
    assert by_id["binary"]["signals"] == []
    assert by_id["binary"]["roles"] == []
    assert by_id["unreadable"]["semantic_disposition"] == "unreadable"
    assert by_id["empty"]["semantic_disposition"] == "structural_only"
    assert by_id["empty"]["skip_reason"] == "empty_content"
    assert by_id["docs"]["semantic_disposition"] == "analysis_required"
    assert by_id["root"]["semantic_disposition"] == "analysis_required"
    assert by_id["root"]["child_node_ids"] == ["binary", "docs", "empty", "unreadable"]
    assert by_id["root"]["coverage"]["node_count"] == len(entries)

    serialized = json.dumps(capsules, ensure_ascii=False, sort_keys=True)
    assert str(root) not in serialized
    assert "file://" not in serialized
    assert "https://example.invalid" not in serialized
    assert "sk-synthetic-placeholder" not in serialized
    assert "semantic_summary" not in serialized
    assert "purpose" not in serialized
    assert "confidence" not in serialized


def test_long_text_signal_is_deterministic_bounded_and_marked_partial(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    entry = _entry(
        root,
        "long",
        parent_node_id=None,
        kind=SemanticNodeKind.FILE,
        relative_hierarchy="long.py",
    )
    long_text = (
        "HEAD\n"
        + "A" * 10_000
        + "\nclass StableBoundary:\n"
        + "B" * 10_000
        + "\nTAIL"
    )
    analysis = _analysis("long", long_text)

    first = build_feature_capsules(
        (entry,),
        {"long": analysis},
        revision_id="revision-1",
    )[0]
    second = build_feature_capsules(
        (entry,),
        {"long": analysis},
        revision_id="revision-1",
    )[0]
    signal = first["signals"][0]

    assert len(signal["text"]) <= MAX_SIGNAL_CHARS
    assert TRUNCATION_MARKER.strip() in signal["text"]
    assert "HEAD" in signal["text"]
    assert "class StableBoundary:" in signal["text"]
    assert signal["text"].endswith("TAIL")
    assert first["analysis_status"] == "partial"
    assert signal["signal_id"] == second["signals"][0]["signal_id"]


def test_capsule_with_only_redacted_text_remains_metadata_only(tmp_path: Path) -> None:
    entry = _entry(
        tmp_path,
        "redacted",
        parent_node_id=None,
        kind=SemanticNodeKind.FILE,
        relative_hierarchy="secret.txt",
    )

    capsule = build_feature_capsules(
        (entry,),
        {"redacted": _analysis("redacted", "OPENAI_API_KEY=sk-placeholder-value")},
        revision_id="revision-1",
    )[0]

    assert capsule["signals"] == []
    assert capsule["semantic_disposition"] == "structural_only"
    assert capsule["skip_reason"] == "policy_redacted"
