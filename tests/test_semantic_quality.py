from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from portfolio_maker.application.semantic_quality import (
    SemanticQualityError,
    ValidatedSemanticAggregateResult,
    ValidatedSemanticNodeResult,
    validate_semantic_work_output,
)
from portfolio_maker.application.semantic_quality_v3 import (
    SEMANTIC_INPUT_SCHEMA_V3,
    SEMANTIC_OUTPUT_SCHEMA_V3,
    SemanticQualityV3Error,
    validate_v3_semantic_output,
)
from portfolio_maker.application.semantic_frontier_v3 import transition_frontier
from portfolio_maker.domain.semantic_frontier import (
    MAX_UNIT_BYTES,
    FrontierAction,
)


NODE_RESULT_KEYS = {
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
AGGREGATE_RESULT_KEYS = {
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


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _coverage(*node_ids: str) -> dict[str, object]:
    return {
        "node_count": len(node_ids),
        "node_ids_sha256": _sha256(list(node_ids)),
    }


def _capsule(
    *,
    node_id: str = "node-file",
    kind: str = "file",
    signal_id: str = "signal-file-purpose",
    analysis_status: str = "pending",
    child_node_ids: list[str] | None = None,
) -> dict[str, object]:
    direct_children = sorted(child_node_ids or [])
    sample = direct_children[:64]
    return {
        "node_id": node_id,
        "parent_node_id": "node-parent",
        "kind": kind,
        "display_name": "component",
        "relative_hierarchy": "component",
        "content_fingerprint": "a" * 64,
        "analysis_status": analysis_status,
        "semantic_disposition": "analysis_required",
        "roles": ["component"],
        "signals": [
            {
                "signal_id": signal_id,
                "kind": "content",
                "text": "근거가 있는 기능 목적을 설명한다",
            }
        ],
        "skip_reason": None,
        "evidence_ids": ["evidence-1"],
        "coverage": _coverage(node_id),
        "target_projection_contract": "direct-child-attestation-v1",
        "direct_child_topology": {
            "direct_child_count": len(direct_children),
            "direct_child_ids_sha256": _sha256(direct_children),
            "sample_child_node_ids": sample,
            "sample_complete": len(direct_children) <= len(sample),
        },
    }


def valid_input_fixture(
    *,
    target: dict[str, object] | None = None,
    stage: str = "leaf",
    child_results: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    targets = [] if stage == "reduce" else [target or _capsule()]
    children = child_results or []
    coverage = (
        _coverage("node-file")
        if stage != "reduce"
        else {
            "node_count": sum(
                int(child["coverage"]["node_count"]) for child in children
            ),
            "node_ids_sha256": _sha256(
                [child["coverage"]["node_ids_sha256"] for child in children]
            ),
        }
    )
    payload: dict[str, object] = {
        "version": 2,
        "revision_id": "revision-test",
        "unit_id": "unit-000001",
        "stage": stage,
        "dependencies": [
            {
                "unit_id": str(child["unit_id"]),
                "output_sha256": str(child["output_sha256"]),
            }
            for child in children
        ],
        "coverage": coverage,
        "targets": targets,
        "child_results": children,
    }
    return {**payload, "input_sha256": _sha256(payload)}


def valid_node_result(
    *,
    target: dict[str, object] | None = None,
    summary: str = "이 구성요소는 검증된 근거를 바탕으로 제품 기능을 제공한다",
    grounding_signal_ids: list[str] | None = None,
) -> dict[str, object]:
    capsule = target or _capsule()
    return {
        "result_kind": "node",
        "node_id": capsule["node_id"],
        "semantic_summary": summary,
        "semantic_roles": ["application-component"],
        "topics": ["근거 기반 기능"],
        "purpose_signals": ["검증된 근거를 제품 기능에 연결한다"],
        "parent_context_signals": [],
        "independence_signals": [],
        "triviality_signals": [],
        "grounding_signal_ids": grounding_signal_ids
        or [capsule["signals"][0]["signal_id"]],
        "semantic_disposition": "analyzed",
        "analysis_status": capsule["analysis_status"],
    }


def valid_output_fixture(
    input_payload: dict[str, object] | None = None,
    *,
    results: list[object] | None = None,
) -> dict[str, object]:
    work_input = input_payload or valid_input_fixture()
    output: dict[str, object] = {
        "version": 2,
        "revision_id": work_input["revision_id"],
        "unit_id": work_input["unit_id"],
        "stage": work_input["stage"],
        "input_sha256": work_input["input_sha256"],
        "dependency_output_sha256s": [
            dependency["output_sha256"]
            for dependency in work_input["dependencies"]
        ],
        "analysis_method": "codex-semantic-v2",
        "coverage": work_input["coverage"],
        "results": results
        if results is not None
        else [valid_node_result(target=work_input["targets"][0])],
    }
    return {**output, "output_sha256": _sha256(output)}


def _rehash_output(output: dict[str, object]) -> dict[str, object]:
    unsigned = dict(output)
    unsigned.pop("output_sha256", None)
    output["output_sha256"] = _sha256(unsigned)
    return output


def _dependency_result(
    *,
    unit_id: str = "unit-000000",
    node_id: str = "node-child",
) -> dict[str, object]:
    result = valid_node_result(
        target=_capsule(node_id=node_id, signal_id=f"signal-{node_id}")
    )
    return {
        "unit_id": unit_id,
        "output_sha256": "b" * 64,
        "coverage": _coverage(node_id),
        "results": [result],
    }


@pytest.mark.parametrize(
    "summary",
    (
        "Indexed content.",
        " indexed   FILE content ",
        "INDEXED-DIRECTORY-STRUCTURE!!!",
        "Approved semantic source root.",
        "Managed content has no extractable topic.",
        "Combines child signals.",
        "Summary for README.",
    ),
)
def test_semantic_quality_rejects_placeholder_summaries(summary: str) -> None:
    work_input = valid_input_fixture()
    output = valid_output_fixture(
        work_input,
        results=[valid_node_result(summary=summary)],
    )

    with pytest.raises(SemanticQualityError, match="placeholder"):
        validate_semantic_work_output(work_input, output)


def test_semantic_quality_rejects_unknown_grounding_signal() -> None:
    work_input = valid_input_fixture()
    output = valid_output_fixture(
        work_input,
        results=[
            valid_node_result(grounding_signal_ids=["unknown-grounding"])
        ],
    )

    with pytest.raises(SemanticQualityError, match="grounding"):
        validate_semantic_work_output(work_input, output)


def test_semantic_quality_rejects_cross_target_grounding() -> None:
    target = _capsule(kind="file")
    work_input = valid_input_fixture(
        target=target,
        child_results=[_dependency_result()],
    )
    output = valid_output_fixture(
        work_input,
        results=[
            valid_node_result(
                target=target,
                grounding_signal_ids=["node-child"],
            )
        ],
    )

    with pytest.raises(SemanticQualityError, match="grounding"):
        validate_semantic_work_output(work_input, output)


@pytest.mark.parametrize("mutation", ("extra", "missing", "wrong_type"))
def test_semantic_quality_rejects_inexact_node_schema(mutation: str) -> None:
    work_input = valid_input_fixture()
    result = valid_node_result()
    if mutation == "extra":
        result["extra"] = True
    elif mutation == "missing":
        result.pop("topics")
    else:
        result["topics"] = "not-an-array"
    output = valid_output_fixture(work_input, results=[result])

    with pytest.raises(SemanticQualityError, match="node result schema|topics"):
        validate_semantic_work_output(work_input, output)


def test_semantic_quality_accepts_direct_child_grounding_for_directory() -> None:
    target = _capsule(kind="directory", node_id="node-directory")
    work_input = valid_input_fixture(
        target=target,
        stage="directory",
        child_results=[_dependency_result()],
    )
    output = valid_output_fixture(
        work_input,
        results=[
            valid_node_result(
                target=target,
                grounding_signal_ids=["node-child"],
            )
        ],
    )

    validated = validate_semantic_work_output(work_input, output)

    assert validated.node_results[0].grounding_signal_ids == ("node-child",)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing", "coverage"),
        ("extra", "coverage"),
        ("duplicate", "coverage"),
        ("wrong_kind", "coverage"),
    ),
)
def test_semantic_quality_rejects_inexact_result_coverage(
    mutation: str,
    message: str,
) -> None:
    work_input = valid_input_fixture()
    result = valid_node_result()
    if mutation == "missing":
        results: list[object] = []
    elif mutation == "extra":
        extra = {**result, "node_id": "node-extra"}
        results = [result, extra]
    elif mutation == "duplicate":
        results = [result, copy.deepcopy(result)]
    else:
        results = [{**result, "result_kind": "aggregate"}]
    output = valid_output_fixture(work_input, results=results)

    with pytest.raises(SemanticQualityError, match=message):
        validate_semantic_work_output(work_input, output)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("revision_id", "different-revision"),
        ("unit_id", "unit-999999"),
        ("stage", "root"),
        ("input_sha256", "f" * 64),
        ("dependency_output_sha256s", ["e" * 64]),
        ("analysis_method", "generic"),
        ("coverage", _coverage("different")),
    ),
)
def test_semantic_quality_rejects_output_envelope_mismatch(
    field: str,
    value: object,
) -> None:
    work_input = valid_input_fixture()
    output = valid_output_fixture(work_input)
    output[field] = value
    _rehash_output(output)

    with pytest.raises(SemanticQualityError, match="output"):
        validate_semantic_work_output(work_input, output)


