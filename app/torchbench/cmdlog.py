"""The transparency log: every command Torch Bench runs, with its output.

Mirrors the research repo's scripts/torch.sh:
  logs/torch_shell/history.log    one line per command  (time  [exit N]  command)
  logs/torch_shell/last.txt       the most recent command and its full output
plus logs/torch_shell/commands.jsonl with full outputs, which the activity log reads.
"""
from __future__ import annotations

import collections
import json
import os
import threading
import time
from typing import Any, Deque, Dict, List, Optional

from . import paths
from .redact import Redactor

MAX_OUTPUT_CHARS = 200_000      # per stream, per command, kept in the log
MAX_JSONL_BYTES = 20 * 1024 * 1024
MEMORY_ENTRIES = 600


def _stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n[... %d more characters not kept in the log]" % (len(text) - MAX_OUTPUT_CHARS)


class CommandLog:
    def __init__(self, redactor: Optional[Redactor] = None, persist: bool = True) -> None:
        self.redactor = redactor or Redactor()
        self.persist = persist
        self._entries: Deque[Dict[str, Any]] = collections.deque(maxlen=MEMORY_ENTRIES)
        self._lock = threading.Lock()
        self._next_id = 1

    def record(self, command: str, exit_code: Optional[int], stdout: str = "", stderr: str = "",
               seconds: float = 0.0, label: str = "", routine: bool = False, where: str = "torch",
               to_disk: bool = True, tag: str = "") -> Dict[str, Any]:
        """Record one command. where: "torch" (ran on the cluster) or "mac" (ran locally).
        tag: a word shown instead of the exit code when there is none ("no login", "started", ...)."""
        clean = self.redactor.clean
        entry = {
            "ts": time.time(),
            "time": "",
            "command": clean(command),
            "exit": exit_code,
            "stdout": _clip(clean(stdout or "")),
            "stderr": _clip(clean(stderr or "")),
            "seconds": round(seconds, 2),
            "label": label,
            "routine": bool(routine),
            "where": where,
            "tag": tag or ("no login" if exit_code is None else "exit %s" % exit_code),
        }
        entry["time"] = _stamp(entry["ts"])
        with self._lock:
            entry["id"] = self._next_id
            self._next_id += 1
            self._entries.append(entry)
            if self.persist and to_disk:
                try:
                    self._write(entry)
                except OSError:
                    pass  # a full local disk must not take the app down
        return entry

    def _write(self, entry: Dict[str, Any]) -> None:
        out = paths.shell_log_dir()
        out.mkdir(parents=True, exist_ok=True)
        tag = entry["tag"]
        one_line = " ".join(entry["command"].split())
        with open(out / "history.log", "a", encoding="utf-8") as f:
            f.write("%s  [%s]  %s\n" % (entry["time"], tag, one_line))
        body = "# %s  %s\n%s%s# exit=%s\n" % (
            entry["time"], one_line, entry["stdout"],
            ("\n" if entry["stdout"] and not entry["stdout"].endswith("\n") else "") + entry["stderr"],
            entry["exit"])
        paths.atomic_write(out / "last.txt", body)
        jsonl = out / "commands.jsonl"
        try:
            if jsonl.exists() and jsonl.stat().st_size > MAX_JSONL_BYTES:
                os.replace(jsonl, out / "commands.1.jsonl")
        except OSError:
            pass
        with open(jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self, after_id: int = 0, include_routine: bool = True, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            items = [e for e in self._entries if e["id"] > after_id and (include_routine or not e["routine"])]
        return items[-limit:]

    def last_id(self) -> int:
        with self._lock:
            return self._next_id - 1
