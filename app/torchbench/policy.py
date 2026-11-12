"""Who may do what, and the checks that guard anything that changes the cluster.

The app itself looks, and copies files with push and pull; every other action below that changes
something answers "not in this version".
The table is here from the start so later milestones add actions in one audited place.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# action -> who may do it once it exists. "launcher" also needs the launcher to be marked safe for colleagues.
ACTIONS = {
    "view": ("owner", "colleague"),          # dashboard, logs, files, commands page
    "terminal": ("owner", "colleague"),      # the escape hatch: the person types as themselves
    "pull": ("owner", "colleague"),          # copies results to the Mac, after a preview
    "push": ("owner",),                      # overwrites the cluster copy, after a preview: for whoever runs the project
    "submit": ("owner", "colleague"),        # M2: registered launchers only; colleagues only "safe" ones
    "cancel": ("owner",),                    # M2: one named job, after confirmation
    "cleanup": ("owner",),                   # M3: reviewed-plan flow only
}
AVAILABLE_NOW = ("view", "terminal", "pull", "push")


def allowed(mode: str, action: str) -> bool:
    return action in AVAILABLE_NOW and mode in ACTIONS.get(action, ())


def _size(n: Optional[float]) -> str:
    """4613200000000 -> '4.6 TB' (decimal units, like the quota itself)."""
    if n is None:
        return "an unknown amount"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while n >= 1000 and i < len(units) - 1:
        n /= 1000.0
        i += 1
    return ("%d %s" % (round(n), units[i])) if n >= 100 or i == 0 else ("%.1f %s" % (n, units[i]))


def _limit(text: str) -> str:
    """'5.0TB' -> '5 TB'"""
    import re
    return re.sub(r"(\d)([A-Za-z])", r"\1 \2", re.sub(r"\.0(?=[A-Za-z])", "", text or ""))


def quota_blocks_submit(quota: Optional[Dict[str, Any]], warn_percent: int) -> List[str]:
    """Reasons a submit must be blocked because storage is nearly full. Empty list = fine.

    A full /scratch once killed a training job at its final save, so submitting above the
    threshold is blocked, not merely warned about. Unknown quota also blocks: no guessing."""
    if not quota or not quota.get("parsed"):
        return ["Storage use could not be read, so it is not known whether there is room for results."]
    problems = []
    for fs in quota.get("filesystems", []):
        if fs["fs"] not in ("/scratch", "/home"):
            continue
        if fs["space_pct"] >= warn_percent:
            problems.append("%s is %.0f%% full (%s of %s)." % (fs["fs"], fs["space_pct"], _size(fs.get("space_used_bytes")),
                                                               _limit(fs["space_limit"]) or "its limit"))
        if fs.get("files_pct") is not None and fs["files_pct"] >= warn_percent:
            problems.append("%s holds %.0f%% of the files it may hold." % (fs["fs"], fs["files_pct"]))
    return problems
