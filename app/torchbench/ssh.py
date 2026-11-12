"""Run one registered command on Torch over the shared login. Never prompts, never hangs.

Works the way the research repo's scripts/torch.sh does:
  1. `ssh -O check <alias>`: is a shared login (ControlMaster) open? If not, nothing runs.
  2. `ssh -o BatchMode=yes <alias> <command>`: BatchMode means ssh can never ask for anything.
  3. The command and its output go to the transparency log.

Extra belts: ControlMaster=no (a status command can never try to *become* a login),
stdin closed, a connect timeout, and a hard timeout that kills the process.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

from .cmdlog import CommandLog
from .commands import RemoteCommand

NOT_CONNECTED = "No shared Torch login is open. Sign in from the Torch Bench window (or run `ssh torch` in Terminal)."


def ssh_program() -> List[str]:
    """The ssh to use. TORCHBENCH_SSH lets tests and demo mode substitute a stand-in."""
    override = os.environ.get("TORCHBENCH_SSH")
    if override:
        return [sys.executable, override] if override.endswith(".py") else [override]
    return ["/usr/bin/ssh"] if os.path.exists("/usr/bin/ssh") else ["ssh"]


@dataclass
class Result:
    exit_code: Optional[int]      # None when nothing ran
    stdout: str = ""
    stderr: str = ""
    seconds: float = 0.0
    not_connected: bool = False
    timed_out: bool = False
    data: bytes = b""             # file bytes, for commands marked binary

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def problem(self) -> str:
        if self.not_connected:
            return NOT_CONNECTED
        if self.timed_out:
            return "Torch did not answer in time."
        if self.exit_code != 0:
            return (self.stderr.strip() or self.stdout.strip() or "The command failed.")[:500]
        return ""


class SSH:
    def __init__(self, alias: str, log: CommandLog) -> None:
        self.alias = alias
        self.log = log
        self._lock = threading.Lock()        # one command at a time: be gentle on the login node
        self._last_connected: Optional[bool] = None

    # -- the shared login -------------------------------------------------------------------
    def check(self) -> bool:
        """True when a shared login is open. Runs locally; touches nothing on Torch."""
        argv = ssh_program() + ["-O", "check", self.alias]
        start = time.time()
        try:
            proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=8, universal_newlines=True, errors="replace")
            connected, code, err = proc.returncode == 0, proc.returncode, proc.stderr
        except subprocess.TimeoutExpired:
            connected, code, err = False, 124, "ssh -O check did not answer"
        except OSError as exc:
            connected, code, err = False, 127, "could not run ssh: %s" % exc
        changed = connected != self._last_connected
        self._last_connected = connected
        # Checked every refresh, so it stays in memory; only a change of state is written to disk.
        self.log.record("ssh -O check %s" % self.alias, code, "", err, time.time() - start,
                        label="Is a shared Torch login open? %s" % ("Yes" if connected else "No"),
                        routine=True, where="mac", to_disk=changed)
        return connected

    def close_login(self) -> bool:
        """Close the shared login (ssh -O exit). Local only."""
        argv = ssh_program() + ["-O", "exit", self.alias]
        start = time.time()
        try:
            proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=8, universal_newlines=True, errors="replace")
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except (subprocess.TimeoutExpired, OSError) as exc:
            code, out, err = 124, "", str(exc)
        self.log.record("ssh -O exit %s" % self.alias, code, out, err, time.time() - start,
                        label="Close the shared Torch login", routine=False, where="mac")
        self._last_connected = None
        return code == 0

    # -- running commands ---------------------------------------------------------------------
    def _argv(self, command: RemoteCommand) -> List[str]:
        return ssh_program() + ["-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ConnectTimeout=10",
                                self.alias, command.text]

    def _vet(self, command: RemoteCommand) -> None:
        if not isinstance(command, RemoteCommand) or not command.from_registry():
            raise PermissionError("Torch Bench only runs commands built by its registry (commands.py).")
        if command.mutating:
            raise PermissionError("This build runs read-only commands only; %r changes things on Torch." % command.name)

    def run(self, command: RemoteCommand) -> Result:
        self._vet(command)
        if command.interactive:
            raise PermissionError("The terminal's start-up line runs only inside the built-in terminal.")
        with self._lock:
            if not self.check():
                self.log.record(command.text, None, "", NOT_CONNECTED, 0.0, label=command.label,
                                routine=command.routine, where="torch")
                return Result(exit_code=None, stderr=NOT_CONNECTED, not_connected=True)
            start = time.time()
            result = Result(exit_code=None)
            try:
                proc = subprocess.run(self._argv(command), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, timeout=command.timeout)
                err = proc.stderr.decode("utf-8", "replace")
                if command.binary:
                    result = Result(exit_code=proc.returncode, stderr=err, data=proc.stdout)
                else:
                    result = Result(exit_code=proc.returncode, stdout=proc.stdout.decode("utf-8", "replace"), stderr=err)
            except subprocess.TimeoutExpired as exc:
                out = (exc.stdout or b"").decode("utf-8", "replace") if not command.binary else ""
                result = Result(exit_code=124, stdout=out,
                                stderr="Stopped after %d s without an answer." % command.timeout, timed_out=True)
            except OSError as exc:
                result = Result(exit_code=127, stderr="could not run ssh: %s" % exc)
            result.seconds = time.time() - start
            logged = result.stdout
            if command.quiet_output and result.exit_code == 0:
                size = len(result.data) if command.binary else len(result.stdout.encode("utf-8"))
                logged = "[%d bytes of file contents, shown in the window and not copied into this log]\n" % size
            self.log.record(command.text, result.exit_code, logged, result.stderr, result.seconds,
                            label=command.label, routine=command.routine, where="torch")
            return result

    def terminal_argv(self, command: RemoteCommand) -> Optional[List[str]]:
        """The ssh command line for the built-in terminal, or None when no shared login is open.
        -tt asks Torch for a terminal; BatchMode and ControlMaster=no mean it can only ride the open login."""
        self._vet(command)
        if not command.interactive:
            raise PermissionError("Only the terminal's start-up line can open a terminal.")
        if not self.check():
            return None
        argv = ssh_program() + ["-tt", "-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ConnectTimeout=10",
                                self.alias]
        return argv + ([command.text] if command.text else [])
