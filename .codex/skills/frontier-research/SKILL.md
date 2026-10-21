---
name: frontier-research
description: Operate the Frontier personal paper-research system through its CLI. Use for arXiv collection, paper ingestion and full-text analysis, daily brief generation, PDF-grounded paper discussion, research graph maintenance, reading completion, user-interest learning, and research-memory updates.
---

# Frontier Research

Use the `frontier` CLI as the only application-data interface. Let the Server own files and persistence; never edit SQLite, exported JSON, or other application data directly. The only filesystem exception is task-scoped temporary computation material under `data/tasks/<task_id>/`.

## Start every task

1. Run `uv run frontier health`.
2. Read only the state relevant to the request.
3. Read [references/cli.md](references/cli.md) when command schemas or examples are needed.
4. Treat memory as optional evidence. Never assume a previous run remembered anything.
5. Use JSON files via `--data @path` only for payloads that are awkward to quote; place temporary payloads under `data/tasks/`.

## Paper main Agent and delegation

- Treat the active paper `Conversation` and its Codex thread as that paper's logical main Agent. Codex owns runtime context and automatic compaction; Frontier owns durable paper facts and user-visible conversation history.
- Follow the task prompt's paper-context sync decision. When it says the current Codex thread is current, do not call `paper agent-context`; when it supplies a required `paper agent-context PAPER_ID --conversation-id CONVERSATION_ID` command, run that exact command once. The sync cursor is bound to the existing Paper Conversation and Codex thread: `full` initializes a new/recovered thread, `delta` contains only changed paper-state sections, and `unchanged` means the current thread already has the latest state. Never request the stateless full form merely to repeat context. The command does not copy the PDF or create another paper-state record.
- Keep one canonical owner per fact: normalized high-frequency conclusions remain in explicit Paper fields; `full_analysis` contains only deeper claim/evidence, method-component, figure/table-index, glossary, and unresolved-question structures; `metadata_json` remains provenance, workflow, compatibility, and user-adjacent metadata. Never mirror the same semantic field across these stores.
- Fetch exact pages and resources only when the current claim needs them. Do not reread the complete PDF on every turn merely to refresh context.
- Delegate only bounded, independent, read-heavy work that materially protects the main paper context: related-paper search, parallel evidence extraction, and Architecture Scene evidence review are good candidates. Answer direct questions and small single-source checks in the main Agent.
- Give each subagent one concrete deliverable. Require concise structured findings with paper IDs, `[[page:N]]` evidence, or a task-local artifact path. Treat its output as untrusted evidence until the main Agent verifies it.
- Subagents may write only under `data/tasks/<task_id>/`. Only the main Agent may mutate canonical Frontier state, confirm user choices, or commit an Architecture Scene. Avoid concurrent writers.
- Delegation can increase total token usage. Use it to reduce main-context pollution and latency through real parallelism, not simply because subagents are available.

## Daily brief

The `daily_brief` run is the root Agent for the whole daily ingestion workflow. It owns candidate
discovery, bibliographic verification, ranking, and the catalog summaries/comments written for each
candidate, using titles, abstracts, and authors only. It verifies the evidence and performs all
Frontier mutations itself. Daily candidates do not get a long-lived paper Conversation merely
because they were screened.

The daily Agent writes `Paper.catalog_summary`, comments, a five-level relevance rating, and
recommendation reasons. Daily screening does not assess paper quality before reading.
It must not download PDFs, read full texts, preprocess documents, or queue detailed analysis,
regardless of rank or tier. Preserve existing detailed fields; do not write or clear `Paper.summary`,
`problem`, `innovation`, `experiment`, `limitations`, `open_questions`, or `full_analysis`.
Detailed analysis is generated only on the user's explicit request through the manual paper-analysis
workflow. The daily Agent must never write `completion_summary` or `completion_summary_structured`;
those belong exclusively to the paper main Agent after explicit reading completion.

