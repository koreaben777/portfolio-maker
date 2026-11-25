# Authenticated bounded-read — 후속 검증 checkpoint

판정: **NEEDS_WORK**. 선행 구현 전체 완료, E 최종 PASS, Task11/12 완료 또는 release 승인이 아니다.

## 범위와 증거

승인된 2026-09-25 계획을 기존 개발 worktree에서 순차 수행했다. HEAD `15ca55093cf7bcaed1182bc08af0d1e38d2a6f0e`. 독립 리뷰는 이전 후보에 대한 checkpoint이며 현재 수정 후보를 재승인한 것이 아니다. 리뷰와 원래 후보 해시는 보존했다.

증거 디렉터리: `.superpowers/sdd/2026-09-25-authenticated-bounded-read/`.
현재 후보: `candidate-hashes-followup.json`, `candidate-followup.patch`. 이전 inherited 변경은 `baseline.patch`로 구분한다.

## 이번 수정과 테스트

| 항목 | 재현/수정 증거 |
|---|---|
| full/prefix pack 마지막 read 이후 rename·동일 inode 변조·symlink 교체 | 6 RED→GREEN; 같은 fd에서 크기/해시/frame 확인, 종료 시 fd와 현재 managed path 비교 |
| node/baseline/children/representative key-value 및 parent 불일치 | 5 RED→GREEN |
| typed scalar, bool/int 혼동, list/range/digest/reference 오류 | 12 RED→GREEN |
| deferred/summary 타입·cursor·audit 정렬 key 불일치 | 8 RED→GREEN |
| full correspondence 여러 단계 사이 index/catalog/structural 교체 | 3 RED→GREEN; 전체 비교 동안 descriptor identity 유지 |
| auth reader rename/inplace/symlink/short read | 4 추가 acceptance, 기존 구현에서 GREEN |
| prepare index build/manifest write/publication/postpublication full/DB bind 실패 | 5 추가 acceptance, 기존 구현에서 GREEN; DB 논리 dump·기존 revision·approval 보존 |

최종 전체 회귀: **1858 passed, 1 skipped — 133.95s** (`followup-final-regression.log`).
확대 opt-in E: **1 passed — 78.20s** (`followup-scale-phases.log`). E test 통과와 전체 E gate 승인은 구분한다.

명령:
```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests ../../.venv/bin/python3 -m pytest -q -p no:cacheprovider
PM_SEMANTIC_EARLY_SCALE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests ../../.venv/bin/python3 -m pytest -q -s -p no:cacheprovider tests/test_semantic_scale_v3.py
```

## 확대 E 관측

정확히 1k/10k synthetic nodes, 독립 project 두 lane. 새 child process이며 OS cache는 비우지 않았다. 동일 issued/receipt replay와 다른 신규 unit을 구분했고 status는 20회 중 nearest-rank p95를 기록했다. 첫 단일-root fixture는 다음 작업이 없어 실패했으며 계획에 명시된 two-lane fallback을 적용했다.

| 지표 | 1k | 10k |
|---|---:|---:|
| prepare+approve 전체 시간 | 5.387s | 53.630s |
| 인증 index bytes | 10,349,466 | 103,796,396 |
| status p95 | 1.078ms | 0.975ms |
| 처음 next | 0.256s | 0.293s |
| 처음 validate | 0.136s | 0.159s |
| 다음 신규 unit next | 0.491s | 0.328s |
| 일반 경로 catalog 전체 authority rows / structural 전체 검증 | 0 / 0 | 0 / 0 |
| 일반 경로 최대 tree get/page proof bytes | 85,667 | 85,742 |
| 최대 반환 / 내부 continuation 보관 rows | 64 / 65 | 64 / 65 |
| 별도 audit 열거 rows | 997 | 9,997 |
| 별도 audit 열거 시간 | 0.037s | 0.395s |
| 별도 full verifier 시간 | 1.446s | 14.954s |
| full verifier 시점 process RSS high-water bytes | 68,386,816 | 83,279,872 |

proof byte 상한은 **tree get/page 한 번**에 대한 것이다. 하나의 lifecycle 호출이 여러 get/page를 수행하며, 합산 bytes를 16MiB 제한으로 오해하면 안 된다. 전체 proof bytes/node reads는 원본 로그에 기록했다.

Audit 열거 proof bytes는 1,937,992→21,113,599로 증가했다. bounded paging이 전체 모집단 순회를 없애지는 않는다. 이 측정은 iterator 비용이며 실제 expansion closure의 sample/digest 동등성 시험을 대체하지 않는다.