def test_semantic_quality_rejects_output_self_hash_mismatch() -> None:
    work_input = valid_input_fixture()
    output = valid_output_fixture(work_input)
    output["output_sha256"] = "f" * 64

    with pytest.raises(SemanticQualityError, match="hash"):
        validate_semantic_work_output(work_input, output)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("semantic_summary", "too short"),
        ("semantic_summary", "문" * 601),
        ("semantic_roles", [f"role-{index}" for index in range(17)]),
        ("topics", [f"topic-{index}" for index in range(25)]),
        ("purpose_signals", [f"목적 신호 {index}이다" for index in range(17)]),
        ("grounding_signal_ids", ["signal-file-purpose"] * 2),
    ),
)
def test_semantic_quality_rejects_bounds_and_duplicates(
    field: str,
    value: object,
) -> None:
    work_input = valid_input_fixture()
    result = valid_node_result()
    result[field] = value
    output = valid_output_fixture(work_input, results=[result])

    with pytest.raises(SemanticQualityError):
        validate_semantic_work_output(work_input, output)


@pytest.mark.parametrize(
    "field",
    (
        "semantic_roles",
        "topics",
        "purpose_signals",
        "parent_context_signals",
        "independence_signals",
        "triviality_signals",
    ),
)
def test_semantic_quality_rejects_duplicate_text_and_id_arrays(
    field: str,
) -> None:
    target = _capsule()
    work_input = valid_input_fixture(target=target)
    result = valid_node_result(target=target)
    result[field] = ["duplicate-value", "duplicate-value"]
    output = valid_output_fixture(work_input, results=[result])

    with pytest.raises(SemanticQualityError):
        validate_semantic_work_output(work_input, output)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("direct_child_count", 1),
        ("direct_child_ids_sha256", "f" * 64),
        ("sample_child_node_ids", ["node-child", "node-child"]),
        ("sample_complete", False),
    ),
)
def test_semantic_quality_rejects_tampered_complete_topology_projection(
    field: str,
    value: object,
) -> None:
    target = _capsule()
    target["direct_child_topology"][field] = value
    work_input = valid_input_fixture(target=target)
    output = valid_output_fixture(
        work_input,
        results=[valid_node_result(target=target)],
    )

    with pytest.raises(SemanticQualityError, match="target"):
        validate_semantic_work_output(work_input, output)


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "Bearer synthetic-credential-value",
        "설명에 /tmp/private-item 경로가 포함된다",
        "설명에 file:///tmp/private-item 경로가 포함된다",
        "설명에 https://example.invalid/private 주소가 포함된다",
        "설명에 .portfolio-maker/managed.json 경로가 포함된다",
        "설명에 private\\folder\\item.txt 경로가 포함된다",
        "설명에 README.md 파일명이 포함된다",
        "설명에 제어\x00문자가 포함된다",
    ),
)
@pytest.mark.parametrize(
    "field",
    (
        "semantic_summary",
        "semantic_roles",
        "topics",
        "purpose_signals",
        "parent_context_signals",
        "independence_signals",
        "triviality_signals",
    ),
)
def test_semantic_quality_rejects_unsafe_text_in_every_family(
    field: str,
    unsafe_value: str,
) -> None:
    work_input = valid_input_fixture()
    result = valid_node_result()
    result[field] = unsafe_value if field == "semantic_summary" else [unsafe_value]
    output = valid_output_fixture(work_input, results=[result])

    with pytest.raises(SemanticQualityError, match="unsafe"):
        validate_semantic_work_output(work_input, output)


