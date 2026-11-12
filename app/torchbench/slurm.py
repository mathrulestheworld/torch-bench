"""Parsers for the scheduler's output. Pure functions: text in, plain dicts out.

The field lists come from commands.py, so the commands and the parsers cannot drift apart.
Each parser is forgiving: lines it cannot read are skipped and counted, never guessed at.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .commands import SACCT_FIELDS, SPRIO_FIELDS, SQUEUE_FIELDS

RUNNING_STATES = ("RUNNING", "COMPLETING", "CONFIGURING", "STAGE_OUT", "SIGNALING", "RESIZING")
WAITING_STATES = ("PENDING", "REQUEUED", "REQUEUE_HOLD", "REQUEUE_FED", "RESV_DEL_HOLD", "SUSPENDED", "STOPPED")
FAILED_STATES = ("FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "PREEMPTED",
                 "SPECIAL_EXIT", "REVOKED")

STATE_WORDS = {
    "RUNNING": "Running", "COMPLETING": "Finishing", "CONFIGURING": "Starting", "PENDING": "Waiting",
    "COMPLETED": "Finished", "FAILED": "Failed", "CANCELLED": "Cancelled", "TIMEOUT": "Ran out of time",
    "OUT_OF_MEMORY": "Ran out of memory", "NODE_FAIL": "Machine failed", "PREEMPTED": "Pre-empted",
    "REQUEUED": "Waiting again", "SUSPENDED": "Paused", "BOOT_FAIL": "Machine failed", "DEADLINE": "Missed deadline",
}


def kind_of(state: str) -> str:
    """running | waiting | done | failed | cancelled | other"""
    if state in RUNNING_STATES:
        return "running"
    if state in WAITING_STATES:
        return "waiting"
    if state == "COMPLETED":
        return "done"
    if state == "CANCELLED":
        return "cancelled"
    if state in FAILED_STATES:
        return "failed"
    return "other"


def state_word(state: str) -> str:
    return STATE_WORDS.get(state, state.replace("_", " ").capitalize())


# -- times -------------------------------------------------------------------------------------
def parse_duration(text: str) -> Optional[int]:
    """'1-02:03:04', '02:03:04', '03:04' or '4' (minutes) -> seconds. None for UNLIMITED / N/A / junk."""
    text = (text or "").strip()
    m = re.match(r"^(?:(\d+)-)?(\d+):(\d+):(\d+)$", text)
    if m:
        d, h, mi, s = (int(x) if x else 0 for x in m.groups())
        return ((d * 24 + h) * 60 + mi) * 60 + s
    m = re.match(r"^(\d+):(\d+)$", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.match(r"^(?:(\d+)-)?(\d+)$", text)   # 'D-HH' or minutes
    if m and m.group(1):
        return (int(m.group(1)) * 24 + int(m.group(2))) * 3600
    if m:
        return int(m.group(2)) * 60
    return None


def parse_time(text: str) -> Optional[float]:
    """'2026-11-10T19:18:00' (cluster local time) -> epoch seconds. None for Unknown / None / N/A."""
    text = (text or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", text)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def human_duration(seconds: Optional[float]) -> str:
    """A rough, readable length of time: '40 s', '12 min', '3.5 h', '2 days'."""
    if seconds is None:
        return ""
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return "%d s" % round(seconds)
    if seconds < 90 * 60:
        return "%d min" % round(seconds / 60)
    if seconds < 36 * 3600:
        hours = seconds / 3600
        return ("%d h" % round(hours)) if hours >= 10 or abs(hours - round(hours)) < 0.05 else ("%.1f h" % hours)
    days = seconds / 86400
    return ("%d days" % round(days)) if days >= 10 or abs(days - round(days)) < 0.05 else ("%.1f days" % days)


# -- job IDs -----------------------------------------------------------------------------------
def split_job_id(job_id: str) -> Tuple[str, str]:
    """'123_4' -> ('123', '4');  '123_[5-18%4]' -> ('123', '[5-18%4]');  '123' -> ('123', '')."""
    base, sep, task = job_id.partition("_")
    return base, task if sep else ""


def array_spec_count(task: str) -> Tuple[int, Optional[int]]:
    """'[5-18%4]' -> (14, 4): how many tasks the expression covers, and the run-at-once limit."""
    m = re.match(r"^\[(.*?)(?:%(\d+))?\]$", task)
    if not m:
        return (1, None)
    count = 0
    for part in m.group(1).split(","):
        r = re.match(r"^(\d+)(?:-(\d+)(?::(\d+))?)?$", part.strip())
        if not r:
            continue
        lo = int(r.group(1))
        hi = int(r.group(2)) if r.group(2) else lo
        step = int(r.group(3)) if r.group(3) else 1
        count += max(0, (hi - lo) // max(1, step) + 1)
    return (max(count, 1), int(m.group(2)) if m.group(2) else None)


def gpu_count(tres: str) -> int:
    """GPUs in a TRES/GRES string: 'cpu=8,gres/gpu=2,mem=96G' or 'gres/gpu:l40s:1' or 'gpu:l40s:4(S:0-1)'."""
    tres = tres or ""
    m = re.search(r"gres/gpu=(\d+)", tres)
    if m:
        return int(m.group(1))
    total = 0
    for m in re.finditer(r"gpu(?::[^:,(=]+)?[:=](\d+)", tres):
        total += int(m.group(1))
    return total


# -- squeue --------------------------------------------------------------------------------------
def parse_squeue(text: str) -> Dict[str, Any]:
    jobs: List[Dict[str, Any]] = []
    skipped = 0
    n = len(SQUEUE_FIELDS)
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split("|", n - 1)     # the job name is last, so a '|' in it is harmless
        if len(parts) != n or not re.match(r"^\d", parts[0]):
            skipped += 1
            continue
        job = dict(zip(SQUEUE_FIELDS, (p.strip() for p in parts)))
        base, task = split_job_id(job["id"])
        job["array_id"], job["task"] = base, task
        job["kind"] = kind_of(job["state"])
        job["state_word"] = state_word(job["state"])
        job["elapsed_s"] = parse_duration(job["elapsed"])
        job["limit_s"] = parse_duration(job["limit"])
        job["start_ts"] = parse_time(job["start"])
        job["submit_ts"] = parse_time(job["submit"])
        job["gpus"] = gpu_count(job["gres"])
        count, limit = array_spec_count(task) if task.startswith("[") else (1, None)
        job["task_count"], job["array_limit"] = count, limit
        if job["reason"] in ("None", "(null)", ""):
            job["reason"] = ""
        jobs.append(job)
    return {"jobs": jobs, "skipped": skipped}


# -- sacct ---------------------------------------------------------------------------------------
def _parse_table(text: str, expected: List[str]) -> Tuple[List[Dict[str, str]], int]:
    """A '|'-separated table with a header line. Columns are matched by header name."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return [], 0
    header = [h.strip() for h in lines[0].split("|")]
    if not set(expected[:2]).issubset(header):       # no header: assume the order we asked for
        header, body = list(expected), lines
    else:
        body = lines[1:]
    rows, skipped, n = [], 0, len(header)
    for line in body:
        parts = line.split("|", n - 1)
        if len(parts) != n:
            skipped += 1
            continue
        rows.append(dict(zip(header, (p.strip() for p in parts))))
    return rows, skipped


