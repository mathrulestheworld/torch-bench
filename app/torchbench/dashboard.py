"""Collect what the dashboard shows, politely.

Each source has its own freshness window, so the login node is asked only what is stale:
the queue about every 30 s, slower-moving things every few minutes, the quota every 10.
Nothing is polled unless a Torch Bench window is open and asking.
"""
from __future__ import annotations

import datetime
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from . import commands, config, policy, reasons, slurm
from .ssh import SSH, Result
from .waits import WaitHistory

FRESH_FOR = {"queue": 25, "history": 120, "fairshare": 300, "priority": 120, "partitions": 120, "quota": 600}
FORCE_GAP_SECONDS = 5
CHECK_EVERY_SECONDS = 10
MAX_TASKS_SHOWN = 200

GOOD_TO_KNOW = [
    {"title": "Keep GPUs busy", "text": "NYU cancels GPU jobs whose average GPU use is under 60%."},
    {"title": "No heavy work on the login node", "text": "Torch Bench only asks the login node quick questions "
                                                         "(queue, status, files). Real work belongs in jobs."},
    {"title": "/scratch is not backed up", "text": "5 TB and 5 million files per person; files untouched for about "
                                                   "60 days are deleted. Keep what matters in /archive (2 TB, backed up) "
                                                   "or on your Mac."},
    {"title": "/home is small", "text": "50 GB. Environments and results belong in /scratch."},
]


def _plural(n: int, one: str, many: str) -> str:
    return "%d %s" % (n, one if n == 1 else many)


def _clock(ts: Optional[float]) -> str:
    if not ts:
        return ""
    d = datetime.datetime.fromtimestamp(ts)
    today = datetime.date.today()
    if d.date() == today:
        return d.strftime("today %H:%M")
    if d.date() == today + datetime.timedelta(days=1):
        return d.strftime("tomorrow %H:%M")
    return d.strftime("%a %d %b %H:%M")


