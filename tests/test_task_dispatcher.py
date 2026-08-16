from __future__ import annotations

import threading

from frontier import agent_runner
from frontier.database import SessionLocal
from frontier.models import TaskRun
from frontier.services import task_dispatcher


def test_dispatcher_is_bounded_and_deduplicates_task_ids(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def run_task(task_id: str) -> None:
        if task_id == first_id:
            started.set()
            assert release.wait(timeout=5)

    monkeypatch.setattr(agent_runner, "run_task", run_task)
    monkeypatch.setattr(task_dispatcher.settings, "agent_max_concurrent_runs", 1)
    monkeypatch.setattr(task_dispatcher, "_QUEUE_CAPACITY_PER_WORKER", 1)
    task_dispatcher.stop_task_dispatcher(wait=True)
    with SessionLocal() as session:
        tasks = [TaskRun(kind="graph_maintenance") for _ in range(3)]
        session.add_all(tasks)
        session.commit()
        first_id, second_id, rejected_id = (task.id for task in tasks)

    try:
        assert task_dispatcher.submit_task(first_id) is True
        assert started.wait(timeout=5)
        assert task_dispatcher.submit_task(first_id) is True
        assert task_dispatcher.submit_task(second_id) is True
        assert task_dispatcher.submit_task(rejected_id) is False
        snapshot = task_dispatcher.task_dispatcher_snapshot()
        assert snapshot["workers"] == 1
        assert snapshot["capacity"] == 1
        assert snapshot["accepted"] == 2
        with SessionLocal() as session:
            rejected = session.get_one(TaskRun, rejected_id)
            assert rejected.status == "failed"
            assert "队列已满" in str(rejected.error)
    finally:
        release.set()
        task_dispatcher.stop_task_dispatcher(wait=True)
