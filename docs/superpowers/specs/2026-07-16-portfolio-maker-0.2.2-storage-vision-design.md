# Portfolio Maker 0.2.2 — Managed History and Codex Vision Design

날짜: 2026-07-16  
상태: 사용자 승인 설계 · 구현 전  
Release 목표: `0.2.2`  
추적 Issue: [#5](https://github.com/koreaben777/portfolio-maker/issues/5), [#14](https://github.com/koreaben777/portfolio-maker/issues/14), 후속 [#8](https://github.com/koreaben777/portfolio-maker/issues/8)

## 1. 결정 요약

0.2.2는 두 개의 연결된 기능을 제공한다.

1. **managed history lifecycle (#5)** — `.portfolio-maker/`가 만든 snapshot, 파생 분석, 그에 대응하는 SQLite 이력을 사용자가 검토·백업·정리할 수 있게 한다.
2. **선택형 Codex vision adapter (#14)** — 사용자가 명시적으로 선택하고 전송을 확인한 승인 로컬 파일을 현재 Codex App이 인식할 수 있는 형식으로 분석해, 마스킹된 구조화 결과만 evidence workflow에 넣는다.

#5를 먼저 구현한다. #14가 생성하는 파생 분석도 같은 보존·정리·복구 경계를 사용해야 하기 때문이다.

## 2. 제품 경계와 불변식

### 2.1 source와 managed state

- 원본 사용자 source는 삭제, 이동, 이름 변경, 복사 대상이 아니다.
- local source는 기존 `source-approval.json`에 포함되고 현재 파일 정책을 통과한 regular file만 #14의 대상이다.
- GitHub repository/activity에는 원본 로컬 파일을 가져오거나 clone하지 않는다. GitHub raw-content 분석은 별도 source adapter 범위다.
- `.portfolio-maker/`에는 기존 text snapshot, 새 sanitized vision analysis, review·cleanup journal, SQLite database와 sidecar만 관리한다.
- public manifest, static HTML, Sites 입력에는 source URI, raw path, raw binary, vision request, vision result 원문을 넣지 않는다.

### 2.2 두 번의 사용자 결정

Codex에 전송해도 된다는 사용자의 전제는 다음 두 결정을 하나로 합치지 않는다.

1. **analysis consent**: 특정 승인 source를 이번 run에서 Codex에 전달·분석하는 동의다. 기본값은 비동의이며 매 request마다 명시 확인한다.
2. **artifact consent**: 결과를 검토해 evidence로 포함하고 현재 artifact policy에 따라 사용하도록 하는 결정이다. analysis success만으로 project, claim, restricted/open_public delivery 승인이 되지 않는다.

### 2.3 형식 지원의 정의

0.2.2는 확장자 allowlist나 로컬 OCR/renderer를 만들지 않는다. 대상은 **현재 Codex App task surface가 직접 인식할 수 있는 승인 local regular file 전부**다. 문서, 스프레드시트, 프레젠테이션, PDF, 이미지 등은 현재 환경이 처리할 수 있으면 동일한 protocol로 전달한다.

현재 session이 특정 파일을 직접 첨부·열람·분석할 수 없으면 이를 `unsupported` 또는 `unavailable` 결과로 기록한다. 지원하지 않는 형식을 이미지/PDF로 변환하거나 외부 OCR service로 보내지 않으며, text-only workflow를 실패시키지 않는다. 독립 배포 가능한 OCR/renderer 및 일반 image extraction은 #8의 후속 범위다.

## 3. Architecture

```text
approved local source
  -> prepare-vision-analysis (CLI: policy + hash + one-time request)
  -> portfolio-vision-analysis skill (explicit consent + current Codex capability)
  -> structured result only (no raw input in managed output)
  -> apply-vision-analysis (CLI: revalidation + masking + pending review)
  -> user include/exclude decision
  -> existing evidence selection -> profile / Markdown / HTML

managed snapshots + sanitized vision analyses + DB rows
  -> history-plan (read-only)
  -> history-export (outside-workspace backup receipt)
  -> history-apply (exact confirmation + journal + quarantine + DB transaction)
  -> purge/recovery completion
```

Codex App/plugin은 content understanding과 user interaction을 담당한다. Python CLI와 application service는 policy enforcement, source hash revalidation, persistence, cleanup, and output safety를 담당한다. CLI는 Codex API key, token, model invocation, raw binary upload를 구현하지 않는다.

## 4. Managed history lifecycle (#5)

### 4.1 Managed units

정리 대상은 다음 세 종류다.

| Unit | Persistent location | Eligibility |
|---|---|---|
| Local text snapshot | `raw/snapshots/local/*.json` + `source_snapshots` row | latest snapshot이 아니고 active evidence/claim/project가 참조하지 않으며 정책의 최소 연령을 통과한 경우 |
| Sanitized vision analysis | `derived/vision/*.json` + `vision_analyses` row | evidence로 include되지 않았고 정책의 최소 연령을 통과한 경우 |
| Finished vision request receipt | `reviews/vision-requests/*.json` + `vision_analysis_requests` row | completed/unsupported/failed/cancelled 상태이고 연결된 analysis/evidence가 없으며 정책의 최소 연령을 통과한 경우 |

`portfolio.db`와 `-journal`, `-wal`, `-shm` sidecar는 하나의 database family로 계속 보존한다. 0.2.2가 database file 자체를 삭제하거나 교체하지 않는다. 정리 후 필요하면 repository lock 안에서 SQLite `VACUUM`을 실행해 해제된 page를 회수한다.

### 4.2 Policy and planning

`history-policy.json`은 `.portfolio-maker/reviews/`에 private managed configuration으로 저장한다. 정책이 없으면 모든 unit을 **retain**한다. 자동 scheduler는 없다.

Policy schema v1은 다음 고정 필드를 사용한다.

```json
{
  "version": 1,
  "minimum_age_days": 30,
  "keep_latest_text_snapshots_per_source": 1,
  "retain_included_vision_analyses": true,
  "retain_pending_review_vision_analyses": true,
  "retain_incomplete_vision_requests": true
}
```

`history-plan`은 read-only command다. 관리 directory와 DB family identity를 검증하고, candidate마다 table row id, relative managed path, content hash, device/inode, size, reason을 포함한 immutable plan을 만든다. path가 managed root 밖이거나 symlink/non-regular file이면 plan을 실패시킨다. report에는 file count와 byte estimate만 표시하며 raw path·content는 표시하지 않는다.

### 4.3 Backup, execution, and recovery

실제 정리는 다음 protocol만 사용한다.

1. `history-export`가 사용자가 지정한 **workspace 밖의 empty directory**에 plan manifest, candidate JSON, SQLite consistent backup을 작성하고 checksum receipt를 만든다. backup destination이 workspace 또는 `.portfolio-maker/` 아래이면 거부한다.
2. `history-apply`는 plan id, export receipt hash, 정확한 confirmation phrase를 모두 요구한다. 이 command는 plan이 만들어진 뒤 source DB/managed file identity가 변했으면 아무것도 삭제하지 않고 `stale_plan`으로 종료한다.
3. repository-wide exclusive lock과 SQLite write transaction 아래에서 candidate regular files를 `.portfolio-maker/quarantine/<execution-id>/`로 same-filesystem rename한다. journal에는 각 단계와 원래 relative path를 fsync해 기록한다.
4. file move가 모두 성공한 뒤 DB rows와 더 이상 참조되지 않는 evidence row를 하나의 transaction으로 삭제한다. transaction 실패 시 quarantine 파일을 원위치로 restore한다.
5. commit 뒤 quarantine 삭제 전 중단되면 journal은 `committed_pending_purge` 상태로 남는다. 다음 `history-apply --resume <execution-id>`는 DB reference를 재검증하고 purge만 재개한다. commit 전 중단은 `history-apply --recover <execution-id>`가 restore한다.
6. purge가 끝난 뒤에만 optional `VACUUM`을 실행한다. `VACUUM` failure는 deleted unit의 rollback 요구가 아니라 `vacuum_pending` journal state로 남기며, 재실행 가능해야 한다.

이 protocol은 original source, approval files, artifact files, semantic-index files, unrelated user files을 candidate로 만들지 않는다.

## 5. Codex vision adapter (#14)

### 5.1 Private request contract

`prepare-vision-analysis --workspace <path> --source-id <id> --confirm-codex-share`는 다음을 확인한 뒤 private request receipt를 만든다.

- source type이 `local_file`이고 `source-approval.json`의 approved URI와 정확히 일치한다.
- 현재 `FilePolicy`를 통과하고 symlink가 아닌 regular file이다.
- descriptor 기반 read로 current SHA-256, size, device/inode를 획득한다.
- source가 정리 중이 아니며 database family와 managed request directory가 안전하다.

receipt에는 `request_id`, source id, display name, source content hash, size, requested-at, adapter name `codex-app-vision-v1`, status `pending`, confirmation timestamp만 저장한다. raw file bytes와 raw absolute path는 receipt, artifact, semantic-index input, public output에 저장하지 않는다. 전용 skill은 request id로 source를 안전하게 다시 열어 현재 Codex App이 제공하는 직접 파일 인식 surface에만 전달한다.

### 5.2 Specialized skill boundary

새 `portfolio-vision-analysis` skill만 raw selected source를 읽을 수 있다. 기존 `portfolio-maker` router, semantic-index, project-curation, artifact skills는 raw source/vision receipt를 계속 읽지 않는다.

skill workflow는 다음 순서로 고정한다.

1. pending request의 display name, size, hash prefix, Codex 전송 사실을 사용자에게 다시 보여 준다.
2. 현재 Codex App session에서 file type을 직접 인식할 수 있는지 확인한다.
3. 가능하면 포트폴리오 관련 사실만 JSON schema에 맞춰 분석한다. 가능하지 않으면 `unsupported`/`unavailable` JSON만 반환한다.
4. `apply-vision-analysis` 전에 source file을 같은 safe-open rule로 다시 hash해 request hash와 일치하는지 확인한다.
5. 어떤 상황에서도 raw source, raw OCR transcript, absolute path, credential-like text를 chat output, review input, artifact로 복사하지 않는다.

### 5.3 Structured result and review state

`apply-vision-analysis --workspace <path> --result <private-result.json>`은 request id, source hash, adapter version, status를 검증한다. completed result schema v1은 다음 필드를 요구한다.

```json
{
  "schema_version": "vision-analysis-v1",
  "request_id": "uuid",
  "source_content_hash": "sha256 hex",
  "adapter": "codex-app-vision-v1",
  "status": "completed",
  "confidence": "low | medium | high",
  "summary": "portfolio-relevant sanitized summary",
  "observations": [
    {
      "kind": "role | deliverable | technology | outcome | context",
      "text": "sanitized observation",
      "confidence": "low | medium | high"
    }
  ],
  "requires_manual_review": true
}
```

All user-controlled strings are length-limited, schema-validated, normalized, and passed through existing secret masking before persistence. `completed` always sets `requires_manual_review=true`; `unsupported`, `unavailable`, `failed`, and `cancelled` cannot contain observations and never create evidence.

The result is saved as `derived/vision/analysis-<request-id>-<result-sha256>.json`. Its row records request id, source id, source hash, result hash, adapter, status, confidence, review status, and relative path. Initial review status is `pending`.

`set-vision-analysis-state --analysis-id <id> --state included|excluded` is the second consent gate. `included` creates or refreshes one derived `evidence_items` row with a non-path locator `derived:vision-analysis:<id>` and a stable id `vision-analysis:<id>:<result-sha256>`. `excluded` removes that derived evidence row if it is not referenced; if it is referenced, the command rejects the state change and requires existing artifact/project review to be updated first. No automatic inclusion exists.

### 5.4 Capability and failure behavior

- The adapter makes no promise that a specific extension works on every account, model, app version, or session.
- A Codex inability to attach, inspect, or reliably interpret the selected file results in a persisted `unsupported` or `unavailable` receipt, not a fallback converter, OCR package, or external API call.
- A user cancellation creates `cancelled`; a source hash mismatch creates `stale_source`; a schema/policy failure creates `failed` with a non-sensitive reason code.
- Failed, unsupported, unavailable, cancelled, and stale requests never block text ingestion, semantic index refresh, profile, draft, manifest, or HTML generation.

## 6. Persistence and migration

The existing SQLite migration style remains additive. New tables are:

```text
vision_analysis_requests
  id, source_id, source_content_hash, source_size, adapter,
  status, confirmation_at, requested_at, completed_at, reason_code

vision_analyses
  id, request_id, source_id, source_content_hash, result_hash,
  relative_path, adapter, status, confidence, review_status,
  created_at, reviewed_at

history_cleanup_plans
  id, policy_hash, manifest_hash, status, created_at, export_receipt_hash,
  execution_id, completed_at

history_cleanup_journal_entries
  execution_id, sequence, stage, unit_kind, row_id, relative_path,
  content_hash, recorded_at
```

Foreign keys tie vision analyses to their request and source; cleanup tables retain a receipt after its candidate rows are removed. Relative managed paths are validated against their exact managed directory descriptor before read, rename, or unlink. Existing 0.1.0/0.2.1 tables and paths stay valid without data rewrite.

## 7. CLI and skill surface

New commands are intentionally narrow.

```text
portfolio-maker history-policy --workspace PATH --write-sample
portfolio-maker history-plan --workspace PATH
portfolio-maker history-export --workspace PATH --plan-id ID --backup-dir PATH
portfolio-maker history-apply --workspace PATH --plan-id ID --export-receipt SHA256 --confirm CONFIRMATION
portfolio-maker history-apply --workspace PATH --resume EXECUTION_ID
portfolio-maker history-apply --workspace PATH --recover EXECUTION_ID

portfolio-maker prepare-vision-analysis --workspace PATH --source-id ID --confirm-codex-share
portfolio-maker apply-vision-analysis --workspace PATH --result PATH
portfolio-maker list-vision-analyses --workspace PATH
portfolio-maker set-vision-analysis-state --workspace PATH --analysis-id ID --state included|excluded
```

`portfolio-vision-analysis` is the only new plugin skill that accepts the private request handle. It owns user-facing consent, Codex capability handling, structured response construction, and direct invocation of the four vision commands. It does not edit SQLite or managed JSON itself.

## 8. Verification gates

### #5 lifecycle

- policy absent means plan has zero candidates; plan is read-only.
- latest and referenced snapshots, included/pending analyses, incomplete requests, artifacts, approval files, and original source files never appear in a plan.
- empty/inside-workspace backup target, stale plan, wrong confirmation, wrong receipt hash, symlinked file/directory, path replacement, database sidecar replacement, and concurrent operation all fail without destructive writes.
- a forced move failure restores all previous candidates; DB transaction failure restores quarantine; post-commit purge interruption resumes idempotently.
- exported manifest and SQLite backup can be checked against plan hashes; `VACUUM` failure leaves an explicit resumable journal status.

### #14 adapter

- unapproved, excluded, missing, non-regular, symlinked, or changed source is refused before Codex analysis.
- no confirmation produces no request; cancellation and unsupported capability create non-evidence receipts.
- result schema, source hash, request status, size/text limits, secret masking, and unsafe strings are validated before a derived file/DB row exists.
- completed analysis begins `pending`, cannot appear in profile/draft/manifest/HTML, and only becomes derived evidence after explicit inclusion.
- excluded, failed, low-confidence, or pending results do not reach any artifact; a normal text-only run passes when no vision capability is present.
- public HTML/manifest tests prove that request IDs, source URI, raw paths, raw input, and derived analysis text are not emitted unless a separately approved existing artifact policy permits a sanitized evidence claim.

## 9. Explicit non-goals

- local OCR engine, PDF renderer, Office converter, or new external LLM API/client
- automatic bulk submission of approved files to Codex
- direct analysis of GitHub repository raw content, Google Drive, network storage, or private repository clones
- raw binary, full raw transcript, absolute source path, credential, or secret publication
- automatic portfolio project creation, claim acceptance, company/JD tailoring, Sites deployment, or `open_public` release based on vision output
- automatic scheduled deletion or deleting user-owned original sources

## 10. Release acceptance

`0.2.2` may release only when #5 and #14 focused suites plus the full existing test suite pass, the new skill's safe input/output boundary is reviewed, a capability-unavailable manual smoke preserves text-only behavior, and the README tutorial explains both confirmation gates and recovery procedure. #8 remains open after the release.
