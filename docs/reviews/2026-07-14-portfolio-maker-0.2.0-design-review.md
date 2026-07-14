# Portfolio Maker 0.2.0 설계·문서 개정 자체 검토

날짜: 2026-07-14
대상 branch: `codex/portfolio-maker-issue-13`
검토 범위: 0.2.0 설계 명세, Phase roadmap, 0.1.0 composition 기준선, README, 개발 원칙, GitHub Issue #13

## 1. 결론

문서 개정은 승인된 설계를 일관되게 반영한다.

- 현재 공개 runtime은 0.1.0으로 유지했다.
- 계층형 의미 인덱스, Project Boundary Detection, medium 이상 automatic inclusion, reversible exclusion, multi-skill Codex plugin 전체를 0.2.0 개발 완료 목표로 명시했다.
- 0.1.0의 review-required composition과 0.2.0 future behavior를 같은 시제로 섞지 않았다.
- semantic index 포함, evidence 승인, artifact inclusion, deployment permission을 별도 authority로 유지했다.
- 개인 근거 지식 그래프와 Google Drive는 0.2.0 기반의 후속 확장으로 남겼다.

## 2. 변경 문서 검토

### 새 권위 명세

`docs/superpowers/specs/2026-07-14-portfolio-maker-0.2.0-semantic-index-plugin-design.md`

- 제품 정의와 non-goal
- plugin/skill responsibility
- source-independent semantic node와 최소 edge
- complete structural crawl와 bottom-up summary
- parent/child/cross-directory boundary rule
- candidate v2, confidence, review/automatic mode
- medium 이상 automatic inclusion과 reversible exclusion
- hash chain, incremental update, failure recovery
- migration, privacy, test strategy, 0.2.0 완료 gate
- personal evidence knowledge graph 후속 방향

### 기존 명세와 roadmap

- 0.1.0 composition 설계에 0.2.0 superseding note를 추가했다.
- Phase roadmap에 0.2.0 release stage와 다음 developer boundary를 추가했다.
- 기존 #4/#2/#1/#11/#12/#13 역사와 구현 기준선을 삭제하지 않았다.

### README

- 기존 미커밋 첫 포트폴리오 튜토리얼을 보존했다.
- 현재 공개 버전 0.1.0과 다음 목표 0.2.0을 분리했다.
- 0.2.0 계획을 현재 실행 가능한 command처럼 설명하지 않았다.
- 0.1.0의 실제 500 local candidate limit 설명을 troubleshooting에 유지했다.

### 개발 원칙

- worktree에 없던 운영 문서를 현재 0.1.0과 승인된 0.2.0 기준으로 추가했다.
- 설계·구현 계획이 승인되면 별도 요청 없이 관련 문서를 동기화하는 원칙을 명문화했다.
- plugin skill 분할, authority separation, migration, failure recovery, release gate를 추가했다.

### GitHub Issue #13

- Issue를 0.1.0 기준선과 0.2.0 전체 release 목표를 함께 추적하도록 개정했다.
- 0.2.0 completion checklist와 non-goal을 추가했다.
- 설계 일부만 완료된 상태에서 Issue를 닫지 않도록 명시했다.

## 3. 실행한 검증

```text
git diff --check
test -f docs/superpowers/specs/2026-07-14-portfolio-maker-0.2.0-semantic-index-plugin-design.md
test -f docs/superpowers/specs/2026-07-14-codex-assisted-project-composition-design.md
test -f docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md
test -f docs/DEVELOPMENT_PRINCIPLES.md
rg CLI parser for documented commands and options
gh issue view/edit 13 --repo koreaben777/portfolio-maker
```

결과:

- whitespace/diff 검사 통과
- 로컬 relative link 대상 파일 존재 확인
- README tutorial의 `discover`, `approve`, `ingest`, `prepare-project-review`, `compose-projects`, `build-profile`, `draft-portfolio`, `render-html` command와 주요 option이 현재 CLI parser에 존재함을 확인
- GitHub Issue #13이 OPEN 상태이며 0.2.0 title/body로 갱신됨을 확인

문서 전용 변경이므로 Python test suite와 Vite build는 다시 실행하지 않았다. 이 문서들은 0.2.0 구현 완료를 주장하지 않으며 구현 단계의 필수 검증으로 명시한다.

## 4. 잔여 위험과 다음 gate

- 0.2.0 code, plugin manifest, child skills, schema migration은 아직 구현되지 않았다.
- 실제 implementation task decomposition과 checkpoint는 별도 implementation plan이 필요하다.
- 현재 branch의 문서 URL은 merge/push 전까지 public `main`에서 열리지 않을 수 있다.
- 실제 user-scope smoke test는 구현 이후 원본 변경 없는 별도 workspace에서 수행해야 한다.
- existing `.portfolio-maker-legacy-20260714T130632/`는 사용자 소유 untracked data로 보고 변경·stage하지 않았다.

다음 gate는 사용자의 문서 검토 승인 후 implementation plan을 작성하는 것이다.
