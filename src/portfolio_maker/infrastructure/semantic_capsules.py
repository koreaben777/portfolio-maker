from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from portfolio_maker.domain.semantic_models import AnalysisStatus, SemanticNodeKind
from portfolio_maker.domain.semantic_work import SemanticDisposition
from portfolio_maker.infrastructure.policy import mask_public_value, mask_secrets
from portfolio_maker.infrastructure.presentation import normalize_label
from portfolio_maker.infrastructure.semantic_analyzers import FileAnalysisInput
from portfolio_maker.infrastructure.semantic_crawler import StructuralEntry


MAX_SIGNAL_CHARS = 8_192
TRUNCATION_MARKER = "\n[TRUNCATED]\n"
_LOCATOR_VALUE = re.compile(
    r"(?im)(\b[\w-]*(?:path|file|directory|dir|root|snapshot|database)[\w-]*\s*[:=]\s*)"
    r"(?:file://)?(?:/|~[/\\]|[A-Za-z]:[/\\]|\\\\)\S+"
)
_LOCATOR_TOKEN = re.compile(
    r"(?i)(?:file://|https?://|(?<![A-Za-z0-9_.-])(?:/|~[/\\]|[A-Za-z]:[/\\]|\\\\))"
    r"[^\s'\"`<>)}\]]+"
)
_CREDENTIAL_VALUE = re.compile(
    r"(?im)(\b[\w-]*credential[\w-]*\s*[:=]\s*)"
    r"(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,}\]\n]+)"
)
_STRUCTURAL_LINE = re.compile(
    r"^\s*(?:#{1,6}\s+|(?:async\s+)?def\s+|class\s+|"
    r"(?:export\s+)?(?:async\s+)?function\s+|(?:interface|type|struct|enum)\s+)"
)
_REDACTED_ASSIGNMENT = re.compile(
    r"(?i)^[A-Za-z0-9_-]*(?:path|file|directory|dir|root|snapshot|database|"
    r"password|api[_-]?key|token|secret|credential)[A-Za-z0-9_-]*"
    r"\s*[:=]\s*\[REDACTED\]\s*$"
)
_GENERATED_PARTS = frozenset(
    {"__pycache__", ".cache", "node_modules", "dist", "build", "coverage"}
)


def build_feature_capsules(
    entries: tuple[StructuralEntry, ...],
    analyses: Mapping[str, FileAnalysisInput],
    *,
    revision_id: str,
    evidence_ids_by_node: Mapping[str, tuple[int, ...]] | None = None,
) -> tuple[dict[str, object], ...]:
    evidence_ids_by_node = evidence_ids_by_node or {}
    ordered_entries = tuple(
        sorted(entries, key=lambda entry: (entry.relative_hierarchy, entry.node_id))
    )
    if len({entry.node_id for entry in ordered_entries}) != len(ordered_entries):
        raise ValueError("semantic capsule node identifiers must be unique")

    children_by_parent: dict[str, list[str]] = {}
    capsules_by_id: dict[str, dict[str, object]] = {}
    for entry in ordered_entries:
        if entry.parent_node_id is not None:
            children_by_parent.setdefault(entry.parent_node_id, []).append(entry.node_id)
        capsules_by_id[entry.node_id] = _direct_capsule(
            entry,
            analyses.get(entry.node_id),
            revision_id,
            evidence_ids_by_node.get(entry.node_id, ()),
        )
    for child_ids in children_by_parent.values():
        child_ids.sort()
    for entry in ordered_entries:
        capsules_by_id[entry.node_id]["child_node_ids"] = children_by_parent.get(
            entry.node_id, []
        )

    bottom_up = sorted(
        ordered_entries,
        key=lambda entry: (
            -_hierarchy_depth(entry.relative_hierarchy),
            entry.relative_hierarchy,
            entry.node_id,
        ),
    )
    for entry in bottom_up:
        capsule = capsules_by_id[entry.node_id]
        children = [
            capsules_by_id[child_id]
            for child_id in children_by_parent.get(entry.node_id, [])
        ]
        if (
            entry.kind in {SemanticNodeKind.SOURCE, SemanticNodeKind.DIRECTORY}
            and capsule["semantic_disposition"]
            == SemanticDisposition.STRUCTURAL_ONLY.value
            and any(
                child["semantic_disposition"]
                == SemanticDisposition.ANALYSIS_REQUIRED.value
                for child in children
            )
        ):
            capsule["semantic_disposition"] = (
                SemanticDisposition.ANALYSIS_REQUIRED.value
            )
            capsule["skip_reason"] = None
        capsule["coverage"] = _coverage(entry.node_id, capsule, children)

    return tuple(capsules_by_id[entry.node_id] for entry in ordered_entries)


