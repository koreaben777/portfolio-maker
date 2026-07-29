---
name: portfolio-vision-analysis
description: Use when the user explicitly selects one approved local regular file for consented Codex App native vision analysis and review.
---

# Portfolio Vision Analysis

Use this skill only for one explicitly selected approved local regular file at
a time. This is a privileged, request-bound workflow. Source approval alone,
authentication, a prior request, or a request to build a portfolio never
authorizes raw-source access.

## Required Sequence

Follow these steps in order:

1. Obtain explicit confirmation that the one selected source may be sent to
   Codex. Only then run `prepare-vision-analysis --confirm-codex-share`:

   ```bash
   portfolio-maker prepare-vision-analysis \
     --workspace . \
     --source-id SOURCE_ID \
     --confirm-codex-share
   ```

2. Read only the pending request and the exact approved source bound to that
   request ID. This is the only raw selected source permission in the plugin.
   It never permits another source, a directory, or a bulk collection.

3. Analyze that source using only the current Codex App native capability.

4. Do not install or invoke OCR, PDF/Office converters, external APIs,
   fallback uploaders, or other network services.

5. Construct either the existing closed-schema completed JSON or the existing
   status-only unsupported/unavailable/failed/cancelled JSON. Do not add fields,
   locators, raw source text, or credentials.

6. Apply the result through the application boundary:

   ```bash
   portfolio-maker apply-vision-analysis \
     --workspace . \
     --result RESULT_JSON
   ```

7. Explain that a completed result begins pending and requires an explicit
   included or excluded decision:

   ```bash
   portfolio-maker set-vision-analysis-state \
     --workspace . \
     --analysis-id ANALYSIS_ID \
     --state included
   ```

   Use `--state excluded` when the user rejects it.

## Safety Boundary

- Operate on one explicitly selected approved local regular file at a time.
- Never bulk-read approved sources and never infer consent.
- Do not edit SQLite, request/result JSON, managed artifacts, or approvals
  directly. Use the CLI boundaries above.
- Keep raw source bytes, extracted text, source locators, paths, credentials,
  and request/result payloads out of conversation summaries and downstream
  skills.
- Treat unsupported, unavailable, failed, and cancelled outcomes as
  non-evidence.
- Hand only explicitly included sanitized evidence to downstream artifact
  policy. Pending and excluded analysis stays out of profile and portfolio
  output.
