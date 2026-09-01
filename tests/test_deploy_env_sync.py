from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = PROJECT_ROOT / "deploy" / "sync-env-defaults.sh"


def _run_sync(env_file: Path, example_file: Path, keys_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SYNC_SCRIPT), str(env_file), str(example_file), str(keys_file)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_sync_env_adds_only_missing_managed_defaults_and_is_idempotent(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=keep-me\nLIMIT=custom\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    example_file = tmp_path / ".env.example"
    example_file.write_text(
        "LIMIT=default\nNEW_LIMIT=42\nUNMANAGED=not-copied\n",
        encoding="utf-8",
    )
    keys_file = tmp_path / "managed.keys"
    keys_file.write_text("LIMIT\nNEW_LIMIT\n", encoding="utf-8")

    first = _run_sync(env_file, example_file, keys_file)

    assert first.returncode == 0, first.stderr
    content = env_file.read_text(encoding="utf-8")
    assert "SECRET=keep-me" in content
    assert "LIMIT=custom" in content
    assert "LIMIT=default" not in content
    assert content.count("NEW_LIMIT=42") == 1
    assert "UNMANAGED" not in content
    assert env_file.stat().st_mode & 0o777 == 0o600

    second = _run_sync(env_file, example_file, keys_file)

    assert second.returncode == 0, second.stderr
    assert "无需修改" in second.stdout
    assert env_file.read_text(encoding="utf-8") == content


def test_sync_env_fails_atomically_when_a_managed_default_is_missing(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    original = "EXISTING=value\n"
    env_file.write_text(original, encoding="utf-8")
    example_file = tmp_path / ".env.example"
    example_file.write_text("FIRST=1\n", encoding="utf-8")
    keys_file = tmp_path / "managed.keys"
    keys_file.write_text("FIRST\nMISSING\n", encoding="utf-8")

    result = _run_sync(env_file, example_file, keys_file)

    assert result.returncode == 1
    assert "MISSING" in result.stderr
    assert env_file.read_text(encoding="utf-8") == original


def test_deployment_profile_is_fully_defined_by_env_example(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.touch()

    result = _run_sync(
        env_file,
        PROJECT_ROOT / ".env.example",
        PROJECT_ROOT / "deploy" / "frontier-2c2g-env.keys",
    )

    assert result.returncode == 0, result.stderr
    managed_keys = [
        line.strip()
        for line in (PROJECT_ROOT / "deploy" / "frontier-2c2g-env.keys")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert f"已补充 {len(managed_keys)} 个缺失配置" in result.stdout
    synced = env_file.read_text(encoding="utf-8")
    assert all(f"{key}=" in synced for key in managed_keys)