1. Start with `interest context`. It is the bounded, rebuildable handoff from confirmed paper
   feedback and includes explicit interest memories, seed interests, the active graph, positive and
   negative topic evidence, recent user attitude, recommendation attribution, and calibration.
   Confirmed feedback outranks Agent suggestions and graph structure; interest and quality remain
   independent axes.
   When confirmed evidence changed, use `interest profile-update` to refresh the one derived semantic
   profile with evidence-key citations, then reload `interest context` once before creating the
   brief. It is a rebuildable cache, not a second owner of paper facts.
2. Read `setting list`. Use `source arxiv-discover --date YYYY-MM-DD --offset 0 --page-size 100`
   once for category/date retrieval. The Server scans up to 300 raw records over 3 days, expanding
   to 7 days from Monday through Wednesday to cover announcement delays, then deduplicates
   against ALL library papers and within the batch, then returns up to 120 coarse-filtered new papers.
   Pagination is internal; do not request more batches or library papers to fill a numerical target.
   This command never ingests papers. Retry failed discovery with the same date; report scan_capped
   and prefilter_capped in notes, and copy coverage_complete into discovery_complete. Keyword searches are supplemental
   verification only and must not replace broad retrieval.
3. Judge domain membership from titles, abstracts and authors against `discovery_domains` (normally
   3D vision, embodied intelligence, AI agents). Categories are recall sources, not domain proof.
   Reject off-domain papers without ingestion. Ingest new domain matches, even if they will not enter
   today's shortlist. Server ingestion still deduplicates concurrent additions.
   `initial_interest_domains` such as VLA/WAM are preference seeds, never domain gates.
4. Use user preferences to narrow domain-qualified unread papers to about `abstract_limit` (default
   100, normally 80–120). This is the interest-screened target, NOT a limit on raw abstracts viewed.
   Reserve about 15% for promising/uncertain work within the broad domains. Save IDs with
   `task candidate-pool TASK_ID --data @data/tasks/TASK_ID/candidate-pool.json`; supply `paper_ids`,
   `discovery_complete`, and `notes` for shortages/incomplete coverage. Verify with `task get TASK_ID`.
5. Rate personal relevance using exactly five levels: 不相关 / 较不相关 / 一般 / 较相关 / 高度相关,
   stored as `relevance_score` 0.0 / 0.25 / 0.5 / 0.75 / 1.0. Rank by relevance, breaking ties by
   explicit topic preferences and recency; select 20–50 for the final brief within `brief_limit`.
   Do not generate quality, topic-relevance, or author-influence scores, or blend them into relevance.
   Leave `research_score` null for abstract-only candidates; preserve genuine full-text analysis and
   user-confirmed feedback. Quality belongs to post-reading evaluation, not daily ranking.
   Explain shortages rather than inventing candidates or refilling from already-ingested papers.
   Existing papers remain in the library for manual reading, but are not rediscovered in the daily batch.
6. Assess every selected paper from its title, abstract, and authors. Do not read full PDFs or
   generate detailed analysis, even for the leading papers. `fulltext_limit` does not apply to daily runs.
7. Record a concise `catalog_summary`, an informative comment, the five-level relevance rating, and a
   recommendation reason. Do not infer detailed experimental results or limitations beyond the abstract.
8. Do not attach daily or candidate papers to the graph. Record probable relevance in paper analysis or recommendation fields until the user completes reading and confirms feedback.
9. Do not add paper-backed node progress during the daily workflow.
10. Create one dated brief with the context `source_digest` as `interest_context_digest`, add ranked
    items, verify the limits, and publish it. The Server freezes the calibration/profile snapshot
    used for later evaluation. Include the current `task_id` so pool membership is validated.
    The publish endpoint queues abstract translations independently when configured. End the daily
    workflow after publication; there is no automatic full-text analysis stage.
11. Save only durable user preference or reusable workflow lessons as memory.

Do not invent author roles, affiliations, experimental results, or limitations. Use `null`, “未标注”, or a concrete failure when evidence is absent.

