"""The one config file (~/torchbench/config.json): everything user- or project-specific lives here.

Every value that can end up inside a command run on the cluster is validated against a strict pattern
here, and shell-quoted again where the command is built (commands.py).
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, List, Tuple

from . import paths

CONFIG_VERSION = 1
MODES = ("colleague", "owner")

NETID_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
REMOTE_DIR_RE = re.compile(r"^/[A-Za-z0-9._/-]{1,255}$")

DEFAULT_HELP_LINKS: List[Dict[str, str]] = [
    {
        "title": "Get a Torch account",
        "url": "https://services.rt.nyu.edu/docs/hpc/getting_started/HPC_Accounts/getting_and_renewing_an_account/",
        "note": "Request or renew your NYU HPC account. Faculty can request one directly; "
                "students and staff need a faculty sponsor.",
    },
    {
        "title": "Join a project so you can run jobs",
        "url": "https://projects.hpc.nyu.edu/",
        "note": "The HPC projects portal (open it on the NYU VPN). An account lets you sign in; "
                "a project allocation lets you run jobs.",
    },
    {
        "title": "Install the NYU VPN on a Mac",
        "url": "https://it.stern.nyu.edu/for-staffadmins/onboarding/articles/nyu-vpn/nyu-vpn-mac",
        "note": "Cisco Secure Client, server vpn.nyu.edu. Needed whenever you are off campus.",
    },
    {
        "title": "Set up Duo (NYU Multi-Factor Authentication)",
        "url": "https://www.nyu.edu/life/information-technology/accounts-and-access/access/mfa.html",
        "note": "Every Torch sign-in asks you to approve a Duo prompt on your phone.",
    },
    {
        "title": "Torch documentation",
        "url": "https://services.rt.nyu.edu/docs/hpc/getting_started/intro/",
        "note": "The official NYU guide to the cluster.",
    },
    {
        "title": "Ask NYU HPC for help",
        "url": "mailto:hpc@nyu.edu",
        "note": "hpc@nyu.edu. Include what you were doing and the lines from the Commands page; "
                "never send a password.",
    },
]

DEFAULTS: Dict[str, Any] = {
    "version": CONFIG_VERSION,
    "netid": "",
    "mode": "colleague",
    "ssh_alias": "torch",
    "remote_host": "login.torch.hpc.nyu.edu",
    "project_name": "",
    "remote_dir": "",
    "sbatch_account": "",
    "default_partition": "l40s_public",
    "gpu_partitions": ["l40s_public", "h200_public"],
    "cpu_partitions": ["cs"],
    "local_dir": "",
    "launchers": [],
    "help_links": DEFAULT_HELP_LINKS,
    "quota_warn_percent": 90,
    "history_days": 7,
}

# What a project owner can hand to a colleague: no identity, no mode.
SHAREABLE_KEYS = (
    "project_name", "remote_dir", "sbatch_account", "default_partition",
    "gpu_partitions", "cpu_partitions", "launchers", "help_links", "remote_host",
)


class ConfigError(ValueError):
    """Raised with a message a non-expert can act on."""


def defaults() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULTS)


def _check_partitions(value: Any, field: str) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(p, str) and NAME_RE.match(p) for p in value):
        raise ConfigError("%s must be a list of partition names (letters, digits, _ and -)." % field)
    return list(value)


def _check_links(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        raise ConfigError("help_links must be a list.")
    out = []
    for link in value:
        if not isinstance(link, dict):
            raise ConfigError("Each help link needs a title and a url.")
        title = str(link.get("title", "")).strip()
        url = str(link.get("url", "")).strip()
        if not title or not re.match(r"^(https://|mailto:)[^\s<>\"']+$", url):
            raise ConfigError("Help link %r needs a title and an https:// or mailto: address." % (title or url))
        out.append({"title": title[:120], "url": url[:500], "note": str(link.get("note", ""))[:400]})
    return out


def validate(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a clean config (defaults filled in) or raise ConfigError."""
    cfg = defaults()
    for key in cfg:
        if key in raw and raw[key] is not None:
            cfg[key] = raw[key]
    cfg["version"] = CONFIG_VERSION

    netid = str(cfg["netid"]).strip().lower()
    if not NETID_RE.match(netid):
        raise ConfigError("Your NetID looks like abc1234: lowercase letters, then digits.")
    cfg["netid"] = netid

    if cfg["mode"] not in MODES:
        raise ConfigError("mode must be one of: %s." % ", ".join(MODES))

    if not ALIAS_RE.match(str(cfg["ssh_alias"])):
        raise ConfigError("ssh_alias must be a simple name such as torch.")
    if not HOST_RE.match(str(cfg["remote_host"])):
        raise ConfigError("remote_host must be a host name such as login.torch.hpc.nyu.edu.")

    project = str(cfg["project_name"]).strip()
    if project and not NAME_RE.match(project):
        raise ConfigError("The project name can use letters, digits, dots, dashes and underscores.")
    cfg["project_name"] = project

    remote_dir = str(cfg["remote_dir"]).strip().rstrip("/")
    if remote_dir:
        if not REMOTE_DIR_RE.match(remote_dir) or ".." in remote_dir.split("/"):
            raise ConfigError("The project folder on Torch must be a full path such as /scratch/abc1234/my-project.")
    cfg["remote_dir"] = remote_dir

    account = str(cfg["sbatch_account"]).strip()
    if account and not NAME_RE.match(account):
        raise ConfigError("The SLURM account can use letters, digits, dashes and underscores.")
    cfg["sbatch_account"] = account

    cfg["gpu_partitions"] = _check_partitions(cfg["gpu_partitions"], "gpu_partitions")
    cfg["cpu_partitions"] = _check_partitions(cfg["cpu_partitions"], "cpu_partitions")
    if not NAME_RE.match(str(cfg["default_partition"])):
        raise ConfigError("default_partition must be a partition name.")

    cfg["local_dir"] = str(cfg["local_dir"]).strip()
    if not isinstance(cfg["launchers"], list):
        raise ConfigError("launchers must be a list.")
    cfg["help_links"] = _check_links(cfg["help_links"])

    try:
        warn = int(cfg["quota_warn_percent"])
        days = int(cfg["history_days"])
    except (TypeError, ValueError):
        raise ConfigError("quota_warn_percent and history_days must be whole numbers.")
    if not 50 <= warn <= 99:
        raise ConfigError("quota_warn_percent must be between 50 and 99.")
    if not 1 <= days <= 60:
        raise ConfigError("history_days must be between 1 and 60.")
    cfg["quota_warn_percent"], cfg["history_days"] = warn, days
    return cfg


