# Frontier CLI reference

Set `FRONTIER_API_BASE_URL` when the Server is not at `http://127.0.0.1:8000`. Every command prints JSON and exits nonzero on error.

## Papers

```bash
uv run frontier paper list --status candidate --limit 50
uv run frontier paper list --query 'PI05' --limit 20
uv run frontier paper get PAPER_ID
uv run frontier paper create --data '{"title":"...","source":"manual"}'
uv run frontier paper add-arxiv 2410.24164
uv run frontier paper add-url https://example.org/paper
uv run frontier paper upload /absolute/path/paper.pdf
uv run frontier paper update PAPER_ID --data '{"summary":"...","research_score":0.9}'
uv run frontier paper download PAPER_ID
uv run frontier paper text PAPER_ID
uv run frontier paper text PAPER_ID --page 7
uv run frontier paper render-page PAPER_ID 7 --dpi 144
uv run frontier paper resources PAPER_ID
uv run frontier paper resources PAPER_ID --kind formula
uv run frontier paper resources PAPER_ID --kind figure --block-id BLOCK_ID
uv run frontier paper context PAPER_ID
uv run frontier paper finish PAPER_ID --data '{"completion_summary_structured":{"research_question":"... [[page:1]]","evidence_and_contribution":"... [[page:4]]","experiments":"... [[page:7]]","limitations":"... [[page:12]]","user_attitude":"..."},"suggested_interested":true,"suggested_quality":true,"suggested_create_node":false,"suggested_tags":["VLA"]}'
```

`paper list --query` searches the saved library by title, abstract, all authors, primary
affiliation, arXiv ID, DOI, and the discovery query that originally ingested the paper. Use it
before an external search so an existing paper and its cached full text can be reused.

Use `paper context` for reading completion and paper chat. It returns metadata, annotations, progress, conversations, and page-separated PDF text. If a claim depends on a figure, table, equation layout, or visual result, use `paper render-page`; the returned absolute PNG path can be opened with the Agent's image-viewing tool.

`paper finish` stores the five completion-summary sections structurally. The Server also renders a
legacy Markdown `completion_summary` for older private clients. `user_attitude` is private and is
excluded from daily and brief summary payloads.

Use `paper resources` for the cached structured arXiv HTML representation. It returns formula TeX,
figure and table captions plus local asset paths, searchable table text, bibliography entries, and
source-to-target anchor links. Filter with `--kind formula|figure|table|reference` or `--block-id`.
When a figure/table has `asset_path`, inspect that local file with the image-viewing tool. A 409
response means conversion is still pending; retry later rather than treating the partial result as
the complete paper.

## Architecture Reader

```bash
uv run frontier architecture status PAPER_ID
uv run frontier architecture checkout PAPER_ID --output data/tasks/TASK_ID/scene.architecture.json --task-id TASK_ID --lock
uv run frontier architecture validate --data @data/tasks/TASK_ID/scene.architecture.json
uv run frontier architecture commit PAPER_ID --file data/tasks/TASK_ID/scene.architecture.json --revision REVISION --task-id TASK_ID
uv run frontier architecture unlock PAPER_ID --task-id TASK_ID
uv run frontier architecture command PAPER_ID --data '{"commands":[{"type":"focus","target":"action_expert"}]}'
uv run frontier architecture upload-asset PAPER_ID data/tasks/TASK_ID/example.png
```

`checkout --lock` acquires the same single-writer lock used by the Web editor and then reads the
latest Scene. Edit only the returned task-local file. `commit` rejects stale revisions and every
validation error; warnings are returned for human review. Use `unlock` when an edit is abandoned.
Initial generation tasks already hold the lock, so their checkout does not need `--lock`.

`architecture command` accepts `focus`, `expand`, `collapse`, `highlight`, `highlight-path`,
`dim-others`, `show-tensor`, and `clear-transient`. It validates all referenced IDs and emits a safe
UI event through the paper conversation stream; it does not mutate the Scene.

## arXiv

```bash
uv run frontier source arxiv-search 'cat:cs.RO AND (all:"vision language action" OR all:VLA)' --max-results 50
uv run frontier source arxiv-search 'ti:"exact paper title" AND au:"author surname"' --max-results 10 --no-ingest
```

Search calls ingest by default. Split large topic sets into focused queries, then rely on Server de-duplication.
For a specifically named comparison paper, search with `--no-ingest` first. If title, author, year,
and arXiv ID do not establish a unique match, ask the user to confirm the intended result before
ingesting. A supplied arXiv ID or one unambiguous exact match does not require an extra confirmation.

## Graph

```bash
uv run frontier graph list --limit 100
uv run frontier graph get NODE_ID
uv run frontier graph subgraph --focus-id NODE_ID --depth 1
uv run frontier graph create-node --data '{"name":"...","core_question":"...","domain":"VLA"}'
uv run frontier graph update-node NODE_ID --data '{"summary":"...","open_questions":"..."}'
uv run frontier graph link SOURCE_NODE_ID TARGET_NODE_ID
uv run frontier graph attach-paper PAPER_ID NODE_ID --reason '...' --key
uv run frontier graph add-progress NODE_ID --data '{"title":"...","content":"...","paper_id":"...","happened_at":"2026-08-11"}'
uv run frontier graph add-idea NODE_ID --data '{"content":"...","status":"to_explore","source_type":"conversation","source_id":"..."}'
uv run frontier graph update-idea IDEA_ID --data '{"status":"supported"}'
```

Allowed idea statuses are `to_explore`, `supported`, `solved`, and `abandoned`.

## Briefs

```bash
uv run frontier brief create --data '{"brief_date":"2026-08-11","title":"2026-08-11 研究简报","task_id":"..."}'
uv run frontier brief add-paper BRIEF_ID PAPER_ID 1 --tier featured
uv run frontier brief add-paper BRIEF_ID PAPER_ID 6 --tier related
uv run frontier brief get BRIEF_ID
uv run frontier brief publish BRIEF_ID
```

Published briefs are immutable. `featured` and total item counts are enforced by Server settings.

## Memory, settings, tasks, and backup

```bash
uv run frontier memory list --kind interest_profile --active
uv run frontier memory create --data '{"kind":"agent_experience","title":"...","content":"...","source":"daily_brief"}'
uv run frontier memory update MEMORY_ID --data '{"content":"...","active":true}'
uv run frontier setting list
uv run frontier task list --limit 20
uv run frontier task get TASK_ID
uv run frontier task retry TASK_ID
uv run frontier backup
```

Memory kinds are normally `interest_profile` and `agent_experience`. Paper facts belong to paper analysis or graph nodes, not memory.