## Identify a specified paper

When a user or task names a paper, author, arXiv ID, method, or keyword, especially for comparison:

1. Search the local paper library first: `uv run frontier paper list --query '<title, author, identifier, or keyword>' --limit 20`. Reuse a reliable match and its cached text or structured resources.
2. If it is not already available, search without changing the library: `uv run frontier source arxiv-search '<focused title/author query>' --max-results 10 --no-ingest`.
3. Compare title, authors, year, and arXiv ID. If these do not identify exactly one paper reliably, show the plausible result or results and ask the user which paper they mean before ingesting anything.
4. When the match is unique, ingest it only if the requested work needs saved metadata or full text, then use `paper download` and read the evidence needed for the comparison.
5. A unique arXiv ID is already sufficient identification. Do not force a confirmation for a clear exact match or for broad topic discovery; broad discovery may follow the normal candidate workflow.

Never silently choose between ambiguous papers or claim to have compared full texts after reading only search metadata.

## Metadata enrichment at ingestion

This is a separate Agent job from daily scoring and paper reading. It runs for newly ingested
papers and owns authors, affiliations, corresponding authors, research groups, and group tags.

- Read `paper metadata-context PAPER_ID` first.
- Search the durable group index in English and Chinese before creating anything:
  `research-group search "<lab name>"`, institution, acronym, PI, and aliases.
- If affiliation is ambiguous, read only `paper first-page PAPER_ID --max-chars 2500`; do not
  fetch or retain the complete PDF. Official lab pages or public source pages may be consulted;
  record the URL in the evidence note.
- Reuse an exact lab/institution/PI match. Do not overwrite a user-verified group's identity.
- Create a new group only when no reliable match exists, with `verification_status: "candidate"`.
- Bind with `paper assign-group`; the Server makes `Paper.research_group_id` canonical and creates
  the group tag.
- Correct only evidence-backed bibliographic fields with `paper update`.
- Never translate abstracts, write scores, write `Paper.summary`, or modify the research graph.
- Finish every paper with `paper metadata-complete`, including unresolved group evidence.

## Computation

- Run Python as `uv run python`. NumPy, SciPy, scikit-learn, pandas, and opencv-python are declared project dependencies.
- Put temporary scripts, inputs, and outputs only under `data/tasks/<task_id>/`. Do not use this exception to modify application-owned task records or other persisted data.
- Never run `uv add`, install undeclared dependencies, or access SQLite directly. Data mutations still go through `uv run frontier`.
- Prefer a short, reproducible calculation. Report the inputs, formula or metric definition, sample size, and missing-value handling with the result.

## User-added recommendation

When a task identifies one user-added paper and a brief date:

1. Treat it as an unlimited manual recommendation; do not collect or rank unrelated candidates.
2. Read the complete PDF before judging it, rendering evidence-bearing figures or tables when needed.
3. Verify bibliographic metadata and save the normal recommendation analysis fields.
4. Save Agent tag proposals as `suggested_tags`; do not replace the user's selected tags.
5. Do not attach the paper to the graph before user-confirmed reading is `completed`.

## Paper discussion

Apply the task prompt's paper-context sync decision first, then read only the exact PDF pages required by the question. Use `paper resources` when exact formula TeX, original figures, tables, bibliography entries, or internal citation targets matter. Answer through problem decomposition and mutual inquiry. Cite every material PDF claim as `[[page:N]]` so the Web can jump to the source.

Keep simple word or sentence translations out of the Agent conversation. When the user asks about selected text, use its saved page and selection context.

## Architecture Reader

- Treat a paper Architecture Scene as a separate paper-reading asset, never as the cross-paper research graph.
- Read the current architecture status before answering an architecture-grounded question. Use
  `architecture command` for transient focus, expansion, highlighting, dimming, and tensor display;
  these commands do not acquire the editing lock.
