# Tasks 11·12 — 조기 자원 gate E NEEDS_WORK

날짜: 2026-09-24

## 범위와 현재 판정

사용자가 Tasks 11·12 캐시·최종 검증·적용 구현 및 작업 재개를 요청했다. 기존 개발 worktree와 R1-I1 후보를 확인하고 baseline 전체 테스트를 실행했다. Task 11 pre-flight에서 steady-state 경로의 전수 검증을 발견하여 E 계측을 앞당겼다.

**E gate: NEEDS_WORK. Task 11 구현 미완료, Task 12 미착수.** 캐시·full verify·finalize·apply를 구현했다고 주장하지 않는다. Task 10 전체 승인 또는 R1-I1 PASS 철회도 아니다. 기존 후보의 무결성 동작을 보존한 상태에서 자원 계약 실패를 새로 재현한 것이다.

- 개발 worktree: `.worktrees/portfolio-maker-0.2.2-cycle-01`
- HEAD: `15ca55093cf7bcaed1182bc08af0d1e38d2a6f0e`
- 보존한 기존 `git diff -- src tests` SHA-256: `cd37578e809ac613d3900a993a5c20874806063ebf72ce6a51a0e2115b9b8a0b`
- 새 테스트: `tests/test_semantic_scale_v3.py`
- 제품 코드 변경: 없음
- Git 쓰기·commit·stage·push·설치·배포·실자료·파일럿: 없음

## 재현과 측정

합성 root와 작은 Python 파일만 생성하여 정확히 1,000/10,000 structural node를 prepare/approve한다. 새 child process에서 실제 status/next/validate를 실행한다. 결과 주입은 합성 validator 입력용이며 의미 품질 검증이 아니다.

```bash
PM_SEMANTIC_EARLY_SCALE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests \
  ../../.venv/bin/python3 -m pytest -q -s -p no:cacheprovider tests/test_semantic_scale_v3.py
```

최초 실행: **1 failed in 19.53s**. 모든 application 호출은 성공했지만 전수 catalog scan 금지 assertion이 실패했다. 독립 reviewer도 같은 실패를 재현했다. 리뷰 후 structural full-verification 호출에도 독립 assertion을 추가했고 **1 failed in 26.34s**로 다시 확인했다. 이 실패는 미해결이며 xfail로 숨기지 않는다. 평상시 suite에서는 명시적인 opt-in 환경변수가 없으면 이 자원 테스트를 skip한다.

최초 실행 측정값:

| node 수 | 호출 | catalog authority rows | catalog 명시 read bytes | structural 명시 read bytes | elapsed |
|---:|---|---:|---:|---:|---:|
| 1,000 | next 최초 | 31,992 | 21,266,432 | 9,275,810 | 0.288s |
| 10,000 | next 최초 | 319,992 | 207,323,136 | 92,580,014 | 2.674s |
| 1,000 | validate 최초 | 39,990 | 26,583,040 | 9,251,317 | 0.311s |
| 10,000 | validate 최초 | 399,990 | 259,153,920 | 92,555,399 | 3.257s |
| 10,000 | next replay | 79,998 | 51,830,784 | 23,132,696 | 0.710s |
| 10,000 | validate replay | 79,998 | 51,830,784 | 23,132,696 | 0.844s |

status는 두 규모 모두 계측한 catalog/structural scan이 0이었다. 한 번의 10k 호출이 5초 미만이라는 사실은 E 통과 근거가 아니다. 시간 비율뿐 아니라 catalog 행·bytes·pack decode가 규모에 비례함을 확인했다.

### 측정 한계

- 새 interpreter를 사용했지만 OS page cache는 비우지 않았다. storage-cold 결과가 아니다.
- warm next는 동일 issued input replay, warm validate는 동일 receipt replay다. 두 번째 새 unit 측정은 아니다.
- 명시 read는 Python `os.read/pread`만 센다. SQLite pager 등 전체 물리 I/O가 아니다.
- RSS는 child process의 누적 high-water mark이며 per-operation allocation이 아니다.
- full-home, 1M-node, p95, 20회 status, 실제 reboot, 품질·P/H acceptance를 측정한 것이 아니다.

## 원인과 선행 계약

1. `SemanticCatalogReader.__init__`는 bounded lookup 전에 `_logical_root()`로 모든 authoritative table row를 재직렬화하고 catalog 전체 file hash도 계산한다.
2. `verify_pack()`는 `verify_pack_prefix()`를 통해 structural pack 전체를 hash/decode한다.
3. `_validate_reopened_authority()`와 `_materialize_bounded_v3_input()` 등이 이 경로를 호출한다. next/validate의 replay 역시 해당 authority 검사를 통과하므로 반복 호출에도 O(N) 비용이 남는다.
4. 현재 whole-file/logical seal만으로는 bounded point read 자체가 기존 immutable generation과 같음을 증명할 수 없다. 단순 skip, mtime-only cache 또는 process-local checked flag로 대체하면 무결성·replacement·restart 계약을 약화시킬 수 있다.

독립 reviewer `20260924_11`은 실패를 재현하고 위 원인을 확인했으며 Task 12 진입을 보류했다. 이 리뷰는 **선행 blocker 검토**이지 Task 11 전체 구현 리뷰가 아니다. 원본 보고서는 evidence 폴더에 보존했다.

## 판단 및 다음 작업

**Ruling:** E 계측을 Task 11 제품 코드 변경보다 먼저 수행했다. 전수 검증이 authority boundary에 결합돼 있어, 세 계층 캐시만 먼저 추가하는 것이 이 gate를 닫는다는 가정을 피하기 위함이다. 비용은 캐시 구현 진행이 보류된 것이다.

**Ruling:** 현재 fail-closed full scan을 그대로 유지하고 안전한 bounded-read authority 계약의 설계·승인을 선행한다. 구현 승인 범위를 조용히 확장하여 seal 형식/신뢰 경계를 바꾸지 않는다. 비용은 현재 next/validate의 규모 비례 비용이 계속 남는 것이다.

다음 설계는 기존 seal에 결합된 authenticated indexed/chunk proof 또는 검증된 generation certificate의 보장 범위를 구체적으로 비교해야 한다. certificate라는 명칭이나 file stat 결합만으로 무결성이 보장된다고 가정하지 않는다. cold process에서도 bounded여야 하며, corruption·replacement/race·restart·stale authority·정상 과거 replay 검사를 유지해야 한다. 승인 후 해당 선행 구현 → E 재검증 → Task 11 캐시/무효화 → 독립 리뷰 → Task 12 순서로 재개한다.

Task 12 뒤의 P, Tasks 13–16, H 및 release는 별도 미실행 상태로 유지한다.

## 검증 기록

- 변경 전 full suite: **1766 passed in 119.75s**.
- 새 계측 테스트 추가 후 기본 full suite: **1766 passed, 1 skipped in 131.11s**. 이후 structural assertion 보강은 별도 E 재실행으로 확인했다.
- E 최초·독립·최종 실행: 모두 의도한 scan gate 실패. GREEN 없음.
- 계측 보강 후 최종 기본 suite: **1766 passed, 1 skipped in 112.62s**. 명시적 E 실행은 실패 상태다.
- 양쪽 worktree 기존 파일 변경/누락 0, HEAD/stash 불변. 기존 tracked src/tests diff hash 동일. 새 테스트/보고서/작업 증거만 추가했다.
- artifact 경로: `.superpowers/sdd/2026-08-02-portfolio-maker-home-scale-adaptive-semantic-frontier/tasks-11-12/` — baseline, RED/full logs, metrics, reviewer-report, progress, preservation-check.