def test_semantic_quality_rejects_sorted_token_dump() -> None:
    work_input = valid_input_fixture()
    result = valid_node_result(
        summary=(
            "api backend database deploy documentation frontend model "
            "portfolio python rag"
        )
    )
    result.update(
        {
            "topics": [
                "api",
                "backend",
                "database",
                "deploy",
                "documentation",
            ],
            "purpose_signals": [],
        }
    )
    output = valid_output_fixture(work_input, results=[result])

    with pytest.raises(SemanticQualityError, match="token-only"):
        validate_semantic_work_output(work_input, output)


def test_semantic_quality_rejects_predicate_free_dump_with_unrelated_topic() -> None:
    work_input = valid_input_fixture()
    result = valid_node_result(summary="alpha beta gamma delta epsilon")
    result.update(
        {
            "topics": ["unrelated-topic"],
            "purpose_signals": [],
            "parent_context_signals": [],
            "independence_signals": [],
            "triviality_signals": [],
        }
    )
    output = valid_output_fixture(work_input, results=[result])

    with pytest.raises(SemanticQualityError, match="token-only"):
        validate_semantic_work_output(work_input, output)


def test_semantic_quality_rejects_placeholder_signal_text() -> None:
    work_input = valid_input_fixture()
    result = valid_node_result()
    result["purpose_signals"] = ["Combines child signals."]
    output = valid_output_fixture(work_input, results=[result])

    with pytest.raises(SemanticQualityError, match="placeholder"):
        validate_semantic_work_output(work_input, output)


