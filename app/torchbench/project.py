"""The project: one folder of code on this Mac and its copy on Torch.

Both locations are the person's choice. A project name fills in the defaults
(~/Desktop/<name> on the Mac, /scratch/<netid>/<name> on Torch), and either can be changed.
Scripts and the app read these locations from the settings; nothing has them typed in.

Creating a project makes the Mac folder if it is missing. The Torch folder is not made here:
the first push creates it, after the usual preview and confirmation.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

from . import commands, config, policy
from .ssh import SSH
from .sync import rsync_program

_EXCLUDE_RE = re.compile(r"""--exclude(?:=|\s+)(?:'([^']+)'|"([^"]+)"|([^\s\\'"]+))""")


def home_dir() -> str:
    return os.path.expanduser("~")


def default_local(name: str) -> str:
    return os.path.join(home_dir(), "Desktop", name)


def default_remote(netid: str, name: str) -> str:
    return "/scratch/%s/%s" % (netid, name)


def excludes_from_push_script(local_dir: str) -> Optional[Dict[str, Any]]:
    """If the project already has a push.sh, keep leaving out what it leaves out."""
    path = os.path.join(local_dir, "push.sh")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(200_000)
    except OSError:
        return None
    patterns: List[str] = []
    for m in _EXCLUDE_RE.finditer(text):
        pattern = next(g for g in m.groups() if g)
        if pattern not in patterns and config.PATTERN_RE.match(pattern):
            patterns.append(pattern)
    if not patterns:
        return None
    return {"push_excludes": patterns, "push_cvs_exclude": "--cvs-exclude" in text}


def check_local_dir(path: str) -> str:
    """A folder inside the person's home folder (not the home folder itself)."""
    path = os.path.normpath(os.path.expanduser(path.strip()))
    home = os.path.realpath(home_dir())
    real = os.path.realpath(path)
    if not os.path.isabs(path) or real == home or not real.startswith(home + os.sep):
        raise config.ConfigError("Choose a folder inside your home folder, such as ~/Desktop/my-project.")
    top = real[len(home):].strip(os.sep).split(os.sep)[0]
    if top in (".ssh", "Library", ".Trash", "torchbench"):
        raise config.ConfigError("That folder cannot be used for a project. Choose one such as ~/Desktop/my-project.")
    return path


def status(cfg: Dict[str, Any], ssh: Optional[SSH], ask_torch: bool = True) -> Dict[str, Any]:
    netid = cfg.get("netid", "")
    local, remote = cfg.get("local_dir", ""), cfg.get("remote_dir", "")
    out: Dict[str, Any] = {
        "configured": bool(local and remote and cfg.get("project_name")),
        "name": cfg.get("project_name", ""), "local_dir": local, "remote_dir": remote,
        "sbatch_account": cfg.get("sbatch_account", ""),
        "local_exists": bool(local) and os.path.isdir(local),
        "local_empty": bool(local) and os.path.isdir(local) and not any(n for n in os.listdir(local) if n != ".DS_Store"),
        "remote_exists": None, "connected": None,
        "can_push": policy.allowed(cfg.get("mode", "colleague"), "push"),
        "can_pull": policy.allowed(cfg.get("mode", "colleague"), "pull"),
        "push_excludes": cfg.get("push_excludes", []), "pull_paths": cfg.get("pull_paths", []),
        "pull_excludes": cfg.get("pull_excludes", []),
        "defaults": {"local_parent": os.path.join(home_dir(), "Desktop"), "remote_parent": "/scratch/%s" % netid},
        "can_choose_folder": sys.platform == "darwin",
        "rsync": os.path.exists(rsync_program()) or rsync_program() == "rsync",
    }
    if remote and ssh is not None and ask_torch:
        result = ssh.run(commands.dir_exists(remote))
        out["connected"] = not result.not_connected
        if result.ok:
            out["remote_exists"] = result.stdout.strip() == "exists"
    return out


def save(current: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and store the project's name and two locations; make the Mac folder if it is missing.
    Returns {"config": <saved>, "created_local": bool, "imported_excludes": bool}."""
    netid = current["netid"]
    name = str(body.get("project_name", "")).strip()
    if not config.NAME_RE.match(name):
        raise config.ConfigError("Give the project a short name using letters, digits, dashes, dots or underscores.")
    local = check_local_dir(str(body.get("local_dir") or default_local(name)))
    remote = str(body.get("remote_dir") or default_remote(netid, name)).strip().rstrip("/")
    raw = dict(current)
    raw.update({"project_name": name, "local_dir": local, "remote_dir": remote,
                "sbatch_account": str(body.get("sbatch_account", current.get("sbatch_account", ""))).strip()})
    imported = False
    if local != current.get("local_dir"):
        found = excludes_from_push_script(local)
        if found:
            raw.update(found)
            imported = True
    cfg = config.validate(raw)            # raises ConfigError with a plain message
    created = False
    if os.path.exists(local) and not os.path.isdir(local):
        raise config.ConfigError("%s exists but is not a folder." % local)
    if not os.path.isdir(local):
        try:
            os.makedirs(local)
            created = True
        except OSError as exc:
            raise config.ConfigError("The folder could not be created on this Mac: %s" % exc)
    config.save(cfg)
    return {"config": cfg, "created_local": created, "imported_excludes": imported}


def choose_folder(start: str = "") -> Dict[str, Any]:
    """Open the macOS folder picker. Returns {"path": ...} or {"cancelled": True}."""
    if sys.platform != "darwin":
        return {"ok": False, "error": "The folder picker is only available on a Mac. Type the folder's path instead."}
    start = start if start and os.path.isdir(start) else os.path.join(home_dir(), "Desktop")
    script = ('set startFolder to POSIX file "%s"\n'
              'tell me to activate\n'
              'POSIX path of (choose folder with prompt "Choose the project folder on this Mac" default location startFolder)'
              % start.replace("\\", "\\\\").replace('"', '\\"'))
    try:
        proc = subprocess.run(["/usr/bin/osascript", "-e", script], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=300, universal_newlines=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": "The folder picker could not be opened (%s). Type the folder's path instead." % exc}
    if proc.returncode != 0:
        return {"ok": True, "cancelled": True}
    return {"ok": True, "path": proc.stdout.strip().rstrip("/")}
