from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "web-build-required.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def _decision(repo: Path) -> str:
    return subprocess.check_output(["bash", str(SCRIPT), str(repo)], text=True).strip()


def test_reuse_requires_successful_build_of_unchanged_frontend(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    web = tmp_path / "web"
    web.mkdir()
    (web / "app.ts").write_text("initial frontend")
    (tmp_path / ".gitignore").write_text("web/dist/\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")
    original = _git(tmp_path, "rev-parse", "HEAD")
    assert _decision(tmp_path) == "build"

    dist = web / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("successful build")
    assert _decision(tmp_path) == "build"
    marker = dist / ".frontier-build-revision"
    marker.write_text(original + "\n")
    assert _decision(tmp_path) == "reuse"

    (tmp_path / "backend.py").write_text("backend change")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "backend")
    assert _decision(tmp_path) == "reuse"

    (web / "app.ts").write_text("changed frontend")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "frontend")
    assert _decision(tmp_path) == "build"
    # Retrying at the same HEAD after a failed build must still rebuild.
    assert _decision(tmp_path) == "build"
    marker.write_text(_git(tmp_path, "rev-parse", "HEAD") + "\n")
    assert _decision(tmp_path) == "reuse"
    (dist / "index.html").unlink()
    assert _decision(tmp_path) == "build"


def test_invalid_build_revision_is_not_reused(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    dist = tmp_path / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("old build")
    marker = dist / ".frontier-build-revision"
    for value in ["invalid", "0" * 40]:
        marker.write_text(value)
        assert _decision(tmp_path) == "build"
