from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import PROJECT_ROOT, settings
from .database import SessionLocal
from .interactive_stream import (
    _stop_process,
    evict_idle_codex_app_servers,
    register_codex_run,
    stream_codex_prompt,
    unregister_codex_run,
)
from .models import (
    ArchitectureDocument,
    Brief,
    Conversation,
    Message,
    Paper,
    TaskRun,
    TranslationCache,
    utcnow,
)
from .services.abstract_translation import (
    ABSTRACT_TRANSLATION_STATE_KEY,
    PAPER_ABSTRACT_TRANSLATION_KIND,
    TARGET_LANGUAGE,
)
from .services.agent_runtime import (
    AgentSlotCancelled,
    AgentSlotTimeout,
    attach_scope_to_process,
    codex_subagent_limit_args,
    create_agent_scope,
    root_agent_slot,
)
from .services.architecture_progress import persist_architecture_progress
from .services.architecture_scene import release_agent_lock_for_task
from .services.common import log_operation
from .services.interest_evidence import FEEDBACK_PROVENANCE_KEY
from .services.manual_recommendations import set_manual_analysis_state
from .services.outbound_network import agent_environment
from .services.translation_provider import stream_translation_prompt


def _find_session_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("thread_id", "session_id", "threadId", "sessionId"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for item in value.values():
            found = _find_session_id(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_session_id(item)
            if found:
                return found
    return None


def _parse_citations(content: str, paper_id: str | None = None) -> list[dict[str, Any]]:
    citations = []
    for match in re.finditer(
        r"\[\[(?:page|p)[:. ]?(\d+(?:\s*[-–—~～]\s*\d+)?"
        r"(?:\s*[,，]\s*\d+(?:\s*[-–—~～]\s*\d+)?)*)(?:\|([^\]]+))?\]\]",
        content,
        re.IGNORECASE,
    ):
        parts = re.split(r"\s*[,，]\s*", match.group(1))
        custom_label = match.group(2)
        for part in parts:
            page_match = re.fullmatch(r"(\d+)(?:\s*[-–—~～]\s*(\d+))?", part)
            if page_match is None:
                continue
            start_page = int(page_match.group(1))
            end_page = int(page_match.group(2)) if page_match.group(2) else None
            page_label = f"{start_page}–{end_page}" if end_page else str(start_page)
            label = custom_label
            if custom_label and len(parts) > 1:
                label = f"{custom_label}（第 {page_label} 页）"
            citation = {
                "paper_id": paper_id,
                "page_number": start_page,
                "label": label or f"第 {page_label} 页",
                "marker": match.group(0),
            }
            if end_page is not None:
                citation["end_page_number"] = end_page
            citations.append(citation)
    return citations


def build_conversation_recovery_prompt(
    session: Session,
    conversation_id: str,
    current_prompt: str,
    *,
    exclude_message_id: str | None = None,
    max_chars: int = 24_000,
) -> str:
    """Rehydrate a new Codex thread from application-owned conversation history."""
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(40)
    )
    messages = list(reversed(session.scalars(statement).all()))
    history: list[str] = []
    used = 0
    for item in reversed(messages):
        if item.id == exclude_message_id:
            continue
        if (item.context_json or {}).get("turn_cancelled"):
            continue
        role = "用户" if item.role == "user" else "Agent"
        context = ""
        if item.role == "user" and item.context_json:
            public_context = {
                key: value
                for key, value in item.context_json.items()
                if key not in {"task_id", "turn_cancelled"}
            }
            if public_context:
                context = f"\n页面上下文：{json.dumps(public_context, ensure_ascii=False)}"
        block = f"{role}：{item.content}{context}"
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining > 200:
                history.append(block[-remaining:])
            break
        history.append(block)
        used += len(block)
    history.reverse()
    transcript = "\n\n".join(history) or "（没有更早的对话消息）"
    return f"""The previous Codex thread is unavailable after a service or environment change.
Continue the same user conversation using the application-persisted history below. Treat it as
conversation context and do not mention the recovery unless the user asks about it.

Persisted conversation history:
{transcript}

Current request:
{current_prompt}
"""


def _paper_agent_context_command(paper_id: str, conversation_id: object) -> str:
    command = f"frontier paper agent-context {paper_id}"
    if isinstance(conversation_id, str) and conversation_id:
        command += f" --conversation-id {conversation_id}"
    return command


def _paper_agent_context_instruction(
    paper_id: str,
    conversation_id: object,
    sync_required: object,
) -> str:
    command = _paper_agent_context_command(paper_id, conversation_id)
    if sync_required is False:
        return (
            "The Server verified that this Codex thread already has the latest paper state. "
            f"Do not call `{command}` in this turn. If the surrounding recovery preamble says the "
            "previous Codex thread is unavailable, ignore this skip and run that command once; the "
            "new thread then receives a `full` initialization."
        )
    return (
        f"Start with `{command}`. Its `full` response initializes this Codex thread, `delta` "
        "contains only changed paper-state sections, and `unchanged` means the existing thread "
        "context is current."
    )


def build_prompt(task: TaskRun) -> str:
    payload = task.payload_json
    ambiguous_paper_action = (
        "ask the user to confirm the intended paper before importing anything"
        if task.kind in {"paper_chat", "global_chat", "graph_maintenance"}
        else "do not import it; report that user confirmation is required"
    )
    research_capabilities = (
        "When the task or user names a paper, author, arXiv ID, method, or keyword, first search "
        "the local paper library using `uv run frontier paper list --query '<title, author, "
        "identifier, or keyword>' --limit 20`. Reuse a reliable local match and its cached full "
        "text or structured resources instead of searching or importing it again. When a specific "
        "paper is not already available, especially for a comparison, search arXiv without "
        "importing it using `uv run frontier source "
        "arxiv-search '<focused title/author query>' --max-results 10 --no-ingest`. If title, "
        "author, and year do not identify one result reliably, "
        f"{ambiguous_paper_action}. If there is one reliable match, import and download it "
        "only when its metadata or full text is needed. Do not impose this confirmation step on "
        "broad topic discovery or a uniquely identified arXiv ID. "
        "Use `uv run frontier paper resources PAPER_ID` when exact formula TeX, original figures, "
        "tables, bibliography entries, or internal citation targets are needed; inspect a returned "
        "local asset path with the image viewer. "
        "You may calculate with `uv run python`; NumPy, SciPy, scikit-learn, pandas, and "
        "opencv-python are installed project dependencies. Put task-specific temporary scripts and "
        f"artifacts only under `data/tasks/{task.id}/`. Never run `uv add`, install undeclared "
        "dependencies, modify application data files, or access the database directly. Report the "
        "calculation inputs, formula or metric definition, sample size, and missing-value handling."
    )
    delegation_policy = (
        "Use Codex subagents selectively for bounded, independent, read-heavy work that would "
        "otherwise pollute this paper's main context, such as related-paper discovery, parallel "
        "evidence extraction, or an Architecture Scene evidence review. Keep direct questions and "
        "small single-source checks in the main Agent. Give each subagent a narrow deliverable and "
        "require concise structured findings with paper identifiers, page evidence, or a task-local "
        "artifact path. Subagents may read Frontier state and write only task-scoped artifacts under "
        f"`data/tasks/{task.id}/`; only the main Agent may run canonical Frontier mutations or commit "
        "an Architecture Scene. Validate every subagent result before using it. Delegation is an "
        "optimization for main-context quality, not a default, because it can increase total token use. "
        "The runtime permits at most one concurrent subagent and one delegation level; never ask a "
        "subagent to delegate further or attempt to bypass those limits. "
    )
    workflow_common = (
        "Use the project-local `frontier-research` skill. "
        "Operate application data only through `uv run frontier` CLI commands. "
        "Never edit the SQLite database or application data files directly. "
        "When this workflow reads a complete paper for reusable analysis, store only incremental "
        "deep knowledge in `Paper.full_analysis` (for example schema version, source PDF version, "
        "claim/evidence items, method components, figure/table index, glossary, and unresolved "
        "questions). Never copy summary, problem, innovation, experiment, limitations, completion "
        "summary, tags, or workflow/provenance metadata into `full_analysis`; keep those in their "
        "existing canonical Paper fields. Do not use `metadata_json` as another knowledge store. "
        "Write every persisted user-facing Agent-generated prose field in concise Simplified "
        "Chinese, even when the paper and source material are in English. Keep technical terms, "
        "formulas, and proper names in their original language when that improves precision. "
        "For mathematical expressions, use `\\(...\\)` for inline math and `\\[...\\]` for "
        "display math. Put display delimiters on their own correctly indented Markdown lines; "
        "do not use dollar-sign math delimiters. "
        f"The task id is {task.id}. {delegation_policy}{research_capabilities}"
    )
    interactive_common = (
        "You are the Frontier research assistant. Respond directly without announcing a plan, "
        "reading instruction files, or describing routine tool use. Use `uv run frontier` only when "
        "application context or a requested mutation requires it; never edit data files directly. "
        "Use concise Simplified Chinese for the final answer and every public progress or reasoning "
        "summary. Keep technical terms, formulas, and proper names in their original language when "
        "that improves precision. "
        "Format responses with concise Markdown. When formulas materially improve the explanation, "
        "use `\\(...\\)` for inline math and `\\[...\\]` for display math. Put display delimiters "
        "on their own correctly indented Markdown lines; do not use dollar-sign math delimiters. "
        f"The task id is {task.id}. {delegation_policy}{research_capabilities}"
    )
    if task.kind == "paper_metadata_enrichment":
        paper_ids = [
            str(value)
            for value in (payload.get("paper_ids") or [])
            if isinstance(value, str)
        ][:10]
        return f"""{workflow_common}

Process metadata enrichment for these newly ingested papers:
{json.dumps(paper_ids, ensure_ascii=False)}

For each paper, in order:
1. Read `frontier paper metadata-context PAPER_ID`.
2. Inspect the title, abstract, ordered authors, affiliations, source URL, and current group.
3. Search the existing group index before creating anything. Run several focused queries with
   `frontier research-group search "<query>" --limit 20` using the English lab name, Chinese
   translation, institution, acronym, PI name, and any alias found in the abstract or paper page.
   Search both English and Chinese when either is plausible. Treat an exact institution plus lab
   or PI match as the same group; do not create a duplicate because of punctuation, abbreviation,
   or translated naming.
4. If the affiliation or group identity is still ambiguous, run
   `frontier paper first-page PAPER_ID --max-chars 2500`. This returns only a short HTML/PDF
   snippet and does not create a document cache. Use only explicit evidence from the snippet.
   You may also consult an official lab page or other public source when the local index is
   ambiguous; record the URL in the evidence note. Never invent a group from a generic institution.
5. If an existing group is the same lab, reuse it. Do not overwrite a user-verified group's name
   or institution. You may add a missing alias or research focus with
   `frontier research-group update GROUP_ID --data @data/tasks/{task.id}/group-update.json`.
6. If no existing group matches, create exactly one new group with
   `frontier research-group create --data @data/tasks/{task.id}/group.json`. Use
   `verification_status: "candidate"`, the explicit lab/group name when available, the institution,
   and a concise research focus. If only an institution is explicit, create an institution-level
   group and mark confidence `low`; never invent a lab name.
7. Bind the paper with `frontier paper assign-group PAPER_ID GROUP_ID`. The server creates the
   group tag automatically.
8. Use `frontier paper update PAPER_ID --data @data/tasks/{task.id}/paper.json` only for
   evidence-backed bibliographic corrections: ordered authors, `first_author`,
   `primary_affiliation`, and `corresponding_author`. Do not write `summary`, scores,
   translations, recommendation fields, or long paper analysis.
9. If the evidence is insufficient, leave the group unbound and record the concrete missing
   evidence in the final summary; do not guess.
10. Finish each paper with
    `frontier paper metadata-complete PAPER_ID --status completed --note "<brief evidence>"`.
    Use `--status failed` only for a concrete source/tool failure; an unresolved group is still a
    completed metadata pass with an explanatory note.

Do not translate abstracts. Do not score the paper. Do not write `Paper.summary`. Do not modify
the research graph. Keep all temporary JSON under `data/tasks/{task.id}/`. Finish with a compact
JSON summary of papers processed, group matches, groups created, and unresolved papers.
"""
    if task.kind == "daily_brief":
        run_date = (
            payload.get("date") or datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
        )
        return f"""{workflow_common}

Run the daily paper workflow for {run_date}. Start with
`frontier interest context --recent-limit 30`; this bounded projection is the canonical handoff from
paper-reading Agents and user-confirmed feedback. It contains explicit interest priors, confirmed
positive and negative interest, quality judgments, final user-selected tags, private attitude
summaries, recommendation attribution, and historical calibration. Treat explicit user screening
feedback and confirmed feedback as strong evidence; Agent scores are weak calibration evidence.
Keep interest and paper quality as
independent axes. A neutral judgment is neither positive nor negative interest. An ignored
recommendation is weak negative-interest evidence, not proof of low
paper quality. Do not learn from Agent suggestions or unread candidates unless the user explicitly
supplied screening labels or a screening comment.
Treat every recommendation without user-confirmed feedback as missing data, regardless of its age,
rank, or whether reading was started. Never interpret not opening, not reading, or not confirming a
paper as neutral or negative evidence; exclude those papers from hit-rate and calibration
denominators. Only `completed` feedback and explicit `ignored` actions are observations in the
confirmed calibration set; explicit screening feedback is a separate pre-reading evidence stream.
Only explicit screening feedback, `completed` feedback, and explicit `ignored` actions are observations. Ignore any
workflow memory that infers preferences from non-response, and never create such a memory.
For `candidate`, `reading`, or `feedback_pending` papers, canonical `interest_level` and
`quality_level` are null. Legacy `is_interested=false` and `is_quality=false` values on those papers
are storage defaults, not user judgments; never use them as ranking or learning evidence.
If confirmed feedback exists and `layers.semantic_profile` is missing, marked `stale`, or its
metadata `feedback_digest` differs from the current `feedback_digest`, rebuild that single derived profile with
`frontier interest profile-update`. Include only durable cross-paper preferences, keep positive and
negative evidence, and cite the provided `evidence_key` values in each semantic preference. Never
copy paper summaries into the profile or create additional feedback-profile memories. A 409 means
evidence changed; reload `interest context` and retry once before ranking. After a successful profile
update, reload `interest context` once and use that new `source_digest` for brief creation.
You are the single daily-workflow root Agent responsible for candidate discovery, bibliographic
verification, ranking, and the catalog summaries and comments saved for every selected paper.
You must verify the evidence, make the final ranking decision, and perform every Frontier mutation
yourself. Candidate analysis must not use or create a paper-reading Conversation;
that long-lived main Agent is reserved for papers the user actively reads and discusses.
Use an abstract-only workflow: score candidates, write
`catalog_summary` and comments, create the brief, and publish it so the user can start reading
immediately. The publish endpoint queues abstract translations independently. Do not wait for
metadata enrichment or group tags before publication. End the daily workflow after publication;
do not download PDFs, read full texts, preprocess documents, or queue detailed analysis for any
daily candidate, regardless of rank, tier, `fulltext_limit`, or `featured_limit`.
Write only pre-reading catalog fields such as `Paper.catalog_summary` and recommendation analysis.
Detailed analysis is generated only when the user explicitly requests it through the manual
paper-analysis workflow. Do not write or clear `Paper.summary`, `problem`, `innovation`, `experiment`,
`limitations`, `open_questions`, or `Paper.full_analysis` in the daily workflow; preserve any existing
analysis. Never write `completion_summary` or `completion_summary_structured`; those are
user-reading conclusions owned exclusively by the paper main Agent after the user finishes reading.
Use the context's `source_digest` when creating the brief as `interest_context_digest`. Adjust
discovery and ranking using topic affinities, recent semantic attitude evidence, hit rates, and
Brier scores. When feedback support is small, avoid overfitting and keep modest cross-domain
exploration. If interest hit rate is weak, tighten personal relevance; if quality hit rate or its
calibration is weak, raise the evidence threshold independently. The graph describes research
questions and accumulated evidence; it is not by itself proof that the user likes a topic.
Read `frontier setting list`. Broad discovery domains are `discovery_domains` (normally 3D vision,
embodied intelligence, and AI agents). These alone define the semantic domain gate. In contrast,
`initial_interest_domains` (VLA, WAM, etc.), explicit interests, and learned preferences guide interest
selection and ranking, never the initial retrieval query or the domain gate. General AI agents include
tool use, planning, multi-agent collaboration, memory, and computer use, not just embodied agents.
Start discovery with `frontier source arxiv-discover --date {run_date} --offset 0 --page-size 100`.
This read-only tool scans the configured broad arXiv categories over the recent candidate_lookback_days
(default 3; Monday through Wednesday use up to 7 days in a single request to cover weekend or holiday
announcement delays), then applies a high-recall server-side prefilter to obvious vision, robotics, embodied,
or agent-related metadata. It scans at most discovery_scan_limit raw records (default 300) and returns
at most discovery_prefilter_limit records (default 120) to you. Inspect every returned item; the
prefilter is only an efficiency filter, not proof of domain membership. It deliberately does not use
VLA, WAM, or other narrow interest keywords. If `scan_capped` is true, record that coverage is bounded
by the raw scan limit; do not claim to have covered the full category set. The Server deduplicates by
version-independent arXiv ID, normalized DOI, and normalized title, and excludes ALL existing library
papers (including candidate) before returning the list. Do not rescan or refill from the library merely
to reach 100; use the returned new-paper batch only. Pagination happens inside the Server, so call this
command once per daily run, except when retrying a failed request. A null next_offset ends discovery;
it does not mean the entire arXiv window was covered. Set candidate-pool discovery_complete from the
returned coverage_complete and explain scan_capped/prefilter_capped or shortages in notes.
Retry a failed discovery request without changing the date or offset. Never silently substitute narrow
keyword searches for broad discovery; keyword search is only supplemental lookup or verification.
Inspect each title, abstract and authors for semantic membership in a broad domain. Categories are
recall sources, not proof of domain membership. Papers that fail the gate must never enter the database.
For domain matches, run `frontier paper add-arxiv`; ingestion still deduplicates races with other
requests. Ingest eligible domain matches before interest shortlisting, even if they
will not be chosen today, so lower-interest domain papers remain available for future discovery.
Then narrow domain-qualified unread papers to about `abstract_limit` candidates (default 100, normally
80-120) using personal interest. This setting is the POST-screening candidate target, not a cap on
raw search results or how many abstracts you may inspect for domain screening. Prefer explicit user
preferences, but reserve about 15% of this shortlist for promising or uncertain work elsewhere in the
broad domains; VLA/WAM keyword absence must never by itself eliminate a paper. Author influence may
modestly improve interest, but never bypass the broad domain gate. Do not fill shortages with off-domain
or already-read papers. Keep shortlist IDs and progress in task-scoped scratch files as needed.
Save this intermediate pool with `frontier task candidate-pool {task.id} --data @data/tasks/{task.id}/candidate-pool.json`.
The JSON contains `paper_ids`, `discovery_complete`, and `notes` explaining shortages or incomplete
coverage. Read `frontier task get {task.id}` to verify persistence. Choose the final brief only from
this saved pool. Create the brief with `task_id={task.id}` so the Server can validate pool membership.
Score quality and relevance independently, rank these candidates, then choose a
continuous final brief between 20 and 50 papers (never more than the
configured brief limit). Rank the entire pool first. Store `topic_relevance_score`,
`author_influence_score`, and independent quality `research_score`; fold author influence into
relevance modestly without displaying it. Bibliographic metadata, research groups, and group tags
are owned by the ingestion metadata Agent. Do not repeat affiliation/group extraction or create
groups in this daily scoring run; use the existing fields and leave missing values blank.
For every paper that enters the final 20–50, save a concise Simplified-Chinese
`catalog_summary` (not `Paper.summary`) and a concise, information-dense Agent comment for every
final brief item; do not put the entire 100-paper intermediate pool in the brief. The comment must explain what the work does
and its concrete methodological or empirical highlight, such as the proposed mechanism, benchmark,
real-robot result, or a reported limitation. Never explain or repeat the numerical quality or
relevance scores in `agent_comment`; scores belong only in their score fields and the ranking.
Before ranking or adding a paper, inspect its existing record and exclude every paper whose
status is not `candidate`; papers in `reading`, `feedback_pending`, `completed`, or `ignored` status
must never appear in the daily recommendations. Screen and score the pool from titles, abstracts,
and authors only. Every selected paper needs a catalog summary, concise comment, both scores,
and a recommendation reason; abstract translations are queued by the publish endpoint. Do not publish
a failure message as a one-paper brief. If fewer than 20 eligible papers exist after broad discovery,
report the shortage explicitly and do not fabricate extra papers. Catalog summaries, Agent comments,
and recommendation reasons must be written in Simplified Chinese. Do not attach daily
or candidate papers to research nodes, create paper-backed node progress, or otherwise treat them
as accumulated evidence: graph connection is allowed only after the user completes reading and
confirms feedback, so the paper status is `completed`. You may describe probable graph relevance
in the recommendation fields. Create and publish exactly one brief for the date as soon as ranking,
catalog summaries, comments, and brief membership are ready. Finish immediately after publication
with a short JSON-like summary containing brief_id and counts; there is no automatic full-text
analysis stage.
"""
    if task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
        paper_id = payload["paper_id"]
        destination = (
            f"as an unlimited manual recommendation for the {payload['date']} brief"
            if task.kind == "manual_paper_analysis"
            else "because the user explicitly requested refreshed metadata and analysis"
        )
        return f"""{workflow_common}

Process paper {paper_id} {destination}. Load its current metadata, download the PDF, and read the complete extracted full text before
making a recommendation. Use rendered pages when figures, tables, or layout carry material evidence.
This is a stateless catalog/brief analysis run, not the paper's long-lived reading Agent. Do not
create or resume a paper Conversation and do not infer the user's attitude from unrelated history.
Do not stop after writing the summary. Complete the bibliography block in the same run: inspect the
first PDF/HTML page (including superscript affiliation markers and correspondence footnotes), then
verify and update the ordered author list, `first_author`, `primary_affiliation`, and
`corresponding_author`. Also write a concise Simplified-Chinese `research_group_summary` describing
the first institution and the main completed research focus of its lab or team. arXiv abstract
metadata is authoritative for author order but usually does not contain affiliations or a reliable
corresponding-author mapping; never infer a role from author position or seniority. If the source
does not explicitly support a field, leave it null and state that evidence is unavailable.
In the same `frontier paper update`, include `metadata_json.research_group_profile` with this
shape: `name`, `institution`, `homepage_url`, `aliases`, `research_focus`,
`representative_contributions`, `paper_contribution`, `is_representative`, `confidence`, and
`evidence`. Keep the profile concise and evidence-backed. `research_focus` is a short list of
durable directions; `representative_contributions` is a short list of the group's notable completed
work themes, not invented paper titles. This profile is merged into the shared research-group index
and must not overwrite a user-verified group.
Save a concise Simplified-Chinese one-line summary, an information-dense Agent comment describing
what the work does and its main highlight, problem, innovation,
experiment evidence, limitations, open_questions, research score, relevance score, recommendation reason, and
`suggested_tags` with
`frontier paper update`. Use explicit unavailable/evidence-insufficient wording for fields that cannot
be established. Do not attach the paper to the graph or add node progress before user-confirmed
reading is completed. Finish with a short summary of fields updated and any concrete source failure.
"""
    if task.kind == "architecture_generation":
        paper_id = payload["paper_id"]
        work_path = f"data/tasks/{task.id}/scene.architecture.json"
        return f"""{workflow_common}

Build the canonical Architecture Scene for paper {paper_id}. The Server already holds the Agent
editing lock for task {task.id}. Read the complete paper and inspect exact figures, tables, formulas,
appendices, and official code only when they materially determine the architecture. Do not guess
missing structure. Preserve the paper's broad composition while using the canonical semantic model.

Checkout the current Scene to `{work_path}` with `frontier architecture checkout`, keeping the
reported revision. Edit only that task-local JSON file. Include Module, Data, Tensor, Edge,
Annotation, Group, minimal SourceReference, and asset references only when supported by evidence.
Keep each Module's fields semantically distinct: `title` is its functional role; optional `symbol`
is only its TeX identifier without `$` delimiters; `architecture` is the concrete model family,
backbone, layer stack, or operator; and `keywords` are only behavioral properties or configurations.
Never place a model architecture such as ViT, DINOv2-B/14, T5, ResNet, Transformer, MLP, or DiT in
`keywords`, and never combine a symbol or a property such as shared weights with `architecture`.
For example, a shared online vision encoder may use `symbol: "g_\\psi"`,
`architecture: "DINOv2-B/14"`, and `keywords: ["shared weights", "fine-tuned"]`. Store Data and
Tensor symbols as TeX without delimiters. In descriptions, annotations, labels, and keywords, use
`\\(...\\)` for inline math and `\\[...\\]` for display math so the Web can render them as formulas.
When a processing step and a Tensor describe the same source-to-target relation, store both on one
Edge with `label` and `tensorId`; never split them into duplicate same-kind Edges. Use separate Edges
only for genuinely distinct flow and conditioning semantics.
Model each semantic operator once. For a feed-forward architecture, keep the main flow acyclic and
never encode an operator's input preparation and returned output as reciprocal Edges between the
same pair of nodes. Keep internal Q/K/V projection, attention output splitting, residual addition,
normalization, and similar sub-steps in that Module's `architecture`, `keywords`, or `description`;
merge inseparable internal phases into one Module. Reciprocal flow Edges are allowed only when the
paper explicitly establishes recurrence, iteration, or feedback. Fix every `edge.duplicate_relation`
and `edge.reciprocal_flow` diagnostic before committing an Agent-generated Scene.

Use stable semantic IDs. Run `frontier architecture validate --data @{work_path}` and fix every
error; review geometry and provenance warnings. This MVP does not use automated screenshots: do not
install or launch a browser automation dependency. Commit the complete file with `frontier
architecture commit`, the checkout revision, and task id {task.id}. Do not modify Server data files
directly and do not finish without a successful commit. Return one concise Chinese sentence stating
that the architecture is ready and mentioning any important evidence limitation.
"""
    if task.kind == "finish_reading":
        paper_id = payload["paper_id"]
        context_instruction = _paper_agent_context_instruction(
            paper_id,
            payload.get("conversation_id"),
            payload.get("paper_context_sync_required"),
        )
        return f"""{workflow_common}

Prepare the completion summary for paper {paper_id} as that paper's long-lived main Agent.
{context_instruction} Synthesize
the accumulated Codex thread, annotations, notes, and reusable `full_analysis`. Read only missing exact
PDF pages or structured resources needed to support the final summary; do not reload the complete paper
when the accumulated evidence is sufficient. Write the complete saved summary in Simplified
Chinese. Save `completion_summary_structured` with exactly these fields: `problem`, `innovation`,
`experiment`, `limitations`, `open_questions`, and `user_attitude`. Every material paper
claim in the first five fields must cite a page as [[page:N]]. Derive `user_attitude` only from the
user's annotations, notes, and conversation; state that evidence is insufficient instead of
inventing an attitude. Save suggested tags and the structured summary with `frontier paper finish`.
Also save `suggested_interest_level` as exactly one of `ignored`, `low`, `neutral`, `high`, or
`interested`. Save `suggested_quality_level` independently as exactly one of `poor`,
`below_average`, `normal`, `good`, or `excellent`.
Read the active research nodes and save concise `suggested_nodes` candidates. Each candidate must
contain `name`, `core_question`, a paper-specific `reason`, optional `domain`, and `node_id` when it
matches an existing node exactly. Propose a new node only when its core research question is genuinely
distinct; never use a merely similar existing node to suppress a justified new candidate.
Do not confirm interest, quality, tags, or node selection on the user's behalf. Do not send the
legacy `summary` field when `completion_summary_structured` is available; the Server creates its
compatibility Markdown.
After saving, return only one concise Chinese sentence confirming completion. Do not narrate loaded
context, tool calls, internal reasoning, or intermediate work in the final response.
"""
    if task.kind == "translate":
        text = payload["text"]
        target = payload.get("target_language", "简体中文")
        page_context = payload.get("page_context", "")
        word_mode = len(text.split()) <= 3 and "\n" not in text
        format_instruction = (
            "This is a word or short term. Return two concise Markdown lines: `**基础释义**：...` "
            "for its standard academic meaning and `**上下文释义**：...` for its meaning in the "
            "selected paper context."
            if word_mode
            else "Return only a precise, natural translation."
        )
        context_instruction = (
            f"\nPage context:\n{page_context}" if word_mode and page_context else ""
        )
        return f"""Translate the following academic text into {target}. {format_instruction}
Preserve technical terms and symbols; add the original English term in parentheses only when it
prevents ambiguity. Do not call tools and do not add unrelated commentary.

Text:
{text}
{context_instruction}
"""
    if task.kind in {"paper_chat", "global_chat", "graph_maintenance"}:
        message = payload.get("message", "")
        context = json.dumps(payload.get("context", {}), ensure_ascii=False)
        if task.kind == "paper_chat":
            paper_id = payload.get("paper_id")
            context_instruction = _paper_agent_context_instruction(
                str(paper_id),
                payload.get("conversation_id"),
                payload.get("paper_context_sync_required"),
            )
            return f"""{interactive_common}

Answer the user's paper-reading question about paper {paper_id}. {context_instruction}
When syncing, merge only sections returned by `delta` and never request a stateless full reload merely
to repeat already loaded state. Read only the exact PDF pages or structured resources needed for the
current question with `frontier paper text --page`, `frontier paper resources`, or `frontier paper
render-page`; do not reload the complete paper by default. Discuss through problem decomposition and mutual inquiry.
Do not mix simple translation results into this conversation. Cite every material paper claim with
[[page:N]]. When the current UI context is the Architecture Reader, use `frontier architecture
status` to read its revision. Use validated `frontier architecture command` calls for focus,
expand, highlight, dim, or tensor explanation. If the user asks for a persistent architecture
change, acquire the Agent lock with `frontier architecture checkout --lock --task-id {task.id}`,
edit only the task-local checkout, validate it, commit the whole Scene with the reported revision,
and release the lock after a failed or abandoned edit. Never mutate Server files directly. Current
UI context: {context}

User message:
{message}
"""
        return f"""{interactive_common}

Handle the user's research-management request. Inspect the current page context and relevant
server state before answering. Use CLI tools for every mutation. Only attach papers to nodes or add
paper-backed node progress after the paper has completed user-confirmed reading (`completed`). Only
save a research idea when the user explicitly asks to save it. When the user explicitly asks to
update, strengthen, weaken, add, or remove a research preference, treat that statement as strong
user evidence. Read `frontier interest context` first, then create or update an explicit
`interest_profile` memory with `frontier memory create` or `frontier memory update`, using
`source=user_conversation`. Record the domain, positive or negative direction, desired recommendation
behavior, scope, and any exclusions in concise Simplified Chinese. Re-read `frontier interest context`
after the mutation and report exactly what changed. Do not write the derived
`derived-interest-profile-v1` directly or invent feedback evidence keys; that profile is rebuilt only
from confirmed paper feedback. A conversational preference update affects the next ranking or daily
brief and must not reorder an already published brief for the current day. If the user is only asking
about a field without explicitly requesting a preference change, answer normally and do not mutate
the interest profile. Current UI context: {context}

User message:
{message}
"""
    raise ValueError(f"Unsupported Agent task kind: {task.kind}")


def _run_translation_task(task_id: str) -> None:
    try:
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if not task or task.status != "queued":
                return
            task.status = "running"
            task.started_at = utcnow()
            prompt = build_prompt(task)
            session.commit()
        content = "".join(
            stream_translation_prompt(
                prompt,
                timeout_seconds=settings.translation_timeout_seconds,
            )
        )
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if not task or task.status == "cancelled":
                return
            task.status = "completed"
            task.result_json = {"response": content}
            task.finished_at = utcnow()
            session.commit()
    except Exception as exc:  # noqa: BLE001
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if task and task.status != "cancelled":
                task.status = "failed"
                task.error = str(exc)
                task.finished_at = utcnow()
                session.commit()


def _run_abstract_translation_batch_task(task_id: str) -> None:
    """Translate every missing abstract in one brief, retaining partial results."""
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if not task or task.status != "queued":
            return
        task.status = "running"
        task.started_at = utcnow()
        items = list((task.payload_json or {}).get("items") or [])
        session.commit()
    translated = 0
    errors: dict[str, str] = {}
    for item in items:
        try:
            content = "".join(
                stream_translation_prompt(
                    f"Translate this academic abstract into {TARGET_LANGUAGE}. Return only the translation, preserving technical terms.\n\n{item['text']}",
                    timeout_seconds=settings.translation_timeout_seconds,
                )
            )
            with SessionLocal() as session:
                existing = session.scalar(
                    select(TranslationCache).where(
                        TranslationCache.selection_key == item["selection_key"]
                    )
                )
                if existing is None:
                    session.add(
                        TranslationCache(
                            selection_key=item["selection_key"],
                            paper_id=item["paper_id"],
                            page_number=0,
                            selected_text=item["text"],
                            target_language=TARGET_LANGUAGE,
                            content=content,
                            position_json={"kind": "abstract"},
                        )
                    )
                else:
                    existing.content = content
                session.commit()
            translated += 1
        except Exception as exc:  # noqa: BLE001
            errors[str(item.get("paper_id"))] = str(exc)
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if not task:
            return
        task.status = "completed" if translated or not errors else "failed"
        task.result_json = {"translated": translated, "failed": len(errors), "errors": errors}
        task.error = "; ".join(errors.values()) if errors else None
        task.finished_at = utcnow()
        brief_id = (task.payload_json or {}).get("brief_id")
        brief = session.get(Brief, brief_id) if brief_id else None
        if brief:
            brief.snapshot_json = {
                **(brief.snapshot_json or {}),
                "abstract_translation_status": "failed" if errors and not translated else "ready",
                "abstract_translation_errors": errors,
            }
        session.commit()


def _run_paper_abstract_translation_task(task_id: str) -> None:
    """Translate one paper abstract and persist it in the shared cache."""
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if not task or task.status != "queued":
            return
        task.status = "running"
        task.started_at = utcnow()
        payload = dict(task.payload_json or {})
        paper_id = payload.get("paper_id")
        paper = session.get(Paper, paper_id) if isinstance(paper_id, str) else None
        if paper is not None:
            metadata = dict(paper.metadata_json or {})
            metadata[ABSTRACT_TRANSLATION_STATE_KEY] = {
                **dict(metadata.get(ABSTRACT_TRANSLATION_STATE_KEY) or {}),
                "status": "processing",
                "task_id": task.id,
                "error": None,
            }
            paper.metadata_json = metadata
        session.commit()
    try:
        text = str(payload.get("text") or "")
        content = "".join(
            stream_translation_prompt(
                f"Translate this academic abstract into {payload.get('target_language') or TARGET_LANGUAGE}. Return only the translation, preserving technical terms.\n\n{text}",
                timeout_seconds=settings.translation_timeout_seconds,
            )
        )
        with SessionLocal() as session:
            existing = session.scalar(
                select(TranslationCache).where(
                    TranslationCache.selection_key == payload.get("selection_key")
                )
            )
            if existing is None:
                session.add(
                    TranslationCache(
                        selection_key=str(payload["selection_key"]),
                        paper_id=str(payload["paper_id"]),
                        page_number=0,
                        selected_text=text,
                        target_language=str(payload.get("target_language") or TARGET_LANGUAGE),
                        content=content,
                        position_json={"kind": "abstract"},
                    )
                )
            else:
                existing.content = content
            paper = session.get(Paper, payload.get("paper_id"))
            if paper is not None:
                metadata = dict(paper.metadata_json or {})
                metadata[ABSTRACT_TRANSLATION_STATE_KEY] = {
                    **dict(metadata.get(ABSTRACT_TRANSLATION_STATE_KEY) or {}),
                    "status": "ready",
                    "task_id": task.id,
                    "error": None,
                }
                paper.metadata_json = metadata
            task = session.get(TaskRun, task_id)
            if task:
                task.status = "completed"
                task.result_json = {"translated": 1}
                task.finished_at = utcnow()
            session.commit()
    except Exception as exc:  # noqa: BLE001
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            paper = session.get(Paper, (task.payload_json or {}).get("paper_id")) if task else None
            if paper is not None:
                metadata = dict(paper.metadata_json or {})
                metadata[ABSTRACT_TRANSLATION_STATE_KEY] = {
                    **dict(metadata.get(ABSTRACT_TRANSLATION_STATE_KEY) or {}),
                    "status": "failed",
                    "task_id": task_id,
                    "error": str(exc),
                }
                paper.metadata_json = metadata
            if task and task.status != "cancelled":
                task.status = "failed"
                task.error = str(exc)
                task.finished_at = utcnow()
            session.commit()


def _graph_feedback_is_active(session: Session, task: TaskRun) -> bool:
    context = (task.payload_json or {}).get("context") or {}
    evidence_key = context.get("feedback_evidence_key")
    paper_id = context.get("paper_id")
    if not isinstance(evidence_key, str) or not isinstance(paper_id, str):
        return True
    paper = session.get(Paper, paper_id)
    provenance = (paper.metadata_json or {}).get(FEEDBACK_PROVENANCE_KEY) if paper else None
    return bool(
        paper
        and paper.status == "completed"
        and isinstance(provenance, dict)
        and provenance.get("active", True)
        and provenance.get("evidence_key") == evidence_key
    )


def _codex_exec_command(
    prompt: str,
    output_path: str,
    *,
    session_id: str | None = None,
) -> list[str]:
    command = [settings.codex_command, "--strict-config"]
    if settings.codex_profile:
        command.extend(["--profile", settings.codex_profile])
    command.extend(codex_subagent_limit_args())
    if session_id:
        command.extend(["exec", "resume"])
    else:
        command.extend(
            [
                "exec",
                "--json",
                "-o",
                output_path,
                "-C",
                str(PROJECT_ROOT),
                "-s",
                settings.codex_sandbox,
            ]
        )
    if settings.codex_sandbox == "workspace-write" and settings.codex_network_access:
        command.extend(["-c", "sandbox_workspace_write.network_access=true"])
    if session_id:
        command.extend(["--json", "-o", output_path, session_id])
    command.append(prompt)
    return command


def run_task(task_id: str) -> None:
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if not task or task.status != "queued":
            return
        task_kind = task.kind
        is_translation = bool(
            task and task.kind in {"translate", "abstract_translation_batch", PAPER_ABSTRACT_TRANSLATION_KIND}
        )
        is_podcast_render = bool(task and task.kind == "podcast_episode_render")
        uses_app_server = bool(task and (task.payload_json or {}).get("conversation_id"))
        remaining_seconds = _remaining_task_seconds(task, settings.agent_task_timeout_seconds)
        if not is_translation and not is_podcast_render and remaining_seconds <= 0:
            _fail_expired_queued_task(session, task)
            return
    if is_podcast_render:
        from .services.podcast_generation import run_podcast_render_task

        run_podcast_render_task(task_id)
        return
    if is_translation and task and task.kind == "abstract_translation_batch":
        _run_abstract_translation_batch_task(task_id)
        return
        if is_translation and task and task.kind == PAPER_ABSTRACT_TRANSLATION_KIND:
            _run_paper_abstract_translation_task(task_id)
            return
    if is_translation:
        _run_translation_task(task_id)
        return
    if uses_app_server:
        _run_app_server_task(task_id)
        return

    def no_longer_queued() -> bool:
        with SessionLocal() as session:
            waiting = session.get(TaskRun, task_id)
            return waiting is None or waiting.status != "queued"

    deadline = time.monotonic() + remaining_seconds
    try:
        with root_agent_slot(
            task_kind=task_kind,
            deadline=deadline,
            cancelled=no_longer_queued,
        ):
            evict_idle_codex_app_servers()
            _run_codex_task(
                task_id,
                timeout_seconds=max(0.001, deadline - time.monotonic()),
            )
    except AgentSlotCancelled:
        return
    except AgentSlotTimeout:
        with SessionLocal() as session:
            waiting = session.get(TaskRun, task_id)
            transition = session.execute(
                update(TaskRun)
                .where(TaskRun.id == task_id, TaskRun.status == "queued")
                .values(
                    status="failed",
                    error=(
                        "Agent exceeded the configured wall-clock timeout while "
                        "waiting for execution capacity."
                    ),
                    result_json={
                        **((waiting.result_json or {}) if waiting else {}),
                        "termination_reason": "capacity_timeout",
                    },
                    finished_at=utcnow(),
                )
            )
            if transition.rowcount == 1:
                if waiting and waiting.kind in {
                    "manual_paper_analysis",
                    "paper_metadata_analysis",
                }:
                    set_manual_analysis_state(
                        session,
                        waiting,
                        "failed",
                        error=(
                            "Agent exceeded the configured wall-clock timeout while "
                            "waiting for execution capacity."
                        ),
                    )
                session.commit()
            else:
                session.rollback()


def _remaining_task_seconds(task: TaskRun, budget_seconds: float) -> float:
    now = utcnow()
    created_at = task.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=now.tzinfo)
    elapsed = max(0.0, (now - created_at).total_seconds())
    return max(0.0, budget_seconds - elapsed)


