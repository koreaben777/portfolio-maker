from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from portfolio_maker.application.semantic_frontier_v3 import (
    SEMANTIC_INPUT_SCHEMA_V3,
    SEMANTIC_OUTPUT_SCHEMA_V3,
)
from portfolio_maker.domain.semantic_frontier import (
    MAX_DEPENDENCIES,
    MAX_UNIT_BYTES,
    FrontierAction,
)


class SemanticQualityV3Error(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedV3SemanticOutput:
    revision_id: str
    run_id: str
    unit_id: str
    input_sha256: str
    action: FrontierAction
    target_node_ids: tuple[str, ...]
    grounding_signal_ids: tuple[str, ...]
    representative_digests: tuple[str, ...]
    rationale: str
    counter_signal_ids: tuple[str, ...]
    confidence: float
    requested_child_node_ids: tuple[str, ...]
    next_child_cursor: str | None


_INPUT_KEYS = {
    "schema_version",
    "revision_id",
    "run_id",
    "unit_id",
    "target_node_ids",
    "approved_local_signal_ids",
    "representatives",
    "exposed_child_node_ids",
    "next_child_cursor",
    "input_sha256",
}
_OUTPUT_KEYS = {
    "schema_version",
    "revision_id",
    "run_id",
    "unit_id",
    "input_sha256",
    "action",
    "target_node_ids",
    "grounding_signal_ids",
    "representative_digests",
    "rationale",
    "counter_signal_ids",
    "confidence",
    "requested_child_node_ids",
    "next_child_cursor",
}
_REPRESENTATIVE_KEYS = {
    "node_id",
    "full_content_sha256",
    "coverage",
    "role_reason",
}
_LOCATOR_PATTERNS = (
    re.compile(r"(?:https?|file|ssh)://"),
    re.compile(r"(?<![a-z0-9])[a-z]:[\\/]"),
    re.compile(r"(?<![a-z0-9])\\\\[^\\\s]+\\[^\\\s]+"),
    re.compile(r"(?<![a-z0-9_.-])/(?:[^\s/]+(?:/[^\s/]+)*)"),
    re.compile(r"(?:^|[\s(\"'])\.{1,2}[\\/][^\s\"')]+"),
    re.compile(
        r"(?:^|[\s(\"'])(?:\.{1,2}[\\/]|[a-z0-9_.-]+[\\/])"
        r"[^\s\"')]*\.[a-z0-9]{1,12}(?:$|[\s\"')])"
    ),
    re.compile(
        r"(?:^|[\s(\"'])[^/\\\s\"')]+\."
        r"(?:md|py|js|ts|tsx|jsx|json|ya?ml|toml|pdf|docx|xlsx|pptx|"
        r"csv|txt|html|css|sql|db)(?:$|[\s\"')])"
    ),
    re.compile(
        r"(?<![a-z0-9_.-])(?:dockerfile|readme|makefile|license)"
        r"(?![a-z0-9_.-])"
    ),
    re.compile(r"(?:git@[^:\s]+:[^\s]+|portfolio\.db|\.portfolio-maker)"),
)


def validate_v3_semantic_output(
    input_payload: object, output_payload: object
) -> ValidatedV3SemanticOutput:
    """Validate a closed v3 action against only its bounded input projection."""

    work_input = _bounded_canonical_object(input_payload, "input")
    output = _bounded_canonical_object(output_payload, "output")
    if set(work_input) != _INPUT_KEYS:
        raise SemanticQualityV3Error("v3 input schema is invalid")
    if work_input.get("schema_version") != SEMANTIC_INPUT_SCHEMA_V3:
        raise SemanticQualityV3Error("v3 input schema version is invalid")
    _validate_input_hash(work_input)
    revision_id = _text(work_input["revision_id"], "input revision")
    run_id = _text(work_input["run_id"], "input run")
    unit_id = _text(work_input["unit_id"], "input unit")
    input_sha256 = _digest(work_input["input_sha256"], "input sha256")
    target_node_ids = _text_array(
        work_input["target_node_ids"], "input target", 1, MAX_DEPENDENCIES
    )
    approved_signal_ids = _text_array(
        work_input["approved_local_signal_ids"],
        "input grounding",
        1,
        MAX_DEPENDENCIES,
    )
    representatives = _representatives(work_input["representatives"])
    exposed_children = _text_array(
        work_input["exposed_child_node_ids"],
        "input child",
        0,
        MAX_DEPENDENCIES,
    )
    input_cursor = _canonical_cursor(work_input["next_child_cursor"], "input child")

    if set(output) != _OUTPUT_KEYS:
        raise SemanticQualityV3Error("v3 output schema is invalid")
    if output.get("schema_version") != SEMANTIC_OUTPUT_SCHEMA_V3:
        raise SemanticQualityV3Error("v3 output schema version is invalid")
    bindings = (
        ("revision_id", revision_id),
        ("run_id", run_id),
        ("unit_id", unit_id),
        ("input_sha256", input_sha256),
    )
    if any(output.get(key) != expected for key, expected in bindings):
        raise SemanticQualityV3Error("v3 output binding is invalid")
    try:
        action = FrontierAction(output["action"])
    except (TypeError, ValueError) as error:
        raise SemanticQualityV3Error("v3 action is invalid") from error
    output_targets = _text_array(
        output["target_node_ids"], "output target", 1, MAX_DEPENDENCIES
    )
    if output_targets != target_node_ids:
        raise SemanticQualityV3Error("v3 target binding is invalid")
    grounding = _text_array(
        output["grounding_signal_ids"], "output grounding", 1, MAX_DEPENDENCIES
    )
    if not set(grounding).issubset(approved_signal_ids):
        raise SemanticQualityV3Error("v3 grounding is not approved")
    if not _is_subsequence(grounding, approved_signal_ids):
        raise SemanticQualityV3Error("v3 grounding binding is noncanonical")
    representative_digests = _digest_array(
        output["representative_digests"], "output representative"
    )
    expected_digests = tuple(item[1] for item in representatives)
    if representative_digests != expected_digests:
        raise SemanticQualityV3Error("v3 representative digest binding is invalid")
    rationale = _bounded_safe_text(output["rationale"], "rationale", 12, 2_000)
    counter_signals = _text_array(
        output["counter_signal_ids"], "output counter signal", 0, MAX_DEPENDENCIES
    )
    if not set(counter_signals).issubset(approved_signal_ids):
        raise SemanticQualityV3Error("v3 counter signal grounding is invalid")
    if not _is_subsequence(counter_signals, approved_signal_ids):
        raise SemanticQualityV3Error("v3 counter signal binding is noncanonical")
    confidence_value = output["confidence"]
    if (
        isinstance(confidence_value, bool)
        or not isinstance(confidence_value, (int, float))
        or not 0.0 <= float(confidence_value) <= 1.0
    ):
        raise SemanticQualityV3Error("v3 confidence is invalid")
    requested_children = _text_array(
        output["requested_child_node_ids"], "output child", 0, MAX_DEPENDENCIES
    )
    next_cursor = _canonical_cursor(output["next_child_cursor"], "output child")
    if action is FrontierAction.EXPAND_CHILDREN:
        if (
            not requested_children
            or not set(requested_children).issubset(exposed_children)
            or not _is_subsequence(requested_children, exposed_children)
            or next_cursor != input_cursor
        ):
            raise SemanticQualityV3Error("v3 requested child binding is invalid")
    elif requested_children or next_cursor is not None:
        raise SemanticQualityV3Error("v3 terminal action cannot request child expansion")
    return ValidatedV3SemanticOutput(
        revision_id,
        run_id,
        unit_id,
        input_sha256,
        action,
        output_targets,
        grounding,
        representative_digests,
        rationale,
        counter_signals,
        float(confidence_value),
        requested_children,
        next_cursor,
    )


def _representatives(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_DEPENDENCIES:
        raise SemanticQualityV3Error("v3 representative projection is invalid")
    result: list[tuple[str, str]] = []
    seen_nodes: set[str] = set()
    seen_digests: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != _REPRESENTATIVE_KEYS:
            raise SemanticQualityV3Error("v3 representative schema is invalid")
        node_id = _text(raw["node_id"], "representative node")
        digest = _digest(raw["full_content_sha256"], "representative full-content digest")
        if raw["coverage"] != "complete":
            raise SemanticQualityV3Error(
                "v3 sampled-only representative cannot claim full-content grounding"
            )
        _bounded_safe_text(raw["role_reason"], "representative role", 1, 128)
        if node_id in seen_nodes or digest in seen_digests:
            raise SemanticQualityV3Error("v3 representative items must be unique")
        seen_nodes.add(node_id)
        seen_digests.add(digest)
        result.append((node_id, digest))
    return tuple(result)


def _bounded_canonical_object(value: object, name: str) -> dict[str, Any]:
    raw: bytes | None = None
    if isinstance(value, Mapping):
        parsed: object = dict(value)
    else:
        if isinstance(value, str):
            try:
                raw = value.encode("utf-8")
            except UnicodeEncodeError as error:
                raise SemanticQualityV3Error(f"v3 {name} JSON is invalid") from error
        elif isinstance(value, bytes):
            raw = value
        else:
            raise SemanticQualityV3Error(f"v3 {name} must be an object")
        if len(raw) > MAX_UNIT_BYTES:
            raise SemanticQualityV3Error(f"v3 {name} exceeds the hard byte limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SemanticQualityV3Error(f"v3 {name} JSON is invalid") from error
        try:
            parsed = json.loads(text, object_pairs_hook=_no_duplicate_keys)
        except SemanticQualityV3Error:
            raise
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise SemanticQualityV3Error(f"v3 {name} JSON is invalid") from error
    if not isinstance(parsed, dict):
        raise SemanticQualityV3Error(f"v3 {name} must be an object")
    canonical = _canonical_bytes(parsed)
    if len(canonical) > MAX_UNIT_BYTES:
        raise SemanticQualityV3Error(f"v3 {name} exceeds the hard byte limit")
    if raw is not None and raw != canonical:
        raise SemanticQualityV3Error(f"v3 {name} JSON must be canonical")
    return parsed


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SemanticQualityV3Error("v3 JSON contains a duplicate key")
        result[key] = value
    return result


def _validate_input_hash(payload: dict[str, object]) -> None:
    unsigned = dict(payload)
    claimed = unsigned.pop("input_sha256")
    if _digest(claimed, "input sha256") != hashlib.sha256(
        _canonical_bytes(unsigned)
    ).hexdigest():
        raise SemanticQualityV3Error("v3 input hash binding is invalid")


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise SemanticQualityV3Error("v3 value is not canonicalizable") from error


def _canonical_cursor(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SemanticQualityV3Error(f"v3 {name} cursor is invalid")
    try:
        parsed = json.loads(value, object_pairs_hook=_no_duplicate_keys)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise SemanticQualityV3Error(f"v3 {name} cursor is invalid") from error
    canonical = json.dumps(
        parsed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if canonical != value:
        raise SemanticQualityV3Error(f"v3 {name} cursor must be canonical")
    return value


def _text_array(
    value: object, name: str, minimum: int, maximum: int
) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise SemanticQualityV3Error(f"v3 {name} list is invalid")
    result = tuple(_text(item, name) for item in value)
    if len(result) != len(set(result)):
        raise SemanticQualityV3Error(f"v3 {name} items must be unique")
    return result


def _is_subsequence(values: tuple[str, ...], authority: tuple[str, ...]) -> bool:
    position = 0
    for value in values:
        try:
            position = authority.index(value, position) + 1
        except ValueError:
            return False
    return True


def _digest_array(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_DEPENDENCIES:
        raise SemanticQualityV3Error(f"v3 {name} list is invalid")
    result = tuple(_digest(item, name) for item in value)
    if len(result) != len(set(result)):
        raise SemanticQualityV3Error(f"v3 {name} items must be unique")
    return result


def _bounded_safe_text(value: object, name: str, minimum: int, maximum: int) -> str:
    text = _text(value, name)
    if not minimum <= len(text) <= maximum:
        raise SemanticQualityV3Error(f"v3 {name} length is invalid")
    if (
        any(unicodedata.category(character).startswith("C") for character in text)
        or _looks_like_locator(text)
    ):
        raise SemanticQualityV3Error(f"v3 {name} contains unsafe locator text")
    return text


def _looks_like_locator(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return any(pattern.search(normalized) is not None for pattern in _LOCATOR_PATTERNS)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise SemanticQualityV3Error(f"v3 {name} is invalid")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise SemanticQualityV3Error(f"v3 {name} is invalid")
    try:
        raw = bytes.fromhex(value)
    except ValueError as error:
        raise SemanticQualityV3Error(f"v3 {name} is invalid") from error
    if raw.hex() != value:
        raise SemanticQualityV3Error(f"v3 {name} is invalid")
    return value
