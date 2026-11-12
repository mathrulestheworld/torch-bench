"""Where Torch Bench keeps its files. All state lives under ~/torchbench (override: TORCHBENCH_HOME)."""
from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    override = os.environ.get("TORCHBENCH_HOME")
    return Path(override).expanduser() if override else Path.home() / "torchbench"


def config_file() -> Path:
    return home() / "config.json"


def logs_dir() -> Path:
    return home() / "logs"


def shell_log_dir() -> Path:
    # Same name as the research repo's logs/torch_shell/, on purpose.
    return logs_dir() / "torch_shell"


def cache_dir() -> Path:
    return home() / "cache"


def run_dir() -> Path:
    return home() / "run"


def ssh_config_file() -> Path:
    override = os.environ.get("TORCHBENCH_SSH_CONFIG")
    return Path(override).expanduser() if override else Path.home() / ".ssh" / "config"


def static_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "static"


def ensure_dirs() -> None:
    for d in (home(), logs_dir(), shell_log_dir(), cache_dir(), run_dir()):
        d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(home(), 0o700)
    except OSError:
        pass


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    """Write a file in one step, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp%d" % os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)