def test_semantic_quality_accepts_meaningful_korean_without_punctuation() -> None:
    work_input = valid_input_fixture()
    result = valid_node_result(
        summary="이 구성요소는 근거를 검증해 사용자의 포트폴리오를 구성한다"
    )
    output = valid_output_fixture(work_input, results=[result])

    validated = validate_semantic_work_output(work_input, output)

    assert validated.node_results[0].semantic_summary.endswith("구성한다")


def test_reduce_aggregate_requires_exact_schema_and_direct_grounding() -> None:
    child = _dependency_result()
    work_input = valid_input_fixture(stage="reduce", child_results=[child])
    member_ids = ["node-child"]
    aggregate = {
        "result_kind": "aggregate",
        "aggregate_id": "aggregate:unit-000001",
        "semantic_summary": "직접 하위 결과를 결합해 상위 분석에 필요한 근거를 제공한다",
        "semantic_roles": ["evidence-aggregate"],
        "topics": ["근거 집계"],
        "purpose_signals": ["직접 하위 결과를 상위 분석에 전달한다"],
        "parent_context_signals": [],
        "independence_signals": [],
        "triviality_signals": [],
        "grounding_signal_ids": member_ids,
        "member_node_ids_sha256": _sha256(member_ids),
        "member_count": 1,
    }
    output = valid_output_fixture(work_input, results=[aggregate])

    validated = validate_semantic_work_output(work_input, output)

    assert isinstance(
        validated.aggregate_results[0],
        ValidatedSemanticAggregateResult,
    )
    assert validated.aggregate_results[0].member_count == 1

    invalid = copy.deepcopy(output)
    invalid["results"][0]["grounding_signal_ids"] = ["transitive-node"]
    _rehash_output(invalid)
    with pytest.raises(SemanticQualityError, match="grounding"):
        validate_semantic_work_output(work_input, invalid)


@pytest.mark.parametrize(
    "mutation",
    (
        "extra_key",
        "missing_member_count",
        "zero_member_count",
        "wrong_member_hash",
        "wrong_aggregate_id",
        "node_result",
    ),
)
def test_reduce_aggregate_rejects_invalid_shape(mutation: str) -> None:
    child = _dependency_result()
    work_input = valid_input_fixture(stage="reduce", child_results=[child])
    aggregate: dict[str, object] = {
        "result_kind": "aggregate",
        "aggregate_id": "aggregate:unit-000001",
        "semantic_summary": "직접 하위 결과를 결합해 상위 분석에 필요한 근거를 제공한다",
        "semantic_roles": ["evidence-aggregate"],
        "topics": ["근거 집계"],
        "purpose_signals": ["직접 하위 결과를 상위 분석에 전달한다"],
        "parent_context_signals": [],
        "independence_signals": [],
        "triviality_signals": [],
        "grounding_signal_ids": ["node-child"],
        "member_node_ids_sha256": _sha256(["node-child"]),
        "member_count": 1,
    }
    if mutation == "extra_key":
        aggregate["extra"] = True
    elif mutation == "missing_member_count":
        aggregate.pop("member_count")
    elif mutation == "zero_member_count":
        aggregate["member_count"] = 0
    elif mutation == "wrong_member_hash":
        aggregate["member_node_ids_sha256"] = "f" * 64
    elif mutation == "wrong_aggregate_id":
        aggregate["aggregate_id"] = "aggregate:unit-999999"
    else:
        aggregate = valid_node_result()
    output = valid_output_fixture(work_input, results=[aggregate])

    with pytest.raises(SemanticQualityError):
        validate_semantic_work_output(work_input, output)


def test_validated_result_is_immutable_and_detached_from_payload() -> None:
    work_input = valid_input_fixture()
    output = valid_output_fixture(work_input)

    validated = validate_semantic_work_output(work_input, output)
    original_summary = validated.node_results[0].semantic_summary
    output["results"][0]["semantic_summary"] = "호출자가 변경한 값이다"

    assert isinstance(validated.node_results[0], ValidatedSemanticNodeResult)
    assert validated.node_results[0].semantic_summary == original_summary
    assert isinstance(validated.node_results, tuple)
    with pytest.raises(AttributeError):
        validated.node_results[0].semantic_summary = "변경 불가"


def _load_fixture(name: str) -> tuple[dict[str, object], dict[str, object]]:
    fixture_dir = Path(__file__).parent / "fixtures" / "semantic_protocol_v2" / name
    return (
        json.loads((fixture_dir / "input.json").read_text(encoding="utf-8")),
        json.loads((fixture_dir / "output.json").read_text(encoding="utf-8")),
    )


def test_korean_fixtures_preserve_distinct_project_purposes() -> None:
    insurance_input, insurance_output = _load_fixture("insurance_rag_ko")
    portfolio_input, portfolio_output = _load_fixture("portfolio_maker_ko")

    insurance = validate_semantic_work_output(
        insurance_input,
        insurance_output,
    )
    portfolio = validate_semantic_work_output(
        portfolio_input,
        portfolio_output,
    )

    assert insurance.node_results[0].semantic_summary != (
        portfolio.node_results[0].semantic_summary
    )
    assert "보험" in insurance.node_results[0].topics
    assert "포트폴리오" in portfolio.node_results[0].topics


