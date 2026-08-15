from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import PROJECT_ROOT, settings
from .database import SessionLocal
from .interactive_stream import register_codex_run, unregister_codex_run
from .models import ArchitectureDocument, Brief, Conversation, Message, Paper, TaskRun, utcnow
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
    if task.kind == "daily_brief":
        run_date = (
            payload.get("date") or datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
        )
        return f"""{workflow_common}

Run the daily paper workflow for {run_date}. Start with
`frontier interest context --recent-limit 30`; this bounded projection is the canonical handoff from
paper-reading Agents and user-confirmed feedback. It contains explicit interest priors, confirmed
positive and negative interest, quality judgments, final user-selected tags, private attitude
summaries, recommendation attribution, and historical calibration. Treat confirmed feedback as
stronger than seed interests or Agent-authored graph structure. Keep interest and paper quality as
independent axes. A neutral judgment is neither positive nor negative interest. An ignored
recommendation is weak negative-interest evidence, not proof of low
paper quality. Do not learn from Agent suggestions or from unread candidates.
If confirmed feedback exists and `layers.semantic_profile` is missing, marked `stale`, or its
metadata `feedback_digest` differs from the current `feedback_digest`, rebuild that single derived profile with
`frontier interest profile-update`. Include only durable cross-paper preferences, keep positive and
negative evidence, and cite the provided `evidence_key` values in each semantic preference. Never
copy paper summaries into the profile or create additional feedback-profile memories. A 409 means
evidence changed; reload `interest context` and retry once before ranking. After a successful profile
update, reload `interest context` once and use that new `source_digest` for brief creation.
You are the single daily-workflow root Agent responsible for candidate discovery, bibliographic
verification, ranking, and the canonical summary and analysis fields saved for every selected paper.
You may delegate independent full-text evidence extraction for different candidates to read-only
subagents, but you must verify their evidence, make the final ranking decision, and perform every
Frontier mutation yourself. Candidate analysis must not use or create a paper-reading Conversation;
that long-lived main Agent is reserved for papers the user actively reads and discusses.
Write only pre-reading catalog fields such as `Paper.summary` and recommendation analysis. Never
write `completion_summary` or `completion_summary_structured`; those are user-reading conclusions
owned exclusively by the paper main Agent after the user finishes reading.
Use the context's `source_digest` when creating the brief as `interest_context_digest`. Adjust
discovery and ranking using topic affinities, recent semantic attitude evidence, hit rates, and
Brier scores. When feedback support is small, avoid overfitting and keep modest cross-domain
exploration. If interest hit rate is weak, tighten personal relevance; if quality hit rate or its
calibration is weak, raise the evidence threshold independently. The graph describes research
questions and accumulated evidence; it is not by itself proof that the user likes a topic.
Collect arXiv candidates using the calibrated interests and relevant graph questions. Respect the current
limits returned by `frontier setting list`: abstracts, full texts, total brief items, and featured
items. Before ranking or adding a paper, inspect its existing record and exclude every paper whose
status is not `candidate`; papers in `reading`, `feedback_pending`, `completed`, or `ignored` status
must never appear in the daily recommendations. Read full PDF text for final candidates and update
their paper analyses. Paper summaries, Agent comments, recommendation reasons, problem, innovation,
experiment, and limitation fields must be written in Simplified Chinese. Do not attach daily
or candidate papers to research nodes, create paper-backed node progress, or otherwise treat them
as accumulated evidence: graph connection is allowed only after the user completes reading and
confirms feedback, so the paper status is `completed`. You may describe probable graph relevance
in the recommendation fields. Create and publish exactly one brief for the date, then write concise
experience memories when useful. If a required source or PDF is unavailable, record the concrete
failure instead of inventing content. Finish with a short JSON-like summary containing brief_id and
counts.
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
Verify and update author names, first author, corresponding author, primary affiliation, source and
publication metadata when the evidence supports them; never invent missing roles or affiliations.
Save a concise Simplified-Chinese one-line summary, one-line Agent comment, problem, innovation,
experiment evidence, limitations, research score, relevance score, recommendation reason, and
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
Chinese. Save `completion_summary_structured` with exactly these fields: `research_question`,
`evidence_and_contribution`, `experiments`, `limitations`, and `user_attitude`. Every material paper
claim in the first four fields must cite a page as [[page:N]]. Derive `user_attitude` only from the
user's annotations, notes, and conversation; state that evidence is insufficient instead of
inventing an attitude. Save suggested tags and the structured summary with `frontier paper finish`.
Also save `suggested_interest_level` as exactly one of `ignored`, `neutral`, or `interested`.
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
save a research idea when the user explicitly asks to save it. Current UI context: {context}

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
        content = "".join(stream_translation_prompt(prompt))
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


def run_task(task_id: str) -> None:
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        is_translation = bool(task and task.kind == "translate")
    if is_translation:
        _run_translation_task(task_id)
        return
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if not task or task.status != "queued":
            return
        if task.kind == "graph_maintenance" and not _graph_feedback_is_active(
            session, task
        ):
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
        if conversation and conversation.codex_session_id:
            command = [
                settings.codex_command,
                "exec",
                "resume",
            ]
            if settings.codex_sandbox == "workspace-write" and settings.codex_network_access:
                command.extend(["-c", "sandbox_workspace_write.network_access=true"])
            command.extend(
                [
                    "--json",
                    "-o",
                    str(output_path),
                    conversation.codex_session_id,
                    prompt,
                ]
            )
        else:
            command = [
                settings.codex_command,
                "exec",
                "--json",
                "-o",
                str(output_path),
                "-C",
                str(PROJECT_ROOT),
                "-s",
                settings.codex_sandbox,
            ]
            if settings.codex_profile:
                command.extend(["--profile", settings.codex_profile])
            if settings.codex_sandbox == "workspace-write" and settings.codex_network_access:
                command.extend(["-c", "sandbox_workspace_write.network_access=true"])
            command.append(prompt)

    environment = agent_environment()
    uv_cache_dir = settings.task_dir / "uv-cache"
    uv_cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=os.name == "posix",
            )
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
            finally:
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
                if document:
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
            final_result = {"response": response}
            if return_code != 0:
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
                    final_error = "Agent finished without saving the required paper analysis fields."
            elif task.kind == "architecture_generation":
                document = session.get(
                    ArchitectureDocument, task.payload_json.get("paper_id")
                )
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
                document = session.get(
                    ArchitectureDocument, task.payload_json.get("paper_id")
                )
                if document:
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
                    document = session.get(
                        ArchitectureDocument, task.payload_json.get("paper_id")
                    )
                    if document:
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
