"""Push and pull: the two operations where Torch Bench itself copies files.

  push   Mac -> Torch   code. Replaces files on Torch that differ; never deletes anything there.
  pull   Torch -> Mac   results and logs. Replaces local copies that differ; never deletes anything.

The habits come from the research repo's scripts (rsync over the shared login, results and logs
kept out of a push, weights kept out of a pull) plus its dry-run-then-confirm pattern:

  1. preview(): an rsync dry run. Nothing is copied. The person sees the exact list of files.
  2. start(plan): the dry run is repeated; only if it still gives the same list does the copy run.
     If anything changed in between, the new preview is shown instead and nothing is copied.

Locations come from the settings file, never from this code. This module is the only place that
builds an rsync command; rsync talks over `ssh -o BatchMode=yes -o ControlMaster=no`, so it can
only ride the open sign-in and can never prompt. Every run is recorded in the activity log.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

from . import policy
from .cmdlog import CommandLog
from .config import PULL_PATH_RE
from .ssh import SSH, ssh_program

PREVIEW_TIMEOUT = 300
PLAN_LIFETIME = 15 * 60
MAX_LISTED = 2000
MAX_OUTPUT = 400_000

_HEADER_RE = re.compile(r"^(sending|receiving) (incremental )?file list|^building file list|^Transfer starting", re.I)
_SKIP_RE = re.compile(r"^(created directory |\./$|sent \d|total size |Number of |Total |Literal |Matched |File list )")
_STAT_FILES_RE = re.compile(r"Number of (?:regular )?files transferred:\s*([\d,]+)")
_STAT_BYTES_RE = re.compile(r"Total transferred file size:\s*([\d,]+)")
_MISSING_RE = re.compile(r"No such file or directory|link_stat .* failed", re.I)
_BAD_OPTION_RE = re.compile(r"unrecognized option|unknown option|invalid option", re.I)


def rsync_program() -> str:
    override = os.environ.get("TORCHBENCH_RSYNC")
    if override:
        return override
    return "/usr/bin/rsync" if os.path.exists("/usr/bin/rsync") else "rsync"


def parse_file_list(output: str) -> List[str]:
    """The files an `rsync -v` run lists (folders left out)."""
    files: List[str] = []
    started = False
    for line in output.splitlines():
        if _HEADER_RE.search(line):
            started = True
            continue
        if not line.strip():
            if started:
                break
            continue
        if _SKIP_RE.search(line):
            continue
        started = True
        name = line.rstrip()
        if name.startswith("deleting "):
            continue
        if not name.endswith("/"):
            files.append(name)
    return files


def parse_stats(output: str) -> Dict[str, Optional[int]]:
    files = _STAT_FILES_RE.search(output)
    size = _STAT_BYTES_RE.search(output)
    return {"files": int(files.group(1).replace(",", "")) if files else None,
            "bytes": int(size.group(1).replace(",", "")) if size else None}


class Sync:
    def __init__(self, cfg: Dict[str, Any], ssh: SSH, log: CommandLog) -> None:
        self.cfg, self.ssh, self.log = cfg, ssh, log
        self._lock = threading.Lock()
        self._plan: Optional[Dict[str, Any]] = None
        self._job: Optional[Dict[str, Any]] = None
        self._proc: Optional[subprocess.Popen] = None
        self._stats_ok = True          # some rsync builds lack --stats; found out on first use

    # -- building the command -------------------------------------------------------------------
    def _transport(self) -> str:
        return " ".join(ssh_program() + ["-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ConnectTimeout=10"])

    def _argv(self, direction: str, rel: str, dry_run: bool) -> List[str]:
        cfg = self.cfg
        argv = [rsync_program(), "-avz"]
        if dry_run:
            argv.append("-n")
        if self._stats_ok:
            argv.append("--stats")
        remote = "%s:%s" % (cfg["ssh_alias"], cfg["remote_dir"])
        if direction == "push":
            if cfg.get("push_cvs_exclude"):
                argv.append("--cvs-exclude")
            argv += ["--exclude=%s" % pattern for pattern in cfg["push_excludes"]]
            argv += ["-e", self._transport(), cfg["local_dir"].rstrip("/") + "/", remote + "/"]
        else:
            argv += ["--exclude=%s" % pattern for pattern in cfg["pull_excludes"]]
            argv += ["-e", self._transport(), "%s/%s/" % (remote, rel), os.path.join(cfg["local_dir"], rel) + "/"]
        return argv

    @staticmethod
    def _shown(argv: List[str]) -> str:
        return " ".join(a if re.match(r"^[A-Za-z0-9_@%+=:,./-]+$", a) else "'%s'" % a.replace("'", "'\\''") for a in argv)

    # -- checks shared by preview and start -----------------------------------------------------
    def _refusal(self, direction: str, rel: str) -> Optional[Dict[str, Any]]:
        cfg = self.cfg
        if direction not in ("push", "pull"):
            return {"ok": False, "error": "Unknown direction."}
        if not cfg.get("local_dir") or not cfg.get("remote_dir"):
            return {"ok": False, "no_project": True, "error": "Set up the project's two folders first."}
        if not policy.allowed(cfg["mode"], direction):
            return {"ok": False, "not_allowed": True,
                    "error": "Pushing code is for the person who runs the project. If that is you, change your role "
                             "under Help, Change my details."}
        if direction == "push":
            if rel:
                return {"ok": False, "error": "A push always sends the whole project folder."}
            if not os.path.isdir(cfg["local_dir"]):
                return {"ok": False, "error": "The project folder on this Mac does not exist: %s" % cfg["local_dir"]}
        elif rel and (not PULL_PATH_RE.match(rel) or ".." in rel.split("/")):
            return {"ok": False, "error": "That folder's name has characters the copy cannot handle safely. "
                                          "Rename it, or copy it from the Terminal."}
        if not self.ssh.check():
            return {"ok": False, "not_connected": True, "error": "Sign in to Torch first."}
        return None

    def _steps(self, direction: str, rel: str) -> List[str]:
        if direction == "push":
            return [""]
        return [rel] if rel else list(self.cfg["pull_paths"])

    def _dry_run(self, direction: str, rel: str) -> Dict[str, Any]:
        """One rsync dry run. Returns files, stats, raw output and whether the source was missing."""
        for _attempt in (1, 2):
            argv = self._argv(direction, rel, dry_run=True)
            start = time.time()
            try:
                proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      timeout=PREVIEW_TIMEOUT, universal_newlines=True, errors="replace")
                code, out, err = proc.returncode, proc.stdout, proc.stderr
            except subprocess.TimeoutExpired:
                code, out, err = 124, "", "The preview took longer than %d s and was stopped." % PREVIEW_TIMEOUT
            except OSError as exc:
                code, out, err = 127, "", "rsync could not be started on this Mac (%s)." % exc
            if code != 0 and self._stats_ok and _BAD_OPTION_RE.search(err):
                self._stats_ok = False           # an rsync without --stats: ask again without it
                continue
            break
        what = "Preview: what a %s would copy%s (nothing is copied)" % (direction, (" from " + rel) if rel else "")
        self.log.record(self._shown(argv), code, out, err, time.time() - start, label=what, routine=False, where="mac")
        missing = code != 0 and bool(_MISSING_RE.search(err)) and direction == "pull"
        return {"code": code, "files": parse_file_list(out) if code == 0 else [], "stats": parse_stats(out),
                "out": out, "err": err, "missing": missing, "command": self._shown(argv)}

    # -- step 1: preview ------------------------------------------------------------------------
    def preview(self, direction: str, rel: str = "") -> Dict[str, Any]:
        if rel.strip().startswith("/"):
            return {"ok": False, "error": "Name a folder inside the project, such as results/run1."}
        rel = rel.strip().rstrip("/")
        refused = self._refusal(direction, rel)
        if refused:
            return refused
        with self._lock:
            if self._job and not self._job["done"]:
                return {"ok": False, "error": "A copy is already running. Wait for it to finish."}
        files: List[str] = []
        total_bytes: Optional[int] = 0
        steps, skipped, commands, raw = [], [], [], []
        for step in self._steps(direction, rel):
            got = self._dry_run(direction, step)
            commands.append(got["command"])
            raw.append(got["out"] + got["err"])
            if got["missing"]:
                skipped.append(step)
                continue
            if got["code"] != 0:
                return {"ok": False, "error": (got["err"].strip() or "The preview failed.")[-600:], "raw": "\n".join(raw)[-8000:]}
            steps.append(step)
            files += [(step + "/" + f) if step else f for f in got["files"]]
            total_bytes = None if total_bytes is None or got["stats"]["bytes"] is None else total_bytes + got["stats"]["bytes"]
        signature = hashlib.sha256("\n".join(sorted(files)).encode("utf-8")).hexdigest()
        plan = {"id": signature[:16] + "%x" % int(time.time()), "direction": direction, "rel": rel, "steps": steps,
                "signature": signature, "made": time.time(), "count": len(files)}
        with self._lock:
            self._plan = plan
        cfg = self.cfg
        return {
            "ok": True, "plan": plan["id"], "direction": direction, "rel": rel, "count": len(files),
            "files": files[:MAX_LISTED], "more": max(0, len(files) - MAX_LISTED), "bytes": total_bytes,
            "from": cfg["local_dir"] if direction == "push" else "%s:%s" % ("Torch", cfg["remote_dir"]),
            "to": ("Torch:%s" % cfg["remote_dir"]) if direction == "push" else cfg["local_dir"],
            "left_out": cfg["push_excludes"] if direction == "push" else cfg["pull_excludes"],
            "not_there": skipped, "commands": commands,
        }

    # -- step 2: copy, only if the preview still holds -------------------------------------------
    def start(self, plan_id: str) -> Dict[str, Any]:
        with self._lock:
            plan = self._plan
            if self._job and not self._job["done"]:
                return {"ok": False, "error": "A copy is already running."}
        if not plan or plan["id"] != plan_id or time.time() - plan["made"] > PLAN_LIFETIME:
            return {"ok": False, "stale": True, "error": "That preview is out of date. Look at a fresh preview first."}
        refused = self._refusal(plan["direction"], plan["rel"])
        if refused:
            return refused
        again = self.preview(plan["direction"], plan["rel"])
        if not again.get("ok"):
            return again
        with self._lock:
            fresh = self._plan
        if not fresh or fresh["signature"] != plan["signature"]:
            again["changed"] = True          # files changed since the preview: show the new list, copy nothing
            return again
        if not fresh["count"]:
            return {"ok": True, "nothing": True}
        job = {"direction": fresh["direction"], "rel": fresh["rel"], "steps": fresh["steps"], "count": fresh["count"],
               "started": time.time(), "output": "", "done": False, "exit": None, "cancelled": False}
        with self._lock:
            self._job, self._plan = job, None
        threading.Thread(target=self._run, args=(job,), name="torchbench-sync", daemon=True).start()
        return {"ok": True, "started": True}

    def _run(self, job: Dict[str, Any]) -> None:
        code = 0
        for step in job["steps"]:
            if job["cancelled"]:
                break
            if job["direction"] == "pull":
                try:
                    os.makedirs(os.path.join(self.cfg["local_dir"], step), exist_ok=True)
                except OSError as exc:
                    job["output"] += "Could not create the folder on this Mac: %s\n" % exc
                    code = 1
                    break
            argv = self._argv(job["direction"], step, dry_run=False)
            start, collected = time.time(), []
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        universal_newlines=True, errors="replace")
                self._proc = proc
                assert proc.stdout is not None
                for line in proc.stdout:
                    collected.append(line)
                    job["output"] = (job["output"] + line)[-MAX_OUTPUT:]
                step_code = proc.wait()
            except OSError as exc:
                step_code = 127
                collected.append("rsync could not be started on this Mac (%s).\n" % exc)
                job["output"] += collected[-1]
            finally:
                self._proc = None
            label = ("Push code to Torch" if job["direction"] == "push"
                     else "Pull %s from Torch to this Mac" % (step or "the project"))
            self.log.record(self._shown(argv), step_code, "".join(collected), "", time.time() - start,
                            label=label, routine=False, where="mac")
            if step_code != 0:
                code = step_code
                break
        job["exit"] = code if not job["cancelled"] else 130
        job["seconds"] = round(time.time() - job["started"], 1)
        job["done"] = True

    def job_state(self) -> Dict[str, Any]:
        with self._lock:
            job = self._job
        if not job:
            return {"active": False}
        return {"active": True, "direction": job["direction"], "rel": job["rel"], "count": job["count"],
                "done": job["done"], "exit": job["exit"], "cancelled": job["cancelled"],
                "output": job["output"][-20000:], "seconds": job.get("seconds", round(time.time() - job["started"], 1))}

    def cancel(self) -> None:
        with self._lock:
            job = self._job
        if job and not job["done"]:
            job["cancelled"] = True
            proc = self._proc
            if proc is not None:
                try:
                    proc.terminate()
                except OSError:
                    pass
