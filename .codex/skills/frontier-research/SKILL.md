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

## Daily brief

1. Read settings, active graph nodes, and active `interest_profile` and `agent_experience` memories.
2. Build focused arXiv queries from the current graph and interests. Include modest breadth for emerging cross-domain work.
3. Ingest candidates and de-duplicate through the Server.
4. Respect current limits exactly: abstract reads, full-text reads, total brief items, and featured items.
5. Rank research value and personal relevance separately. Apply recency as a decay signal, not a hard daily cutoff.
6. Download and read full PDF text before making a final recommendation. When text is insufficient, render the relevant PDF page with `paper render-page` and inspect that image.
7. Record concise analysis: one-line summary, one-line comment, problem, innovation, experiments, limitations, scores, and recommendation reason.
8. Do not attach daily or candidate papers to the graph. Record probable relevance in paper analysis or recommendation fields until the user completes reading and confirms feedback.
9. Do not add paper-backed node progress during the daily workflow.
10. Create one dated brief, add ranked items, verify the limits, and publish it.
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

Load the paper, PDF pages, annotations, reading progress, and existing paper conversation. Use `paper resources` when exact formula TeX, original figures, tables, bibliography entries, or internal citation targets matter. Answer through problem decomposition and mutual inquiry. Cite every material PDF claim as `[[page:N]]` so the Web can jump to the source.

Keep simple word or sentence translations out of the Agent conversation. When the user asks about selected text, use its saved page and selection context.

## Complete reading

When the user explicitly finishes reading:

1. Synthesize the PDF, annotations, notes, and conversation.
2. Produce a concise completion summary with page citations.
3. Suggest interest, paper quality, new-node need, and tags.
4. Save suggestions with `paper finish`.
5. Never confirm feedback, select tags, or admit a paper to the quality library for the user.

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
