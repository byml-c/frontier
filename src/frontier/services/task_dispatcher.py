from __future__ import annotations

import logging
import queue
import threading

from ..config import settings

logger = logging.getLogger(__name__)

_QUEUE_CAPACITY_PER_WORKER = 8
_state_lock = threading.Lock()
_lane_queues: dict[str, queue.Queue[str]] = {}
_workers: list[threading.Thread] = []
_accepted_task_ids: set[str] = set()
_stopping = False


def _run_worker(work_queue: queue.Queue[str], lane: str) -> None:
    from ..agent_runner import run_task

    try:
        while True:
            with _state_lock:
                if _stopping:
                    return
            try:
                task_id = work_queue.get(timeout=0.25)
            except queue.Empty:
                with _state_lock:
                    if _stopping:
                        return
                continue
            try:
                try:
                    run_task(task_id)
                except Exception:
                    logger.exception("Dispatched %s task %s crashed outside its runner", lane, task_id)
                finally:
                    with _state_lock:
                        _accepted_task_ids.discard(task_id)
            finally:
                work_queue.task_done()
    finally:
        with _state_lock:
            current = threading.current_thread()
            if current in _workers:
                _workers.remove(current)


def start_task_dispatcher() -> None:
    """Start separate general and metadata worker lanes once per process."""
    global _stopping, _lane_queues
    with _state_lock:
        if _workers:
            return
        _stopping = False
        lane_counts = {
            "general": settings.general_agent_max_concurrent_runs,
            "metadata": settings.metadata_agent_max_concurrent_runs,
        }
        _lane_queues = {}
        for lane, worker_count in lane_counts.items():
            lane_queue = queue.Queue(
                maxsize=max(worker_count, worker_count * _QUEUE_CAPACITY_PER_WORKER)
            )
            _lane_queues[lane] = lane_queue
            for index in range(worker_count):
                worker = threading.Thread(
                    target=_run_worker,
                    args=(lane_queue, lane),
                    name=f"frontier-{lane}-{index + 1}",
                    daemon=True,
                )
                _workers.append(worker)
                worker.start()


def submit_task(
    task_id: str,
    *,
    fail_on_full: bool = True,
    lane: str = "general",
) -> bool:
    """Submit without blocking; metadata has a queue independent from scoring."""
    start_task_dispatcher()
    accepted = False
    with _state_lock:
        work_queue = _lane_queues.get(lane)
        if _stopping or work_queue is None:
            pass
        elif task_id in _accepted_task_ids:
            return True
        else:
            try:
                work_queue.put_nowait(task_id)
            except queue.Full:
                logger.error("%s task dispatch queue is full; rejected %s", lane, task_id)
            else:
                _accepted_task_ids.add(task_id)
                accepted = True
    if not accepted and fail_on_full:
        _fail_undispatched_task(task_id, error="Agent 任务队列已满，任务未启动")
    return accepted


def _fail_undispatched_task(task_id: str, *, error: str) -> None:
    from sqlalchemy import update

    from ..database import SessionLocal
    from ..models import Paper, TaskRun, utcnow
    from .architecture_scene import mark_architecture_generation_failed
    from .manual_recommendations import set_manual_analysis_state
    from .paper_metadata import mark_metadata_enrichment_failed
    from .podcast_generation import mark_podcast_task_failed

    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        if task is None or task.status != "queued":
            return
        transition = session.execute(
            update(TaskRun)
            .where(TaskRun.id == task_id, TaskRun.status == "queued")
            .values(status="failed", error=error, finished_at=utcnow())
        )
        if transition.rowcount != 1:
            session.rollback()
            return
        if task.kind == "architecture_generation":
            mark_architecture_generation_failed(session, task_id, error)
        elif task.kind in {"manual_paper_analysis", "paper_metadata_analysis"}:
            set_manual_analysis_state(session, task, "failed", error=error)
            session.commit()
        elif task.kind == "paper_metadata_enrichment":
            mark_metadata_enrichment_failed(session, task, error)
        elif task.kind == "paper_abstract_translation":
            from .abstract_translation import ABSTRACT_TRANSLATION_STATE_KEY

            paper_id = (task.payload_json or {}).get("paper_id")
            paper = session.get(Paper, paper_id) if isinstance(paper_id, str) else None
            if paper is not None:
                metadata = dict(paper.metadata_json or {})
                metadata[ABSTRACT_TRANSLATION_STATE_KEY] = {
                    **dict(metadata.get(ABSTRACT_TRANSLATION_STATE_KEY) or {}),
                    "status": "failed",
                    "task_id": task.id,
                    "error": error,
                }
                paper.metadata_json = metadata
            session.commit()
        elif task.kind == "podcast_episode_render":
            mark_podcast_task_failed(session, task, error)
            session.commit()
        else:
            session.commit()


def stop_task_dispatcher(*, wait: bool = False) -> None:
    """Stop both lanes, fail queued work, and optionally join workers."""
    global _stopping
    pending: list[str] = []
    with _state_lock:
        if not _workers:
            return
        _stopping = True
        workers = list(_workers)
        for work_queue in _lane_queues.values():
            while True:
                try:
                    task_id = work_queue.get_nowait()
                except queue.Empty:
                    break
                pending.append(task_id)
                _accepted_task_ids.discard(task_id)
                work_queue.task_done()
    for task_id in pending:
        _fail_undispatched_task(
            task_id,
            error="服务停止前任务尚未启动，已安全终止",
        )
    if wait:
        for worker in workers:
            worker.join()
    with _state_lock:
        if wait or not any(worker.is_alive() for worker in workers):
            _workers.clear()
            _lane_queues.clear()
            _accepted_task_ids.clear()


def task_dispatcher_snapshot() -> dict[str, int | bool]:
    """Expose bounded-pool state for health checks and deterministic tests."""
    with _state_lock:
        general = _lane_queues.get("general")
        metadata = _lane_queues.get("metadata")
        return {
            "running": bool(_workers) and not _stopping,
            "workers": len(_workers),
            "general_workers": settings.general_agent_max_concurrent_runs,
            "metadata_workers": settings.metadata_agent_max_concurrent_runs,
            "capacity": general.maxsize if general is not None else 0,
            "metadata_capacity": metadata.maxsize if metadata is not None else 0,
            "accepted": len(_accepted_task_ids),
            "queued_general": general.qsize() if general is not None else 0,
            "queued_metadata": metadata.qsize() if metadata is not None else 0,
        }
