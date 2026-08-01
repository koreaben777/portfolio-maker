from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

from portfolio_maker.infrastructure.policy import (
    contains_hidden_secret_shaped_public_value,
    mask_public_value,
)


_INPUT_KEYS = {
    "version",
    "revision_id",
    "unit_id",
    "stage",
    "input_sha256",
    "dependencies",
    "coverage",
    "targets",
    "child_results",
}
_OUTPUT_KEYS = {
    "version",
    "revision_id",
    "unit_id",
    "stage",
    "input_sha256",
    "dependency_output_sha256s",
    "analysis_method",
    "coverage",
    "results",
    "output_sha256",
}
_NODE_KEYS = {
    "result_kind",
    "node_id",
    "semantic_summary",
    "semantic_roles",
    "topics",
    "purpose_signals",
    "parent_context_signals",
    "independence_signals",
    "triviality_signals",
    "grounding_signal_ids",
    "semantic_disposition",
    "analysis_status",
}
_TARGET_KEYS = {
    "node_id",
    "parent_node_id",
    "kind",
    "display_name",
    "relative_hierarchy",
    "content_fingerprint",
    "analysis_status",
    "semantic_disposition",
    "roles",
    "signals",
    "skip_reason",
    "evidence_ids",
    "coverage",
    "target_projection_contract",
    "direct_child_topology",
}
_DIRECT_CHILD_TOPOLOGY_KEYS = {
    "direct_child_count",
    "direct_child_ids_sha256",
    "sample_child_node_ids",
    "sample_complete",
}
_AGGREGATE_KEYS = {
    "result_kind",
    "aggregate_id",
    "semantic_summary",
    "semantic_roles",
    "topics",
    "purpose_signals",
    "parent_context_signals",
    "independence_signals",
    "triviality_signals",
    "grounding_signal_ids",
    "member_node_ids_sha256",
    "member_count",
}
_DEPENDENCY_KEYS = {"unit_id", "output_sha256"}
_CHILD_RESULT_KEYS = {"unit_id", "output_sha256", "coverage", "results"}
_BASE_COVERAGE_KEYS = {"node_count", "node_ids_sha256"}
_CAPSULE_COVERAGE_KEYS = {
    *_BASE_COVERAGE_KEYS,
    "content_node_count",
    "structural_only_count",
    "failed_count",
}
_STAGES = {"leaf", "reduce", "directory", "root"}
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9:._-]+")
_URL = re.compile(r"(?i)(?:https?|file)://")
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
_UNC_PATH = re.compile(r"(?<![A-Za-z0-9])\\\\[^\\\s]+\\[^\\\s]+")
_POSIX_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s/]+(?:/[^\s/]+)*)")
_RELATIVE_PATH = re.compile(
    r"(?i)(?:^|[\s(\"'])"
    r"(?:\.{1,2}[\\/]|[A-Za-z0-9_.-]+[\\/])"
    r"[^\s\"')]*\.[A-Za-z0-9]{1,12}(?:$|[\s\"')])"
)
_FILENAME_LOCATOR = re.compile(
    r"(?i)(?:^|[\s(\"'])[^/\\\s\"')]+\."
    r"(?:md|py|js|ts|tsx|jsx|json|ya?ml|toml|pdf|docx|xlsx|pptx|"
    r"csv|txt|html|css|sql|db)(?:$|[\s\"')])"
)
_CREDENTIAL_LOCATOR = re.compile(
    r"(?i)(?:git@[^:\s]+:[^\s]+|ssh://|portfolio\.db|\.portfolio-maker)"
)
_PLACEHOLDERS = {
    "indexed content",
    "indexed file content",
    "indexed directory structure",
    "approved semantic source root",
    "managed content has no extractable topic",
    "combines child signals",
}
_ENGLISH_PREDICATE = re.compile(
    r"(?i)\b(?:is|are|has|have|provides?|supports?|builds?|connects?|"
    r"validates?|combines?|organizes?|serves?|uses?|enables?|delivers?|"
    r"implements?|retrieves?|answers?|creates?|preserves?)\b"
)
_KOREAN_PREDICATE = re.compile(
    r"(?:이다|한다|된다|있다|없다|제공한다|지원한다|구성한다|"
    r"검증한다|연결한다|보존한다|답한다|검색한다|분리된다|다)"
    r"(?:[.!?。！？])?$"
)


