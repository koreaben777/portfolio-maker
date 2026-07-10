# Team Based Review Loop 7 - Re-Review Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `f9a452717647227c1e0f61b471b51c99b5163aad`

Baseline: `8b3e6f2edb8f367b7065039e27b92dd56d9bfa3f`

Status: NEEDS WORK

## Evidence Checked

- MVP Developer completion marker: `[TEAM_REVIEW_FIX_DONE_7]`
- Same newly created reviewer team reused: Parfit, Schrodinger, Raman, Arendt.
- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `86 passed`
- Focused seven-file regression set -> `74 passed`
- `git show --check --format=fuller f9a4527` -> pass
- `git diff --check 8b3e6f2..f9a4527` -> pass
- Initial reproductions for static symlink replacement, relative approval forbidden paths, approval revocation, source freshness, self-discovery, immutable changed-content snapshots, endpoint-level GitHub partial success, malformed top-level JSON, and invalid scan root were rerun.
- Additional adversarial reproductions covered post-validation path replacement, legacy/tampered snapshots, damaged content-addressed snapshots, stale malformed profile recovery, GitHub schema omissions, relative CLI forbidden paths, and case-variant repository exclusions.

## Closed Findings

- Static symbolic link, non-regular file, canonical URI mismatch, and oversize ingestion are rejected before extraction.
- Approval JSON `forbidden_paths` are normalized against the workspace.
- Common Bearer/private-key/token forms are masked for newly extracted snapshots.
- Approval revocation is rechecked before portfolio drafting.
- Deleted or changed source evidence and missing snapshots no longer create current claims.
- `.portfolio-maker` is excluded from repeated discovery.
- Changed-content snapshots use content-addressed immutable paths.
- GitHub endpoint failures preserve earlier successful endpoint results.
- Top-level malformed GitHub/profile JSON has clean CLI handling.
- Invalid discovery roots return a user-facing non-zero error.
- Discovery limits, approval version, source-class deferral, and direct-user-activity deferral are documented.

## Still Open

### P1 - 승인 경로 TOCTOU 우회

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/policy.py:98`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/ingestion.py:39`

검증 helper가 `Path`를 반환한 후 별도의 `read_bytes()`가 실행된다. 두 단계 사이 승인 파일을 비승인 symbolic link로 교체하면 비승인 본문이 수집되는 재현이 세 reviewer lane에서 반복됐다.

Minimum fix: `O_NOFOLLOW`로 열고 같은 file descriptor에서 `fstat`, regular-file/size 검증, read를 수행한다. 상위 경로 교체까지 완전히 지원하지 않으면 명시적 지원 경계와 회귀 테스트를 남긴다.

### P1 - Legacy 또는 변조 snapshot이 현재 masking/evidence 검증을 우회

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:53`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/extractors.py:17`

현재 검증은 raw hash와 snapshot 자기기재 hash를 비교하지만 snapshot `text`와 현재 추출 결과를 비교하지 않는다. 이전 `text-v1` 미마스킹 snapshot이나 metadata를 유지한 변조 snapshot의 합성 credential/fabricated claim이 profile에 들어갈 수 있다.

Minimum fix: masking 변경에 맞춰 extractor version을 올리고 snapshot의 text/extractor를 현재 추출 결과와 비교한다. Legacy mismatch는 재-ingest를 요구해야 한다.

### P1 - 민감 파일명 경계의 잔여 누락

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/policy.py:19`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:27`

일부 알려진 password-manager export 이름은 exact-name 규칙을 벗어나 candidate가 된다. 합성 token 형태의 파일명도 public draft 제목/reference에 남는다.

Minimum fix: 지원 확장자에 맞는 좁은 password-manager export 규칙을 추가하고, filename 자체가 secret pattern이면 discovery/ingestion에서 거부하며 public artifact에서도 재마스킹한다.

### P1 - GitHub repository privacy 필드 누락이 public으로 fail-open

File: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:32`

Repository payload에서 `isPrivate`가 빠지면 `False`로 기본 처리되어 malformed/private 결과가 public으로 분류될 수 있다.

Minimum fix: `isPrivate` 존재와 bool 타입을 필수 검증하고 누락 시 discovery failure status로 처리한다.

### P2 - 손상된 content-addressed snapshot이 재-ingest로 복구되지 않음

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/snapshots.py:18`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/ingestion.py:71`

Hash-named snapshot이 invalid JSON으로 손상된 경우 기존 경로가 존재한다는 이유로 그대로 반환된다. 재-ingest가 성공을 보고해도 다음 profile 생성은 다시 stale 처리한다.

Minimum fix: 기존 snapshot의 schema/source/hash/text를 검증하고 불일치하면 임시 파일에 안전하게 다시 쓴 후 원자 교체한다.

### P2 - 손상된 stale profile이 정상 재생성을 가로막음

File: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:23`