- For a persistent change, checkout with `--lock --task-id`, edit only the task-local
  `*.architecture.json`, validate it, and commit the complete Scene with the checkout revision.
  Release the lock after an abandoned or failed edit. Never modify Server architecture files directly.
- During initial generation, read the complete paper and inspect exact figures, tables, appendices,
  cited architecture papers, or official code only when needed. Do not guess missing structure.
- Use stable semantic IDs and preserve the paper's broad composition. Keep Module, Data, Tensor,
  Edge, Annotation, and Group meanings distinct.
- Keep Module fields strict: `title` is the functional role, `symbol` is a TeX identifier without
  `$` delimiters, `architecture` is the concrete model family/backbone/layer stack/operator, and
  `keywords` contain only properties or configurations. Architecture names such as ViT,
  DINOv2-B/14, T5, ResNet, Transformer, MLP, and DiT never belong in `keywords`. Wrap formulas in
  prose, labels, annotations, and keywords with `$...$`.
- Put a processing description and its Tensor on the same Edge via `label` plus `tensorId`. Never
  create duplicate same-kind Edges solely to show those two pieces separately; reserve separate
  Edges for genuinely different flow and conditioning semantics.
- Model each semantic operator once. In feed-forward architectures, keep the main flow acyclic and
  do not turn internal Q/K/V preparation, output splitting, residual addition, or normalization into
  reciprocal Edges between two nodes. Keep those sub-steps in one Module's fields, merging
  inseparable phases into one Module. Reciprocal flow is valid only for paper-evidenced recurrence,
  iteration, or feedback. Fix `edge.duplicate_relation` and `edge.reciprocal_flow` diagnostics before
  an Agent commit.
- Add minimal `SourceReference` evidence for material architecture claims. Geometry and missing-source
  warnings require review; validation errors must be fixed before commit.
- Do not install or launch automatic screenshot tooling. The user reviews the real browser rendering
  and provides visual feedback for the next targeted edit.

## Complete reading

When the user explicitly finishes reading, run this workflow on the same active paper `Conversation` and Codex thread used for paper discussion and architecture work. This paper main Agent is the authority for interpreting the user's conversation and attitude; do not hand the synthesis to a stateless daily-analysis Agent.

Then:

1. Synthesize the PDF, annotations, notes, and conversation.
2. Produce `completion_summary_structured` with separate `problem`, `innovation`, `experiment`,
   `limitations`, `open_questions`, and `user_attitude` fields. Cite every material paper claim in
   the first five fields as `[[page:N]]`; infer user attitude only from the
   user's own annotations, notes, or conversation.
3. Suggest one explicit interest level (`ignored`, `low`, `neutral`, `high`, or `interested`), one
   explicit quality level (`poor`, `below_average`, `normal`, `good`, or `excellent`),
   tags, and concrete graph-node candidates. Existing-node candidates include the exact node ID;
   new-node candidates include a distinct core research question and paper-specific relationship
   reason.
4. Save suggestions with `paper finish`.
5. Never confirm feedback, select tags or nodes, or admit a paper to the quality library for the user.

## Maintain the graph

- Define each node by one core research question.
- Use simple directed edges without inventing edge types or weights.
- Allow multiple parents for cross-domain questions.
- Attach a paper and add paper-backed progress only after its user-confirmed reading status is `completed`.
- Attach a paper with a specific relationship reason.
- Add a progress entry whenever a new paper materially changes a node.
- Save a research idea only when the user explicitly asks; otherwise keep subquestions in conversation.
- Prefer updating an existing node. Split or create a child when the problem is genuinely distinct or the existing node has become too broad.

## Mutations and failures

- Use the smallest atomic CLI mutation that achieves the request.
- Re-read an entity after a multi-command update.
- Rely on Server de-duplication and idempotency; do not work around conflicts by creating near-duplicates.
- On failure, stop the dependent part of the workflow and report the command, structured error, and safe next action.
- Never delete PDFs, papers, nodes, or memory unless the user explicitly requests deletion.