class SemanticQualityError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSemanticNodeResult:
    node_id: str
    semantic_summary: str
    semantic_roles: tuple[str, ...]
    topics: tuple[str, ...]
    purpose_signals: tuple[str, ...]
    parent_context_signals: tuple[str, ...]
    independence_signals: tuple[str, ...]
    triviality_signals: tuple[str, ...]
    grounding_signal_ids: tuple[str, ...]
    semantic_disposition: str
    analysis_status: str


@dataclass(frozen=True)
class ValidatedSemanticAggregateResult:
    aggregate_id: str
    semantic_summary: str
    semantic_roles: tuple[str, ...]
    topics: tuple[str, ...]
    purpose_signals: tuple[str, ...]
    parent_context_signals: tuple[str, ...]
    independence_signals: tuple[str, ...]
    triviality_signals: tuple[str, ...]
    grounding_signal_ids: tuple[str, ...]
    member_node_ids_sha256: str
    member_count: int


@dataclass(frozen=True)
class ValidatedSemanticWorkOutput:
    version: int
    revision_id: str
    unit_id: str
    stage: str
    input_sha256: str
    dependency_output_sha256s: tuple[str, ...]
    analysis_method: str
    coverage_node_count: int
    coverage_node_ids_sha256: str
    output_sha256: str
    node_results: tuple[ValidatedSemanticNodeResult, ...]
    aggregate_results: tuple[ValidatedSemanticAggregateResult, ...]

    @property
    def primary_summary(self) -> str:
        if self.node_results:
            return self.node_results[0].semantic_summary
        if self.aggregate_results:
            return self.aggregate_results[0].semantic_summary
        return ""


def validate_semantic_work_output(
    input_payload: dict[str, object],
    output_payload: dict[str, object],
) -> ValidatedSemanticWorkOutput:
    _validate_input_envelope(input_payload)
    _validate_output_envelope(input_payload, output_payload)
    targets = _validated_targets(input_payload)
    child_result_ids = _validated_child_result_ids(input_payload)
    raw_results = output_payload["results"]
    assert type(raw_results) is list

    if input_payload["stage"] == "reduce":
        if len(raw_results) != 1:
            raise SemanticQualityError("semantic result coverage is invalid")
        aggregate = _validate_aggregate_result(
            raw_results[0],
            child_result_ids,
            input_unit_id=str(input_payload["unit_id"]),
        )
        node_results: tuple[ValidatedSemanticNodeResult, ...] = ()
        aggregate_results = (aggregate,)
    else:
        expected_ids = tuple(str(target["node_id"]) for target in targets)
        raw_ids = tuple(
            result.get("node_id")
            if type(result) is dict and result.get("result_kind") == "node"
            else None
            for result in raw_results
        )
        if raw_ids != expected_ids or len(set(raw_ids)) != len(raw_ids):
            raise SemanticQualityError("semantic result coverage is invalid")
        node_results = tuple(
            _validate_node_result(
                result,
                target=target,
                direct_dependency_ids=child_result_ids,
            )
            for result, target in zip(raw_results, targets, strict=True)
        )
        aggregate_results = ()

    coverage = output_payload["coverage"]
    assert type(coverage) is dict
    return ValidatedSemanticWorkOutput(
        version=2,
        revision_id=str(output_payload["revision_id"]),
        unit_id=str(output_payload["unit_id"]),
        stage=str(output_payload["stage"]),
        input_sha256=str(output_payload["input_sha256"]),
        dependency_output_sha256s=tuple(
            str(value)
            for value in output_payload["dependency_output_sha256s"]
        ),
        analysis_method="codex-semantic-v2",
        coverage_node_count=int(coverage["node_count"]),
        coverage_node_ids_sha256=str(coverage["node_ids_sha256"]),
        output_sha256=str(output_payload["output_sha256"]),
        node_results=node_results,
        aggregate_results=aggregate_results,
    )


