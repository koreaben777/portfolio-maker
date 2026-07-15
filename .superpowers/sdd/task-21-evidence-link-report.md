# Task 21 Report: Semantic Evidence Links

## Scope

- Modified only `src/portfolio_maker/application/semantic_index.py`,
  `tests/test_semantic_index.py`, and this report.
- Did not modify legacy user data, runtime workspaces, `docs/reviews/`, web
  assets, or unrelated files.
- Used only existing source, ingestion, profile, evidence-selection, and SQLite
  repository APIs. No external LLM or direct SQLite mutation was used.

## Root Cause

Semantic-index preparation created every staged node with an empty
`evidence_ids` tuple. Apply preserved that staged value, so project-review's
current artifact-selection intersection had no local evidence IDs to expose.

## RED Evidence

The new focused test was copied into an isolated archive of HEAD, leaving the
worktree implementation untouched, and run with:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_semantic_index.py::test_prepare_semantic_index_links_current_approved_file_evidence_to_file_nodes -v
```

Result: `1 failed in 0.09s`. The staged file node contained `[]` instead of the
persisted evidence ID `[1]`.

## Implementation

- Select current `master_profile` evidence through `EvidenceSelectionService`,
  preserving source approval, artifact policy, source status, and stale gates.
- Canonicalize selected local-file source URIs and crawler file paths to file
  URIs, then attach matching evidence IDs only to staged file nodes.
- Keep directory/source nodes unlinked and keep evidence IDs out of semantic
  analysis chunks, preserving the locator-free chunk contract.
- Preserve staged evidence IDs through the existing apply path.

## Verification

All commands used `PYTHONDONTWRITEBYTECODE=1`.

| Command | Result |
| --- | --- |
| `python -m pytest tests/test_semantic_index.py::test_prepare_semantic_index_links_current_approved_file_evidence_to_file_nodes -v` | `1 passed in 0.07s` |
| `python -m pytest tests/test_semantic_index.py -v` | `17 passed in 0.48s` |
| `python -m pytest tests/test_project_boundary.py tests/test_evidence_selection.py -v` | `44 passed in 0.48s` |
| `python -m pytest -q` | `509 passed in 11.69s` |
| `git diff --check` | exit 0; no whitespace errors |

## Self-Inspection

- [x] The synthetic test persists and ingests an approved local file through
  existing APIs before preparing the semantic index.
- [x] Mapping occurs only during semantic-index preparation and only for
  current selected local evidence with the same canonical file URI.
- [x] Policy/stale selection gates and locator-free chunks remain intact.
- [x] No debug code, temporary repository artifacts, or unrelated edits were
  introduced.
- [x] Only the three requested files will be staged for the fix commit.

## Concern

No functional concerns found in the requested scope.

## Review Fix Evidence

### Root Cause Confirmation

- Semantic prepare reused `EvidenceSelectionService` with
  `artifact_kind="master_profile"`, so artifact policy incorrectly controlled
  node evidence linkage and malformed policy blocked preparation.
- That selector checked persisted source status but did not compare the current
  local file extraction with the latest managed snapshot, allowing an old
  evidence ID to attach after the file changed on disk.
- URI matching normalized accepted input instead of requiring the original URI
  to equal the canonical empty-authority file URI.

### Focused RED

```bash
PYTHONDONTWRITEBYTECODE=1 python -m pytest \
  tests/test_semantic_index.py::test_prepare_semantic_index_does_not_link_evidence_after_profiled_file_changes \
  tests/test_semantic_index.py::test_prepare_semantic_index_evidence_mapping_ignores_artifact_policy \
  tests/test_semantic_index.py::test_canonical_file_uri_matching_accepts_empty_authority_and_encoded_path \
  tests/test_semantic_index.py::test_canonical_file_uri_matching_rejects_noncanonical_variants -v
```

Result before the fix: `4 failed, 1 passed in 0.16s`. The failures showed the
stale evidence ID remained linked, changed artifact policy removed the link,
malformed artifact policy aborted prepare, and noncanonical URI variants were
accepted.

### Minimal Fix

- Removed artifact-policy and `master_profile` selection from semantic-index
  preparation.
- Reused the approved extractor, current source status, latest snapshot
  metadata, and managed snapshot validator before mapping evidence. A changed
  file is marked `stale_source`, and only the evidence stable ID for the
  verified current snapshot can attach.
- Required file URIs to use an empty authority and to exactly equal the
  canonical resolved URI, preserving canonical percent-encoded paths while
  rejecting `localhost`, noncanonical encoding/path forms, query, and fragment.
- Left source-approval policy hashing/apply checks and locator-free semantic
  chunks unchanged. Project review remains the artifact-selection authority.

### GREEN and Regression Evidence

All commands used `PYTHONDONTWRITEBYTECODE=1`.

| Command | Result |
| --- | --- |
| Focused review regressions above | `5 passed in 0.13s` |
| `python -m pytest tests/test_semantic_index.py -v` | `22 passed in 0.64s` |
| `python -m pytest tests/test_semantic_*.py tests/test_project_*.py tests/test_evidence_selection.py -q` | `118 passed in 1.65s` |
| `python -m pytest -q` | `514 passed in 12.42s` |
| `git diff --check` | exit 0; no whitespace errors |

### Fix Self-Inspection

- [x] Stale profiled local evidence cannot attach after an on-disk mutation.
- [x] Semantic prepare does not read or select through artifact policy.
- [x] Source approval, currentness, stale-state updates, and snapshot identity
  remain enforced before linkage.
- [x] Canonical URI edge cases and legitimate encoded file URIs are covered.
- [x] Source-approval apply checks and locator-free chunks remain covered.
- [x] No legacy data, docs/reviews, web files, or unrelated files were changed.
