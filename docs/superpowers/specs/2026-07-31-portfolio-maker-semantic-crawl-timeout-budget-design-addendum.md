# Portfolio Maker Semantic Crawl Timeout Budget Design Addendum

Status: Task 13 implementation contract

This addendum extends the Task 12 directory watchdog. It does not change source
approval, evidence eligibility, semantic judgment, artifact selection, delivery
scope, or the successful protocol-v2 semantic manifest/final/apply schemas.

## Aggregate boundary

`prepare-semantic-index` accepts `--directory-timeout-budget-seconds`. The
default is `120.0` seconds, the maximum is `3600.0` seconds, and the documented
positive minimum is `0.01` seconds. Booleans, non-numeric values, non-finite
values, values below the minimum, and values above the maximum are rejected
before workspace creation or any managed write.

The controller owns a monotonic timeout-wait budget. It subtracts only elapsed
time from requests which actually time out; successful directory enumeration
does not consume it. Each request receives the smaller of the per-directory
deadline and remaining budget. When the budget is exhausted, the active worker
is reaped, no replacement starts, traversal stops, and a fixed controlled
timeout-budget error is raised. Existing Task 12 per-directory behavior is
unchanged while budget remains.

The aggregate budget is an operational pre-publication boundary. It is not added
to a successful protocol-v2 input manifest, final manifest, receipt, apply
state, or replay schema. The budget never creates a semantic revision when it
is exhausted.

## Private timeout review

On aggregate exhaustion, Portfolio Maker writes exactly one local governance
file at `.portfolio-maker/reviews/semantic-crawl-timeout-review.json`. It is not
a semantic input, evidence, artifact, public manifest, HTML input, or a
database record. Its canonical JSON object has exactly these fields:

```json
{
  "version": 1,
  "status": "approval_required",
  "policy_hash": "<sha256>",
  "root_source_id": "<stable local source id>",
  "directory_timeout_seconds": 30.0,
  "directory_timeout_budget_seconds": 120.0,
  "timed_out_directory_count": 1,
  "unreadable_directory_count": 1,
  "consumed_timeout_wait_seconds": 120.0,
  "timed_out_relative_hierarchies": ["relative/path"],
  "reviewed_failure_sha256": null,
  "review_sha256": "<sha256>"
}
```

`review_sha256` is the SHA-256 of the canonical unsigned object. The document
must be a managed, private regular file; readers reject symlinks, duplicate or
extra keys, malformed types, noncanonical serialization, changed hash, stale
policy, and root-source mismatch. Relative hierarchies are accepted only in
this private document and must be non-absolute, non-empty hierarchy segments
without traversal or control characters. No absolute path, content, URL,
credential, or semantic signal is serialized.

For `approval_required`, the hierarchy list is sorted and unique, its length
equals `timed_out_directory_count`, `consumed_timeout_wait_seconds` equals the
configured aggregate budget, and `reviewed_failure_sha256` is null. For
`resolved`, both counts and consumed wait are zero, the hierarchy list is empty,
and an earlier valid failure hash is retained when one exists.

After a successful later prepare, the same managed file is atomically replaced
with `status: "resolved"`, current policy/root bindings, zero timed-out
hierarchies, and the prior valid failure review hash when available. A stale or
root-mismatched review never authorizes, seeds, or broadens a new crawl.
The resolved replacement is part of the semantic-input publication rollback
boundary: analysis or publish failure restores the exact prior
`approval_required` bytes, and an absent prior review remains absent.
Validation or writing failure is controlled and prevents semantic publication.

## Governance workflow

The source-governance skill may read this one private review to group its stored
relative hierarchies by common ancestor and propose narrow exclusions. It must
show the proposed exclusion, coverage implication, timeout count, and
alternatives, then wait for explicit user approval. It must never edit
`source-approval.json`, add an implicit Library/Containers exclusion, or rerun
preparation automatically. The review remains outside all public and semantic
outputs.

## Acceptance

Tests use injected monotonic clocks and blocking synthetic enumerators to prove
exact budget consumption, final-request shortening, no worker replacement after
exhaustion, interruption cleanup, pre-workspace validation, private-review
schema/trust checks, later resolution, approval-gated skill wording, and the
unchanged Task 12 worker lifecycle. No test scans user data or performs a home
pilot.