def _validate_input_envelope(input_payload: dict[str, object]) -> None:
    if type(input_payload) is not dict or set(input_payload) != _INPUT_KEYS:
        raise SemanticQualityError("semantic input is invalid")
    unsigned = dict(input_payload)
    input_sha256 = unsigned.pop("input_sha256", None)
    if (
        input_payload.get("version") != 2
        or not _is_identifier(input_payload.get("revision_id"))
        or not _is_identifier(input_payload.get("unit_id"))
        or input_payload.get("stage") not in _STAGES
        or not _is_sha256(input_sha256)
        or _sha256(unsigned) != input_sha256
        or not _is_coverage(input_payload.get("coverage"))
        or type(input_payload.get("dependencies")) is not list
        or type(input_payload.get("targets")) is not list
        or type(input_payload.get("child_results")) is not list
    ):
        raise SemanticQualityError("semantic input is invalid")
    dependencies = input_payload["dependencies"]
    child_results = input_payload["child_results"]
    if (
        len(dependencies) != len(child_results)
        or input_payload["stage"] == "reduce"
        and input_payload["targets"]
        or input_payload["stage"] != "reduce"
        and not input_payload["targets"]
    ):
        raise SemanticQualityError("semantic input is invalid")
    for dependency, child in zip(dependencies, child_results, strict=True):
        if (
            type(dependency) is not dict
            or set(dependency) != _DEPENDENCY_KEYS
            or not _is_identifier(dependency.get("unit_id"))
            or not _is_sha256(dependency.get("output_sha256"))
            or type(child) is not dict
            or set(child) != _CHILD_RESULT_KEYS
            or child.get("unit_id") != dependency["unit_id"]
            or child.get("output_sha256") != dependency["output_sha256"]
            or not _is_coverage(child.get("coverage"))
            or type(child.get("results")) is not list
        ):
            raise SemanticQualityError("semantic dependency input is invalid")


def _validate_output_envelope(
    input_payload: dict[str, object],
    output_payload: dict[str, object],
) -> None:
    if type(output_payload) is not dict or set(output_payload) != _OUTPUT_KEYS:
        raise SemanticQualityError("semantic output is invalid")
    unsigned = dict(output_payload)
    output_sha256 = unsigned.pop("output_sha256", None)
    expected_dependency_hashes = [
        dependency["output_sha256"]
        for dependency in input_payload["dependencies"]
    ]
    if (
        output_payload.get("version") != 2
        or output_payload.get("revision_id") != input_payload["revision_id"]
        or output_payload.get("unit_id") != input_payload["unit_id"]
        or output_payload.get("stage") != input_payload["stage"]
        or output_payload.get("input_sha256") != input_payload["input_sha256"]
        or output_payload.get("dependency_output_sha256s")
        != expected_dependency_hashes
        or output_payload.get("analysis_method") != "codex-semantic-v2"
        or output_payload.get("coverage") != input_payload["coverage"]
        or type(output_payload.get("results")) is not list
    ):
        raise SemanticQualityError("semantic output is invalid")
    if not _is_sha256(output_sha256) or _sha256(unsigned) != output_sha256:
        raise SemanticQualityError("semantic output hash is invalid")


def _validated_targets(
    input_payload: dict[str, object],
) -> tuple[dict[str, object], ...]:
    raw_targets = input_payload["targets"]
    assert type(raw_targets) is list
    targets: list[dict[str, object]] = []
    seen: set[str] = set()
    for target in raw_targets:
        if (
            type(target) is not dict
            or set(target) != _TARGET_KEYS
            or target.get("semantic_disposition") != "analysis_required"
            or target.get("kind") not in {"file", "directory", "source"}
            or not _is_identifier(target.get("node_id"))
            or type(target.get("signals")) is not list
            or not _is_nonempty_text(target.get("analysis_status"))
            or target["node_id"] in seen
        ):
            raise SemanticQualityError("semantic target is invalid")
        _validate_direct_child_topology(target)
        seen.add(str(target["node_id"]))
        targets.append(target)
    return tuple(targets)


