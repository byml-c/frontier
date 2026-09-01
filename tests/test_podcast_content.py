from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from frontier.config import settings


def _paper(client: TestClient) -> dict:
    suffix = uuid.uuid4().hex
    response = client.post(
        "/api/papers",
        json={
            "title": f"Podcast source {suffix}",
            "abstract": "A grounded paper abstract.",
            "authors": [{"name": "Ada Lovelace"}],
        },
    )
    assert response.status_code == 200
    return response.json()["paper"]


def _brief(client: TestClient) -> dict:
    # brief_date is unique, so use a far-future, test-specific day derived from UUID.
    day = 10 + int(uuid.uuid4().hex[:2], 16) % 18
    month = 1 + int(uuid.uuid4().hex[2:4], 16) % 12
    response = client.post(
        "/api/briefs",
        json={"brief_date": f"2098-{month:02d}-{day:02d}", "title": "今日论文播客"},
    )
    if response.status_code == 200:
        return response.json()
    # The session-scoped test database can already contain that day.
    for offset in range(1, 13):
        response = client.post(
            "/api/briefs",
            json={
                "brief_date": f"2099-{offset:02d}-01",
                "title": f"今日论文播客 {uuid.uuid4().hex[:6]}",
            },
        )
        if response.status_code == 200 and response.json()["status"] == "draft":
            return response.json()
    raise AssertionError("could not create a test brief")


def test_paper_narration_commit_is_source_bound_and_idempotent(
    client: TestClient,
) -> None:
    paper = _paper(client)
    missing = client.get(f"/api/papers/{paper['id']}/narration").json()
    assert missing["status"] == "missing"
    assert missing["revision"] == 0
    assert missing["current_source_digest"].startswith("sha256:")

    payload = {
        "expected_revision": 0,
        "status": "ready",
        "script_markdown": "# 一篇论文\n\n这是给读者看的文字稿。",
        "speech_text": "一篇论文。这是用于朗读的纯文本。",
        "sections": [{"kind": "summary", "text": "论文摘要"}],
        "citations": [{"page": 1, "claim": "论文主张"}],
        "pronunciations": [{"term": "VLA", "spoken": "V L A"}],
        "source_digest": missing["current_source_digest"],
    }
    committed = client.post(
        f"/api/papers/{paper['id']}/narration/commit", json=payload
    )
    assert committed.status_code == 200
    first = committed.json()
    assert first["revision"] == 1
    assert first["status"] == "ready"
    assert first["content_hash"].startswith("sha256:")

    retried = client.post(
        f"/api/papers/{paper['id']}/narration/commit", json=payload
    )
    assert retried.status_code == 200
    assert retried.json()["revision"] == 1

    conflicting = {**payload, "speech_text": "另一个版本。"}
    response = client.post(
        f"/api/papers/{paper['id']}/narration/commit", json=conflicting
    )
    assert response.status_code == 409
    assert response.json()["detail"]["actual_revision"] == 1

    assert client.patch(
        f"/api/papers/{paper['id']}", json={"abstract": "Source changed."}
    ).status_code == 200
    stale = client.get(f"/api/papers/{paper['id']}/narration").json()
    assert stale["stale"] is True
    assert stale["source_digest"] != stale["current_source_digest"]


def test_podcast_commit_status_and_private_audio_path(client: TestClient) -> None:
    brief = _brief(client)
    missing = client.get(f"/api/briefs/{brief['id']}/podcast").json()
    assert missing["status"] == "missing"

    commit_payload = {
        "expected_revision": 0,
        "status": "scripted",
        "title": "今日论文播客",
        "script_text": "# 今日论文播客\n\n一份适合阅读的 Markdown 稿件。",
        "speech_text": "今日论文播客。一份适合朗读的纯文本稿件。",
        "manifest": {"api_key": "must-not-leak", "sections": []},
        "source_digest": missing["current_source_digest"],
    }
    response = client.post(
        f"/api/briefs/{brief['id']}/podcast/commit", json=commit_payload
    )
    assert response.status_code == 200
    scripted = response.json()
    assert scripted["revision"] == 1
    assert scripted["manifest"]["api_key"] == "[REDACTED]"
    assert "audio_path" not in scripted
    assert scripted["audio_url"] is None

    retry = client.post(
        f"/api/briefs/{brief['id']}/podcast/commit", json=commit_payload
    )
    assert retry.status_code == 200
    assert retry.json()["revision"] == 1

    rendering = client.patch(
        f"/api/briefs/{brief['id']}/podcast/status",
        json={
            "expected_revision": 1,
            "status": "rendering",
            "provider": "aliyun",
            "model": "qwen3-tts-flash",
            "voice": "Cherry",
        },
    )
    assert rendering.status_code == 200
    assert rendering.json()["revision"] == 2

    target = settings.audio_dir / f"test-{brief['id']}.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"RIFF-test-wave")
    ready = client.patch(
        f"/api/briefs/{brief['id']}/podcast/status",
        json={
            "expected_revision": 2,
            "status": "ready",
            "audio_path": str(target),
            "audio_content_hash": "a" * 64,
            "duration_ms": 1234,
            "size_bytes": target.stat().st_size,
        },
    )
    assert ready.status_code == 200
    episode = ready.json()
    assert episode["revision"] == 3
    assert episode["audio_content_hash"] == f"sha256:{'a' * 64}"
    assert episode["audio_url"] == f"/api/podcasts/{episode['id']}/audio"
    assert "audio_path" not in episode


def test_ready_episode_rejects_audio_outside_managed_directory(
    client: TestClient, tmp_path
) -> None:
    brief = _brief(client)
    missing = client.get(f"/api/briefs/{brief['id']}/podcast").json()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"RIFF")
    response = client.post(
        f"/api/briefs/{brief['id']}/podcast/commit",
        json={
            "expected_revision": 0,
            "status": "ready",
            "title": "不安全路径",
            "script_text": "阅读稿",
            "speech_text": "朗读稿",
            "source_digest": missing["current_source_digest"],
            "audio_path": str(outside),
            "audio_content_hash": "b" * 64,
            "duration_ms": 1,
            "size_bytes": 4,
        },
    )
    assert response.status_code == 422
    assert "audio directory" in response.json()["detail"]
