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
from .services.codex_backends import conversation_selection
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
    payload = task.payload_json or {}
    reading = task.kind in {
        "paper_chat",
        "global_chat",
        "manual_paper_analysis",
        "paper_metadata_analysis",
        "architecture_generation",
        "finish_reading",
    }
    delegation = (
        "Reading Agents may use at most one concurrent read-only subagent, no nested delegation. "
        "Only the main Agent may mutate Frontier state. "
        if reading
        else "Never spawn subagents, start another Codex process, or queue per-paper Agent tasks. "
    )
    workflow_common = (
        "Use the project-local frontier-research skill and `uv run frontier` CLI for application data. "
        "Never access SQLite or edit Server files directly. "
        + delegation
        + "Paper content has exactly two Markdown fields: pre_read_summary (before reading) and "
        "post_read_summary (after reading). Put all findings, evidence, methods, experiments and "
        "limitations into those Markdown documents; do not create separate summary fields or JSON copies. "
        "There is one score dimension, from 0 to 1; null means unknown and 0 is a valid score. "
        "Agent may write agent_score; model_score belongs to the prediction service; user_score belongs "
        "only to explicit user input. Never infer user_score from status, suggestions or booleans. "
        "Write persisted prose in Simplified Chinese; preserve technical names and formulas. "
        r"Use \( ... \) for inline math and \[ ... \] for display math. Cite paper evidence with [[page:N]]. "
        "For authors/groups use paper author list/get/create/update/delete, research-group "
        "search/get/create/update/delete and paper assign-group. Verify paper-time affiliations, "
        "distinguish current affiliations, and preserve user-confirmed evidence. "
        f"Temporary artifacts belong only under data/tasks/{task.id}/. "
        "Search the local library first with frontier paper list --query; ask for confirmation when "
        "a named paper is ambiguous. Never run uv add or install dependencies. "
    )
    interactive_common = (
        workflow_common + "Respond directly to the user's question without routine tool narration. "
    )
    if task.kind == "daily_brief":
        run_date = (
            payload.get("date") or datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
        )
        recovery_sources = payload.get("recovery_source_task_ids") or []
        discovery_step = (
            f"Read source tasks {json.dumps(recovery_sources)}. "
            "Union and deduplicate their saved candidate_pool.paper_ids; retain only status=candidate papers. "
            "Recheck current human ratings and prior recommendations. Do NOT run arxiv-discover again "
            "or rediscover with arxiv-sync. Preserve their discovery_run_id when available."
            if recovery_sources
            else f"Run `frontier source arxiv-sync --date {run_date}` once; retry incomplete coverage unchanged. "
            "Use only this discovery_run_id and its returned candidate IDs. "
            "If valid RSS candidates were saved during an API outage, preserve coverage_complete=false "
            "and publish the nonempty available pool with an explicit coverage note; do not rerun "
            "a usable RSS batch repeatedly or describe incomplete coverage as no new papers."
        )
        return (
            workflow_common
            + f"""
Run the whole daily workflow for {run_date} serially in this single background Agent.
Read frontier interest context and frontier setting list. If the derived profile is stale, update it
using explicit evidence keys with interest profile-update, then reload interest context once.
{discovery_step}
Review agent_review_paper_ids only;
respect confident model scores and preserve all user feedback. Use titles, abstracts and authors
for screening. Do not read full texts, download complete PDFs or create paper Conversations.
For selected papers, verify first_author and primary_affiliation with paper metadata-context and,
only when needed, a bounded paper first-page snippet. This affiliation snippet is the sole
exception to abstract-only screening. Unknown affiliation stays null. Do not create groups here.
Save the complete eligible pool using `frontier task candidate-pool {task.id}` with paper_ids,
discovery_complete, discovery_run_id and notes. Rank eligible candidate papers by agent_score,
falling back to model_score; null is unknown, never a zero. Exclude already recommended or human-rated
papers. For the final 20–50 papers (within brief_limit), save pre_read_summary as concise Markdown
combining an overview, evidence-backed highlights and the recommendation reason, plus agent_score.
Preserve existing post_read_summary. Extract model_short_name/search_aliases only from source evidence.
Create one brief with task_id={task.id}, date and interest_context_digest=source_digest; add the ranked
papers and publish. Daily pages read current Paper records: never create a paper-content snapshot.
If fewer papers exist, explain the shortage; if none exist, save an empty complete pool with notes
and publish an empty brief. Never refill from old library papers. Stop after publication.
"""
        )
    if task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
        return (
            workflow_common
            + f"""
Read paper {payload["paper_id"]} fully via frontier paper download/text and relevant rendered pages.
Write a source-grounded post_read_summary Markdown document and a concise pre_read_summary;
include page citations, author/affiliation evidence and unknowns. Save agent_score if justified.
Do not write user_score or confirm feedback. Do not create a paper Conversation for this one-shot task.
"""
        )
    if task.kind == "finish_reading":
        paper_id = str(payload["paper_id"])
        sync = _paper_agent_context_instruction(
            paper_id, payload.get("conversation_id"), payload.get("paper_context_sync_required")
        )
        return (
            workflow_common
            + f"""
{sync}
Consolidate the current discussion and verified paper evidence into one post_read_summary Markdown
for paper {paper_id}. Do not reread the whole PDF merely to refresh context. Include findings,
limitations and open questions naturally in Markdown; no fixed form or required section fields.
Use frontier paper finish with post_read_summary, optional suggested_score, suggested_tags and
suggested_nodes. Suggestions are not user_score. The user confirms ratings, tags and graph links.
"""
        )
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
    task_kind: str | None = None,
) -> list[str]:
    command = [settings.codex_command, "--strict-config"]
    if settings.codex_profile:
        command.extend(["--profile", settings.codex_profile])
    command.extend(
        codex_subagent_limit_args(
            allow_subagents=task_kind
            in {
                None,
                "paper_chat",
                "paper_analysis",
                "manual_paper_analysis",
                "paper_metadata_analysis",
                "architecture_generation",
                "finish_reading",
            }
        )
    )
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
            task
            and task.kind
            in {"translate", "abstract_translation_batch", PAPER_ABSTRACT_TRANSLATION_KIND}
        )
        uses_app_server = bool(
            task and task.kind != "daily_brief" and (task.payload_json or {}).get("conversation_id")
        )
        remaining_seconds = _remaining_task_seconds(task, settings.agent_task_timeout_seconds)
        if not is_translation and remaining_seconds <= 0:
            _fail_expired_queued_task(session, task)
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
        codex_selection = task.payload_json.get("codex") or (
            conversation_selection(session, conversation.id) if conversation else None
        )
        recovery_prompt = (
            build_conversation_recovery_prompt(session, conversation.id, prompt)
            if conversation
            else None
        )
        session.commit()

    response_parts: list[str] = []
    thread_id = session_id
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log_file:
            for event in stream_codex_prompt(
                recovery_prompt if session_id is None and recovery_prompt else prompt,
                session_id=session_id,
                recovery_prompt=recovery_prompt,
                run_id=task_id,
                timeout_seconds=remaining_seconds,
                **({"selection": codex_selection} if codex_selection else {}),
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
            task_kind=task.kind,
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
                if not paper or not all((paper.pre_read_summary,)):
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
