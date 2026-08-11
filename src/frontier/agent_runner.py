from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .config import PROJECT_ROOT, settings
from .database import SessionLocal
from .models import Brief, Conversation, Message, Paper, TaskRun, utcnow
from .services.common import log_operation
from .services.manual_recommendations import set_manual_analysis_state
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
        r"\[\[(?:page|p)[:. ]?(\d+)(?:\|([^\]]+))?\]\]", content, re.IGNORECASE
    ):
        citations.append(
            {
                "paper_id": paper_id,
                "page_number": int(match.group(1)),
                "label": match.group(2) or f"第 {match.group(1)} 页",
                "marker": match.group(0),
            }
        )
    return citations


def build_prompt(task: TaskRun) -> str:
    payload = task.payload_json
    workflow_common = (
        "Use the project-local `frontier-research` skill. "
        "Operate application data only through `uv run frontier` CLI commands. "
        "Never edit the SQLite database or data files directly. "
        f"The task id is {task.id}."
    )
    interactive_common = (
        "You are the Frontier research assistant. Respond directly without announcing a plan, "
        "reading instruction files, or describing routine tool use. Use `uv run frontier` only when "
        "application context or a requested mutation requires it; never edit data files directly. "
        "Format responses with concise Markdown. Use standard LaTeX delimiters for mathematical "
        "expressions when formulas materially improve the explanation. "
        f"The task id is {task.id}."
    )
    if task.kind == "daily_brief":
        run_date = (
            payload.get("date") or datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
        )
        return f"""{workflow_common}

Run the daily paper workflow for {run_date}. Read relevant active memories and graph nodes first.
Collect arXiv candidates using the research interests maintained in the graph. Respect the current
limits returned by `frontier setting list`: abstracts, full texts, total brief items, and featured
items. Read full PDF text for final candidates and update their paper analyses. Do not attach daily
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
Verify and update author names, first author, corresponding author, primary affiliation, source and
publication metadata when the evidence supports them; never invent missing roles or affiliations.
Save a concise one-line summary, one-line Agent comment, problem, innovation, experiment evidence,
limitations, research score, relevance score, recommendation reason, and `suggested_tags` with
`frontier paper update`. Use explicit unavailable/evidence-insufficient wording for fields that cannot
be established. Do not attach the paper to the graph or add node progress before user-confirmed
reading is completed. Finish with a short summary of fields updated and any concrete source failure.
"""
    if task.kind == "finish_reading":
        paper_id = payload["paper_id"]
        return f"""{workflow_common}

Prepare the completion summary for paper {paper_id}. Load the paper, extracted PDF pages,
annotations, notes, and its paper conversation. Summarize the question, evidence, contribution,
experiment, limitations, the user's apparent attitude, and suggested tags. Every material paper
claim must cite a page as [[page:N]]. Save only suggestions with `frontier paper finish`; do not
confirm interest, quality, tags, or node creation on the user's behalf.
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
            return f"""{interactive_common}

Answer the user's paper-reading question about paper {paper_id}. Use `frontier paper context` to
load PDF text and reading artifacts. Discuss through problem decomposition and mutual inquiry.
Do not mix simple translation results into this conversation. Cite every material paper claim with
[[page:N]]. Current UI context: {context}

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
            if not task or task.status == "running":
                return
            task.status = "running"
            task.started_at = utcnow()
            prompt = build_prompt(task)
            session.commit()
        content = "".join(stream_translation_prompt(prompt))
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if not task:
                return
            task.status = "completed"
            task.result_json = {"response": content}
            task.finished_at = utcnow()
            session.commit()
    except Exception as exc:  # noqa: BLE001
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if task:
                task.status = "failed"
                task.error = str(exc)
                task.finished_at = utcnow()
                session.commit()


def run_task(task_id: str) -> None:
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        is_translation = bool(task and task.kind == "translate")
    if is_translation:
        _run_translation_task(task_id)
        return
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if not task or task.status == "running":
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

    environment = os.environ.copy()
    environment["FRONTIER_API_BASE_URL"] = settings.api_base_url
    uv_cache_dir = settings.task_dir / "uv-cache"
    uv_cache_dir.mkdir(parents=True, exist_ok=True)
    environment["UV_CACHE_DIR"] = str(uv_cache_dir)
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
            )
            session_id = None
            assert process.stdout is not None
            for line in process.stdout:
                log_file.write(line)
                log_file.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = session_id or _find_session_id(event)
            return_code = process.wait()
        response = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if not task:
                return
            task.codex_session_id = session_id
            daily_brief = None
            if task.kind == "daily_brief":
                daily_brief = session.scalar(
                    select(Brief).where(Brief.task_id == task.id, Brief.status == "published")
                )
            if return_code != 0:
                task.status = "failed"
                task.error = f"Codex exited with status {return_code}. See {log_path}."
            elif task.kind == "daily_brief" and daily_brief is None:
                task.status = "failed"
                task.error = "Agent finished without publishing the required daily brief."
                task.result_json = {"response": response}
            elif task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
                paper = session.get(Paper, task.payload_json.get("paper_id"))
                if not paper or not all(
                    (paper.summary, paper.agent_comment, paper.recommendation_reason)
                ):
                    task.status = "failed"
                    task.error = "Agent finished without saving the required paper analysis fields."
                    task.result_json = {"response": response}
                else:
                    task.status = "completed"
                    task.result_json = {"response": response}
            else:
                task.status = "completed"
                task.result_json = {"response": response}
            if task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
                set_manual_analysis_state(
                    session,
                    task,
                    "completed" if task.status == "completed" else "failed",
                    error=task.error,
                )
            task.finished_at = utcnow()
            conversation_id = task.payload_json.get("conversation_id")
            if conversation_id:
                conversation = session.get(Conversation, conversation_id)
                if conversation:
                    conversation.codex_session_id = session_id or conversation.codex_session_id
                    if return_code == 0:
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
                action=f"task.{task.status}",
                entity_type="task",
                entity_id=task.id,
                task_id=task.id,
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 - persist all runner failures for the user
        with SessionLocal() as session:
            task = session.get(TaskRun, task_id)
            if task:
                task.status = "failed"
                task.error = str(exc)
                task.finished_at = utcnow()
                if task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
                    set_manual_analysis_state(session, task, "failed", error=task.error)
                session.commit()


def enqueue_task(kind: str, payload: dict[str, Any] | None = None) -> str:
    with SessionLocal() as session:
        task = TaskRun(kind=kind, payload_json=payload or {})
        session.add(task)
        session.commit()
        return task.id
