"""The registry of commands Torch Bench may run on the cluster.

This file is the encoded form of the safety rules:
  * The SSH layer (ssh.py) runs only RemoteCommand objects, and only this module can make them.
    There is no function anywhere that takes a free-form string and runs it on Torch.
  * Every command here is a cheap queue/status/file lookup: nothing that computes on the login node.
  * Every argument is validated against a strict pattern and then shell-quoted.
  * M0 registers read-only commands only. A command marked mutating is refused by the SSH layer
    unless it is run through the explicit confirmation path that later milestones add
    (push, pull, registered launchers, guarded scancel, the reviewed cleanup flow).
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
    label: str           # plain-language description shown in the Commands page
    routine: bool        # a periodic status check (hidden by default in the Commands page)
    mutating: bool       # changes something on the cluster
    timeout: int         # seconds
    key: object = None   # proof that the registry built it

    def from_registry(self) -> bool:
        return self.key is _REGISTRY_KEY


def _make(name: str, text: str, label: str, routine: bool = True, timeout: int = 30) -> RemoteCommand:
    return RemoteCommand(name=name, text=text, label=label, routine=routine, mutating=False,
                         timeout=timeout, key=_REGISTRY_KEY)


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


def all_names() -> List[str]:
    return ["probe", "queue", "history", "fairshare", "priority", "partitions", "quota", "job_detail", "disk_usage"]
