# Portfolio Maker Semantic Crawl Watchdog Design Addendum

Status: Task 12 implementation contract

This addendum extends the 2026-07-31 resumable Codex semantic-index design. It
does not change source approval, evidence eligibility, semantic judgment, artifact
selection, or delivery policy.

## Failure boundary

`prepare-semantic-index` must not call directory enumeration in the controlling
process. A filesystem `opendir` call can remain blocked without returning an
exception, so ordinary `try`/`except` and worker threads cannot provide a bounded
result.

Directory enumeration uses one reusable `spawn` process. Each request contains
one directory locator; the response materializes only immediate child names and
no-follow stat metadata. Recursion, policy decisions, stable node identity,
ordering, and parent/child ownership remain in the controlling process.

The worker protocol is versioned and exact. A crash, EOF, unknown request ID,
wrong protocol, malformed response, or unexpected exception is an internal
controlled failure. It is never downgraded to an unreadable source node.

## Deadline contract

- CLI flag: `--directory-timeout-seconds`
- default: `30.0` seconds per directory
- maximum: `300.0` seconds per directory
- accepted values: finite real numbers greater than zero and no greater than the
  maximum
- rejected before crawl or managed writes: booleans, non-numbers, NaN, infinity,
  zero, negative values, and values above the maximum

The parent waits only for the configured deadline. On timeout it terminates and
joins the worker, escalates to kill and joins again if needed, then starts a fresh
worker before another directory request. Normal completion reuses the worker.
Every return and interruption path closes the channel and joins the worker; no
thread, live helper, or zombie may remain.

## Result semantics

A non-root timeout keeps that directory node with `analysis_status=unreadable`,
does not enumerate or infer descendants, records exactly one timeout, and
continues with later siblings. It does not change approval or create an automatic
exclusion.

A root timeout raises a controlled semantic-index error before revision creation
or semantic publication. No input manifest, work plan, private structural or
locator chunk, or semantic report is written.

The existing immediate `OSError` behavior remains unreadable. Symlinks remain
no-follow, hard-link aliases remain distinct, explicit exclusions remain pruned,
ordering and stable IDs remain deterministic, and there is no global node cap.

## Protocol-v2 binding

The exact protocol-v2 input manifest gains these fields:

- `directory_timeout_seconds`: the normalized configured deadline
- `timed_out_directory_count`: nonnegative timeout count
- `unreadable_directory_count`: nonnegative unreadable-directory count

The counts must agree with the prepared crawl and cannot exceed the structural
node count. Exact-schema loaders used by status, materialization, validation,
finalization, apply, and replay reject missing, extra, malformed, or changed
values. The canonical manifest bytes and existing chunk/work-plan hashes remain
the trust boundary; no locator enters these fields.

## Presentation and privacy

Successful prepare stdout and the private semantic report may expose only:

- configured directory timeout;
- timed-out directory count;
- unreadable directory count;
- existing aggregate work estimates.

They must not expose absolute or relative locators, directory names, source
content, credentials, or semantic signals. Controlled worker failures use fixed,
non-disclosing messages.

## Acceptance

Synthetic, injected enumeration tests must prove child timeout continuation, root
fail-closed behavior, crash and malformed-protocol rejection, worker replacement
and cleanup, deterministic output, CLI value validation, safe aggregate output,
and protocol-v2 tamper rejection. Tests must not scan user data or wait for the
production deadline.
