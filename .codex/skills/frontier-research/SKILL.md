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
- Paper prose has exactly two canonical Markdown fields: `pre_read_summary` and `post_read_summary`. Put methods, evidence, experiments, limitations and open questions inside these documents. Never add structured summary sections, analysis JSON copies, or paper-content snapshots. `metadata_json` stores provenance and workflow state only.
- There is one score dimension, with three independent sources: `user_score`, `agent_score`, and `model_score`. Each is a real number in [0,1]; null means unscored and zero is valid. Do not create interest, quality, or relevance sub-scores. Only explicit user input sets `user_score`; never infer it from status, suggestions, or booleans.
- Fetch exact pages and resources only when the current claim needs them. Do not reread the complete PDF on every turn merely to refresh context.
- Only paper reading/discussion Agents may use a subagent, at most one concurrently and one level deep.
  Use it only for bounded read-only evidence work; only the main Agent may write canonical state.
- Daily/background Agents must never spawn subagents, launch another Codex, or enqueue per-paper Agents.

## Author and group cards

During a paper discussion, curate evidence-backed cards with the user through
`paper author list/get/create/update/delete` and `research-group search/get/create/update/delete`.
Use `paper assign-group` for the paper's canonical group. Read command `--help` before mutations.
Author cards are scoped to a paper: preserve author order, distinguish paper-time `affiliation`
from `current_affiliation`, and include `evidence`. Never merge people by name alone, infer a lab
from its institution, or assert user verification without explicit user agreement.

## Daily brief

Foreground and background each have one independent root-Agent slot; foreground reading may
use one child, while background work must never delegate.
A daily run uses exactly one main Agent, executes serially, and never delegates or queues metadata
Agents. In this same run, curate first-author and main completing-institution text for selected
papers: use author order from metadata, `paper metadata-context`, and only when necessary the
bounded `paper first-page` snippet. Save `first_author`, verified `primary_affiliation`, and
per-author affiliations/evidence through the CLI. This first-page snippet is the sole exception
to abstract-only screening; do not read/download complete PDFs. Preserve verified user facts and
record missing evidence as unknown. Group cards are curated with the user during reading.

The daily Agent owns discovery, abstract-based screening, bibliography checks, ranking and publication.
It uses no long-lived paper Conversations and does not download full PDFs or generate full-text
analysis. Preserve existing `post_read_summary`, explicit user scores, notes and verified metadata.

1. Read `interest context` and `setting list`. Confirmed scalar user scores and explicit preferences
   are the evidence for personalization. Refresh a stale derived profile with evidence-key citations
   through `interest profile-update`, then reload context once.
2. Run `source arxiv-sync --date YYYY-MM-DD` once for the task date. It fixes the cutoff at that
   date's configured daily time, trains from explicit user scores, stages every arXiv update since
   the durable cursor, deduplicates and applies the domain gate. There is no daily numeric scan cap.
   Retry incomplete coverage unchanged. Recover interrupted runs from saved same-date candidate
   pools when the task prompt specifies recovery source IDs; recheck current eligibility.
3. Use only this discovery batch. Independently review `agent_review_paper_ids` using title,
   abstract and authors; preserve confident model routing. Save a continuous `agent_score` and clear
   `model_review_required`. Do not copy model predictions into Agent scores. Exclude previously
   published recommendations and explicitly rated or commented papers. Never refill from old papers.
4. Save the complete eligible pool with `task candidate-pool`, `paper_ids`, `discovery_complete`,
   `discovery_run_id` and notes for shortages or failures. Recovery without a discovery run ID may
   only reuse same-date saved pools.
5. Rank by `agent_score`, falling back to `model_score`; null is unknown. Select 20–50 papers within
   `brief_limit`. Verify first-author/main-institution text inline as described above. For final
   items write a concise `pre_read_summary` Markdown combining overview, grounded highlights and
   recommendation reason. Do not infer detailed results beyond source evidence. Extract
   `model_short_name` and `search_aliases` only from explicit metadata evidence.
6. Do not attach candidate papers to the research graph or create paper-backed graph progress.
7. Create one dated brief with the current task ID and the context `source_digest` as
   `interest_context_digest`, add ranked members and publish. Brief membership is fixed at publication;
   all displayed paper content comes from the current Paper record. Never create content snapshots.
   The Server may queue independent abstract translation requests; no further Codex job is required.
   Explain shortages. For an empty complete window, save an empty pool with notes and publish an
   empty brief. Stop after publication.
8. Save only durable explicit preferences or reusable workflow lessons as memory.

Do not invent roles, affiliations or experimental results. Unknown facts remain null or are described
as unknown. Group cards are curated during user reading, without separate metadata Agents.

## Identify a specified paper

When a user or task names a paper, author, arXiv ID, method, or keyword, especially for comparison:

1. Search the local paper library first: `uv run frontier paper list --query '<title, author, identifier, or keyword>' --limit 20`. Reuse a reliable match and its cached text or structured resources.
2. If it is not already available, search without changing the library: `uv run frontier source arxiv-search '<focused title/author query>' --max-results 10 --no-ingest`.
3. Compare title, authors, year, and arXiv ID. If these do not identify exactly one paper reliably, show the plausible result or results and ask the user which paper they mean before ingesting anything.
4. When the match is unique, ingest it only if the requested work needs saved metadata or full text, then use `paper download` and read the evidence needed for the comparison.
5. A unique arXiv ID is already sufficient identification. Do not force a confirmation for a clear exact match or for broad topic discovery; broad discovery may follow the normal candidate workflow.

Never silently choose between ambiguous papers or claim to have compared full texts after reading only search metadata.

## Computation

- Run Python as `uv run python`. NumPy, SciPy, scikit-learn, pandas, and opencv-python are declared project dependencies.
- Put temporary scripts, inputs, and outputs only under `data/tasks/<task_id>/`. Do not use this exception to modify application-owned task records or other persisted data.
- Never run `uv add`, install undeclared dependencies, or access SQLite directly. Data mutations still go through `uv run frontier`.
- Prefer a short, reproducible calculation. Report the inputs, formula or metric definition, sample size, and missing-value handling with the result.

## User-added recommendation

When a task identifies one user-added paper and a brief date:

1. Treat it as an unlimited manual recommendation; do not collect or rank unrelated candidates.
2. Read the complete PDF before judging it, rendering evidence-bearing figures or tables when needed.
3. Verify bibliographic metadata and save `pre_read_summary`, `post_read_summary`, and a justified `agent_score`.
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

1. Synthesize verified paper evidence, annotations and discussion into one `post_read_summary`
   Markdown document, with material paper claims cited as `[[page:N]]`. No fixed section fields are
   required. Keep private user attitudes in `user_note`, not in public paper summaries.
2. Suggest an optional scalar `suggested_score` in [0,1], tags, and concrete graph-node candidates.
   Existing-node candidates include the exact node ID; new ones include a distinct research question
   and paper-specific relationship reason. Suggestions do not set `user_score`.
3. Save through `paper finish` using `post_read_summary`, `suggested_score`, `suggested_tags`, and
   `suggested_nodes`. The paper must already be in reading status.
4. Let the user confirm their score (including null), tags and graph links. Never confirm on behalf
   of the user or invent a second rating dimension.

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