def _validated_child_result_ids(
    input_payload: dict[str, object],
) -> tuple[str, ...]:
    raw_children = input_payload["child_results"]
    assert type(raw_children) is list
    result_ids: list[str] = []
    for child in raw_children:
        assert type(child) is dict
        raw_results = child["results"]
        assert type(raw_results) is list
        if not raw_results:
            raise SemanticQualityError("semantic dependency result is invalid")
        for result in raw_results:
            if type(result) is not dict:
                raise SemanticQualityError("semantic dependency result is invalid")
            kind = result.get("result_kind")
            if kind == "node":
                if set(result) != _NODE_KEYS or not _is_identifier(
                    result.get("node_id")
                ):
                    raise SemanticQualityError(
                        "semantic dependency result is invalid"
                    )
                result_id = str(result["node_id"])
            elif kind == "aggregate":
                if set(result) != _AGGREGATE_KEYS or not _is_identifier(
                    result.get("aggregate_id")
                ):
                    raise SemanticQualityError(
                        "semantic dependency result is invalid"
                    )
                result_id = str(result["aggregate_id"])
            else:
                raise SemanticQualityError("semantic dependency result is invalid")
            if result_id in result_ids:
                raise SemanticQualityError("semantic dependency result is invalid")
            result_ids.append(result_id)
    return tuple(result_ids)


def _validate_node_result(
    raw: object,
    *,
    target: dict[str, object],
    direct_dependency_ids: tuple[str, ...],
) -> ValidatedSemanticNodeResult:
    if type(raw) is not dict or set(raw) != _NODE_KEYS:
        raise SemanticQualityError("semantic node result schema is invalid")
    if (
        raw.get("result_kind") != "node"
        or raw.get("node_id") != target["node_id"]
        or raw.get("semantic_disposition") != "analyzed"
        or raw.get("analysis_status") != target["analysis_status"]
    ):
        raise SemanticQualityError("semantic node result identity is invalid")
    kind = str(target["kind"])
    semantic_summary = _validated_summary(
        raw.get("semantic_summary"),
        maximum=600 if kind == "file" else 800,
        display_name=target.get("display_name"),
    )
    arrays = _validated_semantic_arrays(raw)
    own_signal_ids = _target_signal_ids(target)
    allowed_grounding = (
        own_signal_ids
        if kind == "file"
        else own_signal_ids + direct_dependency_ids
    )
    grounding = _validated_grounding(
        raw.get("grounding_signal_ids"),
        allowed_grounding,
    )
    _reject_token_only(
        semantic_summary,
        arrays["topics"],
        arrays["purpose_signals"],
        arrays["parent_context_signals"],
        arrays["independence_signals"],
        arrays["triviality_signals"],
    )
    return ValidatedSemanticNodeResult(
        node_id=str(raw["node_id"]),
        semantic_summary=semantic_summary,
        semantic_roles=arrays["semantic_roles"],
        topics=arrays["topics"],
        purpose_signals=arrays["purpose_signals"],
        parent_context_signals=arrays["parent_context_signals"],
        independence_signals=arrays["independence_signals"],
        triviality_signals=arrays["triviality_signals"],
        grounding_signal_ids=grounding,
        semantic_disposition="analyzed",
        analysis_status=str(raw["analysis_status"]),
    )


def _validate_direct_child_topology(target: dict[str, object]) -> None:
    topology = target.get("direct_child_topology")
    if (
        target.get("target_projection_contract")
        != "direct-child-attestation-v1"
        or type(topology) is not dict
        or set(topology) != _DIRECT_CHILD_TOPOLOGY_KEYS
        or type(topology.get("direct_child_count")) is not int
        or int(topology["direct_child_count"]) < 0
        or not _is_sha256(topology.get("direct_child_ids_sha256"))
        or type(topology.get("sample_complete")) is not bool
    ):
        raise SemanticQualityError("semantic target topology is invalid")
    sample = _validated_identifier_array(
        topology.get("sample_child_node_ids"),
        maximum=64,
        field="target child sample IDs",
    )
    count = int(topology["direct_child_count"])
    if (
        len(sample) > count
        or bool(topology["sample_complete"]) != (count <= len(sample))
        or topology["sample_complete"]
        and len(sample) != count
        or topology["sample_complete"]
        and topology["direct_child_ids_sha256"] != _sha256(list(sample))
        or not topology["sample_complete"]
        and len(sample) != 64
    ):
        raise SemanticQualityError("semantic target topology is invalid")


