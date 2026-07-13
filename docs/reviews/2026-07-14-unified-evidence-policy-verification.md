# Issue #12 통합 근거 정책 검증 기록

날짜: 2026-07-14
상태: 구현 검증 완료
추적 Issue: #12 (열린 상태 유지)

## 구현 범위

- `source-approval.json`에 `excluded_directories`와 private activity approval을 추가하고,
  기존 `forbidden_paths`를 alias로 읽는다.
- `artifact-approval.json`의 생성물별 `restricted`/`open_public` 선택을 공통
  `EvidenceSelectionService`에 연결한다.
- SQLite origin migration은 기존 workspace를 보존하고 local/public/private origin과
  selection provenance를 artifact input manifest에 기록한다.
- private GitHub는 gh 인증·opt-in·allowlist·정확한 activity 승인까지 통과한 metadata만
  restricted artifact에 안전한 label로 반영한다. private repository raw clone/ingest는 없다.
- `portfolio-public.json`과 `portfolio.html`은 build-time manifest를 사용하며 자동 hosting을
  실행하지 않는다. `open_public`은 local/private origin을 거부하고 public deployment gate를
  별도로 통과해야 한다.

## 실행 결과

다음 명령은 모두 성공했다.

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_approval.py tests/test_artifact_approval.py tests/test_local_discovery.py tests/test_github_connector.py tests/test_github_private_policy.py tests/test_cli.py
172 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_sqlite_repository.py tests/test_evidence_selection.py
59 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_issue12_builders.py tests/test_profile_and_portfolio.py tests/test_public_portfolio.py tests/test_render_html.py tests/test_static_site.py --maxfail=1
70 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q tests/test_cli.py tests/test_render_html.py tests/test_static_site.py
33 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
359 passed

(cd web/portfolio && npm ci && npm run build)
PASS; Vite production build completed

(cd web/portfolio && ./node_modules/.bin/tsc --noEmit)
PASS
```

Static output validation, no-runtime-fetch scan, `git diff --check`, and
`git show --check --format=short HEAD` are rerun after the final implementation commit.

## 잔여 범위와 위험

- Issue #3 회사/JD 맞춤 생성, 실제 Sites hosting, public URL 배포는 구현하지 않는다.
- `open_public`은 별도 policy 재생성·검증 gate이며 이 작업에서는 hosting을 실행하지 않았다.
- GitHub endpoint 오류와 rate limit은 기존 fail-open discovery 계약을 따른다. 성공한 discovery와
  approval 재검증 전에는 activity를 artifact에 넣지 않는다.
- 기존 artifact policy가 없는 workspace는 0.1.0 호환 경로로 public GitHub evidence만
  manifest/HTML에 사용한다.