def parse_sacct(text: str) -> Dict[str, Any]:
    rows, skipped = _parse_table(text, SACCT_FIELDS)
    jobs: List[Dict[str, Any]] = []
    for r in rows:
        job_id = r.get("JobID", "")
        if not re.match(r"^\d", job_id):
            skipped += 1
            continue
        raw_state = r.get("State", "")
        state = raw_state.split(" ")[0].rstrip("+")
        base, task = split_job_id(job_id)
        submit, eligible, start, end = (parse_time(r.get(k, "")) for k in ("Submit", "Eligible", "Start", "End"))
        jobs.append({
            "id": job_id, "array_id": base, "task": task,
            "task_count": array_spec_count(task)[0] if task.startswith("[") else 1,
            "name": r.get("JobName", ""), "state": state, "state_note": raw_state[len(state):].strip(),
            "kind": kind_of(state), "state_word": state_word(state),
            "partition": r.get("Partition", ""),
            "submit": r.get("Submit", ""), "start": r.get("Start", ""), "end": r.get("End", ""),
            "submit_ts": submit, "eligible_ts": eligible, "start_ts": start, "end_ts": end,
            "elapsed": r.get("Elapsed", ""), "elapsed_s": parse_duration(r.get("Elapsed", "")),
            "limit": r.get("Timelimit", ""), "exit_code": r.get("ExitCode", ""),
            "gpus": gpu_count(r.get("AllocTRES", "") or r.get("ReqTRES", "")),
            "reason": "" if r.get("Reason", "") in ("None", "") else r.get("Reason", ""),
        })
    return {"jobs": jobs, "skipped": skipped}


