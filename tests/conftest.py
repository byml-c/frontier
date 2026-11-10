from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Generator
from importlib import import_module
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="frontier-tests-"))
os.environ["FRONTIER_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["FRONTIER_ENABLE_SCHEDULER"] = "false"
os.environ["FRONTIER_ENABLE_CODEX_RUNS"] = "false"
os.environ["FRONTIER_ACCESS_PASSWORD"] = ""
TEST_CODEX_HOME = TEST_DATA_DIR / "codex"
TEST_CODEX_HOME.mkdir()
(TEST_CODEX_HOME / "config.toml").write_text(
    'model = "test-model"\nmodel_reasoning_effort = "high"\n'
)
(TEST_CODEX_HOME / "models_cache.json").write_text(
    json.dumps(
        {
            "models": [
                {
                    "slug": "test-model",
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                    "default_reasoning_level": "high",
                }
            ]
        }
    )
)
TEST_BACKENDS_FILE = TEST_DATA_DIR / "codex-backends.json"
TEST_BACKENDS_FILE.write_text(
    json.dumps({"backends": [{"name": "codex", "codex_home": str(TEST_CODEX_HOME)}]})
)
os.environ["FRONTIER_CODEX_BACKENDS_FILE"] = str(TEST_BACKENDS_FILE)

app = import_module("frontier.main").app


@pytest.fixture(scope="session")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