def _validate_aggregate_result(
    raw: object,
    direct_dependency_ids: tuple[str, ...],
    *,
    input_unit_id: str,
) -> ValidatedSemanticAggregateResult:
    if type(raw) is not dict or set(raw) != _AGGREGATE_KEYS:
        raise SemanticQualityError("semantic aggregate result schema is invalid")
    if (
        raw.get("result_kind") != "aggregate"
        or raw.get("aggregate_id") != f"aggregate:{input_unit_id}"
        or type(raw.get("member_count")) is not int
        or int(raw["member_count"]) <= 0
        or raw["member_count"] != len(direct_dependency_ids)
        or raw.get("member_node_ids_sha256")
        != _sha256(list(direct_dependency_ids))
    ):
        raise SemanticQualityError("semantic aggregate result identity is invalid")
    semantic_summary = _validated_summary(
        raw.get("semantic_summary"),
        maximum=800,
        display_name=None,
    )
    arrays = _validated_semantic_arrays(raw)
    grounding = _validated_grounding(
        raw.get("grounding_signal_ids"),
        direct_dependency_ids,
    )
    _reject_token_only(
        semantic_summary,
        arrays["topics"],
        arrays["purpose_signals"],
        arrays["parent_context_signals"],
        arrays["independence_signals"],
        arrays["triviality_signals"],
    )
    return ValidatedSemanticAggregateResult(
        aggregate_id=str(raw["aggregate_id"]),
        semantic_summary=semantic_summary,
        semantic_roles=arrays["semantic_roles"],
        topics=arrays["topics"],
        purpose_signals=arrays["purpose_signals"],
        parent_context_signals=arrays["parent_context_signals"],
        independence_signals=arrays["independence_signals"],
        triviality_signals=arrays["triviality_signals"],
        grounding_signal_ids=grounding,
        member_node_ids_sha256=str(raw["member_node_ids_sha256"]),
        member_count=int(raw["member_count"]),
    )


def _validated_semantic_arrays(
    raw: dict[str, object],
) -> dict[str, tuple[str, ...]]:
    return {
        "semantic_roles": _validated_text_array(
            raw.get("semantic_roles"), maximum=16, field="semantic roles"
        ),
        "topics": _validated_text_array(
            raw.get("topics"), maximum=24, field="topics"
        ),
        "purpose_signals": _validated_text_array(
            raw.get("purpose_signals"), maximum=16, field="purpose signals"
        ),
        "parent_context_signals": _validated_text_array(
            raw.get("parent_context_signals"),
            maximum=16,
            field="parent context signals",
        ),
        "independence_signals": _validated_text_array(
            raw.get("independence_signals"),
            maximum=16,
            field="independence signals",
        ),
        "triviality_signals": _validated_text_array(
            raw.get("triviality_signals"),
            maximum=16,
            field="triviality signals",
        ),
    }


def _validated_summary(
    value: object,
    *,
    maximum: int,
    display_name: object,
) -> str:
    normalized = _normalized_phrase(value)
    if (
        normalized in _PLACEHOLDERS
        or normalized.startswith("summary for ")
        or normalized == _normalized_phrase(display_name)
    ):
        raise SemanticQualityError("semantic summary is a placeholder")
    text = _validated_semantic_text(value)
    if len(text) < 20 or len(text) > maximum:
        raise SemanticQualityError("semantic summary bounds are invalid")
    return text


def _validated_text_array(
    value: object,
    *,
    maximum: int,
    field: str,
) -> tuple[str, ...]:
    if type(value) is not list or len(value) > maximum:
        raise SemanticQualityError(f"{field} are invalid")
    values = tuple(_validated_semantic_text(item) for item in value)
    if len(set(values)) != len(values):
        raise SemanticQualityError(f"{field} are invalid")
    return values


