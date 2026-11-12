"""Turn the scheduler's one-word reasons into sentences a newcomer can act on."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

LOW_FAIRSHARE = 0.25


def fairshare_words(value: Optional[float]) -> Dict[str, str]:
    """Plain words for the fair-share score (0 to 1; higher means you go sooner)."""
    if value is None:
        return {"level": "unknown", "title": "Not available", "detail": "Torch did not report a fair-share score."}
    if value >= 0.5:
        return {"level": "good", "title": "Good standing",
                "detail": "You have used little compute lately, so your jobs are favoured in the queue."}
    if value >= LOW_FAIRSHARE:
        return {"level": "middle", "title": "Middle of the pack",
                "detail": "Your recent use is about average. Waits depend mostly on how busy the GPUs are."}
    return {"level": "low", "title": "Low for now",
            "detail": "You have used a lot of compute recently, so other people's jobs go first. "
                      "The score recovers by itself as your recent usage ages out."}


def explain(reason: str, fairshare: Optional[float] = None, typical_wait: str = "",
            array_limit: Optional[int] = None) -> Dict[str, Any]:
    """title: one short line. detail: what it means and what (if anything) to do.
    stuck=True means the job will not start by itself."""
    r = (reason or "").strip()
    wait = (" Jobs here have typically started within about %s lately (an estimate)." % typical_wait) if typical_wait else ""
    stuck = False

    if r in ("", "None"):
        title, detail = "About to be scheduled", "The scheduler has not looked at this job yet. It usually takes a minute."
    elif r == "Priority":
        if fairshare is not None and fairshare < LOW_FAIRSHARE:
            title = "Other jobs go first for now"
            detail = ("You've used a lot of GPU hours recently, so other jobs go first "
                      "(your fair-share score is %.2f on a scale of 0 to 1). Nothing is wrong and nothing needs doing."
                      % fairshare) + wait
        else:
            title = "In line behind other jobs"
            detail = "Jobs ahead of this one start first. Nothing is wrong and nothing needs doing." + wait
    elif r == "Resources":
        title = "Next in line, waiting for a free machine"
        detail = "This job is at the front of the queue and starts as soon as the GPUs or CPUs it asked for are free." + wait
    elif r == "Dependency":
        title = "Waiting for an earlier job to finish"
        detail = "This job was set up to start only after another job ends. It moves on by itself."
    elif r == "DependencyNeverSatisfied":
        title, stuck = "Will not start: the job it depends on did not succeed", True
        detail = ("This job was waiting for an earlier job that failed or was cancelled, so it can never start. "
                  "Look at the earlier job's log, then cancel this one or resubmit.")
    elif r == "JobArrayTaskLimit":
        title = "Waiting its turn in the batch"
        detail = ("The batch runs only %s tasks at once, by design. The rest start as earlier ones finish."
                  % (array_limit if array_limit else "a few"))
    elif r == "BeginTime":
        title, detail = "Scheduled to start later", "The job asked not to start before a set time."
    elif r == "JobHeldUser":
        title, stuck = "On hold", True
        detail = "This job was put on hold from your account and stays there until it is released."
    elif r in ("JobHeldAdmin", "launch failed requeued held"):
        title, stuck = "On hold by the cluster", True
        detail = "An administrator or the system put this job on hold. Email hpc@nyu.edu with the job ID."
    elif re.match(r"^QOS(Max|Grp|MinGRES)", r) or r == "QOSJobLimit":
        title = "At a usage limit"
        detail = ("You (or your group) are already using as much as this queue allows at once (%s). "
                  "This job starts when a running job finishes." % r)
    elif re.match(r"^Assoc(Max|Grp)", r) or r == "AssociationJobLimit":
        title = "Project at a usage limit"
        detail = ("Your project account has reached a limit (%s). This job starts when the project's "
                  "running jobs finish, or the limit resets." % r)
    elif r.startswith("ReqNodeNotAvail") or r in ("NodeDown", "Reserved for maintenance"):
        title = "The machines it needs are unavailable"
        detail = "Often scheduled maintenance. The job starts when the machines come back. NYU HPC announces maintenance by email."
    elif r in ("PartitionDown", "PartitionInactive"):
        title, detail = "This queue is closed right now", "The partition is not accepting work at the moment, usually for maintenance."
    elif r in ("PartitionTimeLimit", "PartitionNodeLimit", "BadConstraints", "InvalidAccount", "InvalidQOS",
               "PartitionConfig", "QOSNotAllowed"):
        title, stuck = "Will not start as submitted", True
        detail = ("The job asks for something this queue does not allow (%s). It needs to be cancelled and "
                  "resubmitted with different settings." % r)
    elif r == "Reservation":
        title, detail = "Waiting for a reservation", "The job is tied to a reserved time slot that has not begun."
    elif r in ("Licenses", "Burst_buffer_resources"):
        title, detail = "Waiting for a shared resource", "The job needs a shared resource (%s) that is busy." % r
    else:
        title = "Waiting"
        detail = "The scheduler's reason is “%s”. If this lasts, send the job ID to hpc@nyu.edu." % r
    return {"title": title, "detail": detail, "stuck": stuck, "code": r}
