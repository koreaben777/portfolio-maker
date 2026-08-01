# Portfolio Maker 고 fan-out semantic target projection 설계 부록

> 상태: Cycle 14 Task 14 승인 구현 계약
> 적용 범위: `codex-semantic-v2` 저장 상태와 Codex work target 사이의 경계

## 문제

기존 feature capsule은 direct child 식별자 전체를 `child_node_ids`에 넣고 그
capsule을 그대로 단일 semantic work template과 input에 복사했다. 실제 source
node가 수천 개의 direct child를 가지면 단일 work unit이 고정된 262,144 byte
상한을 넘었다. 경로 제외나 상한 증가는 전체 구조를 회피하거나 무제한 입력을
허용하므로 해결책이 아니다.

## 권위와 projection

`codex-semantic-v2` capsule chunk와 revision별 private structural chunk는 기존과
동일하게 정렬된 전체 `child_node_ids`를 보존한다. Manifest의 capsule hash,
work-plan hash, input self-hash와 private structural 검증이 이 상태를 결합한다.

Codex에 전달하는 target은 full capsule에서 매번 재생성하는
`direct-child-attestation-v1` projection이다. Persisted work input의 projection을
권위로 신뢰하지 않는다.

```json
{
  "target_projection_contract": "direct-child-attestation-v1",
  "direct_child_topology": {
    "direct_child_count": 3585,
    "direct_child_ids_sha256": "<canonical sorted full ID array SHA-256>",
    "sample_child_node_ids": ["<at most 64 sorted IDs>"],
    "sample_complete": false
  }
}
```

- `direct_child_count`는 전체 direct child 수다.
- `direct_child_ids_sha256`는 전체 ID를 정렬한 canonical JSON 배열의 SHA-256이다.
- `sample_child_node_ids`는 의미 문맥을 위한 정렬된 앞 64개 이하의 표본이다.
- `sample_complete`는 count가 sample 길이 이하일 때만 true다.
- target에는 완전 목록으로 오해될 수 있는 `child_node_ids`를 넣지 않는다.

Work-plan template hash와 materialized input hash는 이 projection을 포함한다. Plan
검증과 input 검증은 full capsule에서 projection을 다시 계산하므로 count, digest,
sample 또는 completeness 변조는 fail closed한다. Direct-child fan-out이 커져도 한
target의 sample은 64개로 고정된다.

## Semantic output과 finalization

Semantic node output에는 `child_node_ids`를 받지 않는다. Codex/LLM은 summary,
roles, topics와 grounded semantic facts만 제안한다. Finalization은 분석된 node도
검증된 private structural record의 전체 `child_node_ids`를 사용한다. 따라서
projection이나 LLM output이 구조 node를 생략·대체·추가할 수 없다.

## 호환성과 재개

Persisted capsule/manifest의 `codex-semantic-v2` 구조와 전체 topology 의미는
유지한다. 다만 이 변경 전 생성된 staging work plan은 이전 full-capsule template
hash와 output schema를 사용하므로 조용히 resume하지 않는다. Source approval을
다시 확인한 뒤 `prepare-semantic-index`를 fresh 실행해 새 plan/input을 만들어야
한다. Active revision과 원본 source는 변경하지 않는다.

## 검증 기준

- 3,584개와 10,000개 direct child를 가진 analysis target의 planning 성공
- 모든 work template과 materialized input이 262,144 byte 이하
- 3,584개 실제 파일 fixture의 prepare, semantic lifecycle, finalize 성공
- final node가 private structural child list 전체를 정확히 복원
- rehashed projection tamper와 private structural child-list tamper 거부
- small-tree, reduce, resume, replay, quality, privacy 계약 회귀 없음
