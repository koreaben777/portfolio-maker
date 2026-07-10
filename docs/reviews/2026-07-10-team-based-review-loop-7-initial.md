# Team Based Review Loop 7 - Initial Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `8b3e6f2edb8f367b7065039e27b92dd56d9bfa3f`

Status: NEEDS WORK

## Review Team

기존 리뷰어를 재사용하지 않고 모델 변경 후 객관성 확보를 위해 네 신규 서브에이전트를 생성했다.

- `@ponytail`: Parfit (`019f49b4-a987-7a21-a5be-c8c18bf5c0de`)
- `agency-router` / `codebase-onboarding`: Schrodinger (`019f49b4-a5ff-7631-bb45-d759afb9c62a`)
- `agency-router` / `technical-writer`: Raman (`019f49b4-a231-77b2-86a0-d60399584a91`)
- `agency-router` / `reality-checker`: Arendt (`019f49b4-acf7-7b71-8bb7-6b97b0af9091`)

각 리뷰어는 이전 PASS 결론을 전제로 삼지 않고 같은 commit을 독립적으로 검토했다.

## Evidence Checked

- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `70 passed`
- `git show --check --format=fuller HEAD` -> pass
- `git diff --check` -> pass
- `./.venv/bin/portfolio-maker --help` -> pass
- 승인 경로 교체, 상대 forbidden path, 승인 철회 후 단독 draft, 원본 삭제/변경, 반복 discovery, 갱신 snapshot, GitHub 후기 endpoint 실패, 잘못된 JSON 구조를 합성 데이터로 재현했다.
- 테스트 통과와 release readiness를 분리해 판정했다. 기존 70개 테스트는 아래 경계 조건을 포함하지 않는다.

## Findings

### P1 - 승인된 경로 교체로 비승인 파일을 수집할 수 있음

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/ingestion.py:47`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/policy.py:69`

발견 및 승인 후 승인 경로를 민감 파일을 가리키는 symbolic link로 교체하면 ingestion이 링크 대상을 읽는다. `classify_path()`는 승인 경로의 이름을 검사하지만 `read_bytes()`는 링크 대상을 읽고, ingestion 단계에는 discovery의 크기 제한도 없다. Snapshot의 `source_uri`도 승인 URI가 아닌 canonical target URI로 기록될 수 있다.

Minimum fix: ingestion 직전에 symbolic link, 비정규 파일, canonical URI mismatch, 크기 초과를 거부하고 focused regression tests를 추가한다. TOCTOU를 완전히 닫기 어렵다면 지원 경계와 잔여 위험을 명시한다.

### P1 - 상대 `forbidden_paths`가 실행 CWD에 따라 달라짐

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/ingestion.py:29`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:22`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/policy.py:64`

승인 파일의 `"private"` 같은 상대 경로가 workspace가 아니라 프로세스 CWD에서 resolve된다. 실행 위치가 바뀌면 사용자가 금지한 workspace 내부 파일이 candidate가 되어 수집될 수 있다.

Minimum fix: 상대 경로를 `WorkspacePaths.workspace` 기준으로 정규화하거나 승인 parser에서 절대 경로만 허용한다.

### P1 - 민감 파일 및 Bearer credential 보호가 불완전함

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/policy.py:19`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/policy.py:30`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/extractors.py:17`

일반적인 password export 파일명이 candidate로 분류되고, 합성 `Authorization: Bearer ...` credential이 snapshot과 profile claim에 남는 경로가 재현됐다. 이는 architecture spec의 password-manager export 제외 및 secret masking 경계와 충돌한다.

Minimum fix: 좁고 설명 가능한 민감 파일명 규칙과 Bearer/private-key/token prefix masking을 추가하고 합성 값 기반 회귀 테스트를 남긴다.

### P1 - 승인 철회 후 단독 `draft-portfolio`가 이전 source를 유지함

File: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:13`

Profile 생성 후 approval을 철회하고 `draft-portfolio`만 다시 실행하면, stale `master-profile.json`을 신뢰해 철회된 source의 이름이 public draft에 남는다. 기존 테스트는 철회 후 profile부터 재생성해 이 명령 경계를 검증하지 않는다.

Minimum fix: draft 단계에서 현재 approval/forbidden policy와 profile freshness를 검증하거나, 검증된 profile을 먼저 재생성하도록 command contract를 강제한다.

### P1 - 삭제·변경·유실된 evidence로 claim을 생성함

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:26`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:85`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design.md:381`

Ingestion 후 원본을 변경·삭제해도 기존 snapshot claim이 생성되고 source status는 `ingested`로 남는다. Snapshot 파일이 없으면 근거가 없는 fallback claim까지 생성된다. 이는 artifact 생성 전 current hash/snapshot을 확인한다는 명세와 직접 충돌한다.

Minimum fix: profile 생성 전 원본 존재, current hash, latest snapshot 존재/hash를 검증한다. 불일치는 재-ingest를 요구하거나 `STALE_SOURCE`로 전환해 제외하고, snapshot 없는 source에는 claim을 만들지 않는다.

### P2 - 반복 discovery가 `.portfolio-maker`를 자기 입력으로 수집함

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:30`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/policy.py:8`

Workspace가 scan root 안에 있을 때 두 번째 discovery부터 `discovery-report.md`, approval, snapshot, artifact가 candidate가 될 수 있어 자기 증폭과 evidence 오염이 발생한다.

