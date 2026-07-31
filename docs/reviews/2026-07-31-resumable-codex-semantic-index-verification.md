# 재개 가능한 Codex 의미 인덱스 합성 검증

- 날짜: 2026-07-31
- immutable parent: `26886d614808d02ad148fb04bea5f15d5f2b2085`
- 예정 subject: `test: verify grounded semantic workflow`
- 검토 대상: 이 보고서를 포함하는 commit
- 상태: 합성 workflow 검증 완료, 독립 review 및 별도 full-home pilot 대기

이 파일은 자기 자신을 포함하는 최종 Git hash를 기록하지 않는다. 최종 hash는 이
commit의 외부 Task 11 review record에서 확인한다.

## 검증한 흐름

세 checked-in fixture가 각자 명시한 의미를 사용해 production
`codex-semantic-v2` 경계를 통과했다.

1. source ingest와 현재 evidence 갱신
2. `prepare-semantic-index`
3. `semantic-index-status`의 work unit/managed byte 견적
4. dependency-ready unit 하나씩 `next-semantic-work`
5. matching managed output 작성
6. `validate-semantic-work`의 quality 검증과 receipt
7. status 기반 재개와 complete 확인
8. `finalize-semantic-index`
9. `apply-semantic-index`
10. locator-free chunked `prepare-project-review --version v3`
11. 기존 candidate v2 schema와 project decision compatibility 확인

Insurance RAG fixture는 보험 약관 검색과 그래프 근거 검증, PlayMCP fixture는
공모전 목적과 독립 lifecycle, cross-directory fixture는 백엔드·프론트엔드 통합
전달이라는 서로 다른 summary, role, topic, purpose, independence signal을 사용한다.
filename, token 정렬, template 또는 일반 `Synthetic summary`로 최종 의미를 만들지
않는다.

## RED와 GREEN

시작 full suite:

```text
1100 passed, 4 failed
```

실패는 Task 11이 소유한 다음 네 acceptance test뿐이었다.

- `test_coherent_parent_becomes_one_project`
- `test_independent_contest_child_is_split`
- `test_known_projects_do_not_regress_to_zero_candidates`
- `test_cross_directory_fixture_preserves_artifact_scope_boundary`

원인은 protocol-v1 helper가 protocol-v2 work input에서 존재하지 않는 `nodes` 배열을
읽으려 한 전환 결함이었다.

수정 후:

```text
tests/test_semantic_acceptance.py                         5 passed
13-file focused semantic suite                         273 passed
plugin/CLI documentation suite                          76 passed
full Python suite                                     1105 passed
```

모든 pytest는 candidate source를 명시한 root virtual environment로 실행했다.

## 안전·복구 회귀

focused/full suite에서 다음 경계를 함께 검증했다.

- placeholder summary, sorted token dump, token-only 또는 ungrounded 결과는 receipt 없음
- broken/root/late-replacement symlink는 follow하지 않음
- crawl 뒤 transient file 소멸은 fail-closed 상태로 처리
- `chunk-10000.json` 이상의 이름과 10,001 work unit/analysis target은 global cap 없이 처리
- work receipt가 dependency stale을 전이하고 마지막 유효 receipt 다음부터 재개
- interrupted semantic import가 이전 active revision을 유지하고 import receipt 다음부터 재개
- protocol-v1 active revision과 legacy row는 additive migration 후 읽기 보존
- writable existing SQLite DB는 staged multi-chunk import 뒤 다시 열림
- main DB·journal/WAL/SHM family replacement, cross-process read, pre/post-commit 경계는
  adversarial repository 회귀로 보호
- final manifest, receipt, policy, symlink, unexpected entry, project-review refresh tamper는
  stale authenticated output을 남기지 않음

acceptance harness는 SQLite를 직접 열거나 SQL을 실행하지 않고 application/repository
public interface만 사용한다. receipt, final manifest, active DB state를 직접 제조하지 않는다.

## CLI·package·frontend

- top-level help와 semantic command help 7개: 모두 exit `0`
- package: sdist와 wheel `portfolio_maker-0.2.2` 생성 성공
- package build는 candidate의 기존 ignored build residue를 보존하기 위해
  `/private/tmp`의 immutable 복제본에서 실행하고 임시 파일을 제거함
- root virtual environment에 `build` module이 없어 첫 환경 probe는 실패했으며,
  임시 target에 설치한 `build 1.5.0`을 root Python의 `PYTHONPATH`로만 제공해 재실행함
- setuptools는 2027-02-18 전 `project.license` 표현 갱신을 권고함
- frontend isolated `npm ci`: 16 packages audit, high severity advisory 2건
- Vite build: 4 modules transformed, static `dist` 생성 성공
- advisory는 자동 수정하지 않았고 lockfile/web source를 변경하지 않음

## 승인 경계

semantic index 완료는 다음 승인을 대체하지 않는다.

- source/evidence 승인
- artifact별 include/exclude와 delivery scope
- candidate 검토와 project include/exclude
- restricted/open_public 재생성
- Sites 또는 Git 사용
- push, deployment, release, publication

이번 검증은 합성 fixture와 임시 workspace만 사용했다. root management checkout,
보존된 pilot, 실제 사용자 source, 실제 private URL, installed plugin, remote, GitHub
Issue, Sites, push, tag, deploy, publish 상태는 변경하지 않았다.

## 잔여 조건

이 결과만으로 full-home 규모의 실제 Codex 의미 판단 성능이나 사용자 project 품질을
보증하지 않는다. 독립 Task 11 review가 PASS한 뒤에도, fresh clean candidate와 별도
사용자 승인을 받은 full-home pilot restart gate를 새로 통과해야 한다. 이전
protocol-v1 pilot의 staging 결과를 재사용하거나 이어서 적용해서는 안 된다.
