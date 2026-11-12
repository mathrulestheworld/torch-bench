"""The read-only file browser and log viewer.

Everything here goes through the guarded file commands in commands.py. Browsing starts from one of
the person's own folders (config.places: the project, home, scratch, archive); paths are relative to
it, and Torch itself refuses anything that resolves outside it. Nothing is written, moved or deleted
on the cluster.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List

from . import commands, config
from .ssh import SSH, Result

MAX_ENTRIES = 5000
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".svg": "image/svg+xml"}
LOG_ENDINGS = (".out", ".err", ".log")

# Common ways a job dies, with one line on what it means and what to do.
LOG_PROBLEMS = [
    (re.compile(r"CUDA out of memory|OutOfMemoryError|CUDA error: out of memory", re.I),
     "The GPU ran out of memory",
     "The job needed more GPU memory than the GPU has. Use a smaller batch or model, or a larger GPU."),
    (re.compile(r"Disk quota exceeded|No space left on device", re.I),
     "Storage is full",
     "Torch refused to write because your storage is full. Free space in /scratch (see Storage on the Jobs page), then rerun."),
    (re.compile(r"ModuleNotFoundError|No module named"),
     "A Python package is missing",
     "The job's environment does not have a package it needs, or the job ran in the wrong environment."),
    (re.compile(r"oom[_-]kill|Out Of Memory|OOM Killed|exceeded memory limit|oom-kill event", re.I),
     "The job ran out of main memory",
     "It used more RAM than it asked for and was stopped. Ask for more memory when submitting."),
    (re.compile(r"DUE TO TIME LIMIT"),
     "The job ran out of time",
     "It reached its time allowance and was stopped. Ask for more time, or continue from a checkpoint."),
    (re.compile(r"CANCELLED AT \S+|\*\*\* JOB \d+ .*CANCELLED"),
     "The job was cancelled",
     "By you, by an administrator, or by NYU's rule against GPU jobs that use under 60% of the GPU."),
    (re.compile(r"Traceback \(most recent call last\)"),
     "The program stopped with a Python error",
     "The lines after this one say what went wrong and where."),
    (re.compile(r"Segmentation fault|core dumped", re.I),
     "The program crashed",
     "A low-level crash, often a library or driver mismatch in the environment."),
    (re.compile(r"NODE FAILURE|node failure|NODE_FAIL"),
     "The machine failed",
     "Not your fault. Resubmit the job."),
    (re.compile(r"FileNotFoundError|No such file or directory"),
     "A file was not where the job expected",
     "If it was just pushed, check that it was not left out of the push."),
    (re.compile(r"Permission denied"),
     "Permission denied",
     "The job tried to read or write somewhere it is not allowed to."),
    (re.compile(r"slurmstepd: error|srun: error"),
     "The scheduler reported an error",
     "See the line itself for what happened."),
]
MAX_PROBLEMS = 60


def scan_log(text: str) -> List[Dict[str, Any]]:
    """Lines of a log that match a known failure, each with a plain explanation."""
    found: List[Dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        for pattern, title, explanation in LOG_PROBLEMS:
            if pattern.search(line):
                found.append({"line": number, "text": line.strip()[:300], "title": title, "explanation": explanation})
                break
        if len(found) >= MAX_PROBLEMS:
            break
    return found


def kind_of(name: str) -> str:
    """How the window should show a file: markdown, json, csv, image, log or text."""
    ext = os.path.splitext(name.lower())[1]
    if ext in (".md", ".markdown"):
        return "markdown"
    if ext == ".json":
        return "json"
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext in IMAGE_TYPES:
        return "image"
    if ext in LOG_ENDINGS:
        return "log"
    return "text"


def _trim_partial_utf8(data: bytes) -> bytes:
    """Drop an unfinished multi-byte character at the very end, so following never splits one."""
    for back in range(1, min(4, len(data)) + 1):
        byte = data[-back]
        if byte & 0b11000000 == 0b10000000:
            continue                      # a continuation byte: keep looking for the lead byte
        if byte & 0b10000000 == 0:
            return data                   # plain ASCII at the end: nothing unfinished
        need = 2 if byte >> 5 == 0b110 else 3 if byte >> 4 == 0b1110 else 4 if byte >> 3 == 0b11110 else 1
        return data if back >= need else data[:-back]
    return data


def parse_listing(text: str) -> Dict[str, Any]:
    """`find -printf "%y\\t%s\\t%T@\\t%Y\\t%f\\n"` -> entries, folders first."""
    kinds = {"d": "folder", "f": "file", "l": "link"}
    entries: List[Dict[str, Any]] = []
    skipped = 0
    lines = text.split("\n")
    for line in lines:
        if not line:
            continue
        parts = line.split("\t", 4)
        if len(parts) != 5 or not parts[1].isdigit():
            skipped += 1
            continue
        y, size, mtime, target, name = parts
        try:
            modified = float(mtime)
        except ValueError:
            skipped += 1
            continue
        kind = kinds.get(y, "other")
        entries.append({
            "name": name, "kind": kind, "size": int(size) if kind == "file" else None,
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)), "modified_ts": modified,
            # A link is shown but never followed: where it points may be outside the project.
            "link_to": {"d": "folder", "f": "file"}.get(target, "missing") if kind == "link" else None,
            "show_as": kind_of(name) if kind == "file" else None,
        })
    truncated = len(entries) > MAX_ENTRIES
    entries = entries[:MAX_ENTRIES]
    entries.sort(key=lambda e: (e["kind"] != "folder", e["name"].lower()))
    return {"entries": entries, "truncated": truncated, "skipped": skipped}


class Files:
    def __init__(self, cfg: Dict[str, Any], ssh: SSH) -> None:
        self.cfg, self.ssh = cfg, ssh

    def places(self) -> List[Dict[str, str]]:
        return config.places(self.cfg)

    def _root(self, place: str) -> str:
        for entry in self.places():
            if entry["key"] == place:
                return entry["path"]
        raise commands.CommandRefused("That is not one of your folders: %r" % (place,))

    @staticmethod
    def _fail(result: Result) -> Dict[str, Any]:
        return {"ok": False, "not_connected": result.not_connected, "error": result.problem()}

    def list(self, place: str, rel: str) -> Dict[str, Any]:
        root = self._root(place)
        rel = commands.check_rel_path(rel)
        result = self.ssh.run(commands.list_dir(root, rel))
        if not result.ok:
            return self._fail(result)
        out = parse_listing(result.stdout)
        out.update({"ok": True, "place": place, "rel": rel, "root": root})
        return out

    def read(self, place: str, rel: str, start: int = 0, following: bool = False) -> Dict[str, Any]:
        """start = -1 opens at the end (logs). following=True fetches only what was added after `start`."""
        root = self._root(place)
        rel = commands.check_rel_path(rel)
        result = self.ssh.run(commands.read_file(root, rel, start, following))
        if not result.ok:
            return self._fail(result)
        head, _, body = result.data.partition(b"\n")
        try:
            size, begun = (int(x) for x in head.decode("ascii").split())
        except ValueError:
            return {"ok": False, "error": "Torch's answer could not be read."}
        binary = b"\x00" in body[:8192]
        if not binary:
            body = _trim_partial_utf8(body)
        end = begun + len(body)
        text = "" if binary else body.decode("utf-8", "replace")
        name = rel.rsplit("/", 1)[-1]
        return {
            "ok": True, "place": place, "rel": rel, "name": name, "size": size, "start": begun, "end": end,
            "more_before": begun > 0, "more_after": end < size, "binary": binary,
            "show_as": kind_of(name), "text": text,
            "problems": scan_log(text) if not binary and (kind_of(name) in ("log", "text")) else [],
        }

    def download(self, place: str, rel: str) -> Dict[str, Any]:
        root = self._root(place)
        rel = commands.check_rel_path(rel)
        result = self.ssh.run(commands.download_file(root, rel))
        if not result.ok:
            return self._fail(result)
        name = rel.rsplit("/", 1)[-1]
        ext = os.path.splitext(name.lower())[1]
        return {"ok": True, "name": name, "data": result.data,
                "content_type": IMAGE_TYPES.get(ext, "application/octet-stream")}

    def job_logs(self, job_id: str) -> Dict[str, Any]:
        if not self.cfg.get("remote_dir"):
            return {"ok": False, "no_project": True,
                    "error": "Job logs are looked up in the project's logs folder, and no project folder is set yet "
                             "(Help, Change my details)."}
        result = self.ssh.run(commands.job_logs(self.cfg["remote_dir"], job_id))
        if not result.ok:
            return self._fail(result)
        files = []
        for line in result.stdout.split("\n"):
            parts = line.split("\t", 2)
            if len(parts) != 3 or not parts[0].isdigit():
                continue
            try:
                modified = float(parts[1])
            except ValueError:
                continue
            files.append({"place": "project", "rel": "logs/" + parts[2], "name": parts[2], "size": int(parts[0]),
                          "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(modified)), "modified_ts": modified})
        # This task's own files first (…_<task>.out), then the rest of the batch, newest first.
        exact = job_id if "_" in job_id else None
        files.sort(key=lambda f: (0 if exact and exact in f["name"] else 1, -f["modified_ts"]))
        return {"ok": True, "job": job_id, "files": files}