def load() -> Tuple[Dict[str, Any], str]:
    """Return (config, problem). config is None-like ({}) when not set up yet."""
    path = paths.config_file()
    if not path.exists():
        return {}, ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ConfigError("The file does not hold a settings object.")
        return validate(raw), ""
    except (OSError, ValueError) as exc:
        return {}, "Could not read %s: %s" % (path, exc)


def save(raw: Dict[str, Any]) -> Dict[str, Any]:
    cfg = validate(raw)
    paths.ensure_dirs()
    paths.atomic_write(paths.config_file(), json.dumps(cfg, indent=2, sort_keys=True) + "\n")
    return cfg


def shareable(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """The project half of a config, safe to hand to a colleague (no NetID, no mode)."""
    out = {key: copy.deepcopy(cfg[key]) for key in SHAREABLE_KEYS if key in cfg}
    out["torchbench_project_file"] = 1
    # A colleague's scratch folder is their own: turn /scratch/<owner>/x into a template.
    netid = cfg.get("netid", "")
    if netid and out.get("remote_dir", "").startswith("/scratch/%s/" % netid):
        out["remote_dir_template"] = out.pop("remote_dir").replace("/scratch/%s/" % netid, "/scratch/{netid}/", 1)
    return out


def all_partitions(cfg: Dict[str, Any]) -> List[str]:
    seen: List[str] = []
    for p in list(cfg.get("gpu_partitions", [])) + list(cfg.get("cpu_partitions", [])):
        if p not in seen:
            seen.append(p)
    return seen
