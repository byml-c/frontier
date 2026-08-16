from __future__ import annotations

import re
from typing import Any

from ..config import settings
from ..database import SessionLocal
from ..models import TaskRun, utcnow

ARCHITECTURE_PROGRESS_KEY = "architectureProgress"


def _short_progress_text(value: str, limit: int = 220) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1]}…"


def _command_labels(command: str) -> tuple[str, str]:
    if re.search(r"frontier\s+paper\s+text\b", command):
        page = re.search(r"--page\s+(\d+)", command)
        target = f"论文第 {page.group(1)} 页" if page else "论文正文"
        return f"正在读取{target}", f"已读取{target}"
    if re.search(r"frontier\s+paper\s+resources\b", command):
        return "正在读取论文图表与结构", "已读取论文图表与结构"
    if re.search(r"frontier\s+paper\s+render-page\b", command):
        return "正在检查论文页面", "已检查论文页面"
    architecture_actions = {
        "checkout": ("正在准备架构 JSON", "已准备架构 JSON"),
        "validate": ("正在校验架构 JSON", "架构 JSON 已通过校验"),
        "commit": ("正在提交架构图", "已提交架构图"),
        "upload-asset": ("正在上传架构图片", "已上传架构图片"),
        "status": ("正在读取当前架构", "已读取当前架构"),
    }
    architecture = re.search(r"frontier\s+architecture\s+([\w-]+)", command)
    if architecture and architecture.group(1) in architecture_actions:
        return architecture_actions[architecture.group(1)]
    if "scene.architecture.json" in command:
        return "正在处理架构 JSON", "已处理架构 JSON"
    return "正在运行研究工具", "已完成研究工具调用"


def architecture_progress_updates(
    event: dict[str, Any], scene_line_count: int | None = None
) -> list[dict[str, str]]:
    """Convert background Codex JSONL events into public, durable activity rows."""
    updates: list[dict[str, str]] = []
    event_type = event.get("type")
    if event_type == "thread.started":
        updates.append(
            {
                "id": "agent-startup",
                "kind": "status",
                "label": "Agent 已连接",
                "status": "completed",
            }
        )
    elif event_type == "thread":
        updates.append(
            {
                "id": "agent-startup",
                "kind": "status",
                "label": "Agent 已连接",
                "status": "completed",
            }
        )
        updates.append(
            {
                "id": "architecture-analysis",
                "kind": "reasoning",
                "label": "正在分析论文架构",
                "status": "running",
            }
        )
    elif event_type == "activity":
        status = event.get("status")
        if status in {"running", "completed", "failed", "cancelled"}:
            updates.append(
                {
                    "id": str(event.get("activity_id") or "architecture-activity"),
                    "kind": str(event.get("kind") or "status"),
                    "label": str(event.get("label") or "正在处理论文架构"),
                    **(
                        {"detail": _short_progress_text(event["detail"])}
                        if isinstance(event.get("detail"), str) and event["detail"].strip()
                        else {}
                    ),
                    "status": status,
                }
            )
    elif event_type == "turn.started":
        updates.append(
            {
                "id": "architecture-analysis",
                "kind": "reasoning",
                "label": "正在分析论文架构",
                "status": "running",
            }
        )
    elif event_type in {"item.started", "item.completed"}:
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "command_execution":
            running, completed = _command_labels(str(item.get("command") or ""))
            exit_code = item.get("exit_code")
            status = "running" if event_type == "item.started" else (
                "failed" if isinstance(exit_code, int) and exit_code != 0 else "completed"
            )
            updates.append(
                {
                    "id": str(item.get("id") or "architecture-tool"),
                    "kind": "tool",
                    "label": (
                        running
                        if status == "running"
                        else completed
                        if status == "completed"
                        else f"{running.removeprefix('正在')}未完成"
                    ),
                    "status": status,
                }
            )
        elif (
            event_type == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
            and item["text"].strip()
        ):
            updates.append(
                {
                    "id": str(item.get("id") or "architecture-update"),
                    "kind": "commentary",
                    "label": "Agent 更新",
                    "detail": _short_progress_text(item["text"]),
                    "status": "completed",
                }
            )
    if scene_line_count is not None:
        updates.append(
            {
                "id": "architecture-json-lines",
                "kind": "tool",
                "label": "正在写入架构 JSON",
                "detail": f"当前 {scene_line_count} 行",
                "status": "running",
            }
        )
    return updates


def _scene_line_count(task_id: str) -> int | None:
    path = settings.task_dir / task_id / "scene.architecture.json"
    try:
        with path.open(encoding="utf-8") as scene_file:
            return sum(1 for _ in scene_file)
    except (OSError, UnicodeError):
        return None


def persist_architecture_progress(task_id: str, event: dict[str, Any]) -> None:
    updates = architecture_progress_updates(event, _scene_line_count(task_id))
    if not updates:
        return
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if not task or task.kind != "architecture_generation" or task.status not in {
            "queued",
            "running",
        }:
            return
        result = dict(task.result_json or {})
        progress = dict(result.get(ARCHITECTURE_PROGRESS_KEY) or {})
        activities = [
            dict(item) for item in progress.get("activities", []) if isinstance(item, dict)
        ]
        for update_item in updates:
            index = next(
                (index for index, item in enumerate(activities) if item.get("id") == update_item["id"]),
                None,
            )
            if index is None:
                activities.append(update_item)
            else:
                activities[index] = {**activities[index], **update_item}
        progress["activities"] = activities[-80:]
        progress["updatedAt"] = utcnow().isoformat()
        result[ARCHITECTURE_PROGRESS_KEY] = progress
        task.result_json = result
        session.commit()