def _validated_grounding(
    value: object,
    allowed: tuple[str, ...],
) -> tuple[str, ...]:
    grounding = _validated_identifier_array(
        value,
        maximum=64,
        field="grounding",
    )
    if not grounding or any(identifier not in allowed for identifier in grounding):
        raise SemanticQualityError("semantic grounding is invalid")
    return grounding


def _validated_identifier_array(
    value: object,
    *,
    maximum: int | None,
    field: str,
) -> tuple[str, ...]:
    if (
        type(value) is not list
        or maximum is not None
        and len(value) > maximum
        or not all(_is_identifier(item) for item in value)
    ):
        raise SemanticQualityError(f"semantic {field} are invalid")
    values = tuple(str(item) for item in value)
    if len(set(values)) != len(values):
        raise SemanticQualityError(f"semantic {field} are invalid")
    return values


def _target_signal_ids(target: dict[str, object]) -> tuple[str, ...]:
    raw_signals = target["signals"]
    assert type(raw_signals) is list
    signal_ids: list[str] = []
    for signal in raw_signals:
        if (
            type(signal) is not dict
            or not _is_identifier(signal.get("signal_id"))
            or signal["signal_id"] in signal_ids
        ):
            raise SemanticQualityError("semantic target signal is invalid")
        signal_ids.append(str(signal["signal_id"]))
    return tuple(signal_ids)


def _validated_semantic_text(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise SemanticQualityError("semantic text is unsafe")
    normalized = " ".join(value.split())
    if normalized != value:
        raise SemanticQualityError("semantic text is unsafe")
    normalized_phrase = _normalized_phrase(value)
    if (
        normalized_phrase in _PLACEHOLDERS
        or normalized_phrase.startswith("summary for ")
    ):
        raise SemanticQualityError("semantic text is a placeholder")
    if (
        any(unicodedata.category(character).startswith("C") for character in value)
        or "[REDACTED]" in value
        or contains_hidden_secret_shaped_public_value(value)
        or mask_public_value(value) != value
        or _looks_like_locator(value)
    ):
        raise SemanticQualityError("semantic text is unsafe")
    return value


def _looks_like_locator(value: str) -> bool:
    return any(
        pattern.search(value) is not None
        for pattern in (
            _URL,
            _WINDOWS_PATH,
            _UNC_PATH,
            _POSIX_PATH,
            _RELATIVE_PATH,
            _FILENAME_LOCATOR,
            _CREDENTIAL_LOCATOR,
        )
    )


def _reject_token_only(
    summary: str,
    _topics: tuple[str, ...],
    *signal_arrays: tuple[str, ...],
) -> None:
    if any(signal_arrays):
        return
    predicate = (
        _ENGLISH_PREDICATE.search(summary) is not None
        or _KOREAN_PREDICATE.search(summary) is not None
    )
    if not predicate:
        raise SemanticQualityError("semantic summary is token-only")


def _normalized_phrase(value: object) -> str:
    if type(value) is not str:
        return ""
    return " ".join(
        re.sub(r"[^\w]+", " ", value.casefold(), flags=re.UNICODE).split()
    )


def _is_nonempty_text(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


def _is_identifier(value: object) -> bool:
    return (
        _is_nonempty_text(value)
        and _SAFE_IDENTIFIER.fullmatch(str(value)) is not None
    )


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _is_coverage(value: object) -> bool:
    keys = set(value) if type(value) is dict else set()
    return (
        type(value) is dict
        and (
            keys == _BASE_COVERAGE_KEYS
            or keys == _CAPSULE_COVERAGE_KEYS
        )
        and type(value.get("node_count")) is int
        and int(value["node_count"]) >= 0
        and all(
            type(value.get(field)) is int and int(value[field]) >= 0
            for field in keys - _BASE_COVERAGE_KEYS
        )
        and _is_sha256(value.get("node_ids_sha256"))
    )


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()
