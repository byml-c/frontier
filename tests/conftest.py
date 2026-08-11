from __future__ import annotations

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

app = import_module("frontier.main").app


@pytest.fixture(scope="session")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client
