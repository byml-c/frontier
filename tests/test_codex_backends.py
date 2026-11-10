from __future__ import annotations

import json

import pytest

from frontier.models import Conversation, TaskRun
from frontier.services import codex_backends as routes


def configure(tmp_path, monkeypatch):
    for name in ("codex", "deepseek"):
        home = tmp_path / name
        home.mkdir()
        (home / "config.toml").write_text(
            'model = "shared-model"\nmodel_reasoning_effort = "high"\n'
            'model_provider = "private-provider"\n'
            + ('model_catalog_json = "models.json"\n' if name == "deepseek" else "")
        )
        (home / ("models.json" if name == "deepseek" else "models_cache.json")).write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "shared-model",
                            "display_name": "Shared model",
                            "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                            "default_reasoning_level": "low",
                        },
                        {"slug": "secret-hidden", "visibility": "hide"},
                    ]
                }
            )
        )
    path = tmp_path / "backends.json"
    path.write_text(
        json.dumps(
            {
                "default_backend": "codex",
                "backends": [
                    {"name": "codex", "codex_home": "codex"},
                    {"name": "deepseek", "codex_home": "deepseek"},
                ],
            }
        )
    )
    monkeypatch.setattr(routes.settings, "codex_backends_file", path)
    monkeypatch.setattr(routes.settings, "codex_profile", None)
    return path


def test_catalog_reads_both_homes_without_exposing_paths_or_hidden_models(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    catalog = routes.backend_catalog()
    assert [row["name"] for row in catalog["backends"]] == ["codex", "deepseek"]
    assert all(row["available"] for row in catalog["backends"])
    assert str(tmp_path) not in json.dumps(catalog)
    assert "secret-hidden" not in json.dumps(catalog)
    assert catalog["backends"][1]["models"][0]["default_reasoning_effort"] == "high"
    backend, selected = routes.resolve_selection(
        {"backend": "deepseek", "model": "shared-model", "reasoning_effort": "low"}
    )
    assert backend.home == tmp_path / "deepseek"
    assert selected == {"backend": "deepseek", "model": "shared-model", "reasoning_effort": "low"}


@pytest.mark.parametrize(
    "selection",
    [
        {"backend": "unregistered"},
        {"model": "unlisted"},
        {"reasoning_effort": "ultra"},
    ],
)
def test_selection_rejects_unconfigured_values(tmp_path, monkeypatch, selection):
    configure(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        routes.resolve_selection(selection)


def test_missing_backend_config_is_unavailable(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    (tmp_path / "deepseek" / "config.toml").unlink()
    assert routes.backend_catalog()["backends"][1]["available"] is False
    assert routes.resolve_selection()[1]["backend"] == "codex"


def test_catalog_without_cache_only_offers_configured_model(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    (tmp_path / "codex" / "models_cache.json").unlink()
    assert routes.backend_catalog()["backends"][0]["models"] == [
        {
            "id": "shared-model",
            "name": "shared-model",
            "reasoning_efforts": ["high"],
            "default_reasoning_effort": "high",
        }
    ]


def test_duplicate_backend_names_rejected(tmp_path, monkeypatch):
    path = configure(tmp_path, monkeypatch)
    path.write_text('{"backends":[{"name":"x","codex_home":"a"},{"name":"x","codex_home":"b"}]}')
    with pytest.raises(ValueError, match="Invalid"):
        routes.configured_backends()


def test_home_tilde_expands_and_missing_file_uses_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "routes.json"
    path.write_text('{"backends":[{"name":"codex","codex_home":"~/.codex"}]}')
    monkeypatch.setattr(routes.settings, "codex_backends_file", path)
    assert routes.configured_backends()[1]["codex"].home == tmp_path / ".codex"
    path.unlink()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom"))
    assert routes.configured_backends()[1]["codex"].home == tmp_path / "custom"


def test_switching_backend_starts_new_thread_and_retains_selection(client, tmp_path, monkeypatch):
    from frontier.database import SessionLocal

    configure(tmp_path, monkeypatch)
    with SessionLocal() as session:
        conversation = Conversation(kind="global", codex_session_id="private-thread")
        session.add(conversation)
        session.flush()
        session.add(
            TaskRun(
                kind="global_chat",
                status="completed",
                payload_json={
                    "conversation_id": conversation.id,
                    "codex": {
                        "backend": "codex",
                        "model": "shared-model",
                        "reasoning_effort": "high",
                    },
                },
            )
        )
        session.commit()
        assert (
            routes.prepare_conversation_selection(session, conversation, None)["backend"] == "codex"
        )
        assert conversation.codex_session_id == "private-thread"
        selected = routes.prepare_conversation_selection(
            session, conversation, {"backend": "deepseek"}
        )
        assert selected["backend"] == "deepseek"
        assert conversation.codex_session_id is None


def test_models_api_lists_catalog_and_rejects_home_in_message(client, tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    response = client.get("/api/conversations/models")
    assert response.status_code == 200
    assert response.json()["backends"][1]["name"] == "deepseek"
    conversation = client.post("/api/conversations", json={"kind": "global"}).json()
    monkeypatch.setattr(routes.settings, "enable_codex_runs", True)
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={
            "content": "Hello",
            "codex": {"backend": "codex", "model": "shared-model", "codex_home": "/arbitrary-home"},
        },
    )
    assert response.status_code == 422
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={
            "content": "Hello",
            "codex": {"backend": "codex", "model": "unlisted"},
        },
    )
    assert response.status_code == 422
    assert client.get(f"/api/conversations/{conversation['id']}").json()["messages"] == []
