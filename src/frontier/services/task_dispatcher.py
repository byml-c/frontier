from __future__ import annotations

import logging
import queue
import threading

from ..config import settings

logger = logging.getLogger(__name__)

_QUEUE_CAPACITY_PER_WORKER = 8
_state_lock = threading.Lock()
_work_queue: queue.Queue[str] | None = None
_workers: list[threading.Thread] = []
_accepted_task_ids: set[str] = set()
_stopping = False


def _run_worker(work_queue: queue.Queue[str]) -> None:
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
                    logger.exception("Dispatched task %s crashed outside its runner", task_id)
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
    """Start the fixed worker pool once for this service process."""
    global _stopping, _work_queue
    with _state_lock:
        if _workers:
            return
        _stopping = False
        worker_count = settings.agent_max_concurrent_runs
        _work_queue = queue.Queue(
            maxsize=max(worker_count, worker_count * _QUEUE_CAPACITY_PER_WORKER)
        )
        for index in range(worker_count):
            worker = threading.Thread(
                target=_run_worker,
                args=(_work_queue,),
                name=f"frontier-task-{index + 1}",
                daemon=True,
            )
            _workers.append(worker)
            worker.start()


def submit_task(task_id: str) -> bool:
    """Submit without blocking; duplicate pending/running IDs are accepted once."""
    start_task_dispatcher()
    accepted = False
    with _state_lock:
        if _stopping or _work_queue is None:
            pass
        elif task_id in _accepted_task_ids:
            return True
        else:
            try:
                _work_queue.put_nowait(task_id)
            except queue.Full:
                logger.error("Task dispatch queue is full; rejected %s", task_id)
            else:
                _accepted_task_ids.add(task_id)
                accepted = True
    if not accepted:
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
    """Stop accepting work and fail bounded queued items without blocking shutdown."""
    global _stopping, _work_queue
    pending: list[str] = []
    with _state_lock:
        if not _workers or _work_queue is None:
            return
        _stopping = True
        workers = list(_workers)
        while True:
            try:
                task_id = _work_queue.get_nowait()
            except queue.Empty:
                break
            pending.append(task_id)
            _accepted_task_ids.discard(task_id)
            _work_queue.task_done()
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
            _accepted_task_ids.clear()
            _work_queue = None


def task_dispatcher_snapshot() -> dict[str, int | bool]:
    """Expose bounded-pool state for health checks and deterministic tests."""
    with _state_lock:
        return {
            "running": bool(_workers) and not _stopping,
            "workers": len(_workers),
            "accepted": len(_accepted_task_ids),
            "queued": _work_queue.qsize() if _work_queue is not None else 0,
            "capacity": _work_queue.maxsize if _work_queue is not None else 0,
        }
