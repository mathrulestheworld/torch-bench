"""The guided sign-in: Torch Bench starts `ssh <alias>` in a terminal it controls and walks the
person through each step in the window, instead of sending them to Terminal.

What this does and does not do (safety rule 1):
  * It starts ssh, reads what ssh prints, opens the Microsoft sign-in page and shows the one-time code.
  * The person does the signing in: they enter the code, their NetID password and approve Duo
    themselves, in Microsoft's page and on their phone.
  * Nothing they type is stored. Text typed into a prompt here goes straight to ssh and is kept
    only in memory, only so it can be blanked out of logs.
  * Anything ssh prints that is not recognised is shown as-is with a reply box, so an unexpected
    prompt never strands anyone.

Step detection parses what ssh prints. The patterns below follow NYU's documented Torch prompt
("Authenticate with PIN ... at https://microsoft.com/devicelogin and press ENTER") and must be
confirmed against a recorded real sign-in (see README: "Recording a real sign-in").
"""
from __future__ import annotations

import errno
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from . import paths
from .cmdlog import CommandLog
from .redact import Redactor
from .ssh import SSH, ssh_program

MARKER = "TORCHBENCH_LOGIN_OK"
MAX_TRANSCRIPT = 64_000
SESSION_LIMIT_SECONDS = 20 * 60       # a device code expires well before this
QUIET_SECONDS = 1.5                   # no output for this long + an unfinished line = a prompt

# Only Microsoft's own sign-in pages are opened automatically.
TRUSTED_LOGIN_HOSTS = ("microsoft.com", "www.microsoft.com", "login.microsoftonline.com",
                       "login.microsoft.com", "aka.ms")

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-_]|\r")
# The code group is case-sensitive on purpose (capitals and digits), so ordinary words never match.
DEVICE_PATTERNS = [
    # Torch, per NYU's docs:
    #   "(abc123@log-1) Authenticate with PIN BUUM5YHJX at https://microsoft.com/devicelogin and press ENTER."
    re.compile(r"(?i:\bPIN)\s+(?P<code>[A-Z0-9-]{6,12})\s+(?i:at)\s+(?P<url>https://\S+)"),
    # Microsoft's generic wording:
    #   "... open the page https://microsoft.com/devicelogin and enter the code ABCD12345 to authenticate."
    re.compile(r"(?i:open the page)\s+(?P<url>https://\S+)\s+(?i:and enter the code)\s+(?P<code>[A-Z0-9-]{6,12})\b"),
    re.compile(r"(?P<url>https://\S*(?i:device)\S*)(?s:.{0,80}?)(?i:\bcode)[:\s]+(?P<code>[A-Z0-9-]{6,12})\b"),
]
PRESS_ENTER_RE = re.compile(r"(?i)press\s+(enter|return)")
HOSTKEY_RE = re.compile(r"(?i)are you sure you want to continue connecting")
FINGERPRINT_RE = re.compile(r"(SHA256:[A-Za-z0-9+/=]+)")
PASSWORD_RE = re.compile(r"(?i)(pass(word|phrase)|verification code)[^\n]{0,60}:\s*$")
DUO_RE = re.compile(r"(?i)\bduo\b|two-factor|passcode or option")
NETWORK_ERRORS_RE = re.compile(
    r"(?i)could not resolve hostname|operation timed out|connection timed out|no route to host|"
    r"network is unreachable|connection refused|connection reset")
DENIED_RE = re.compile(r"(?i)permission denied|authentication failed|too many authentication failures")

ENDED = ("connected", "failed", "vpn", "cancelled")