def bounded_masked_signal(
    value: str,
    structural_lines: tuple[str, ...],
) -> tuple[str, bool]:
    if len(value) <= MAX_SIGNAL_CHARS:
        return value, False
    structure = "\n".join(dict.fromkeys(structural_lines))[:2_048]
    separators = 2 if structure else 0
    remaining = (
        MAX_SIGNAL_CHARS - len(TRUNCATION_MARKER) - len(structure) - separators
    )
    head_size = max(0, remaining // 2)
    tail_size = max(0, remaining - head_size)
    bounded = value[:head_size]
    if structure:
        bounded += "\n" + structure + "\n"
    bounded += TRUNCATION_MARKER
    if tail_size:
        bounded += value[-tail_size:]
    return bounded[:MAX_SIGNAL_CHARS], True


def _direct_capsule(
    entry: StructuralEntry,
    analysis: FileAnalysisInput | None,
    revision_id: str,
    evidence_ids: tuple[int, ...],
) -> dict[str, object]:
    status = entry.status
    roles: tuple[str, ...] = ()
    signals: list[dict[str, str]] = []
    skip_reason: str | None = None

    if status is AnalysisStatus.UNSUPPORTED:
        disposition = SemanticDisposition.UNSUPPORTED
    elif status in {AnalysisStatus.UNREADABLE, AnalysisStatus.FAILED}:
        disposition = SemanticDisposition.UNREADABLE
    elif analysis is not None and analysis.status is AnalysisStatus.UNSUPPORTED:
        status = analysis.status
        disposition = SemanticDisposition.UNSUPPORTED
    elif analysis is not None and analysis.status in {
        AnalysisStatus.UNREADABLE,
        AnalysisStatus.FAILED,
    }:
        status = analysis.status
        disposition = SemanticDisposition.UNREADABLE
    elif analysis is not None:
        status = analysis.status
        roles = tuple(sorted(set(analysis.semantic_roles)))
        masked = _mask_signal(analysis.masked_excerpt)
        if _is_generated_or_cache(entry, roles):
            disposition = SemanticDisposition.STRUCTURAL_ONLY
            roles = ()
            skip_reason = "generated_or_cache_signal"
        elif _contains_safe_signal(masked):
            structural_lines = tuple(
                line.strip()
                for line in masked.splitlines()
                if _STRUCTURAL_LINE.match(line)
            )
            bounded, truncated = bounded_masked_signal(masked, structural_lines)
            if truncated:
                status = AnalysisStatus.PARTIAL
            text_sha256 = hashlib.sha256(bounded.encode("utf-8")).hexdigest()
            signal_id = _stable_signal_id(
                revision_id,
                entry.node_id,
                "masked_text",
                text_sha256,
            )
            signals.append(
                {
                    "signal_id": signal_id,
                    "kind": "masked_text",
                    "text": bounded,
                }
            )
            disposition = SemanticDisposition.ANALYSIS_REQUIRED
        else:
            disposition = SemanticDisposition.STRUCTURAL_ONLY
            roles = ()
            skip_reason = (
                "policy_redacted"
                if masked.strip()
                else "empty_content"
            )
    else:
        disposition = SemanticDisposition.STRUCTURAL_ONLY
        skip_reason = "no_text_signal"

    return {
        "node_id": entry.node_id,
        "parent_node_id": entry.parent_node_id,
        "kind": entry.kind.value,
        "display_name": normalize_label(mask_public_value(entry.display_name)),
        "relative_hierarchy": entry.relative_hierarchy,
        "content_fingerprint": (
            analysis.content_fingerprint
            if analysis is not None
            else entry.content_fingerprint
        ),
        "analysis_status": status.value,
        "semantic_disposition": disposition.value,
        "roles": list(roles),
        "child_node_ids": [],
        "signals": signals,
        "skip_reason": skip_reason,
        "evidence_ids": list(sorted(set(evidence_ids))),
        "coverage": {},
    }


def _mask_signal(value: str) -> str:
    masked = mask_public_value(mask_secrets(value))
    masked = _CREDENTIAL_VALUE.sub(r"\1[REDACTED]", masked)
    masked = _LOCATOR_VALUE.sub(r"\1[REDACTED]", masked)
    return _LOCATOR_TOKEN.sub("[REDACTED]", masked)


def _contains_safe_signal(value: str) -> bool:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return bool(lines) and not all(
        line == "[REDACTED]" or _REDACTED_ASSIGNMENT.fullmatch(line)
        for line in lines
    )


def _stable_signal_id(
    revision_id: str,
    node_id: str,
    signal_kind: str,
    text_sha256: str,
) -> str:
    canonical = "\0".join(
        (revision_id, node_id, signal_kind, text_sha256)
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _coverage(
    node_id: str,
    capsule: dict[str, object],
    children: list[dict[str, object]],
) -> dict[str, object]:
    child_coverages = [child["coverage"] for child in children]
    node_count = 1 + sum(int(coverage["node_count"]) for coverage in child_coverages)
    content_count = int(bool(capsule["signals"])) + sum(
        int(coverage["content_node_count"]) for coverage in child_coverages
    )
    structural_count = int(
        capsule["semantic_disposition"]
        != SemanticDisposition.ANALYSIS_REQUIRED.value
    ) + sum(
        int(coverage["structural_only_count"]) for coverage in child_coverages
    )
    failed_count = int(
        capsule["semantic_disposition"]
        in {
            SemanticDisposition.UNSUPPORTED.value,
            SemanticDisposition.UNREADABLE.value,
        }
    ) + sum(int(coverage["failed_count"]) for coverage in child_coverages)
    digest_payload = [
        node_id,
        *[
            str(coverage["node_ids_sha256"])
            for coverage in child_coverages
        ],
    ]
    return {
        "node_count": node_count,
        "content_node_count": content_count,
        "structural_only_count": structural_count,
        "failed_count": failed_count,
        "node_ids_sha256": hashlib.sha256(
            json.dumps(
                digest_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def _is_generated_or_cache(
    entry: StructuralEntry,
    roles: tuple[str, ...],
) -> bool:
    parts = {
        part.casefold()
        for part in entry.relative_hierarchy.replace("\\", "/").split("/")
    }
    return bool(
        parts & _GENERATED_PARTS
        or {"generated", "cache", "generated-or-cache"} & set(roles)
    )


def _hierarchy_depth(relative_hierarchy: str) -> int:
    return 0 if relative_hierarchy == "." else relative_hierarchy.count("/") + 1