def test_independent_child_fixture_preserves_competition_boundary() -> None:
    work_input, output = _load_fixture("playmcp_independent_child_ko")

    validated = validate_semantic_work_output(work_input, output)

    assert validated.node_results[0].independence_signals
    assert any(
        "공모전" in signal
        for signal in validated.node_results[0].independence_signals
    )


def test_fixture_hashes_are_canonical_and_self_consistent() -> None:
    for name in (
        "insurance_rag_ko",
        "portfolio_maker_ko",
        "playmcp_independent_child_ko",
    ):
        work_input, output = _load_fixture(name)
        unsigned_input = dict(work_input)
        input_sha256 = unsigned_input.pop("input_sha256")
        unsigned_output = dict(output)
        output_sha256 = unsigned_output.pop("output_sha256")

        assert input_sha256 == _sha256(unsigned_input)
        assert output_sha256 == _sha256(unsigned_output)
        assert output["coverage"] == work_input["coverage"]
        assert output["input_sha256"] == input_sha256
        assert set(output["results"][0]) == NODE_RESULT_KEYS


def _v3_input() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": SEMANTIC_INPUT_SCHEMA_V3,
        "revision_id": "revision-v3",
        "run_id": "run-v3",
        "unit_id": "unit-v3",
        "target_node_ids": ["target"],
        "approved_local_signal_ids": ["signal-purpose", "signal-counter"],
        "representatives": [
            {
                "node_id": "target",
                "full_content_sha256": "b" * 64,
                "coverage": "complete",
                "role_reason": "entrypoint",
            }
        ],
        "exposed_child_node_ids": ["child-1", "child-2"],
        "next_child_cursor": '{"after":"child-2"}',
    }
    return {**payload, "input_sha256": _sha256(payload)}


def _v3_output(action: FrontierAction) -> dict[str, object]:
    work_input = _v3_input()
    expanding = action is FrontierAction.EXPAND_CHILDREN
    return {
        "schema_version": SEMANTIC_OUTPUT_SCHEMA_V3,
        "revision_id": work_input["revision_id"],
        "run_id": work_input["run_id"],
        "unit_id": work_input["unit_id"],
        "input_sha256": work_input["input_sha256"],
        "action": action.value,
        "target_node_ids": ["target"],
        "grounding_signal_ids": ["signal-purpose"],
        "representative_digests": ["b" * 64],
        "rationale": "검증된 목적과 실행 진입점 신호를 함께 근거로 경계를 판단한다.",
        "counter_signal_ids": ["signal-counter"],
        "confidence": 0.8,
        "requested_child_node_ids": ["child-2"] if expanding else [],
        "next_child_cursor": work_input["next_child_cursor"] if expanding else None,
    }


@pytest.mark.parametrize("action", tuple(FrontierAction))
def test_v3_quality_accepts_exactly_four_grounded_actions(
    action: FrontierAction,
) -> None:
    """Catches a missing closed action branch or changing the enum contract."""
    validated = validate_v3_semantic_output(_v3_input(), _v3_output(action))

    assert validated.action is action
    assert validated.target_node_ids == ("target",)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("action", "action"),
        ("schema", "schema"),
        ("locator", "schema"),
        ("topology", "schema"),
        ("target", "target"),
        ("grounding", "grounding"),
        ("digest", "representative"),
        ("child", "child"),
        ("sampled", "full-content"),
    ),
)
def test_v3_quality_rejects_unbound_or_invented_output(
    mutation: str, message: str
) -> None:
    """Catches accepting invented topology/locator/evidence or stale bindings."""
    work_input = _v3_input()
    output = _v3_output(FrontierAction.EXPAND_CHILDREN)
    if mutation == "action":
        output["action"] = "complete"
    elif mutation == "schema":
        output["schema_version"] = "semantic-frontier-output-v4"
    elif mutation == "locator":
        output["locator"] = "/private/source"
    elif mutation == "topology":
        output["topology"] = ["child-3"]
    elif mutation == "target":
        output["target_node_ids"] = ["invented-target"]
    elif mutation == "grounding":
        output["grounding_signal_ids"] = ["invented-signal"]
    elif mutation == "digest":
        output["representative_digests"] = ["c" * 64]
    elif mutation == "child":
        output["requested_child_node_ids"] = ["child-3"]
    else:
        work_input["representatives"][0]["coverage"] = "sampled"
        unsigned = dict(work_input)
        unsigned.pop("input_sha256")
        work_input["input_sha256"] = _sha256(unsigned)

    with pytest.raises(SemanticQualityV3Error, match=message):
        validate_v3_semantic_output(work_input, output)