def _fail_expired_queued_task(session: Session, task: TaskRun) -> None:
    error = "Agent exceeded the configured wall-clock timeout while queued."
    transition = session.execute(
        update(TaskRun)
        .where(TaskRun.id == task.id, TaskRun.status == "queued")
        .values(
            status="failed",
            error=error,
            result_json={
                **(task.result_json or {}),
                "termination_reason": "queue_timeout",
            },
            finished_at=utcnow(),
        )
    )
    if transition.rowcount != 1:
        session.rollback()
        return
    if task.kind == "architecture_generation":
        document = session.get(ArchitectureDocument, (task.payload_json or {}).get("paper_id"))
        if document and document.generation_task_id == task.id:
            document.status = "ready" if document.scene_json else "failed"
            document.error = error
            document.generation_task_id = None
        release_agent_lock_for_task(session, task.id)
    elif task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
        set_manual_analysis_state(session, task, "failed", error=error)
    log_operation(
        session,
        actor="system",
        action="task.failed_queue_timeout",
        entity_type="task",
        entity_id=task.id,
        task_id=task.id,
    )
    session.commit()


def _run_app_server_task(task_id: str) -> None:
    """Run a conversation-bound background task through its app-server owner.

    A persisted Codex thread may stay loaded in the app-server between turns. Starting
    an independent ``codex exec resume`` for that same thread creates two writers and
    fails immediately. The pool serializes every background turn for that conversation.
    """
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if not task or task.status != "queued":
            return
        remaining_seconds = _remaining_task_seconds(task, settings.agent_task_timeout_seconds)
        if remaining_seconds <= 0:
            _fail_expired_queued_task(session, task)
            return
        if task.kind == "graph_maintenance" and not _graph_feedback_is_active(session, task):
            task.status = "cancelled"
            task.finished_at = utcnow()
            task.result_json = {
                **(task.result_json or {}),
                "stream_status": "cancelled",
            }
            session.commit()
            return
        task.status = "running"
        task.started_at = utcnow()
        task_kind = task.kind
        is_architecture = task_kind == "architecture_generation"
        log_path = settings.task_dir / f"{task.id}.jsonl"
        task.log_path = str(log_path)
        prompt = build_prompt(task)
        conversation_id = task.payload_json.get("conversation_id")
        conversation = (
            session.get(Conversation, conversation_id) if isinstance(conversation_id, str) else None
        )
        session_id = conversation.codex_session_id if conversation else None
        recovery_prompt = (
            build_conversation_recovery_prompt(session, conversation.id, prompt)
            if conversation and session_id
            else None
        )
        session.commit()

    response_parts: list[str] = []
    thread_id = session_id
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log_file:
            for event in stream_codex_prompt(
                prompt,
                session_id=session_id,
                recovery_prompt=recovery_prompt,
                run_id=task_id,
                timeout_seconds=remaining_seconds,
            ):
                log_file.write(json.dumps(event, ensure_ascii=False) + "\n")
                log_file.flush()
                if is_architecture:
                    persist_architecture_progress(task_id, event)
                if event.get("type") == "thread":
                    thread_id = str(event["thread_id"])
                    with SessionLocal() as session:
                        running = session.get(TaskRun, task_id)
                        current_conversation = (
                            session.get(Conversation, conversation_id)
                            if isinstance(conversation_id, str)
                            else None
                        )
                        if running and running.status == "running":
                            running.codex_session_id = thread_id
                            if current_conversation:
                                current_conversation.codex_session_id = thread_id
                            session.commit()
                elif event.get("type") == "delta":
                    response_parts.append(str(event.get("delta") or ""))

        response = "".join(response_parts)
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if not task:
                return
            if task.status == "cancelled":
                return
            document = (
                session.get(ArchitectureDocument, task.payload_json.get("paper_id"))
                if is_architecture
                else None
            )
            base_revision = int(task.payload_json.get("base_revision") or 0)
            completed = not is_architecture or bool(
                document
                and document.status == "ready"
                and document.scene_json
                and document.revision > base_revision
            )
            final_status = "completed" if completed else "failed"
            final_error = (
                None
                if completed
                else "Agent finished without committing a valid Architecture Scene."
            )
            transition = session.execute(
                update(TaskRun)
                .where(TaskRun.id == task_id, TaskRun.status == "running")
                .values(
                    status=final_status,
                    error=final_error,
                    result_json={
                        **(task.result_json or {}),
                        "response": response,
                        "termination_reason": "completed",
                    },
                    codex_session_id=thread_id,
                    finished_at=utcnow(),
                )
            )
            if transition.rowcount != 1:
                session.rollback()
                return
            if is_architecture and document and document.generation_task_id == task.id:
                document.status = "ready" if completed or document.scene_json else "failed"
                document.error = final_error
                document.generation_task_id = None
            if is_architecture:
                release_agent_lock_for_task(session, task.id)
            current_conversation = (
                session.get(Conversation, conversation_id)
                if isinstance(conversation_id, str)
                else None
            )
            if current_conversation:
                current_conversation.codex_session_id = (
                    thread_id or current_conversation.codex_session_id
                )
                if task_kind in {"paper_chat", "global_chat", "graph_maintenance"}:
                    paper_id = (
                        task.payload_json.get("paper_id") if task_kind == "paper_chat" else None
                    )
                    session.add(
                        Message(
                            conversation_id=current_conversation.id,
                            role="assistant",
                            content=response,
                            citations_json=_parse_citations(response, paper_id),
                        )
                    )
            log_operation(
                session,
                actor="system",
                action=f"task.{final_status}",
                entity_type="task",
                entity_id=task.id,
                task_id=task.id,
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 - persist all runner failures for the user
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if task and task.status != "cancelled":
                task.status = "failed"
                task.error = str(exc)
                task.result_json = {
                    **(task.result_json or {}),
                    "response": "".join(response_parts),
                    "termination_reason": "failed",
                }
                task.codex_session_id = thread_id
                task.finished_at = utcnow()
                document = (
                    session.get(ArchitectureDocument, task.payload_json.get("paper_id"))
                    if is_architecture
                    else None
                )
                if is_architecture and document and document.generation_task_id == task.id:
                    document.status = "ready" if document.scene_json else "failed"
                    document.error = task.error
                    document.generation_task_id = None
                if is_architecture:
                    release_agent_lock_for_task(session, task.id)
                log_operation(
                    session,
                    actor="system",
                    action="task.failed",
                    entity_type="task",
                    entity_id=task.id,
                    task_id=task.id,
                )
                session.commit()


def _run_codex_task(task_id: str, *, timeout_seconds: float | None = None) -> None:
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if not task or task.status != "queued":
            return
        if task.kind == "graph_maintenance" and not _graph_feedback_is_active(session, task):
            task.status = "cancelled"
            task.finished_at = utcnow()
            task.result_json = {
                **(task.result_json or {}),
                "stream_status": "cancelled",
            }
            log_operation(
                session,
                actor="system",
                action="task.cancelled_stale_feedback",
                entity_type="task",
                entity_id=task.id,
                task_id=task.id,
            )
            session.commit()
            return
        task.status = "running"
        task.started_at = utcnow()
        if task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
            set_manual_analysis_state(session, task, "processing")
        log_path = settings.task_dir / f"{task.id}.jsonl"
        output_path = settings.task_dir / f"{task.id}.last.md"
        task.log_path = str(log_path)
        session.commit()
        prompt = build_prompt(task)
        conversation = None
        conversation_id = task.payload_json.get("conversation_id")
        if conversation_id:
            conversation = session.get(Conversation, conversation_id)
        command = _codex_exec_command(
            prompt,
            str(output_path),
            session_id=(
                conversation.codex_session_id
                if conversation and conversation.codex_session_id
                else None
            ),
        )

    environment = agent_environment()
    uv_cache_dir = settings.task_dir / "uv-cache"
    uv_cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        timed_out = threading.Event()
        with (
            create_agent_scope(task_id) as scope,
            log_path.open("w", encoding="utf-8") as log_file,
        ):
            process = subprocess.Popen(
                scope.wrap_command(command),
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=os.name == "posix",
            )
            attach_scope_to_process(process, scope)

            def stop_after_timeout() -> None:
                if process.poll() is None:
                    timed_out.set()
                    process._frontier_termination_reason = "timeout"
                    _stop_process(process)

            wall_timeout = timeout_seconds or settings.agent_task_timeout_seconds
            timeout = threading.Timer(
                wall_timeout,
                stop_after_timeout,
            )
            timeout.daemon = True
            timeout.start()
            try:
                register_codex_run(task_id, process)
                session_id = None
                assert process.stdout is not None
                for line in process.stdout:
                    log_file.write(line)
                    log_file.flush()
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if task.kind == "architecture_generation" and isinstance(event, dict):
                        persist_architecture_progress(task_id, event)
                    session_id = session_id or _find_session_id(event)
                return_code = process.wait()
                scope.termination_reason = (
                    "timeout"
                    if timed_out.is_set()
                    else "completed"
                    if return_code == 0
                    else "process_exit"
                )
            finally:
                timeout.cancel()
                unregister_codex_run(task_id, process)
        response = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if not task:
                return
            if task.status == "cancelled":
                document = session.scalar(
                    select(ArchitectureDocument).where(
                        ArchitectureDocument.generation_task_id == task_id
                    )
                )
                if document and document.generation_task_id == task.id:
                    document.status = "ready" if document.scene_json else "missing"
                    document.error = None
                    document.generation_task_id = None
                release_agent_lock_for_task(session, task_id)
                session.commit()
                return
            task.codex_session_id = session_id
            daily_brief = None
            if task.kind == "daily_brief":
                daily_brief = session.scalar(
                    select(Brief).where(Brief.task_id == task.id, Brief.status == "published")
                )
            final_status = "completed"
            final_error = None
            termination_reason = (
                "timeout"
                if timed_out.is_set()
                else "completed"
                if return_code == 0
                else "process_exit"
            )
            final_result = {
                "response": response,
                "termination_reason": termination_reason,
                "resource_snapshot": scope.last_snapshot,
            }
            if timed_out.is_set():
                final_status = "failed"
                final_error = (
                    f"Agent exceeded the configured wall-clock timeout of {wall_timeout} seconds."
                )
            elif return_code != 0:
                final_status = "failed"
                final_error = f"Codex exited with status {return_code}. See {log_path}."
            elif task.kind == "daily_brief" and daily_brief is None:
                final_status = "failed"
                final_error = "Agent finished without publishing the required daily brief."
            elif task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
                paper = session.get(Paper, task.payload_json.get("paper_id"))
                if not paper or not all(
                    (paper.summary, paper.agent_comment, paper.recommendation_reason)
                ):
                    final_status = "failed"
                    final_error = (
                        "Agent finished without saving the required paper analysis fields."
                    )
            elif task.kind == "architecture_generation":
                document = session.get(ArchitectureDocument, task.payload_json.get("paper_id"))
                base_revision = int(task.payload_json.get("base_revision") or 0)
                if (
                    document is None
                    or document.status != "ready"
                    or not document.scene_json
                    or document.revision <= base_revision
                ):
                    final_status = "failed"
                    final_error = "Agent finished without committing a valid Architecture Scene."
            transition = session.execute(
                update(TaskRun)
                .where(TaskRun.id == task_id, TaskRun.status == "running")
                .values(
                    status=final_status,
                    error=final_error,
                    result_json=final_result,
                    codex_session_id=session_id,
                    finished_at=utcnow(),
                )
            )
            if transition.rowcount != 1:
                session.rollback()
                return
            if task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
                set_manual_analysis_state(
                    session,
                    task,
                    "completed" if final_status == "completed" else "failed",
                    error=final_error,
                )
            if task.kind == "architecture_generation":
                document = session.get(ArchitectureDocument, task.payload_json.get("paper_id"))
                if document and document.generation_task_id == task.id:
                    if final_status == "failed":
                        document.status = "ready" if document.scene_json else "failed"
                        document.error = final_error
                    else:
                        document.status = "ready"
                        document.error = None
                    document.generation_task_id = None
                release_agent_lock_for_task(session, task.id)
            conversation_id = task.payload_json.get("conversation_id")
            if conversation_id:
                conversation = session.get(Conversation, conversation_id)
                if conversation:
                    conversation.codex_session_id = session_id or conversation.codex_session_id
                    if return_code == 0 and task.kind in {
                        "paper_chat",
                        "global_chat",
                        "graph_maintenance",
                    }:
                        paper_id = (
                            task.payload_json.get("paper_id") if task.kind == "paper_chat" else None
                        )
                        session.add(
                            Message(
                                conversation_id=conversation.id,
                                role="assistant",
                                content=response,
                                citations_json=_parse_citations(response, paper_id),
                            )
                        )
            log_operation(
                session,
                actor="system",
                action=f"task.{final_status}",
                entity_type="task",
                entity_id=task.id,
                task_id=task.id,
            )
            release_agent_lock_for_task(session, task.id)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - persist all runner failures for the user
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if task and task.status != "cancelled":
                task.status = "failed"
                task.error = str(exc)
                task.finished_at = utcnow()
                if task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
                    set_manual_analysis_state(session, task, "failed", error=task.error)
                if task.kind == "architecture_generation":
                    document = session.get(ArchitectureDocument, task.payload_json.get("paper_id"))
                    if document and document.generation_task_id == task.id:
                        document.status = "ready" if document.scene_json else "failed"
                        document.error = task.error
                        document.generation_task_id = None
                    release_agent_lock_for_task(session, task.id)
                else:
                    release_agent_lock_for_task(session, task.id)
                session.commit()


def enqueue_task(kind: str, payload: dict[str, Any] | None = None) -> str:
    with SessionLocal() as session:
        task = TaskRun(kind=kind, payload_json=payload or {})
        session.add(task)
        session.commit()
        return task.id