`draft_portfolio()`는 바로 뒤에서 `build_profile()`로 profile을 재생성하지만, 그 전에 기존 profile을 검증해 손상된 stale artifact가 있으면 복구하지 못하고 종료한다.

Minimum fix: 기존 profile 선행 검증을 제거하고 재생성한 profile만 검증한다.

### P2 - 추가 입력·상태 경계 결함

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:30`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/ingestion.py:65`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:174`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:140`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/approval.py:73`

재현된 잔여 항목:

- CLI `--forbidden-path` 상대 경로는 approval JSON과 달리 CWD 기준이다.
- 동일 내용 source가 stale 후 복구되면 같은 snapshot path를 가리키는 중복 DB row가 생긴다.
- `excluded_repositories` 비교가 case-sensitive라 case variant가 통과한다.
- 빈 snapshot에 `Approved evidence captured.` 합성 claim이 생긴다.
- 존재하지 않는 `~user` 확장 오류가 clean CLI mapping을 우회한다.

Minimum fix: 각 입력을 canonicalize하고, same hash/valid snapshot이면 상태만 복구하며, evidence-empty에는 claim을 만들지 않고, path normalization 오류를 `ApprovalFormatError`로 변환한다.

### P2 - Endpoint JSON과 product contract의 잔여 drift

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:98`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:27`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/.agents/skills/portfolio-maker/SKILL.md:25`

재현/대조 결과:

- Workflow payload `{}`와 empty review item이 fail-open하여 가짜 또는 빈 activity가 생성될 수 있다.
- Draft는 profile claim evidence를 사용하지 않고 placeholder만 만든다. 현재 동작을 유지한다면 `portfolio skeleton`으로 문서화해야 한다.
- 문서상 첫 discovery 전에 `excluded_repositories`를 적용할 수 없다. Approval 편집 후 re-discovery를 필수로 적거나 순서를 바꿔야 한다.

## Newly Found

- Empty GitHub commit message의 `IndexError`는 implementation self-review 중 발견되어 `f9a4527`에서 닫혔다.
- 위 P1/P2는 대부분 초기 정적 재현을 고친 뒤 더 강한 adversarial input 또는 legacy state에서 새로 드러났다.

## Ponytail Cleanup

초기 cleanup은 이번 보안 fix와 분리한 판단은 타당하지만 개선 요청 자체는 남아 있다.

- 3,111줄 historical plan의 source/test 전문 중복: 약 1,800~2,100줄 축약 가능.
- 단순 artifact writer wrapper와 전용 테스트: 약 40줄 축약 가능.
- GitHub activity DTO 전 필드 복사: 약 20줄 축약 가능.
- Runtime에서 생성·소비되지 않는 `SourceStatus.APPROVED`, `approved_at`, 그리고 저장할 필요 없는 `SourceApproval.version` 상태를 정리하거나 reserved 계약으로 명시할 필요가 있다.

## Next Minimal Checks

1. File descriptor 기반 승인 파일 read로 post-validation swap 재현을 닫는다.
2. Extractor/masking version migration과 snapshot text integrity를 검증한다.
3. 손상 snapshot 재-ingest 복구와 stale malformed profile overwrite를 검증한다.
4. GitHub required privacy/endpoint fields와 casefold exclusion을 검증한다.
5. CLI/approval의 모든 상대·tilde path normalization을 clean error contract에 맞춘다.
6. Empty evidence, same-content restore, public filename masking을 검증한다.
7. README/skill/spec에서 portfolio skeleton 및 approval-before-discovery 순서를 정렬한다.
8. Full suite, focused reproductions, `git diff --check`, `git show --check`를 다시 실행한다.

## Re-Review Outcome

NEEDS WORK. Initial Loop 7 findings were substantially improved and test coverage increased from 70 to 86, but P1 privacy/integrity defects and P2 recovery/schema errors remain reproducible. Per the one-cycle skill guardrail and the user's request for exactly one additional loop, no second fixback cycle was started. No remote was added and nothing was pushed.
