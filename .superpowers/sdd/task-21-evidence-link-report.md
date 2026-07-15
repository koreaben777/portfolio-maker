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