def _default_open(url: str) -> bool:
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def _default_copy(text: str) -> bool:
    try:
        proc = subprocess.run(["/usr/bin/pbcopy"], input=text.encode("utf-8"), timeout=5)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def reachable(host: str, port: int = 22, timeout: float = 5.0) -> bool:
    """Can this Mac reach the login host at all? (If not: almost always the VPN.)"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def trusted_login_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in TRUSTED_LOGIN_HOSTS


class LoginSession:
    """One guided sign-in. Steps: starting, waiting, hostkey, device, duo, password, prompt,
    connected, failed, vpn, cancelled."""

    def __init__(self, ssh: SSH, host: str, log: CommandLog, redactor: Redactor,
                 opener: Callable[[str], bool] = _default_open,
                 copier: Callable[[str], bool] = _default_copy,
                 check_reachable: Callable[[str], bool] = reachable) -> None:
        self.ssh, self.host, self.log, self.redactor = ssh, host, log, redactor
        self._open, self._copy, self._reachable = opener, copier, check_reachable
        self._lock = threading.RLock()
        self._fd: Optional[int] = None
        self._proc: Optional[subprocess.Popen] = None
        self._raw = ""              # everything ssh printed (ANSI stripped), capped
        self._seg = 0               # where the text after the last thing the person did begins
        self._last_output = 0.0
        self._started = 0.0
        self._marker_seen = False
        self._handled_codes: List[str] = []
        self._stopped = False       # cancelled or timed out: the reader thread must not relabel the outcome
        self.step = "idle"
        self.message = ""
        self.url = ""
        self.code = ""
        self.url_trusted = False
        self.needs_enter = False
        self.browser_opened = False
        self.copied = False
        self.fingerprint = ""
        self.exit_code: Optional[int] = None

    # -- lifecycle ----------------------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self.step not in ("idle",) + ENDED:
                return
            self.step, self.message = "starting", ""
            self._started = self._last_output = time.time()
        if self.ssh.check():
            self._finish("connected", "A shared Torch login is already open.")
            return
        if not self._reachable(self.host):
            self._finish("vpn", "This Mac cannot reach %s." % self.host)
            return
        import pty  # imported here so the module loads anywhere; a pty needs macOS/Linux
        argv = ssh_program() + ["-o", "ConnectTimeout=20", self.ssh.alias, "echo", MARKER]
        self.log.record("ssh %s  (guided sign-in, in a terminal Torch Bench controls)" % self.ssh.alias, None,
                        label="Start the Torch sign-in", routine=False, where="mac", tag="started")
        master, slave = pty.openpty()
        helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ptyexec.py")
        try:
            proc = subprocess.Popen([sys.executable, helper, os.ttyname(slave)] + argv,
                                    stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                    close_fds=True, env=dict(os.environ, TERM="dumb"))
        except OSError as exc:
            os.close(master)
            os.close(slave)
            self._finish("failed", "Could not start ssh on this Mac (%s)." % exc)
            return
        os.close(slave)
        pid, fd = proc, master
        with self._lock:
            self._proc, self._fd = pid, fd
            self.step = "waiting"
            self.message = "Contacting Torch"
        threading.Thread(target=self._reader, name="torchbench-login", daemon=True).start()

    def _reader(self) -> None:
        fd = self._fd
        while fd is not None:
            try:
                chunk = os.read(fd, 4096)
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                break            # EIO: ssh closed its side
            if not chunk:
                break
            self._feed(chunk.decode("utf-8", "replace"))
        self._on_exit()

    def _feed(self, text: str) -> None:
        text = ANSI_RE.sub("", text)
        with self._lock:
            self._raw += text
            if len(self._raw) > MAX_TRANSCRIPT:
                drop = len(self._raw) - MAX_TRANSCRIPT
                self._raw = self._raw[drop:]
                self._seg = max(0, self._seg - drop)
            self._last_output = time.time()
            self._analyse()

    def _analyse(self) -> None:
        """Decide which step the person is on from what ssh printed since they last acted."""
        seg = self._raw[self._seg:]
        if MARKER in seg:
            self._marker_seen = True
            self.step, self.message = "waiting", "Signed in. Finishing up"
            return
        if self.step in ENDED:
            return
        for pattern in DEVICE_PATTERNS:
            m = pattern.search(seg)
            if m:
                code, url = m.group("code"), m.group("url").rstrip(".,;)")
                self.step, self.url, self.code = "device", url, code
                self.url_trusted = trusted_login_url(url)
                self.needs_enter = bool(PRESS_ENTER_RE.search(seg))
                if code not in self._handled_codes:
                    self._handled_codes.append(code)
                    self.redactor.remember(code)
                    self.copied = self._copy(code)
                    self.browser_opened = self._open(url) if self.url_trusted else False
                return
        if HOSTKEY_RE.search(seg):
            fp = FINGERPRINT_RE.search(seg)
            self.step, self.fingerprint = "hostkey", fp.group(1) if fp else ""
            return
        if PASSWORD_RE.search(seg):
            self.step = "password"
            return
        if DUO_RE.search(seg):
            self.step = "duo"
            return

    def _on_exit(self) -> None:
        code: Optional[int] = None
        if self._proc is not None:
            try:
                code = self._proc.wait(timeout=10)
                if code < 0:
                    code = 128 - code
            except (subprocess.TimeoutExpired, OSError):
                code = None
        with self._lock:
            self.exit_code = code
            self._close_fd()
            if self._stopped:
                return
            text = self._raw
        if self._marker_seen and self.ssh.check():
            self._finish("connected", "Signed in to Torch.")
        elif self._marker_seen:
            self._finish("failed", "Torch accepted the sign-in, but the shared login did not stay open. "
                                   "Check the connection settings step, then try again.")
        elif NETWORK_ERRORS_RE.search(text):
            self._finish("vpn", "The connection to %s dropped or could not be made." % self.host)
        elif DENIED_RE.search(text):
            self._finish("failed", "Torch did not accept the sign-in. Codes expire after a few minutes: "
                                   "try again, and check that your Torch account is active.")
        else:
            self._finish("failed", "The sign-in stopped before it finished.")

    def _finish(self, step: str, message: str) -> None:
        with self._lock:
            self.step, self.message = step, message
        self.log.record("ssh %s  (guided sign-in)" % self.ssh.alias, self.exit_code if step != "connected" else 0,
                        "", "" if step == "connected" else message,
                        time.time() - self._started, label="Torch sign-in: %s" % message, routine=False, where="mac")

    def _close_fd(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    # -- things the person does ---------------------------------------------------------------
    def send(self, text: str, secret: bool = True) -> bool:
        """Pass one line the person typed (or an empty line for Enter) straight to ssh."""
        with self._lock:
            if self._fd is None or self.step in ENDED:
                return False
            if secret and text:
                self.redactor.remember(text)
            try:
                os.write(self._fd, text.encode("utf-8") + b"\n")
            except OSError:
                return False
            self._seg = len(self._raw)
            self._last_output = time.time()
            self.step, self.message = "waiting", "Waiting for Torch"
            return True

    def cancel(self, step: str = "cancelled", message: str = "Sign-in cancelled.") -> None:
        with self._lock:
            if self.step in ENDED or self.step == "idle":
                return
            self._stopped = True
            self.step, self.message = step, message
            proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
        self.log.record("ssh %s  (guided sign-in)" % self.ssh.alias, None, "", message, time.time() - self._started,
                        label="Torch sign-in: %s" % message, routine=False, where="mac", tag="stopped")

    # -- what the window shows ----------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            active = self.step not in ENDED and self.step != "idle"
            too_long = active and now - self._started > SESSION_LIMIT_SECONDS
        if too_long:
            self.cancel("failed", "The sign-in took too long and was stopped. Try again.")
        with self._lock:
            seg = self._raw[self._seg:]
            # An unfinished line followed by silence: ssh is waiting for an answer we do not recognise.
            if (self.step == "waiting" and seg.strip() and not seg.endswith("\n")
                    and now - self._last_output > QUIET_SECONDS and not self._marker_seen):
                self.step = "prompt"
            return {
                "step": self.step,
                "message": self.message,
                "url": self.url,
                "code": self.code if self.step == "device" else "",
                "url_trusted": self.url_trusted,
                "needs_enter": self.needs_enter,
                "browser_opened": self.browser_opened,
                "copied": self.copied,
                "fingerprint": self.fingerprint,
                "host": self.host,
                # Shown as-is so an unexpected prompt is never hidden. Not written to any log.
                "raw": self._raw[-4000:],
                "seconds": int(now - self._started) if self._started else 0,
            }

    def save_transcript(self) -> str:
        """Save what ssh printed (codes and typed text blanked) for debugging step detection."""
        with self._lock:
            text = self.redactor.clean(self._raw)
        out = paths.logs_dir() / "login"
        out.mkdir(parents=True, exist_ok=True)
        path = out / ("transcript_%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
        paths.atomic_write(path, "# Torch Bench sign-in transcript (one-time codes and typed text are blanked)\n"
                                 "# final step: %s  %s\n%s\n" % (self.step, self.message, text))
        return str(path)
