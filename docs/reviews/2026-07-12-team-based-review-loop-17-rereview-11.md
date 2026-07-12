# Team Based Review Loop 17 - Re-Review 11 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `35c5652a614bddbdda8174f0963ed375f3044ff8`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 10 report, `d3dcf60..35c5652`, current Phase 1 policy, GitHub parser and profile serialization paths.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `261 passed in 8.11s`.
- Focused GitHub/discovery/profile/draft/SQLite suite -> `192 passed in 2.52s`.
- `git diff --check d3dcf60..35c5652` and `git show --check --format=short 35c5652` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes. Three lanes passed; reality-checker found the P2 below.

## Closed Findings

- Persistent forbidden-path guidance, GitHub project safety reconciliation, local project isolation, and whitespace-only commit subject rejection are covered and passed independent review.
- `@ponytail`, logical flow, and technical-writer lanes reported no further P1/P2/P3 findings in their scopes.

## Still Open / Newly Found Findings

### P2 - Arbitrary GitHub state can enter public-safe profile metadata unmasked

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:377` accepts arbitrary nonblank state text. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:154` serializes it to public-safe master profile metadata without `mask_public_value()`. Direct reproduction used a synthetic bearer-shaped PR state: parser accepted it, exact URL approval produced one claim, and the marker appeared in `master-profile.json`. A control-only legacy state also survived eligibility and serialized as an empty string.

Minimal fix: validate normalized state values at parsing for each supported activity type and revalidate legacy stored rows before profile output. Reject empty/invalid states, and defensively apply public-value redaction before serializing state. Add parser and legacy-row rebuild regressions.

## Ponytail Cleanup

No new cleanup. State validation is a necessary public-evidence boundary.

## Next Minimal Checks

1. Define minimal valid normalized states for each GitHub activity type at parser and legacy revalidation boundaries.
2. Redact state defensively in public profile payload and Markdown.
3. Run focused/full tests and direct bearer-shaped/control-only state regressions.