한 receipt replay에서 result-prefix 2회, 합산 요청 길이 9,824 bytes, 명시적 result read 24,560 bytes를 관측했다. 긴 receipt history의 상한은 입증하지 않았다. 최초 생성 중인 result file의 I/O는 일부 attribution이 빠지며 SQLite pager I/O도 관측 범위 밖이다. RSS는 앞선 연산을 포함한 process high-water다. 준비 시간은 증분 index 비용만이 아니라 준비+승인 전체다. build 별도 RSS와 memory-retention 증명은 남아 있다.

## 리뷰 처분과 유지한 결정

- 매 partial read마다 auth/structural 전체 seal 검증을 요구한 지적은 승인된 partial/full 구분과 충돌한다. 구현하지 않았다. 미조회 손상 검출 지연은 승인된 비용이며 full 검증은 유지한다.
- inline value는 전체 leaf 64KiB 상한에 묶여 현재 1MiB value 상한보다 작다. 주장된 우회는 재현되지 않았다. 상수 변경 때 재검토해야 한다.
- nonfinal singleton unary branch는 동일 child height를 위한 것이다. 최종 단독 root는 승격한다. 최대 level당 한 unary tail 비용을 허용한다.
- 전역 generation을 디렉터리 이름과 비교하는 수정은 적용하지 않았다. 임시 `.<revision>.prepare`와 일반 fixture가 있으며 실제 승인 경계는 manifest/proposal이다. full verifier API의 독립 binding 처분은 아직 열려 있다.
- auth framing은 operation-scoped fd에서 읽는다. 구조 payload는 bounded PMPackReader를 사용한다. 작은 framing 중복을 감수하며 SQL fallback이나 persistent checked cache는 추가하지 않았다.
- reader allow-list는 기존 full reader와 새 authenticated reader의 명시적 union이다. 다른 reader를 추가하면 의도적인 변경이 필요하다.
- 모든 증거를 보존하며 commit 대신 후보 해시/로그로 추적한다. Git 쓰기 금지가 스킬의 기본 commit/cleanup보다 우선한다.

## 남은 acceptance / 다음 작업

1. typed cross-projection joins, 모든 malformed-wire/전체 tree 구조 및 자원 실패 사례, full-verifier generation API 판단.
2. 실제 legacy build fixture, fsync·crash/restart·기존 active 보존·publication failure 전체 matrix.
3. consumed proof 손상 시 lifecycle DB/exchange 불변, legacy/R1 별도 증거, 실제 audit closure sample/digest 동등성.
4. build/full exporter 메모리 보관 증거와 original E gate 기준의 최종 판정. audit 및 receipt-history 알고리즘 변경은 이 선행 범위 밖이다.

이전/이번 회귀 통과를 미검증 항목의 PASS로 확장하지 않는다. 1M nodes, home pilot, 실자료, 재부팅, semantic-quality, 배포 검증은 하지 않았다. 원 Task11 cache와 Task12 finalize/atomic apply는 별도 미완료다.

## 보존

`preservation-followup.json`: 두 checkout HEAD/stash 동일, root baseline 파일 변경 없음, baseline 파일 누락 없음. 기존 `sqlite_repository.py`와 domain receipt authority는 변경하지 않았다. 기존 7개 수정 파일에 scale test 변경만 추가되었고 4개 auth source/test 새 파일은 계속 보존한다. `git diff --check` 통과. 설치·Git 쓰기·실자료 접근 없음.

## 추가 발견 — 2026-09-25 consumed-root 실패 순서

이후 추가한 실제 root-byte 손상 검사 4건 중 **2 failed, 2 passed** (`acceptance-consumed-root.log`). 따라서 위 1858-pass는 이전 후보의 기록이며 현재 전체 GREEN을 뜻하지 않는다.

`validate`와 receipt replay는 DB·파일 snapshot을 보존한다. `next`와 issued replay는 손상 proof를 거부하지만 검증 전에 singleton 예약/active-time DB 갱신을 수행하므로 전체 DB 불변 assertion이 실패한다. 기존 구현은 경쟁에서 진 run이 managed/source 파일을 읽지 않도록 claim을 먼저 수행하며, source drift 테스트도 실행 lock 보존을 명시한다.

이는 손상 proof를 승인하는 결함이 아니라 **새 계획의 실패 시 전체 DB 불변 조건과 기존 claim-first/active-accounting 조건의 충돌**이다. 검증을 claim 앞으로 단순 이동하거나 R1 accounting을 되돌리는 수정을 하지 않았다. 제품 코드는 그대로이며 실패 테스트를 보존했다. 원자적 예약/검증 설계 변경 또는 허용되는 예약/시간 갱신 범위의 명시적 승인 없이는 해당 acceptance를 완료 처리할 수 없다.