# -- sshare --------------------------------------------------------------------------------------
def parse_sshare(text: str) -> Dict[str, Any]:
    rows, skipped = _parse_table(text, ["Account", "User"])
    out = []
    for r in rows:
        try:
            fair = float(r.get("FairShare", ""))
        except ValueError:
            skipped += 1
            continue
        usage = None
        try:
            usage = float(r.get("EffectvUsage", ""))
        except ValueError:
            pass
        out.append({"account": r.get("Account", "").strip(), "user": r.get("User", "").strip(),
                    "fairshare": fair, "effective_usage": usage, "raw_usage": r.get("RawUsage", "")})
    return {"rows": out, "skipped": skipped}


# -- sprio ---------------------------------------------------------------------------------------
def parse_sprio(text: str) -> Dict[str, Any]:
    jobs: Dict[str, Dict[str, float]] = {}
    skipped = 0
    n = len(SPRIO_FIELDS)
    for line in text.splitlines():
        parts = [p.strip() for p in line.strip().split("|")]
        if len(parts) != n:
            if line.strip():
                skipped += 1
            continue
        if not re.match(r"^\d", parts[0]):
            continue                                   # the header line
        try:
            values = [float(p) if p not in ("", "N/A") else 0.0 for p in parts[1:]]
        except ValueError:
            skipped += 1
            continue
        base = split_job_id(parts[0])[0]
        jobs.setdefault(base, dict(zip(SPRIO_FIELDS[1:], values)))
    return {"jobs": jobs, "skipped": skipped}


# -- sinfo ---------------------------------------------------------------------------------------
_USABLE_NODE_STATES = ("idle", "mix", "mixed")


def parse_sinfo(text: str) -> Dict[str, Any]:
    """Per partition: GPUs in total, in use, and free on machines that can take work right now."""
    parts: Dict[str, Dict[str, Any]] = {}
    skipped = 0
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 5:
            if line.strip():
                skipped += 1
            continue
        name, _node, state, gres, used = tokens[0].rstrip("*"), tokens[1], tokens[2].lower(), tokens[3], tokens[4]
        p = parts.setdefault(name, {"partition": name, "nodes": 0, "nodes_usable": 0,
                                    "gpus_total": 0, "gpus_used": 0, "gpus_free": 0})
        total, in_use = gpu_count(gres), gpu_count(used)
        p["nodes"] += 1
        p["gpus_total"] += total
        p["gpus_used"] += min(in_use, total)
        if state in _USABLE_NODE_STATES:               # a trailing * ~ # $ etc. means not usable right now
            p["nodes_usable"] += 1
            p["gpus_free"] += max(0, total - in_use)
    return {"partitions": parts, "skipped": skipped}


