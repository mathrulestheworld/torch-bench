"""The `torchbench` command.

  torchbench            start Torch Bench (if needed) and open it in your browser
  torchbench stop       stop it
  torchbench status     is it running? is a Torch login open?
  torchbench demo       try the window with made-up data; touches neither Torch nor your settings
  torchbench record-fixtures [folder]
                        save Torch's real answers to the read-only status commands (for the tests)
  torchbench version
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__, paths


def _running() -> Optional[Dict[str, Any]]:
    try:
        info = json.loads((paths.run_dir() / "server.json").read_text())
        os.kill(int(info["pid"]), 0)
        with urllib.request.urlopen(info["url"], timeout=3) as resp:
            if resp.status == 200:
                return info
    except Exception:
        return None
    return None


def _open(url: str) -> None:
    if os.environ.get("TORCHBENCH_NO_BROWSER") == "1":
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass


def cmd_start(foreground: bool = False) -> int:
    info = _running()
    if info:
        print("Torch Bench is already running: %s" % info["url"])
        _open(info["url"])
        return 0
    paths.ensure_dirs()
    if foreground:
        from .server import serve
        serve(on_ready=lambda i: (print("Torch Bench is running: %s  (Ctrl-C to stop)" % i["url"]), _open(i["url"])))
        return 0
    log = open(paths.logs_dir() / "server.log", "a")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get("PYTHONPATH", "")
    subprocess.Popen([sys.executable, "-m", "torchbench", "serve"], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     start_new_session=True, env=env)
    for _ in range(50):
        time.sleep(0.2)
        info = _running()
        if info:
            print("Torch Bench is running: %s" % info["url"])
            print("Stop it with: torchbench stop")
            _open(info["url"])
            return 0
    print("Torch Bench did not start. See %s" % (paths.logs_dir() / "server.log"), file=sys.stderr)
    return 1


def cmd_stop() -> int:
    try:
        info = json.loads((paths.run_dir() / "server.json").read_text())
        os.kill(int(info["pid"]), signal.SIGTERM)
        print("Torch Bench stopped. (Your Torch login, if open, stays open; `ssh -O exit torch` closes it.)")
    except (OSError, ValueError, KeyError):
        print("Torch Bench is not running.")
    return 0


def cmd_status() -> int:
    from . import config
    from .cmdlog import CommandLog
    from .ssh import SSH
    info = _running()
    print("Torch Bench %s: %s" % (__version__, ("running at %s" % info["url"]) if info else "not running"))
    cfg, problem = config.load()
    if problem:
        print(problem)
    if not cfg:
        print("Not set up yet. Run `torchbench` and follow the setup page.")
        return 0
    connected = SSH(cfg["ssh_alias"], CommandLog(persist=False)).check()
    print("NetID %s, role %s. Torch login: %s" % (cfg["netid"], cfg["mode"], "open" if connected else "not open"))
    return 0


def cmd_demo() -> int:
    """Made-up data served by a stand-in for ssh. Nothing leaves this Mac."""
    repo = Path(__file__).resolve().parent.parent.parent
    fake = repo / "tests" / "fake_ssh.py"
    if not fake.exists():
        print("Demo data is not installed (tests/fake_ssh.py is missing).", file=sys.stderr)
        return 1
    home = Path(tempfile.mkdtemp(prefix="torchbench-demo-"))
    os.environ.update({
        "TORCHBENCH_HOME": str(home), "TORCHBENCH_SSH": str(fake), "TORCHBENCH_DEMO": "1",
        "TORCHBENCH_SSH_CONFIG": str(home / "ssh_config"), "FAKE_SSH_STATE": str(home / "fake_ssh_state"),
        "FAKE_SSH_FIXTURES": str(repo / "tests" / "fixtures" / "demo"),
    })
    from .server import serve
    print("Demo mode: made-up data, nothing is sent to Torch, your real settings are untouched.")
    serve(port=8652, on_ready=lambda i: (print("Open %s  (Ctrl-C to stop)" % i["url"]), _open(i["url"])))
    return 0


def cmd_record_fixtures(folder: Optional[str]) -> int:
    """Run each read-only status command once over the open login and save the raw answers."""
    import datetime
    from . import commands, config
    from .cmdlog import CommandLog
    from .ssh import SSH
    cfg, problem = config.load()
    if not cfg:
        print(problem or "Set up Torch Bench first (run `torchbench`).", file=sys.stderr)
        return 1
    ssh = SSH(cfg["ssh_alias"], CommandLog())
    if not ssh.check():
        print("No shared Torch login is open. Sign in first, then re-run.", file=sys.stderr)
        return 2
    out = Path(folder).expanduser() if folder else paths.home() / "fixtures" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    since = (datetime.date.today() - datetime.timedelta(days=cfg["history_days"])).isoformat()
    todo = [
        ("squeue", commands.queue()), ("sacct", commands.history(since)),
        ("sshare", commands.fairshare(cfg["netid"])), ("sprio", commands.priority(cfg["netid"])),
        ("sinfo", commands.partitions(config.all_partitions(cfg))), ("myquota", commands.quota()),
    ]
    index = []
    for name, command in todo:
        result = ssh.run(command)
        (out / (name + ".txt")).write_text(result.stdout, encoding="utf-8")
        if result.stderr.strip():
            (out / (name + ".stderr.txt")).write_text(result.stderr, encoding="utf-8")
        index.append({"file": name + ".txt", "command": command.text, "exit": result.exit_code})
        print("%-8s exit %s  %d lines   %s" % (name, result.exit_code, len(result.stdout.splitlines()), command.text))
    (out / "INDEX.json").write_text(json.dumps({"recorded": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                "netid": cfg["netid"], "commands": index}, indent=2))
    print("\nSaved to %s\nThese hold your job names and NetID. Look them over before adding them to the repo "
          "(tests/fixtures/real/)." % out)
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "start"
    if command in ("start", "open"):
        return cmd_start(foreground="--foreground" in args)
    if command == "serve":
        from .server import serve
        serve()
        return 0
    if command == "stop":
        return cmd_stop()
    if command == "status":
        return cmd_status()
    if command == "demo":
        return cmd_demo()
    if command == "record-fixtures":
        return cmd_record_fixtures(args[1] if len(args) > 1 else None)
    if command in ("version", "--version", "-V"):
        print("Torch Bench %s" % __version__)
        return 0
    print(__doc__.strip())
    return 0 if command in ("help", "--help", "-h") else 64


if __name__ == "__main__":
    sys.exit(main())
