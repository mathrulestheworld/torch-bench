"""The built-in terminal: a real shell on the Torch login node, over the shared login.

This is the one deliberate escape hatch from the read-only rule: whatever the person types here,
they do as themselves. Torch Bench's part is limited to:
  * starting `ssh -tt` (BatchMode, ControlMaster=no: it can only ride the open login, never prompt);
  * passing keystrokes in and screen output out, unchanged;
  * keeping a record of what appeared on screen. Only what the shell shows is recorded, never
    keystrokes, so anything typed at a hidden prompt is not in the record; token-shaped text and
    NAME=secret assignments are blanked as well.
Torch Bench never types into the terminal itself.
"""
from __future__ import annotations

import errno
import fcntl
import os
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from typing import Any, Dict, List, Optional

from . import paths
from .cmdlog import CommandLog
from .login import ANSI_RE
from .redact import Redactor

KEEP_BYTES = 512 * 1024           # of screen output held for the window to (re)attach to
MAX_WRITE = 64 * 1024
CONSOLE_TAIL_CHARS = 60_000       # of the record shown in the activity log when the session ends


class TerminalSession:
    def __init__(self, argv: List[str], log: CommandLog, redactor: Redactor, shown_as: str) -> None:
        self.argv, self.log, self.redactor, self.shown_as = argv, log, redactor, shown_as
        self._cond = threading.Condition()
        self._buf = bytearray()
        self._base = 0                 # stream offset of _buf[0]
        self._fd: Optional[int] = None
        self._proc: Optional[subprocess.Popen] = None
        self._line = ""                # an unfinished line waiting to be recorded
        self._record = None
        self.record_path = ""
        self.closed = False
        self.exit_code: Optional[int] = None
        self.started = 0.0

    # -- lifecycle ----------------------------------------------------------------------------
    def start(self, rows: int = 30, cols: int = 110) -> None:
        import pty
        master, slave = pty.openpty()
        self._set_size(slave, rows, cols)
        helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ptyexec.py")
        try:
            self._proc = subprocess.Popen([sys.executable, helper, os.ttyname(slave)] + self.argv,
                                          stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                          close_fds=True, env=dict(os.environ, TERM="xterm-256color"))
        finally:
            os.close(slave)
        self._fd = master
        self.started = time.time()
        folder = paths.logs_dir() / "terminal"
        folder.mkdir(parents=True, exist_ok=True)
        self.record_path = str(folder / ("session_%s.log" % time.strftime("%Y%m%d-%H%M%S")))
        self._record = open(self.record_path, "a", encoding="utf-8")
        os.chmod(self.record_path, 0o600)
        self._record.write("# Torch Bench terminal session, %s\n# %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), self.shown_as))
        self.log.record(self.shown_as, None, "What appears in the terminal is being recorded in %s\n" % self.record_path,
                        label="Terminal opened on the Torch login node", routine=False, where="torch", tag="started")
        threading.Thread(target=self._reader, name="torchbench-terminal", daemon=True).start()

    def _reader(self) -> None:
        fd = self._fd
        while fd is not None:
            try:
                chunk = os.read(fd, 8192)
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                break
            if not chunk:
                break
            with self._cond:
                self._buf += chunk
                if len(self._buf) > KEEP_BYTES:
                    drop = len(self._buf) - KEEP_BYTES
                    del self._buf[:drop]
                    self._base += drop
                self._cond.notify_all()
            self._record_text(chunk.decode("utf-8", "replace"))
        self._finish()

    def _record_text(self, text: str) -> None:
        text = self._line + ANSI_RE.sub("", text)
        while "\x08" in text:                  # apply backspaces so corrected typing reads as it looked
            i = text.index("\x08")
            text = text[:max(i - 1, 0)] + text[i + 1:] if i and text[i - 1] != "\n" else text[:i] + text[i + 1:]
        text = text.replace("\x07", "")
        lines = text.split("\n")
        self._line = lines.pop()
        if len(self._line) > 20_000:           # a very long unbroken line: record it in pieces
            lines.append(self._line)
            self._line = ""
        if lines and self._record:
            try:
                self._record.write(self.redactor.clean("\n".join(lines)) + "\n")
                self._record.flush()
            except (OSError, ValueError):
                pass

    def _finish(self) -> None:
        code: Optional[int] = None
        if self._proc is not None:
            try:
                code = self._proc.wait(timeout=10)
                if code < 0:
                    code = 128 - code
            except (subprocess.TimeoutExpired, OSError):
                code = None
        with self._cond:
            if self.closed:
                return
            self.closed, self.exit_code = True, code
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            self._cond.notify_all()
        tail = ""
        if self._record:
            try:
                if self._line:
                    self._record.write(self.redactor.clean(self._line) + "\n")
                self._record.close()
                with open(self.record_path, encoding="utf-8", errors="replace") as f:
                    tail = f.read()[-CONSOLE_TAIL_CHARS:]
            except (OSError, ValueError):
                pass
            self._record = None
        self.log.record(self.shown_as, code, tail, "", time.time() - self.started,
                        label="Terminal closed (full record: %s)" % self.record_path, routine=False, where="torch")

    def close(self) -> None:
        """Hang up, the way closing a terminal window does; insist after two seconds."""
        proc = self._proc
        if proc is None or self.closed:
            return
        try:
            os.killpg(proc.pid, signal.SIGHUP)
        except OSError:
            try:
                proc.terminate()
            except OSError:
                pass

        def insist() -> None:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        timer = threading.Timer(2.0, insist)
        timer.daemon = True
        timer.start()

    # -- the window's side --------------------------------------------------------------------
    def read(self, since: int, wait: float = 20.0) -> Dict[str, Any]:
        """Screen output from stream offset `since` on. Waits up to `wait` seconds for something new."""
        deadline = time.time() + wait
        with self._cond:
            while not self.closed and self._base + len(self._buf) <= since:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            start = max(since, self._base)
            data = bytes(self._buf[start - self._base:])
            return {"data": data, "next": self._base + len(self._buf), "skipped": start > since,
                    "closed": self.closed, "exit": self.exit_code}

    def write(self, text: str) -> bool:
        data = text.encode("utf-8")
        if len(data) > MAX_WRITE:
            return False
        with self._cond:
            fd = self._fd
        if fd is None or self.closed:
            return False
        try:
            os.write(fd, data)
            return True
        except OSError:
            return False

    @staticmethod
    def _set_size(fd: int, rows: int, cols: int) -> None:
        rows, cols = max(2, min(int(rows), 500)), max(2, min(int(cols), 1000))
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def resize(self, rows: int, cols: int) -> bool:
        with self._cond:
            fd = self._fd
        if fd is None or self.closed:
            return False
        try:
            self._set_size(fd, rows, cols)       # the kernel tells ssh, which tells Torch
            return True
        except (OSError, ValueError, struct.error):
            return False