def test_v3_quality_rejects_duplicate_json_keys_and_nonexpansion_smuggling() -> None:
    """Catches parser key collapse and child IDs hidden in a terminal action."""
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    output["requested_child_node_ids"] = ["child-1"]
    with pytest.raises(SemanticQualityV3Error, match="child"):
        validate_v3_semantic_output(work_input, output)

    duplicate = json.dumps(_v3_output(FrontierAction.ACCEPT_BOUNDARY))
    duplicate = duplicate[:-1] + ',"confidence":0.1}'
    with pytest.raises(SemanticQualityV3Error, match="duplicate"):
        validate_v3_semantic_output(work_input, duplicate)


@pytest.mark.parametrize(
    ("action", "field", "expected"),
    (
        (FrontierAction.ACCEPT_BOUNDARY, "accepted_node_ids", ("target",)),
        (FrontierAction.STOP_NON_PROJECT, "stopped_node_ids", ("target",)),
        (FrontierAction.DEFER_ON_DEMAND, "deferred_node_ids", ("target",)),
        (FrontierAction.EXPAND_CHILDREN, "enqueued_child_node_ids", ("child-2",)),
    ),
)
def test_frontier_transition_is_pure_and_closed(
    action: FrontierAction, field: str, expected: tuple[str, ...]
) -> None:
    """Catches conflating one closed action with another state transition."""
    validated = validate_v3_semantic_output(_v3_input(), _v3_output(action))

    transition = transition_frontier(validated, budget_exhausted=False)

    assert transition.action is action
    assert getattr(transition, field) == expected
    assert transition.paused_budget is False
    assert transition.converged is False


def test_budget_exhaustion_defers_expansion_without_stop_or_convergence() -> None:
    """Catches turning a hard budget into a semantic non-project conclusion."""
    validated = validate_v3_semantic_output(
        _v3_input(), _v3_output(FrontierAction.EXPAND_CHILDREN)
    )

    transition = transition_frontier(
        validated,
        budget_exhausted=True,
        resume_cursor='{"after":"child-2"}',
    )

    assert transition.enqueued_child_node_ids == ()
    assert transition.deferred_node_ids == ("child-2",)
    assert transition.stopped_node_ids == ()
    assert transition.paused_budget is True
    assert transition.converged is False


@pytest.mark.parametrize("action", tuple(FrontierAction))
@pytest.mark.parametrize("budget_exhausted", (False, True))
def test_budget_state_is_global_and_separate_from_every_unit_action(
    action: FrontierAction, budget_exhausted: bool
) -> None:
    """Catches terminal unit receipts hiding a globally exhausted frontier budget."""
    validated = validate_v3_semantic_output(_v3_input(), _v3_output(action))
    kwargs: dict[str, object] = {"budget_exhausted": budget_exhausted}
    if budget_exhausted:
        kwargs.update(
            {
                "remaining_deferred_node_ids": ("remaining-node",),
                "resume_cursor": '{"after":"remaining-node"}',
            }
        )

    transition = transition_frontier(validated, **kwargs)

    assert transition.action is action
    assert transition.paused_budget is budget_exhausted
    assert transition.converged is False
    if budget_exhausted:
        assert transition.enqueued_child_node_ids == ()
        assert "remaining-node" in transition.deferred_node_ids
        assert transition.next_child_cursor == '{"after":"remaining-node"}'
    elif action is FrontierAction.EXPAND_CHILDREN:
        assert transition.enqueued_child_node_ids == ("child-2",)


@pytest.mark.parametrize("action", tuple(FrontierAction))
def test_every_exhausted_action_requires_explicit_resume_cursor(
    action: FrontierAction,
) -> None:
    validated = validate_v3_semantic_output(_v3_input(), _v3_output(action))
    with pytest.raises(ValueError, match="resume cursor.*required"):
        transition_frontier(
            validated,
            budget_exhausted=True,
            remaining_deferred_node_ids=("remaining-node",),
        )


@pytest.mark.parametrize(
    "resume_cursor",
    (
        '"after-node"',
        '["after-node"]',
        '{"unknown":"after-node"}',
        '{"lane":"lane-a"}',
        '{"lane":"lane-a","offset":true}',
        '{"lane":"lane-a","offset":-1}',
        '{"after":""}',
        '{"after":" after-node "}',
        '{ "after": "after-node" }',
        '{"after":"a","after":"b"}',
        '{"after":"' + ("x" * 4_097) + '"}',
    ),
)
def test_resume_cursor_rejects_noncanonical_or_open_schema(
    resume_cursor: str,
) -> None:
    validated = validate_v3_semantic_output(
        _v3_input(), _v3_output(FrontierAction.DEFER_ON_DEMAND)
    )
    with pytest.raises(ValueError, match="resume cursor"):
        transition_frontier(
            validated,
            budget_exhausted=True,
            remaining_deferred_node_ids=("remaining-node",),
            resume_cursor=resume_cursor,
        )


