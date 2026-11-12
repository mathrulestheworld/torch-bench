"""Torch Bench's own wait-time estimate: how long did recent jobs really wait?

Every job seen in `sacct` with a known start is recorded once (eligible -> start, so time spent
waiting on a dependency does not count as queue time). The record is kept in
~/torchbench/cache/waits.json so the estimate keeps improving beyond sacct's window.
These are estimates and are always labelled as such in the window.
"""
from __future__ import annotations

import json
import statistics
import time
from typing import Any, Dict, List, Optional

from . import paths
from .slurm import human_duration

KEEP_DAYS = 60
RECENT_DAYS = 7
MIN_SAMPLES = 3


class WaitHistory:
    def __init__(self, persist: bool = True) -> None:
        self.persist = persist
        self._records: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.persist:
            return
        try:
            data = json.loads((paths.cache_dir() / "waits.json").read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._records = {k: v for k, v in data.items() if isinstance(v, dict)}
        except (OSError, ValueError):
            self._records = {}

    def record(self, sacct_jobs: List[Dict[str, Any]], now: Optional[float] = None) -> int:
        """Add newly started jobs. Returns how many were new."""
        self._load()
        now = now or time.time()
        added = 0
        for job in sacct_jobs:
            start = job.get("start_ts")
            ready = job.get("eligible_ts") or job.get("submit_ts")
            if not start or not ready or not job.get("partition") or job["id"] in self._records:
                continue
            wait = start - ready
            if wait < 0 or wait > 30 * 86400:
                continue
            self._records[job["id"]] = {"partition": job["partition"], "wait": int(wait), "start": int(start)}
            added += 1
        cutoff = now - KEEP_DAYS * 86400
        stale = [k for k, v in self._records.items() if v.get("start", 0) < cutoff]
        for k in stale:
            del self._records[k]
        if self.persist and (added or stale):
            try:
                paths.atomic_write(paths.cache_dir() / "waits.json", json.dumps(self._records))
            except OSError:
                pass
        return added

    def summary(self, now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
        """Per partition: the typical (median) wait of your jobs that started in the last 7 days."""
        self._load()
        now = now or time.time()
        by_part: Dict[str, List[int]] = {}
        for rec in self._records.values():
            if rec.get("start", 0) >= now - RECENT_DAYS * 86400:
                by_part.setdefault(rec["partition"], []).append(int(rec["wait"]))
        out: Dict[str, Dict[str, Any]] = {}
        for part, waits in by_part.items():
            waits.sort()
            median = statistics.median(waits)
            out[part] = {
                "partition": part, "samples": len(waits), "median_s": int(median),
                "longest_s": waits[-1],
                "enough": len(waits) >= MIN_SAMPLES,
                "text": human_duration(median), "longest_text": human_duration(waits[-1]),
            }
        return out
