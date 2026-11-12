"""The ~/.ssh/config entry that lets one sign-in be shared by every later command.

Torch Bench adds one clearly marked block and never edits anything outside it. If a working
`Host torch` entry already exists (the owner's Mac), it is left exactly as it is.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any, Dict, List

from . import paths
from .ssh import ssh_program

BEGIN = "# >>> torchbench >>> (added by Torch Bench; remove this block to undo)"
END = "# <<< torchbench <<<"


def block(netid: str, alias: str, host: str) -> str:
    return "\n".join([
        BEGIN,
        "Host %s" % alias,
        "  HostName %s" % host,
        "  User %s" % netid,
        "  # One sign-in, shared by every later command:",
        "  ControlMaster auto",
        "  ControlPath ~/.ssh/torchbench-%C",
        "  ControlPersist 12h",
        "  ServerAliveInterval 60",
        "  ServerAliveCountMax 3",
        "  # NYU's recommended settings: the login name points at several machines with different keys.",
        "  StrictHostKeyChecking no",
        "  UserKnownHostsFile /dev/null",
        "  LogLevel ERROR",
        END,
        "",
    ])


def effective(alias: str) -> Dict[str, str]:
    """What ssh itself would use for this alias (`ssh -G`). Reads config only; connects to nothing."""
    try:
        proc = subprocess.run(ssh_program() + ["-G", alias], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=8, universal_newlines=True, errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        return {}
    out: Dict[str, str] = {}
    if proc.returncode != 0:
        return out
    for line in proc.stdout.splitlines():
        key, _, value = line.strip().partition(" ")
        key = key.lower()
        if key and key not in out:
            out[key] = value.strip()
    return out


def _read() -> str:
    try:
        return paths.ssh_config_file().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _has_block(text: str) -> bool:
    return BEGIN.split(" (")[0] in text and END in text


def _other_entry_for(alias: str, text: str) -> bool:
    """Is there a Host line naming this alias outside our block?"""
    inside = False
    for line in text.splitlines():
        if line.startswith("# >>> torchbench >>>"):
            inside = True
        elif line.startswith(END):
            inside = False
        elif not inside:
            m = re.match(r"^\s*Host\s+(.+)$", line, re.I)
            if m and alias in m.group(1).split():
                return True
    return False


def status(cfg: Dict[str, Any]) -> Dict[str, Any]:
    alias, host, netid = cfg["ssh_alias"], cfg["remote_host"], cfg["netid"]
    text = _read()
    eff = effective(alias)
    problems: List[str] = []
    if eff.get("hostname", "").lower() != host.lower():
        problems.append("`ssh %s` does not point at %s yet." % (alias, host))
    else:
        if eff.get("user", "") != netid:
            problems.append("`ssh %s` signs in as %s, not %s." % (alias, eff.get("user") or "your Mac user", netid))
        if eff.get("controlmaster", "false").lower() not in ("auto", "true", "yes", "autoask"):
            problems.append("Connection sharing (ControlMaster) is not switched on for `%s`." % alias)
        if eff.get("controlpath", "none").lower() == "none":
            problems.append("No ControlPath is set for `%s`." % alias)
    has_block = _has_block(text)
    other = _other_entry_for(alias, text)
    return {
        "ok": not problems,
        "problems": problems,
        "has_block": has_block,
        "own_entry": other and not has_block,       # the user's own entry; Torch Bench will not touch it
        "can_write": bool(problems) and not (other and not has_block),
        "file": str(paths.ssh_config_file()),
        "block": block(netid, alias, host),
    }


def install(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Add (or refresh) the marked block. Backs the file up first; never edits outside the block."""
    st = status(cfg)
    if st["ok"]:
        return st
    if not st["can_write"]:
        raise RuntimeError(
            "Your SSH settings already have their own `Host %s` entry, so Torch Bench will not change it. "
            "Add the ControlMaster, ControlPath and ControlPersist lines shown here to that entry." % cfg["ssh_alias"])
    path = paths.ssh_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    text = _read()
    if text:
        backup = path.with_name(path.name + ".torchbench-backup-" + time.strftime("%Y%m%d-%H%M%S"))
        paths.atomic_write(backup, text)
    new_block = block(cfg["netid"], cfg["ssh_alias"], cfg["remote_host"])
    if _has_block(text):
        pattern = re.compile(r"# >>> torchbench >>>.*?# <<< torchbench <<<\n?", re.S)
        text = pattern.sub(lambda _m: new_block, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text = text + ("\n" if text else "") + new_block
    paths.atomic_write(path, text, mode=0o600)
    return status(cfg)