@pytest.mark.parametrize(
    "resume_cursor",
    ('{"after":"after-node"}', '{"lane":"lane-a","offset":0}'),
)
def test_resume_cursor_accepts_only_two_closed_variants(resume_cursor: str) -> None:
    validated = validate_v3_semantic_output(
        _v3_input(), _v3_output(FrontierAction.DEFER_ON_DEMAND)
    )
    transition = transition_frontier(
        validated,
        budget_exhausted=True,
        remaining_deferred_node_ids=("remaining-node",),
        resume_cursor=resume_cursor,
    )
    assert transition.next_child_cursor == resume_cursor


@pytest.mark.parametrize(
    "kwargs",
    (
        {"resume_cursor": '{"after":"after-node"}'},
        {"remaining_deferred_node_ids": ("remaining-node",)},
    ),
)
def test_nonexhausted_transition_rejects_run_resume_authority(
    kwargs: dict[str, object],
) -> None:
    validated = validate_v3_semantic_output(
        _v3_input(), _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    )
    with pytest.raises(ValueError, match="exhausted budget"):
        transition_frontier(validated, budget_exhausted=False, **kwargs)


@pytest.mark.parametrize("side", ("input", "output"))
@pytest.mark.parametrize("form", ("pretty", "reordered"))
def test_v3_quality_rejects_noncanonical_raw_json(side: str, form: str) -> None:
    """Catches accepting semantically equal bytes outside the canonical wire contract."""
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    payload = work_input if side == "input" else output
    if form == "pretty":
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    else:
        raw = json.dumps(
            dict(reversed(tuple(payload.items()))),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    with pytest.raises(SemanticQualityV3Error, match="canonical"):
        validate_v3_semantic_output(
            raw if side == "input" else _canonical_bytes(work_input),
            raw if side == "output" else _canonical_bytes(output),
        )


@pytest.mark.parametrize("side", ("input", "output"))
def test_v3_quality_accepts_only_canonical_raw_json(side: str) -> None:
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)

    validated = validate_v3_semantic_output(
        _canonical_bytes(work_input) if side == "input" else work_input,
        _canonical_bytes(output) if side == "output" else output,
    )

    assert validated.action is FrontierAction.ACCEPT_BOUNDARY