Minimum fix: `paths.root`를 항상 discovery 금지 경로로 넘기고 `.portfolio-maker`를 기본 제외 이름으로도 방어한다.

### P2 - snapshot history가 같은 파일을 덮어써 이력이 거짓이 됨

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/snapshots.py:18`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:147`

한 source를 변경 후 재수집하면 SQLite에는 서로 다른 hash의 두 row가 생기지만 두 row의 `snapshot_path`는 같다. 파일은 최신 내용으로 덮어써져 과거 row의 hash와 실제 snapshot이 불일치한다.

Minimum fix: content hash 또는 snapshot id를 파일명에 포함해 immutable snapshot을 저장하거나 DB를 단일 latest row 계약으로 바꾼다.

### P2 - 같은 repository의 후기 endpoint 실패가 선행 성공 활동을 폐기함

File: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:175`

한 repository에서 PR 조회가 성공한 뒤 workflow endpoint가 실패하면 `repo_activities` 전체가 append되기 전에 예외가 잡혀, 앞서 성공한 활동도 결과에서 사라진다.

Minimum fix: endpoint별 실패를 격리하고 성공 결과를 즉시 누적하며 endpoint별 status를 기록한다.

### P2 - 구조가 잘못된 JSON이 clean CLI error를 우회함

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:32`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:13`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/adapters/cli.py:56`

구문상 valid하지만 예상 구조가 아닌 GitHub payload나 profile JSON은 `TypeError`/`KeyError` traceback을 낼 수 있다. `safe terminal output, exit codes only` 계약이 모든 외부/artifact JSON 경계에 적용되지 않는다.

Minimum fix: 필요한 최소 schema/type를 경계에서 검증하고 전용 user-facing error로 변환한다.

### P3 - Discovery 제한이 결과에 표시되지 않음

File: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:150`

Repository/PR/issue는 100개 제한이며 일부 `gh api` 호출은 pagination하지 않지만 report와 README에 truncation/limit가 표시되지 않는다.

Minimum fix: 0.1.0 제한과 결과가 잘릴 수 있음을 report/README에 명시한다. Pagination은 실제 요구가 생길 때 추가한다.

### P3 - 문서·approval envelope 계약의 잔여 drift

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design.md:73`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design.md:245`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/approval.py:27`

Spec은 사용자 지정 source class 제외와 direct-user-activity 우선순위를 현재 동작처럼 표현하지만 구현 경로가 없다. Sample approval의 `version`은 생성되지만 parser가 호환성 의미를 검증하지 않고 spec의 runtime field 설명에도 빠져 있다.

Minimum fix: 미구현 주장을 deferred로 옮기고, `version`을 검증·문서화하거나 의미가 없다면 제거한다.

### P3 - 존재하지 않는 scan root가 성공으로 종료됨

Files:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/local_discovery.py:38`
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/adapters/cli.py:67`

오타가 있는 `--home` 경로가 permission skip처럼 기록되고 CLI 0으로 종료될 수 있다.

Minimum fix: root 존재/디렉터리 여부를 먼저 검증하고 짧은 non-zero CLI error를 반환한다.

## Ponytail Cleanup

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/plans/2026-07-09-portfolio-maker-mvp.md`: 3,111줄의 historical implementation plan 중 약 2,138줄이 source/test 전문을 중복 보관한다. 실행 계획의 목적·검증·결과만 남기고 실제 file/commit 링크로 축약하면 약 1,800~2,100줄을 줄일 수 있다.
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/artifacts.py`: 단순 `Path.write_text()` wrapper와 전용 테스트는 약 40줄의 간접 계층이다. 이번 보안 fix와 결합하지 말고 독립 P3 cleanup으로만 고려한다.
- `GitHubActivityCandidate`와 `GitHubActivity`의 전 필드 복사는 약 20줄의 중복이다. 다만 connector/domain 경계를 유지할 명확한 이유가 있으면 문서화하고 그대로 둘 수 있다.
- `SourceStatus.APPROVED`와 `sources.approved_at`은 runtime에서 생성·사용되지 않는다. Migration 부담이 없는 0.1.0 시점에 제거하거나 reserved schema임을 명시한다.

## Closed / Non-Issues

- 현재 commit은 dependency-free runtime을 유지한다.
- 기존 승인 JSON object/type 검증, GitHub repository 단위 부분 실패 보존, GitHub activity upsert, expected approval error의 clean CLI mapping은 유지된다.
- `e7d2957..8b3e6f2`는 runtime code를 변경하지 않았고 review skill/report publication 준비만 추가했다.

## Next Minimal Checks

1. 위 P1/P2마다 focused failing test를 먼저 추가하고 red를 확인한다.
2. 최소 수정 후 각 focused test와 전체 suite를 실행한다.
3. approval 철회 후 `draft-portfolio` 단독 실행, stale/changed source, symbolic link/size bypass, 상대 forbidden path를 재현한다.
4. 반복 discovery가 `.portfolio-maker`를 후보로 만들지 않는지 확인한다.
5. 변경 snapshot row들이 서로 다른 immutable file을 가리키는지 확인한다.
6. GitHub endpoint별 성공 결과 보존과 malformed JSON의 clean CLI error를 확인한다.
7. `git diff --check`와 `git show --check --format=short HEAD`를 통과한다.

## Initial Outcome

NEEDS WORK. 테스트는 통과하지만 개인정보·승인·evidence freshness 경계의 재현 가능한 결함이 있으므로 `8b3e6f2`의 원격 publication 조건은 충족되지 않았다.
