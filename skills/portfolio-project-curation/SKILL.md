---
name: portfolio-project-curation
description: Use when inferring, regenerating, or explaining Portfolio Maker project candidates from a semantic review input.
---

# Portfolio Project Curation

Use this skill to turn an approved, safe semantic review input into explainable
project candidates for human review. The input is semantic review data only:
do not read raw files, arbitrary evidence, private URLs, credentials, or
unapproved local sources to fill gaps.

## Workflow

1. Confirm that the review input is the complete approved input and use only
   its node summaries, hierarchy, topics, and available evidence IDs. Do not
   infer facts from a path or filename that the input does not support.
2. Compare each parent with its descendants before deciding whether the parent
   remains the coherent project boundary, whether a child is independently a
   project, or whether multiple directories form one semantic cluster.
3. Emit candidate review input with grounded rationale, explicit counter
   signals, confidence, and an unassigned list. Keep uncertain or unsupported
   material unassigned rather than forcing a project.
4. Treat the result as candidate review input. It is not semantic project
   approval, artifact approval, deployment approval, or permission to create an
   automatic project output.

## Candidate v2 Contract

The candidate payload uses `version: 2` and the current review input's
`review_input_sha256`. Every candidate has exactly these model-aligned fields:

- `id`, `project_id`, `title`, `overview`
- `boundary_type`: `directory_root`, `independent_child`,
  `cross_directory_cluster`, or `manual`
- `boundary_node_ids`, `boundary_fingerprint`
- `evidence_ids`, `grouping_rationale`, `counter_signals`, `review_reasons`
- `confidence`: `low`, `medium`, or `high`

Use only node IDs and positive evidence IDs present in the review input. Keep
IDs unique and do not invent an evidence ID, node, fingerprint, claim, title,
or project fact. Each `grouping_rationale` item must cite the supporting input
evidence IDs, for example `[evidence_id=101] ...`; rationale without a
traceable ID is not grounded. `counter_signals` is a list, including `[]`
when none is present, and must state material evidence against the proposed
boundary rather than silently discarding it.

## Boundary Judgment

### Parent coherence

Keep a `directory_root` candidate when the parent summary describes one
coherent product, outcome, or work unit and its children read as components,
iterations, documentation, or implementation parts of that same unit. Record
child evidence that supports the parent and any counter-signal that suggests a
separate product.

### Independent child

Use `independent_child` only when the child has multiple converging semantic
signals of an independently meaningful product: a distinct purpose or user
problem, a distinct output or lifecycle, and evidence that is not merely a
subcomponent of the parent. A README, `package.json`, `.git`, manifest,
file-count, folder name, or any other single marker is only a signal and never
the decisive project-boundary rule. Do not split every marked nested folder;
avoid child explosion and state the evidence supporting the split.

### Cross-directory cluster

Use `cross_directory_cluster` only when two or more nodes have multiple
explicit semantic links to the same product or outcome. Explain why the
directories belong together, cite the evidence IDs for those links, and list
counter-signals such as separate users, release lifecycles, or conflicting
purposes. Similar names, shared tooling, or a common file type alone do not
justify a cluster.

Use `manual` or low confidence when the input cannot distinguish these cases.
Never manufacture certainty from missing semantic evidence.

## Confidence and Unassigned Evidence

- `high`: several independent semantic signals converge, the boundary is
  coherent, and no material counter-signal remains.
- `medium`: a meaningful candidate exists but parent/child or cross-directory
  ambiguity remains; preserve the counter-signals and recommend review.
- `low`: evidence is sparse, conflicting, or insufficient to justify a stable
  boundary.

Evidence without a supported project relationship belongs in
`unassigned_evidence_ids`. Include only IDs available in the review input,
keep them disjoint from assigned evidence, and explain why they remain
unassigned. Unassigned evidence is a valid review outcome, not a reason to
invent a project.

## Privacy and Authority Boundaries

Keep titles, overviews, rationale, counter-signals, and review reasons free of
absolute paths, raw locators, file URLs, private repository URLs, credentials,
and other sensitive local details. Use redacted semantic labels only when the
approved input already provides them. Semantic review input authorizes
analysis of that input only; it does not authorize broader discovery,
evidence approval, artifact inclusion, public delivery, or materialization.