@pytest.mark.parametrize("form", ("escaped_text", "alternate_number"))
def test_v3_quality_rejects_noncanonical_escapes_and_numbers(form: str) -> None:
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    if form == "escaped_text":
        raw = json.dumps(output, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    else:
        raw = _canonical_bytes(output).decode("utf-8").replace(
            '"confidence":0.8', '"confidence":8e-1'
        )
    with pytest.raises(SemanticQualityV3Error, match="canonical"):
        validate_v3_semantic_output(work_input, raw)


@pytest.mark.parametrize("side", ("input", "output"))
def test_v3_quality_rejects_oversized_raw_whitespace_before_parse(side: str) -> None:
    """Catches parser work on a tiny payload padded beyond the raw byte cap."""
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    payload = work_input if side == "input" else output
    raw = b" " * (MAX_UNIT_BYTES + 1) + _canonical_bytes(payload)

    with pytest.raises(SemanticQualityV3Error, match="byte limit"):
        validate_v3_semantic_output(
            raw if side == "input" else work_input,
            raw if side == "output" else output,
        )


@pytest.mark.parametrize("side", ("input", "output"))
def test_v3_quality_rejects_invalid_utf8_and_duplicate_keys_on_both_sides(
    side: str,
) -> None:
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    with pytest.raises(SemanticQualityV3Error, match="JSON"):
        validate_v3_semantic_output(
            b"\xff" if side == "input" else work_input,
            b"\xff" if side == "output" else output,
        )

    payload = work_input if side == "input" else output
    raw = _canonical_bytes(payload).decode("utf-8")
    duplicate = raw[:-1] + ',"schema_version":"duplicate"}'
    with pytest.raises(SemanticQualityV3Error, match="duplicate"):
        validate_v3_semantic_output(
            duplicate if side == "input" else work_input,
            duplicate if side == "output" else output,
        )


def test_v3_quality_caps_canonical_input_and_output_mappings() -> None:
    """Catches applying the hard byte cap only to the model output."""
    work_input = _v3_input()
    work_input["approved_local_signal_ids"].append("x" * 299_910)
    unsigned = dict(work_input)
    unsigned.pop("input_sha256")
    work_input["input_sha256"] = _sha256(unsigned)
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    output["input_sha256"] = work_input["input_sha256"]
    assert len(_canonical_bytes(work_input)) == 300_473
    with pytest.raises(SemanticQualityV3Error, match="input.*byte limit"):
        validate_v3_semantic_output(work_input, output)

    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    output["rationale"] = "가" * 100_000
    assert len(_canonical_bytes(output)) > MAX_UNIT_BYTES
    with pytest.raises(SemanticQualityV3Error, match="output.*byte limit"):
        validate_v3_semantic_output(work_input, output)


@pytest.mark.parametrize(
    "locator",
    (
        "ssh://private.example/repo",
        "src/main.py",
        "HTTP://PRIVATE.EXAMPLE/repo",
        "src／main.py",
        "./private-source",
        "../private-source",
        "C:\\private\\main.py",
        "git@private.example:repo/project.git",
    ),
)
def test_v3_quality_rejects_concrete_locator_invention_after_nfkc(
    locator: str,
) -> None:
    """Catches concrete locator disclosure hidden by case or Unicode separators."""
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    output["rationale"] = f"검증 근거는 충분하지만 {locator} 위치는 노출하지 않는다."

    with pytest.raises(SemanticQualityV3Error, match="locator"):
        validate_v3_semantic_output(work_input, output)


def test_v3_quality_rejects_locator_in_representative_role_reason() -> None:
    work_input = _v3_input()
    work_input["representatives"][0]["role_reason"] = "src/main.py"
    unsigned = dict(work_input)
    unsigned.pop("input_sha256")
    work_input["input_sha256"] = _sha256(unsigned)
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    output["input_sha256"] = work_input["input_sha256"]

    with pytest.raises(SemanticQualityV3Error, match="locator"):
        validate_v3_semantic_output(work_input, output)


@pytest.mark.parametrize("field", ("rationale", "role_reason"))
@pytest.mark.parametrize(
    "locator",
    (
        "Dockerfile",
        "readme",
        "MAKEFILE",
        "License",
        "Ｄｏｃｋｅｒｆｉｌｅ",
        "deploy/Dockerfile",
        "docs／README",
        "build/Makefile",
        "legal/LICENSE",
    ),
)
def test_v3_quality_rejects_concrete_extensionless_locator_tokens(
    field: str, locator: str
) -> None:
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    if field == "rationale":
        output["rationale"] = f"검증 근거와 함께 {locator} 위치를 언급한다."
    else:
        work_input["representatives"][0]["role_reason"] = locator
        unsigned = dict(work_input)
        unsigned.pop("input_sha256")
        work_input["input_sha256"] = _sha256(unsigned)
        output["input_sha256"] = work_input["input_sha256"]
    with pytest.raises(SemanticQualityV3Error, match="locator"):
        validate_v3_semantic_output(work_input, output)


@pytest.mark.parametrize("field", ("rationale", "role_reason"))
@pytest.mark.parametrize(
    "locator",
    (
        "deploy/Dockerfile-old",
        "build/Makefile_2026",
        "legal/LICENSE2",
        "docs/README.bak",
        "deploy/custom-Dockerfile",
        "DOCS/ReAdMe.BAK",
        "Ｄｅｐｌｏｙ／ｃｕｓｔｏｍ－Ｄｏｃｋｅｒｆｉｌｅ",
    ),
)
def test_v3_quality_rejects_reserved_stem_inside_relative_path_leaf(
    field: str, locator: str
) -> None:
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    if field == "rationale":
        output["rationale"] = f"검증 근거와 함께 {locator} 위치를 언급한다."
    else:
        work_input["representatives"][0]["role_reason"] = locator
        unsigned = dict(work_input)
        unsigned.pop("input_sha256")
        work_input["input_sha256"] = _sha256(unsigned)
        output["input_sha256"] = work_input["input_sha256"]
    with pytest.raises(SemanticQualityV3Error, match="locator"):
        validate_v3_semantic_output(work_input, output)


@pytest.mark.parametrize("field", ("rationale", "role_reason"))
@pytest.mark.parametrize(
    "ordinary",
    ("preDockerfile", "Dockerfiles", "makefiled", "licensed", "readable"),
)
def test_v3_quality_allows_reserved_substrings_without_path_context(
    field: str, ordinary: str
) -> None:
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    prose = f"The ordinary word {ordinary} is prose, not a concrete locator."
    if field == "rationale":
        output["rationale"] = prose
    else:
        work_input["representatives"][0]["role_reason"] = prose
        unsigned = dict(work_input)
        unsigned.pop("input_sha256")
        work_input["input_sha256"] = _sha256(unsigned)
        output["input_sha256"] = work_input["input_sha256"]
    validated = validate_v3_semantic_output(work_input, output)
    assert validated.action is FrontierAction.ACCEPT_BOUNDARY


def test_v3_quality_allows_generic_locator_concepts_without_concrete_value() -> None:
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    output["rationale"] = (
        "URL path file repository 개념만 설명하며 구체적인 위치 값은 포함하지 않는다."
    )

    validated = validate_v3_semantic_output(work_input, output)

    assert validated.action is FrontierAction.ACCEPT_BOUNDARY


def test_v3_quality_does_not_overblock_ordinary_extensionless_words() -> None:
    work_input = _v3_input()
    output = _v3_output(FrontierAction.ACCEPT_BOUNDARY)
    output["rationale"] = (
        "Licensed software uses a readable build process while generic URL, path, "
        "file, and repository concepts remain abstract."
    )
    validated = validate_v3_semantic_output(work_input, output)
    assert validated.action is FrontierAction.ACCEPT_BOUNDARY
