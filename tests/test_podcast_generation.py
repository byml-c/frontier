from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from frontier import agent_runner
from frontier.api import briefs as briefs_api
from frontier.database import SessionLocal
from frontier.models import PaperNarration, PodcastEpisode, TaskRun
from frontier.services import podcast_generation
from frontier.services.podcast_audio import AudioRenderResult


def _published_brief(client: TestClient) -> tuple[dict, dict]:
    suffix = uuid.uuid4().hex
    paper_response = client.post(
        "/api/papers",
        json={
            "title": f"Grounded Podcast Paper {suffix}",
            "abstract": "这项研究建立了一个可验证的机器人学习基准。",
            "authors": [{"name": "Ada Lovelace"}],
        },
    )
    assert paper_response.status_code == 200
    paper = paper_response.json()["paper"]
    assert client.patch(
        f"/api/papers/{paper['id']}",
        json={
            "summary": "论文研究机器人策略的可靠评估。",
            "problem": "现有评估难以复现。",
            "innovation": "作者提出了固定数据和指标的评测协议。",
            "experiment": "实验比较了三类策略并报告消融结果。",
            "limitations": "目前只覆盖仿真环境。",
            "recommendation_reason": "方法与实验都可复核。",
        },
    ).status_code == 200
    for year in range(1970, 1988):
        response = client.post(
            "/api/briefs",
            json={
                "brief_date": f"{year}-12-31",
                "title": "每日论文播客",
                "introduction": "今天关注可靠的机器人学习评估。",
            },
        )
        if response.status_code == 200 and response.json()["status"] == "draft":
            brief = response.json()
            break
    else:
        raise AssertionError("could not create a draft brief")
    assert client.post(
        f"/api/briefs/{brief['id']}/items",
        json={"paper_id": paper["id"], "rank": 1, "tier": "related"},
    ).status_code == 200
    published = client.post(f"/api/briefs/{brief['id']}/publish")
    assert published.status_code == 200
    return published.json(), paper


def test_generate_is_idempotent_and_direct_runner_renders_audio(
    client: TestClient, monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(podcast_generation.settings, "aliyun_tts_api_key", None)
    brief, paper = _published_brief(client)
    submitted: list[str] = []
    monkeypatch.setattr(briefs_api, "submit_task", submitted.append)

    first = client.post(f"/api/briefs/{brief['id']}/podcast/generate")
    second = client.post(f"/api/briefs/{brief['id']}/podcast/generate")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"]
    assert submitted == [first.json()["task_id"], first.json()["task_id"]]
    queued = client.get(f"/api/briefs/{brief['id']}/podcast").json()
    assert queued["status"] == "generating"

    def fake_render(script_text, target_path):  # type: ignore[no-untyped-def]
        assert "可靠评估" in script_text
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"RIFF-test-wave")
        return AudioRenderResult(
            path=target_path,
            content_hash="a" * 64,
            size_bytes=14,
            duration_seconds=12.5,
            provider="aliyun",
            model="qwen3-tts-instruct-flash",
            voice="Cherry",
            manifest={"chunks": [{"characters": len(script_text)}]},
        )

    monkeypatch.setattr(podcast_generation, "render_podcast_audio", fake_render)
    monkeypatch.setattr(
        agent_runner,
        "_run_codex_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("podcast rendering must not invoke Codex")
        ),
    )
    agent_runner.run_task(first.json()["task_id"])

    ready = client.get(f"/api/briefs/{brief['id']}/podcast").json()
    assert ready["status"] == "ready"
    assert ready["duration_ms"] == 12_500
    assert "## 第 1 篇" in ready["script_text"]
    assert ready["audio_url"].endswith("/audio")
    narration = client.get(f"/api/papers/{paper['id']}/narration").json()
    assert narration["status"] == "ready"
    assert "核心创新" in narration["script_markdown"]
    assert client.get(ready["audio_url"]).content == b"RIFF-test-wave"


def test_render_failure_is_visible_and_retry_creates_a_new_task(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(podcast_generation.settings, "aliyun_tts_api_key", None)
    brief, _paper = _published_brief(client)
    monkeypatch.setattr(briefs_api, "submit_task", lambda _task_id: None)
    queued = client.post(f"/api/briefs/{brief['id']}/podcast/generate").json()
    monkeypatch.setattr(
        podcast_generation,
        "render_podcast_audio",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            podcast_generation.SpeechProviderError("测试中的安全失败")
        ),
    )

    agent_runner.run_task(queued["task_id"])

    failed = client.get(f"/api/briefs/{brief['id']}/podcast").json()
    assert failed["status"] == "failed"
    assert failed["error"] == "测试中的安全失败"
    retried = client.post(f"/api/briefs/{brief['id']}/podcast/generate").json()
    assert retried["task_id"] != queued["task_id"]
    assert retried["status"] == "queued"
    with SessionLocal() as session:
        old_task = session.get(TaskRun, queued["task_id"])
        episode = session.get(PodcastEpisode, failed["id"])
        narration = session.query(PaperNarration).filter_by(
            paper_id=_paper["id"]
        ).one()
        assert old_task is not None and old_task.status == "failed"
        assert episode is not None and episode.generation_task_id == retried["task_id"]
        assert narration.status == "ready"


def test_generate_rejects_draft_brief(client: TestClient) -> None:
    response = client.post(
        "/api/briefs",
        json={"brief_date": "1969-12-31", "title": "尚未发布"},
    )
    assert response.status_code == 200

    generated = client.post(
        f"/api/briefs/{response.json()['id']}/podcast/generate"
    )

    assert generated.status_code == 409
    assert generated.json()["detail"] == "只能为已发布的简报生成播客"