class Dashboard:
    def __init__(self, cfg: Dict[str, Any], ssh: SSH, waits: Optional[WaitHistory] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.cfg, self.ssh, self.waits, self.clock = cfg, ssh, waits or WaitHistory(), clock
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {}
        self._at: Dict[str, float] = {}
        self._errors: Dict[str, str] = {}
        self._refreshing = False
        self._last_force = 0.0
        self._reload_all = False     # after a new sign-in: re-read everything once, but keep showing data ages
        self.connected: Optional[bool] = None
        self.checked_at = 0.0
        self.node = ""

    # -- refreshing -----------------------------------------------------------------------------
    def _stale(self, source: str, now: float) -> bool:
        return self._reload_all or now - self._at.get(source, 0.0) >= FRESH_FOR[source]

    def kick(self, force: bool = False, wait: bool = False) -> None:
        """Bring things up to date if anything is stale. Called whenever the window asks for the dashboard.

        The connection check is local and quick, so it happens here and the answer the window gets is
        current. Anything that needs Torch runs in the background, one refresh at a time."""
        with self._lock:
            now = self.clock()
            if force and now - self._last_force < FORCE_GAP_SECONDS:
                force = False
            if force:
                self._last_force = now
            if self._refreshing:
                return
            recheck = force or self.connected is None or now - self.checked_at >= CHECK_EVERY_SECONDS
            waiting = any(j["kind"] == "waiting" for j in self._data.get("queue", {}).get("jobs", []))
            stale = force or any(self._stale(s, now) for s in FRESH_FOR if s != "priority") \
                or (waiting and self._stale("priority", now))
            if not recheck and not (stale and self.connected):
                return
            self._refreshing = True
        try:
            if recheck:
                connected = self.ssh.check()
                with self._lock:
                    self.connected, self.checked_at = connected, self.clock()
        except Exception:
            with self._lock:
                self._refreshing = False
            raise
        if not (self.connected and stale):
            with self._lock:
                self._refreshing = False
            return
        thread = threading.Thread(target=self._refresh, args=(force,), name="torchbench-refresh", daemon=True)
        thread.start()
        if wait:
            thread.join()

    def _run(self, source: str, command: commands.RemoteCommand, parse: Callable[[str], Any]) -> bool:
        """Run one source. Returns False when the login has gone, so the refresh stops asking."""
        result: Result = self.ssh.run(command)
        if result.not_connected:
            return False
        with self._lock:
            self._at[source] = self.clock()
            if result.ok:
                try:
                    self._data[source] = parse(result.stdout)
                    self._errors.pop(source, None)
                except Exception as exc:  # a parser must never take the dashboard down
                    self._errors[source] = "Could not read Torch's answer (%s)." % exc.__class__.__name__
            else:
                self._errors[source] = result.problem()
        return True

    def _refresh(self, force: bool) -> None:
        try:
            cfg, now = self.cfg, self.clock()
            force = force or self._reload_all
            since = (datetime.date.today() - datetime.timedelta(days=cfg["history_days"])).isoformat()
            plan = [
                ("queue", lambda: commands.queue(), slurm.parse_squeue),
                ("history", lambda: commands.history(since), slurm.parse_sacct),
                ("fairshare", lambda: commands.fairshare(cfg["netid"]), slurm.parse_sshare),
                ("partitions", lambda: commands.partitions(config.all_partitions(cfg)), slurm.parse_sinfo),
                ("quota", lambda: commands.quota(), slurm.parse_myquota),
            ]
            for source, build, parse in plan:
                if force or self._stale(source, now):
                    if not self._run(source, build(), parse):
                        with self._lock:
                            self.connected = False
                        return
                    if source == "history" and "history" not in self._errors:
                        self.waits.record(self._data.get("history", {}).get("jobs", []))
            # Priority make-up is only worth asking for when something is waiting.
            waiting = any(j["kind"] == "waiting" for j in self._data.get("queue", {}).get("jobs", []))
            if waiting and (force or self._stale("priority", now)):
                if not self._run("priority", commands.priority(cfg["netid"]), slurm.parse_sprio):
                    with self._lock:
                        self.connected = False
                    return
            self._reload_all = False
        finally:
            with self._lock:
                self._refreshing = False

    def forget_connection(self) -> None:
        with self._lock:
            self.connected = None
            self._reload_all = True

    # -- on-demand lookups ------------------------------------------------------------------------
    def job_detail(self, job_id: str) -> Dict[str, Any]:
        result = self.ssh.run(commands.job_detail(job_id))
        if not result.ok:
            gone = "invalid job id" in (result.stderr + result.stdout).lower()
            return {"ok": False, "not_connected": result.not_connected,
                    "error": "Torch no longer keeps live details for this job (it finished a while ago)."
                             if gone else result.problem()}
        fields = slurm.parse_scontrol_job(result.stdout)
        keep = ["JobId", "JobName", "JobState", "Reason", "Dependency", "Partition", "QOS", "Account", "Priority",
                "SubmitTime", "EligibleTime", "StartTime", "EndTime", "RunTime", "TimeLimit", "NodeList",
                "ReqTRES", "AllocTRES", "TresPerNode", "ArrayTaskId", "ArrayTaskThrottle", "ExitCode",
                "WorkDir", "StdOut", "StdErr", "Command"]
        return {"ok": True, "fields": [{"key": k, "value": fields[k]} for k in keep if fields.get(k)],
                "raw": result.stdout.strip()[:8000]}

    def measure_disk(self) -> Dict[str, Any]:
        if not self.cfg.get("remote_dir"):
            return {"ok": False, "error": "No project folder on Torch is set in the settings."}
        result = self.ssh.run(commands.disk_usage(self.cfg["remote_dir"]))
        if not result.ok:
            return {"ok": False, "not_connected": result.not_connected, "error": result.problem()}
        m = re.match(r"^\s*(\S+)\s", result.stdout)
        return {"ok": True, "size": m.group(1) if m else result.stdout.strip(), "folder": self.cfg["remote_dir"],
                "measured": time.strftime("%H:%M")}

    # -- what the window shows --------------------------------------------------------------------
    def _source(self, name: str, now: float) -> Dict[str, Any]:
        at = self._at.get(name)
        return {"age_s": int(now - at) if at else None, "error": self._errors.get(name, ""),
                "loaded": name in self._data}

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = self.clock()
            data = dict(self._data)
            meta = {name: self._source(name, now) for name in FRESH_FOR}
            connected, refreshing, checked = self.connected, self._refreshing, self.checked_at
        cfg = self.cfg
        fair = self._fairshare(data.get("fairshare"))
        waits = self.waits.summary()
        prio = data.get("priority", {}).get("jobs", {})
        queue_jobs = data.get("queue", {}).get("jobs", [])
        history_jobs = [j for j in data.get("history", {}).get("jobs", [])
                        if j["kind"] in ("done", "failed", "cancelled", "other")]

        queue_groups = [self._queue_group(g, fair.get("value"), waits, prio) for g in slurm.group_jobs(queue_jobs)]
        queue_groups.sort(key=lambda g: {"running": 0, "waiting": 1}.get(g["kind"], 2))
        history_groups = [self._history_group(g) for g in slurm.group_jobs(history_jobs)]
        history_groups.sort(key=lambda g: g["end_ts"] or 0, reverse=True)

        quota = data.get("quota")
        warn = cfg["quota_warn_percent"]
        quota_view = {
            "filesystems": quota["filesystems"] if quota else [],
            "parsed": bool(quota and quota["parsed"]),
            "raw": quota["raw"] if quota else "",
            "warn_percent": warn,
            "warnings": policy.quota_blocks_submit(quota, warn) if quota and quota["parsed"] else [],
        }
        running = sum(g["counts"].get("running", 0) for g in queue_groups)
        waiting = sum(g["counts"].get("waiting", 0) for g in queue_groups)
        failed = sum(g["counts"].get("failed", 0) for g in history_groups)
        finished = sum(g["counts"].get("done", 0) for g in history_groups)
        return {
            "connected": connected, "refreshing": refreshing, "checked_age_s": int(now - checked) if checked else None,
            "netid": cfg["netid"], "mode": cfg["mode"], "project": cfg["project_name"],
            "remote_dir": cfg["remote_dir"], "history_days": cfg["history_days"],
            "sources": meta,
            "summary": {"running": running, "waiting": waiting, "failed": failed, "finished": finished,
                        "headline": self._headline(running, waiting, meta["queue"]["loaded"]),
                        "subline": self._subline(failed, finished, cfg["history_days"], meta["history"]["loaded"])},
            "queue": queue_groups,
            "history": history_groups,
            "fairshare": fair,
            "quota": quota_view,
            "partitions": self._partitions(data.get("partitions"), waits),
            "good_to_know": GOOD_TO_KNOW,
        }

    @staticmethod
    def _headline(running: int, waiting: int, loaded: bool) -> str:
        if not loaded:
            return ""
        if not running and not waiting:
            return "Nothing is running or waiting."
        if running and waiting:
            return "%s running and %s waiting." % (_plural(running, "job is", "jobs are"), _plural(waiting, "is", "are"))
        if running:
            return "%s running." % _plural(running, "job is", "jobs are")
        return "%s waiting to start." % _plural(waiting, "job is", "jobs are")

    @staticmethod
    def _subline(failed: int, finished: int, days: int, loaded: bool) -> str:
        if not loaded:
            return ""
        span = "the last %d days" % days if days != 1 else "the last day"
        if failed:
            return "%s in %s; %s." % (_plural(failed, "job failed", "jobs failed"), span,
                                      _plural(finished, "finished normally", "finished normally"))
        if finished:
            return "Nothing has failed in %s; %s." % (span, _plural(finished, "job finished normally",
                                                                     "jobs finished normally"))
        return "No jobs have finished in %s." % span

    def _fairshare(self, parsed: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        rows = (parsed or {}).get("rows", [])
        if not rows:
            return {"value": None, "account": "", "rows": [], **reasons.fairshare_words(None)}
        account = self.cfg.get("sbatch_account")
        main = next((r for r in rows if r["account"] == account), None) or min(rows, key=lambda r: r["fairshare"])
        return {"value": main["fairshare"], "account": main["account"], "rows": rows,
                **reasons.fairshare_words(main["fairshare"])}

    def _queue_group(self, g: Dict[str, Any], fair: Optional[float], waits: Dict[str, Any],
                     prio: Dict[str, Any]) -> Dict[str, Any]:
        tasks = g["tasks"]
        pending = [t for t in tasks if t["kind"] == "waiting"]
        out: Dict[str, Any] = {
            "id": g["id"], "name": g["name"], "partition": g["partition"], "is_array": g["is_array"],
            "kind": g.get("kind", "other"), "counts": g["counts"], "total": g["total"],
            "array_limit": g["array_limit"], "gpus": max((t["gpus"] for t in tasks), default=0),
            "tasks": [{"id": t["id"], "state": t["state"], "state_word": t["state_word"], "kind": t["kind"],
                       "elapsed": t["elapsed"], "limit": t["limit"], "elapsed_s": t["elapsed_s"],
                       "limit_s": t["limit_s"], "node": t["nodelist"], "task_count": t["task_count"],
                       "reason": t["reason"]} for t in tasks[:MAX_TASKS_SHOWN]],
            "hidden_tasks": max(0, len(tasks) - MAX_TASKS_SHOWN),
        }
        if pending:
            first = pending[0]
            typical = waits.get(first["partition"], {})
            out["why"] = reasons.explain(first["reason"], fair, typical.get("text", "") if typical.get("enough") else "",
                                         first.get("array_limit") or g["array_limit"])
            now = self.clock()
            if first["start_ts"] and first["start_ts"] > now:
                out["scheduler_estimate"] = "The scheduler's current estimate: starts around %s." % _clock(first["start_ts"])
            if first["submit_ts"]:
                out["waited"] = slurm.human_duration(now - first["submit_ts"])
            if g["id"] in prio:
                out["priority"] = prio[g["id"]]
        return out

    @staticmethod
    def _history_group(g: Dict[str, Any]) -> Dict[str, Any]:
        tasks = g["tasks"]
        ends = [t["end_ts"] for t in tasks if t["end_ts"]]
        starts = [t["start_ts"] for t in tasks if t["start_ts"]]
        longest = max((t["elapsed_s"] or 0 for t in tasks), default=0)
        return {
            "id": g["id"], "name": g["name"], "partition": g["partition"], "is_array": g["is_array"],
            "kind": g.get("kind", "other"), "counts": g["counts"], "total": g["total"],
            "end_ts": max(ends) if ends else None, "ended": _clock(max(ends)) if ends else "",
            "started": _clock(min(starts)) if starts else "",
            "longest": slurm.human_duration(longest) if longest else "",
            "tasks": [{"id": t["id"], "state": t["state"], "state_word": t["state_word"], "kind": t["kind"],
                       "elapsed": t["elapsed"], "exit_code": t["exit_code"], "ended": _clock(t["end_ts"]),
                       "note": t["state_note"]} for t in tasks[:MAX_TASKS_SHOWN]],
            "hidden_tasks": max(0, len(tasks) - MAX_TASKS_SHOWN),
        }

    def _partitions(self, parsed: Optional[Dict[str, Any]], waits: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        found = (parsed or {}).get("partitions", {})
        for name in config.all_partitions(self.cfg):
            info = dict(found.get(name) or {})
            is_gpu = name in self.cfg["gpu_partitions"]
            w = waits.get(name)
            out.append({
                "partition": name, "role": "GPU" if is_gpu else "CPU",
                "readable": bool(info) and (not is_gpu or info.get("gpus_total", 0) > 0),
                "gpus_free": info.get("gpus_free"), "gpus_total": info.get("gpus_total"),
                "nodes": info.get("nodes"), "nodes_usable": info.get("nodes_usable"),
                "typical_wait": w["text"] if w and w["enough"] else "",
                "wait_samples": w["samples"] if w else 0,
                "longest_wait": w["longest_text"] if w and w["enough"] else "",
            })
        return out
