from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from frontier import agent_runner
from frontier.api import architectures
from frontier.database import SessionLocal
from frontier.models import ArchitectureDocument, Conversation, TaskRun
from frontier.services.architecture_progress import architecture_progress_updates
from frontier.services.architecture_scene import ArchitectureError


@pytest.fixture(autouse=True)
def _enable_architecture_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(architectures.settings, "enable_codex_runs", True)
    monkeypatch.setattr(architectures, "submit_task", lambda _task_id: True)


def create_architecture_paper(client: TestClient, suffix: str) -> dict:
    response = client.post(
        "/api/papers",
        json={
            "title": f"Architecture Reader Contract {suffix}",
            "abstract": "A model connects an observation encoder to an action head.",
            "authors": [{"name": "Architecture Tester"}],
            "source": "manual",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["paper"]


def test_architecture_generation_reuses_active_paper_main_conversation(
    client: TestClient,
) -> None:
    paper_id = create_architecture_paper(client, "active-root")["id"]
    with SessionLocal() as session:
        archived = Conversation(
            kind="paper",
            reference_id=paper_id,
            title="旧论文问答",
            status="archived",
            codex_session_id="archived-thread",
        )
        active = Conversation(
            kind="paper",
            reference_id=paper_id,
            title="论文问答",
            status="active",
            codex_session_id="active-thread",
        )
        session.add_all([archived, active])
        session.commit()
        active_id = active.id

    conversation = client.post(
        "/api/conversations",
        json={"kind": "paper", "reference_id": paper_id, "title": "新标题"},
    )
    assert conversation.status_code == 200, conversation.text
    assert conversation.json()["id"] == active_id

    generated = client.post(
        f"/api/papers/{paper_id}/architecture/generate",
        json={"rebuild": True},
    )
    assert generated.status_code == 200, generated.text
    with SessionLocal() as session:
        task = session.scalar(
            select(TaskRun)
            .where(TaskRun.kind == "architecture_generation")
            .order_by(TaskRun.created_at.desc())
            .limit(1)
        )
        assert task is not None
        assert task.payload_json["conversation_id"] == active_id


def sample_scene(*, layout_epoch: int = 1) -> dict:
    return {
        "schemaVersion": 1,
        "layoutEpoch": layout_epoch,
        "nodes": [
            {
                "id": "observation",
                "type": "data",
                "title": "RGB Observation",
                "position": {"x": 40, "y": 100},
                "sourceIds": ["page-2"],
            },
            {
                "id": "encoder",
                "type": "module",
                "title": "Vision Encoder",
                "symbol": "g_\\psi",
                "architecture": "ViT",
                "keywords": ["pretrained"],
                "position": {"x": 360, "y": 100},
                "sourceIds": ["page-2"],
            },
        ],
        "tensors": [
            {
                "id": "visual_tokens",
                "symbol": "h_v",
                "shape": "[B,N,D]",
                "sourceIds": ["page-2"],
            }
        ],
        "edges": [
            {
                "id": "observation_to_encoder",
                "source": "observation",
                "target": "encoder",
                "tensorId": "visual_tokens",
                "sourceIds": ["page-2"],
            }
        ],
        "annotations": [],
        "groups": [],
        "sources": [{"id": "page-2", "kind": "paper-page", "pageNumber": 2}],
        "assets": [],
    }


def test_architecture_background_generation_uses_paper_app_server_thread(
    client: TestClient,
    monkeypatch,
) -> None:
    paper_id = create_architecture_paper(client, "app-server-writer")["id"]
    with SessionLocal() as session:
        session.add(
            Conversation(
                kind="paper",
                reference_id=paper_id,
                title="论文问答",
                status="active",
                codex_session_id="paper-thread",
            )
        )
        session.commit()

    generated = client.post(
        f"/api/papers/{paper_id}/architecture/generate",
        json={"rebuild": True},
    )
    assert generated.status_code == 200, generated.text
    task_id = generated.json()["taskId"]
    observed: dict = {}

    def fake_stream(prompt: str, session_id: str | None = None, **options):
        observed.update(prompt=prompt, session_id=session_id, **options)
        yield {"type": "thread", "thread_id": "paper-thread"}
        yield {
            "type": "activity",
            "activity_id": "tool-1",
            "kind": "tool",
            "label": "已提交架构图",
            "status": "completed",
        }
        yield {"type": "delta", "delta": "架构已生成。"}
        with SessionLocal() as session:
            document = session.get(ArchitectureDocument, paper_id)
            assert document is not None
            document.scene_json = sample_scene()
            document.status = "ready"
            document.revision += 1
            session.commit()

    monkeypatch.setattr(agent_runner, "stream_codex_prompt", fake_stream)
    monkeypatch.setattr(
        agent_runner,
        "_codex_exec_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("architecture generation must not start codex exec resume")
        ),
    )

    agent_runner.run_task(task_id)

    assert observed["session_id"] == "paper-thread"
    assert observed["run_id"] == task_id
    assert agent_runner.settings.agent_task_timeout_seconds - 1 < observed["timeout_seconds"]
    assert observed["timeout_seconds"] <= agent_runner.settings.agent_task_timeout_seconds
    with SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        conversation = session.scalar(
            select(Conversation).where(
                Conversation.reference_id == paper_id,
                Conversation.status == "active",
            )
        )
        document = session.get(ArchitectureDocument, paper_id)
        assert task is not None and task.status == "completed"
        assert task.result_json["response"] == "架构已生成。"
        activities = task.result_json["architectureProgress"]["activities"]
        assert any(item["id"] == "tool-1" for item in activities)
        assert conversation is not None and conversation.codex_session_id == "paper-thread"
        assert document is not None and document.generation_task_id is None
        assert document.lock_owner_id is None


def test_architecture_progress_uses_real_tool_events_and_line_count() -> None:
    updates = architecture_progress_updates(
        {
            "type": "item.started",
            "item": {
                "id": "validate-scene",
                "type": "command_execution",
                "command": "rtk uv run frontier architecture validate --data @scene.architecture.json",
            },
        },
        scene_line_count=184,
    )
    assert updates == [
        {
            "id": "validate-scene",
            "kind": "tool",
            "label": "正在校验架构 JSON",
            "status": "running",
        },
        {
            "id": "architecture-json-lines",
            "kind": "tool",
            "label": "正在写入架构 JSON",
            "detail": "当前 184 行",
            "status": "running",
        },
    ]


def test_architecture_validation_rejects_broken_references(client: TestClient) -> None:
    invalid = sample_scene()
    invalid["edges"][0]["target"] = "missing"
    response = client.post("/api/papers/_/architecture/validate", json=invalid)
    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False
    assert "edge.endpoint_missing" in {item["code"] for item in result["diagnostics"]}


def test_architecture_validation_preserves_module_tex_symbol(client: TestClient) -> None:
    response = client.post("/api/papers/_/architecture/validate", json=sample_scene())

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is True
    assert result["scene"]["nodes"][1]["symbol"] == "g_\\psi"


def test_architecture_validation_flags_duplicate_and_reciprocal_flows(
    client: TestClient,
) -> None:
    scene = sample_scene()
    scene["edges"].extend(
        [
            {
                "id": "observation_to_encoder_process",
                "source": "observation",
                "target": "encoder",
                "label": "projection",
            },
            {
                "id": "encoder_to_observation_return",
                "source": "encoder",
                "target": "observation",
                "label": "split output + residual",
            },
        ]
    )

    response = client.post("/api/papers/_/architecture/validate", json=scene)

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is True
    codes = {item["code"] for item in result["diagnostics"]}
    assert "edge.duplicate_relation" in codes
    assert "edge.reciprocal_flow" in codes


def test_user_lock_commit_revision_and_agent_exclusion(client: TestClient) -> None:
    paper = create_architecture_paper(client, "locking")
    path = f"/api/papers/{paper['id']}/architecture"
    missing = client.get(path).json()
    assert missing["status"] == "missing"
    assert missing["revision"] == 0

    locked = client.post(f"{path}/lock", json={"ownerId": "browser-1"})
    assert locked.status_code == 200
    assert locked.json()["lock"]["ownerType"] == "user"

    generated = client.post(f"{path}/generate", json={"rebuild": True})
    assert generated.status_code == 423

    saved = client.put(
        path,
        json={
            "expectedRevision": 0,
            "ownerId": "browser-1",
            "scene": sample_scene(),
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "ready"
    assert saved.json()["revision"] == 1

    stale = client.put(
        path,
        json={
            "expectedRevision": 0,
            "ownerId": "browser-1",
            "scene": sample_scene(layout_epoch=2),
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["actualRevision"] == 1

    released = client.post(f"{path}/lock/release", json={"ownerId": "browser-1"})
    assert released.status_code == 200
    assert released.json()["lock"] is None


def test_generation_lock_blocks_user_and_cancel_releases_it(client: TestClient) -> None:
    paper = create_architecture_paper(client, "generation")
    path = f"/api/papers/{paper['id']}/architecture"
    generation = client.post(f"{path}/generate", json={"rebuild": False})
    assert generation.status_code == 200, generation.text
    payload = generation.json()
    assert payload["status"] == "generating"
    assert payload["lock"]["ownerType"] == "agent"
    assert payload["progress"]["activities"][0]["label"] == "正在启动 Agent"
    task = client.get(f"/api/tasks/{payload['taskId']}").json()
    conversation_id = task["payload_json"]["conversation_id"]
    conversations = client.get(
        f"/api/conversations?kind=paper&reference_id={paper['id']}"
    ).json()
    assert conversations[0]["id"] == conversation_id

    concurrent = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "Explain the architecture while it is being generated."},
    )
    assert concurrent.status_code == 409

    blocked = client.post(f"{path}/lock", json={"ownerId": "browser-2"})
    assert blocked.status_code == 423

    cancelled = client.post(f"/api/tasks/{payload['taskId']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    current = client.get(path).json()
    assert current["status"] == "missing"
    assert current["lock"] is None
    assert current["taskId"] is None


def test_architecture_generate_claim_is_atomic_and_single_owner(
    client: TestClient,
) -> None:
    paper = create_architecture_paper(client, "atomic-generation")
    path = f"/api/papers/{paper['id']}/architecture/generate"

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path, json={"rebuild": True}), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    with SessionLocal() as session:
        tasks = session.scalars(
            select(TaskRun).where(
                TaskRun.kind == "architecture_generation",
                TaskRun.payload_json["paper_id"].as_string() == paper["id"],
            )
        ).all()
        document = session.get_one(ArchitectureDocument, paper["id"])
        assert len(tasks) == 1
        assert document.generation_task_id == tasks[0].id
        assert document.lock_owner_id == tasks[0].id


def test_architecture_generate_rolls_back_task_and_document_on_lock_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = create_architecture_paper(client, "atomic-rollback")

    def fail_lock(*_args, **_kwargs):
        raise ArchitectureError("injected lock failure")

    monkeypatch.setattr(architectures, "acquire_architecture_lock", fail_lock)
    response = client.post(
        f"/api/papers/{paper['id']}/architecture/generate",
        json={"rebuild": True},
    )
    assert response.status_code == 400
    with SessionLocal() as session:
        assert session.get(ArchitectureDocument, paper["id"]) is None
        assert session.scalar(
            select(TaskRun).where(
                TaskRun.kind == "architecture_generation",
                TaskRun.payload_json["paper_id"].as_string() == paper["id"],
            )
        ) is None


def test_architecture_payload_heals_terminal_generation_pointer(client: TestClient) -> None:
    paper = create_architecture_paper(client, "terminal-pointer")
    with SessionLocal() as session:
        task = TaskRun(
            kind="architecture_generation",
            status="failed",
            error="worker stopped",
            payload_json={"paper_id": paper["id"]},
        )
        session.add(task)
        session.flush()
        session.add(
            ArchitectureDocument(
                paper_id=paper["id"],
                status="generating",
                generation_task_id=task.id,
                lock_owner_type="agent",
                lock_owner_id=task.id,
            )
        )
        session.commit()

    response = client.get(f"/api/papers/{paper['id']}/architecture")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["taskId"] is None
    assert response.json()["lock"] is None


def test_architecture_generate_disabled_leaves_no_queued_state(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = create_architecture_paper(client, "disabled-generation")
    monkeypatch.setattr(architectures.settings, "enable_codex_runs", False)

    response = client.post(
        f"/api/papers/{paper['id']}/architecture/generate",
        json={"rebuild": True},
    )
    assert response.status_code == 503
    with SessionLocal() as session:
        assert session.get(ArchitectureDocument, paper["id"]) is None
        assert session.scalar(
            select(TaskRun).where(
                TaskRun.kind == "architecture_generation",
                TaskRun.payload_json["paper_id"].as_string() == paper["id"],
            )
        ) is None


def test_architecture_lock_claim_has_one_owner(client: TestClient) -> None:
    paper = create_architecture_paper(client, "atomic-user-lock")
    path = f"/api/papers/{paper['id']}/architecture/lock"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda owner: client.post(path, json={"ownerId": owner}),
                ["browser-a", "browser-b"],
            )
        )

    assert sorted(response.status_code for response in responses) == [200, 423]
    current = client.get(f"/api/papers/{paper['id']}/architecture").json()
    assert current["lock"]["ownerId"] in {"browser-a", "browser-b"}


def test_cancel_only_clears_architecture_pointer_owned_by_that_task(
    client: TestClient,
) -> None:
    paper = create_architecture_paper(client, "cancel-owner")
    with SessionLocal() as session:
        cancelled_task = TaskRun(
            kind="architecture_generation",
            payload_json={"paper_id": paper["id"]},
        )
        owner_task = TaskRun(
            kind="architecture_generation",
            payload_json={"paper_id": paper["id"]},
        )
        session.add_all([cancelled_task, owner_task])
        session.flush()
        session.add(
            ArchitectureDocument(
                paper_id=paper["id"],
                status="generating",
                generation_task_id=owner_task.id,
                lock_owner_type="agent",
                lock_owner_id=owner_task.id,
            )
        )
        session.commit()
        cancelled_id = cancelled_task.id
        owner_id = owner_task.id

    response = client.post(f"/api/tasks/{cancelled_id}/cancel")
    assert response.status_code == 200
    with SessionLocal() as session:
        document = session.get_one(ArchitectureDocument, paper["id"])
        assert document.generation_task_id == owner_id
        assert document.lock_owner_id == owner_id


def test_architecture_commands_are_validated_against_current_scene(client: TestClient) -> None:
    paper = create_architecture_paper(client, "commands")
    path = f"/api/papers/{paper['id']}/architecture"
    client.post(f"{path}/lock", json={"ownerId": "browser-3"})
    saved = client.put(
        path,
        json={
            "expectedRevision": 0,
            "ownerId": "browser-3",
            "scene": sample_scene(),
        },
    )
    assert saved.status_code == 200

    valid = client.post(
        f"{path}/commands",
        json={
            "commands": [
                {"type": "focus", "target": "encoder"},
                {"type": "show-tensor", "tensorId": "visual_tokens"},
            ]
        },
    )
    assert valid.status_code == 200, valid.text
    assert len(valid.json()["architectureCommands"]) == 2

    invalid = client.post(
        f"{path}/commands",
        json={"commands": [{"type": "expand", "target": "missing"}]},
    )
    assert invalid.status_code == 422
