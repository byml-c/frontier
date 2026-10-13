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
uv run frontier paper agent-context PAPER_ID
uv run frontier paper agent-context PAPER_ID --conversation-id CONVERSATION_ID
uv run frontier paper context PAPER_ID
uv run frontier paper finish PAPER_ID --data '{"completion_summary_structured":{"problem":"... [[page:1]]","innovation":"... [[page:4]]","experiment":"... [[page:7]]","limitations":"... [[page:12]]","open_questions":"... [[page:12]]","user_attitude":"..."},"suggested_interest_level":"interested","suggested_quality_level":"excellent","suggested_tags":["VLA"],"suggested_nodes":[{"node_id":"EXISTING_NODE_ID","name":"VLA","core_question":"如何让机器人把视觉与语言理解转化为可泛化的动作策略？","reason":"论文为该问题提供新证据"}]}'
```

`paper list --query` searches the saved library by title, abstract, all authors, primary
affiliation, arXiv ID, DOI, and the discovery query that originally ingested the paper. Use it
before an external search so an existing paper and its cached full text can be reused.

Use the task prompt's `paper agent-context --conversation-id` command only when it says synchronization is required. The first/new thread receives `full`, later state changes receive `delta`, and unchanged state skips the command entirely. The stateless form remains available for manual inspection. Both omit PDF text; follow them with targeted `paper text --page`, `paper resources`, or `paper render-page` calls for exact evidence. Use the larger `paper context` only for workflows that genuinely need page-separated full text.

`full_analysis` is the reusable deep-analysis slot. Keep only non-duplicated structures such as claim/evidence items, method components, figure/table indexes, glossary entries, unresolved questions, a schema version, and the source PDF version. Explicit Paper summary fields remain canonical for their respective conclusions; `metadata_json` is not a second paper-knowledge store.

`paper finish` stores the six completion-summary sections structurally. The Server also renders a
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
uv run frontier source arxiv-discover --date 2026-10-10 --offset 0 --page-size 100
uv run frontier task candidate-pool TASK_ID --data @data/tasks/TASK_ID/candidate-pool.json
uv run frontier source arxiv-search 'ti:"exact paper title" AND au:"author surname"' --max-results 10 --no-ingest
```

Daily discovery retrieves category/date pages internally, without narrow interest keywords or ingestion.
One call scans up to 300 raw metadata records (3 days by default, expanding to 7 days from Monday
through Wednesday to cover announcement delays), excludes ALL library matches by
version-independent arXiv ID, normalized DOI or title, removes batch duplicates, and returns at most
120 broad-concept/category matches. `page_size` is the upstream page size, not the Agent output limit.
`next_offset` is null: do not repeatedly fetch more batches. `total_results` is the upstream query size;
`scan_capped` and `prefilter_capped` expose truncation. Pass `coverage_complete` as discovery_complete.
`candidate_lookback_days` defines the inclusive UTC submission-date window. Semantic domain screening
must precede ingestion. Use `abstract_limit` as the post-interest target (default 100, ±20%).
Save `{"paper_ids":["..."],"discovery_complete":true,"notes":"..."}` as the intermediate pool;
select the final 20–50 only from that pool. Shortages or incomplete coverage require notes.
Before saving each final recommendation, recheck first author, first affiliation, and corresponding author
from explicit source markers and save `research_group_summary` plus `metadata_json.agent_bibliography`
with confidence and evidence. The parsed bibliographic fields are hints; user-edited fields are protected.
Ordinary `arxiv-search` calls still ingest by default and are for supplementary specific lookups.
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

## Paper narrations and daily podcast

```bash
uv run frontier narration get PAPER_ID
uv run frontier podcast get BRIEF_ID
uv run frontier podcast generate BRIEF_ID
```

Publishing a brief automatically queues its single-speaker Chinese podcast when
`FRONTIER_ALIYUN_TTS_API_KEY` is configured. `podcast generate` is the explicit generation and
retry entry point. The render task first persists one source-grounded narration per paper, then
combines them into the episode script and Alibaba Cloud audio. Use the low-level `narration commit`,
`podcast commit`, and `podcast status` commands only for controlled Agent/service workflows; inspect
their JSON fields with `--help` and the matching `get` command before committing a revision.

## Memory, settings, tasks, and backup

```bash
uv run frontier interest context --recent-limit 30
uv run frontier interest profile-update --data @data/tasks/TASK_ID/interest-profile.json
uv run frontier memory list --kind interest_profile --active
uv run frontier memory create --data '{"kind":"agent_experience","title":"...","content":"...","source":"daily_brief"}'
uv run frontier memory update MEMORY_ID --data '{"content":"...","active":true}'
uv run frontier setting list
uv run frontier task list --limit 20
uv run frontier task get TASK_ID
uv run frontier task retry TASK_ID
uv run frontier backup
```

`interest context` is the daily Agent's compact closed-loop input. It derives confirmed positive
and negative evidence from canonical Paper state, attributes feedback to immutable Brief items when
possible, and reports hit-rate and score calibration without copying paper facts into Memory.
`interest profile-update` compare-and-swaps the one derived semantic profile against the context
`feedback_digest` and rejects stale or unknown evidence references.

Memory kinds are normally `interest_profile` and `agent_experience`. Paper facts belong to paper analysis or graph nodes, not memory.