# -- myquota (NYU's own tool) ----------------------------------------------------------------------
_UNIT = {"": 1, "B": 1, "K": 1e3, "KB": 1e3, "M": 1e6, "MB": 1e6, "G": 1e9, "GB": 1e9, "T": 1e12, "TB": 1e12,
         "P": 1e15, "PB": 1e15}
_USAGE_RE = re.compile(r"([\d.]+)\s*([KMGTP]?B?)\s*\(\s*([\d.]+)\s*%\s*\)", re.I)
_ALLOC_RE = re.compile(r"([\d.]+)\s*([KMGTP]B)\s*/\s*([\d.]+)\s*([KMB]?)", re.I)


def parse_myquota(text: str) -> Dict[str, Any]:
    """Lines such as
         /scratch  $SCRATCH  No/Flushed  5.0TB/5.0M  4613.20GB(92.26%)/2437112(48.74%)
    The format is NYU's own and may change: anything unreadable leaves parsed=False so the
    window shows the raw text instead of a made-up number."""
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("/"):
            continue
        usages = list(_USAGE_RE.finditer(line))
        if not usages:
            continue
        fs = line.split()[0]
        space = usages[0]
        entry: Dict[str, Any] = {
            "fs": fs,
            "space_used": "%s%s" % (space.group(1), space.group(2).upper()),
            "space_used_bytes": float(space.group(1)) * _UNIT.get(space.group(2).upper(), 1),
            "space_pct": float(space.group(3)),
            "files_used": None, "files_pct": None, "space_limit": "", "files_limit": "",
            "backed_up": None, "flushed": "flush" in line.lower(),
        }
        if len(usages) > 1:
            entry["files_used"] = usages[1].group(1) + usages[1].group(2)
            entry["files_pct"] = float(usages[1].group(3))
        alloc = _ALLOC_RE.search(line[:space.start()])
        if alloc:
            entry["space_limit"] = "%s%s" % (alloc.group(1), alloc.group(2).upper())
            entry["files_limit"] = "%s%s" % (alloc.group(3), alloc.group(4).upper())
        if re.search(r"\byes\b", line, re.I):
            entry["backed_up"] = True
        elif re.search(r"\bno\b", line, re.I):
            entry["backed_up"] = False
        entry["worst_pct"] = max(entry["space_pct"], entry["files_pct"] or 0.0)
        out.append(entry)
    return {"filesystems": out, "parsed": bool(out), "raw": text.strip()[:4000]}


# -- scontrol show job -----------------------------------------------------------------------------
_KV_RE = re.compile(r"(?:^|\s)([A-Za-z][\w/:.-]*)=(.*?)(?=\s+[A-Za-z][\w/:.-]*=|$)")


def parse_scontrol_job(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if out:
                break                                   # first job record only
            continue
        for m in _KV_RE.finditer(line):
            out.setdefault(m.group(1), m.group(2).strip())
    return out


# -- grouping array tasks so the list reads like the launcher that made them ----------------------
def group_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per job or per array. Order: first appearance."""
    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for job in jobs:
        key = job["array_id"]
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"id": key, "name": job["name"], "partition": job["partition"], "is_array": False,
                               "tasks": [], "counts": {}, "array_limit": None}
            order.append(key)
        g["tasks"].append(job)
        if job["task"]:
            g["is_array"] = True
        n = job.get("task_count", 1) or 1
        g["counts"][job["kind"]] = g["counts"].get(job["kind"], 0) + n
        if job.get("array_limit"):
            g["array_limit"] = job["array_limit"]
    out = []
    for key in order:
        g = groups[key]
        g["total"] = sum(g["counts"].values())
        for kind in ("running", "waiting", "failed", "cancelled", "done", "other"):
            if g["counts"].get(kind):
                g["kind"] = kind                           # the most "live" kind present
                break
        out.append(g)
    return out
