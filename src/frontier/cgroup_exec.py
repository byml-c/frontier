from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    """Move this process into a prepared cgroup, then replace it with Codex."""
    try:
        separator = sys.argv.index("--")
        cgroup = Path(sys.argv[1])
        command = sys.argv[separator + 1 :]
    except (ValueError, IndexError):
        raise SystemExit("usage: python -m frontier.cgroup_exec CGROUP -- COMMAND [ARG ...]")
    if separator != 2 or not command:
        raise SystemExit("usage: python -m frontier.cgroup_exec CGROUP -- COMMAND [ARG ...]")
    (cgroup / "cgroup.procs").write_text(str(os.getpid()), encoding="utf-8")
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
