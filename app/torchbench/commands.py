"""The registry of commands Torch Bench may run on the cluster.

This file is the encoded form of the safety rules:
  * The SSH layer (ssh.py) runs only RemoteCommand objects, and only this module can make them.
    There is no function anywhere that takes a free-form string and runs it on Torch.
  * Every command here is a cheap queue/status/file lookup: nothing that computes on the login node.
  * Every argument is validated against a strict pattern and then shell-quoted.
  * Only read-only commands are registered so far. A command marked mutating is refused by the SSH
    layer unless it is run through the explicit confirmation path that later milestones add
    (push, pull, registered launchers, guarded scancel, the reviewed cleanup flow).
  * File commands (list, read, download, a job's logs) take one of the person's own folders (the
    project, home, scratch, archive: config.places) plus a path *relative to it*, and run inside one
    fixed guard script that resolves the real path on Torch and refuses anything that ends up
    outside that folder, symlinks included.
  * The built-in terminal is the one deliberate escape hatch. Its start-up line is built here too;
    what the person types afterwards is their own.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import List

from .config import NAME_RE, NETID_RE, REMOTE_DIR_RE

_REGISTRY_KEY = object()

JOB_ID_RE = re.compile(r"^[0-9]{1,12}(_[0-9]{1,6})?$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

# Field lists are fixed here so the parsers (slurm.py) and the commands cannot drift apart.
SQUEUE_FORMAT = "%i|%T|%M|%l|%P|%r|%S|%V|%D|%b|%N|%Q|%j"
SQUEUE_FIELDS = ["id", "state", "elapsed", "limit", "partition", "reason", "start", "submit",
                 "nodes", "gres", "nodelist", "priority", "name"]
SACCT_FIELDS = ["JobID", "State", "Partition", "Submit", "Eligible", "Start", "End", "Elapsed",
                "Timelimit", "ExitCode", "AllocTRES", "ReqTRES", "Reason", "JobName"]
SSHARE_FIELDS = ["Account", "User", "RawShares", "NormShares", "RawUsage", "EffectvUsage", "FairShare"]
SPRIO_FORMAT = "%i|%Y|%A|%F|%J|%P|%Q|%T"
SPRIO_FIELDS = ["id", "priority", "age", "fairshare", "jobsize", "partition", "qos", "tres"]
SINFO_FORMAT = "PartitionName:40,NodeHost:40,StateCompact:20,Gres:100,GresUsed:100"


class CommandRefused(ValueError):
    """An argument did not pass validation, so no command was built."""


@dataclass(frozen=True)
class RemoteCommand:
    name: str            # registry name, e.g. "queue"
    text: str            # the exact text run on Torch
    label: str           # plain-language description shown in the activity log
    routine: bool        # a periodic status check (hidden by default in the activity log)
    mutating: bool       # changes something on the cluster
    timeout: int         # seconds
    key: object = None   # proof that the registry built it
    binary: bool = False        # the answer is file bytes, not text
    quiet_output: bool = False  # file contents: log a one-line summary instead of the contents
    interactive: bool = False   # the terminal's start-up line; run under a pty, not by SSH.run

    def from_registry(self) -> bool:
        return self.key is _REGISTRY_KEY


def _make(name: str, text: str, label: str, routine: bool = True, timeout: int = 30, binary: bool = False,
          quiet_output: bool = False, interactive: bool = False) -> RemoteCommand:
    return RemoteCommand(name=name, text=text, label=label, routine=routine, mutating=False,
                         timeout=timeout, key=_REGISTRY_KEY, binary=binary, quiet_output=quiet_output,
                         interactive=interactive)


def _need(pattern: "re.Pattern[str]", value: str, what: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise CommandRefused("Not a valid %s: %r" % (what, value))
    return value


def probe() -> RemoteCommand:
    return _make("probe", "hostname", "Check which Torch login node answered", routine=True, timeout=15)


def queue() -> RemoteCommand:
    return _make("queue", "squeue --me -h -o %s" % shlex.quote(SQUEUE_FORMAT),
                 "List your jobs in the queue (with the scheduler's start estimate)")


def history(since_date: str) -> RemoteCommand:
    _need(DATE_RE, since_date, "date")
    return _make("history", "sacct -X -P -S %s --format=%s" % (since_date, ",".join(SACCT_FIELDS)),
                 "List your jobs since %s" % since_date)


def fairshare(netid: str) -> RemoteCommand:
    _need(NETID_RE, netid, "NetID")
    return _make("fairshare", "sshare -U -u %s -P -o %s" % (shlex.quote(netid), ",".join(SSHARE_FIELDS)),
                 "Look up your fair-share standing")


def priority(netid: str) -> RemoteCommand:
    _need(NETID_RE, netid, "NetID")
    return _make("priority", "sprio -u %s -o %s" % (shlex.quote(netid), shlex.quote(SPRIO_FORMAT)),
                 "Look up what makes up your waiting jobs' priority")


def partitions(names: List[str]) -> RemoteCommand:
    if not names:
        raise CommandRefused("No partitions configured")
    for n in names:
        _need(NAME_RE, n, "partition name")
    return _make("partitions", "sinfo -h -N -p %s -O %s" % (shlex.quote(",".join(names)), shlex.quote(SINFO_FORMAT)),
                 "Count free GPUs on %s" % ", ".join(names))


def quota() -> RemoteCommand:
    return _make("quota", "myquota", "Check your storage quota")


def job_detail(job_id: str) -> RemoteCommand:
    _need(JOB_ID_RE, job_id, "job ID")
    return _make("job_detail", "scontrol show job %s" % job_id,
                 "Show the scheduler's details for job %s" % job_id, routine=False)


def disk_usage(remote_dir: str) -> RemoteCommand:
    _need(REMOTE_DIR_RE, remote_dir, "folder on Torch")
    if ".." in remote_dir.split("/"):
        raise CommandRefused("Not a valid folder on Torch: %r" % remote_dir)
    return _make("disk_usage", "du -sh %s" % shlex.quote(remote_dir),
                 "Measure the real size of %s (fresher than myquota)" % remote_dir, routine=False, timeout=180)


# -- files: read-only, inside the project folder -------------------------------------------------
# One guard, shared by every file command. $1 is the starting folder, $2 the requested path inside it.
# realpath follows symlinks, so a link that points outside the folder is refused too.
GUARD = ('r=$(realpath -e -- "$1" 2>/dev/null) && p=$(realpath -e -- "$2" 2>/dev/null) || { echo "No such file or folder." >&2; exit 4; }; '
         'case "$p" in "$r"|"$r"/*) ;; *) echo "That is outside this folder." >&2; exit 3;; esac; ')
LIST_SCRIPT = (GUARD + '[ -d "$p" ] || { echo "That is not a folder." >&2; exit 5; }; '
               'find -P "$p" -mindepth 1 -maxdepth 1 -printf "%y\\t%s\\t%T@\\t%Y\\t%f\\n" | head -n 5001')
# Prints "<size> <start>" then the file from byte <start>. $3 = start (-1: the last $4 bytes), $5 = most bytes sent.
READ_SCRIPT = (GUARD + '[ -f "$p" ] || { echo "That is not a file." >&2; exit 5; }; s=$(stat -c %s -- "$p"); o=$3; '
               'if [ "$o" -lt 0 ] || [ "$o" -gt "$s" ]; then o=$(( s > $4 ? s - $4 : 0 )); fi; '
               'echo "$s $o"; tail -c +$((o+1)) -- "$p" | head -c "$5"')
DOWNLOAD_SCRIPT = (GUARD + '[ -f "$p" ] || { echo "That is not a file." >&2; exit 5; }; s=$(stat -c %s -- "$p"); '
                   '[ "$s" -le "$3" ] || { echo "That file is too large to download through the window." >&2; exit 6; }; '
                   'cat -- "$p"')
JOB_LOGS_SCRIPT = ('r=$(realpath -e -- "$1" 2>/dev/null) || { echo "The project folder was not found on Torch." >&2; exit 4; }; '
                   '[ -d "$r/logs" ] || { echo "The project has no logs folder." >&2; exit 5; }; '
                   'find -P "$r/logs" -maxdepth 2 -type f -name "*$2*" -printf "%s\\t%T@\\t%P\\n" | head -n 500')

MAX_VIEW_BYTES = 1024 * 1024          # a file opened from the top
TAIL_BYTES = 256 * 1024               # a log opened at the end
FOLLOW_BYTES = 512 * 1024             # the most new text fetched per follow step
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
REL_PATH_BAD = re.compile(r"[\x00-\x1f\x7f]")


def check_rel_path(rel: str) -> str:
    """A path relative to a starting folder: '' (the folder itself) or 'results/run1/SUMMARY.md'."""
    if not isinstance(rel, str) or len(rel) > 1024 or REL_PATH_BAD.search(rel) or rel.startswith("/"):
        raise CommandRefused("Not a valid path inside the folder: %r" % (rel,))
    parts = [part for part in rel.split("/") if part != ""]
    if any(part in (".", "..") for part in parts):
        raise CommandRefused("Not a valid path inside the folder: %r" % (rel,))
    return "/".join(parts)


def _project(root: str) -> str:
    _need(REMOTE_DIR_RE, root, "folder on Torch")
    if ".." in root.split("/"):
        raise CommandRefused("Not a valid folder on Torch: %r" % root)
    return root


def _script(script: str, *args: str) -> str:
    return "bash -c %s torchbench %s" % (shlex.quote(script), " ".join(shlex.quote(str(a)) for a in args))


def _target(root: str, rel: str) -> str:
    rel = check_rel_path(rel)
    return root + ("/" + rel if rel else "")


def list_dir(root: str, rel: str) -> RemoteCommand:
    root = _project(root)
    return _make("list_dir", _script(LIST_SCRIPT, root, _target(root, rel)),
                 "List the folder %s" % _target(root, rel), routine=False)


def read_file(root: str, rel: str, start: int = 0, following: bool = False) -> RemoteCommand:
    """start >= 0: from that byte. start = -1: the end of the file (for logs)."""
    root = _project(root)
    if not isinstance(start, int) or isinstance(start, bool) or start < -1 or start > 10 ** 15:
        raise CommandRefused("Not a valid position in the file: %r" % (start,))
    cap = FOLLOW_BYTES if following else (TAIL_BYTES if start < 0 else MAX_VIEW_BYTES)
    where = "the end of " if start < 0 else ("new lines of " if following else "")
    return _make("read_file", _script(READ_SCRIPT, root, _target(root, rel), start, TAIL_BYTES, cap),
                 "Read %s%s" % (where, _target(root, rel)), routine=following, binary=True, quiet_output=True)


def download_file(root: str, rel: str) -> RemoteCommand:
    root = _project(root)
    if not check_rel_path(rel):
        raise CommandRefused("Choose a file to download.")
    return _make("download_file", _script(DOWNLOAD_SCRIPT, root, _target(root, rel), MAX_DOWNLOAD_BYTES),
                 "Download %s" % _target(root, rel), routine=False, timeout=300, binary=True, quiet_output=True)


def job_logs(root: str, job_id: str) -> RemoteCommand:
    root = _project(root)
    _need(JOB_ID_RE, job_id, "job ID")
    return _make("job_logs", _script(JOB_LOGS_SCRIPT, root, job_id.split("_")[0]),
                 "Find the log files of job %s" % job_id, routine=False)


def terminal(remote_dir: str = "") -> RemoteCommand:
    """The start-up line of the built-in terminal: a login shell, opened in the project folder when there is one
    (otherwise wherever Torch puts you, normally your home folder)."""
    text = ""
    if remote_dir:
        text = "cd %s ; exec $SHELL -l" % shlex.quote(_project(remote_dir))
    return _make("terminal", text, "Open a terminal on the Torch login node", routine=False, timeout=0, interactive=True)


def all_names() -> List[str]:
    return ["probe", "queue", "history", "fairshare", "priority", "partitions", "quota", "job_detail", "disk_usage",
            "list_dir", "read_file", "download_file", "job_logs", "terminal"]
