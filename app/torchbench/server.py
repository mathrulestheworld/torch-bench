"""The local web server behind the Torch Bench window.

  * Listens on 127.0.0.1 only.
  * Every /api call must carry the token that is written into the page when it is served, and the
    Host header must be this server's own address. Together these stop other web pages open in the
    browser from talking to Torch Bench.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__, commands, config, paths, project, sshconf
from .cmdlog import CommandLog
from .dashboard import GOOD_TO_KNOW, Dashboard
from .files import Files
from .login import LoginSession
from .redact import Redactor
from .ssh import SSH
from .sync import Sync
from .terminal import TerminalSession

DEFAULT_PORT = 8642
MAX_BODY = 256 * 1024
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8"}
STATIC_NAME_RE = re.compile(r"^(vendor/)?[A-Za-z0-9][A-Za-z0-9._-]*$")


class Raw:
    """A non-JSON answer (a downloaded file)."""

    def __init__(self, body: bytes, content_type: str, filename: str) -> None:
        self.body, self.content_type, self.filename = body, content_type, filename

TORCH_IN_TWO_SENTENCES = (
    "Torch is NYU's shared supercomputer: a large pool of GPU and CPU machines that researchers use by "
    "handing in jobs, which wait in a queue and run when machines are free. Torch Bench lets you watch "
    "those jobs, and later read their results, from this window instead of the command line."
)


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message, self.status = message, status


class App:
    """Everything the server needs, built from the config file."""

    def __init__(self) -> None:
        paths.ensure_dirs()
        self.token = secrets.token_urlsafe(24)
        self.demo = os.environ.get("TORCHBENCH_DEMO") == "1"
        self.redactor = Redactor()
        self.log = CommandLog(self.redactor)
        self.cfg: Dict[str, Any] = {}
        self.config_problem = ""
        self.ssh: Optional[SSH] = None
        self.dashboard: Optional[Dashboard] = None
        self.login: Optional[LoginSession] = None
        self.files: Optional[Files] = None
        self.sync: Optional[Sync] = None
        self.term: Optional[TerminalSession] = None
        self._lock = threading.Lock()
        self.stop_server = lambda: None
        self.load()

    def load(self) -> None:
        self.cfg, self.config_problem = config.load()
        if self.cfg:
            self.ssh = SSH(self.cfg["ssh_alias"], self.log)
            self.dashboard = Dashboard(self.cfg, self.ssh)
            self.files = Files(self.cfg, self.ssh)
            self.sync = Sync(self.cfg, self.ssh, self.log)
        else:
            self.ssh = self.dashboard = self.files = self.sync = None

    def need_config(self) -> None:
        if not self.cfg or self.ssh is None or self.dashboard is None:
            raise ApiError("Torch Bench is not set up yet.", 409)

    # -- setup --------------------------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        ssh_ok = False
        connected = False
        if self.cfg and self.ssh:
            ssh_ok = sshconf.status(self.cfg)["ok"]
            connected = self.ssh.check() if ssh_ok else False
        return {
            "version": __version__, "demo": self.demo,
            "configured": bool(self.cfg), "config_problem": self.config_problem,
            "config": self.cfg or config.defaults(),
            "ssh_ready": ssh_ok, "connected": connected,
            "about": TORCH_IN_TWO_SENTENCES,
            "places": config.places(self.cfg) if self.cfg else [],
            "good_to_know": GOOD_TO_KNOW,
            "home": str(paths.home()),
        }

    def save_setup(self, body: Dict[str, Any]) -> Dict[str, Any]:
        raw = dict(self.cfg) if self.cfg else config.defaults()
        for key in ("netid", "mode", "project_name", "remote_dir", "sbatch_account", "default_partition",
                    "local_dir", "gpu_partitions", "cpu_partitions", "help_links", "launchers", "remote_host",
                    "quota_warn_percent", "history_days", "browse_roots"):
            if key in body:
                raw[key] = body[key]
        netid = str(raw.get("netid", "")).strip().lower()
        old_netid = self.cfg.get("netid", "") if self.cfg else ""
        if old_netid and netid != old_netid and "remote_dir" not in body:      # a corrected NetID moves the project path with it
            for top in ("scratch", "home", "archive"):
                prefix = "/%s/%s/" % (top, old_netid)
                if str(raw.get("remote_dir", "")).startswith(prefix):
                    raw["remote_dir"] = "/%s/%s/%s" % (top, netid, raw["remote_dir"][len(prefix):])
        if self.demo and not str(raw.get("project_name", "")).strip():   # the demo comes with a made-up project
            raw["project_name"], raw["sbatch_account"] = "demo-project", raw.get("sbatch_account") or "torch_pr_000_general"
            if os.path.isdir(project.default_local("demo-project")):
                raw["local_dir"] = project.default_local("demo-project")
        template = body.get("remote_dir_template")
        if isinstance(template, str) and "{netid}" in template and not str(raw.get("remote_dir", "")).strip():
            raw["remote_dir"] = template.replace("{netid}", netid)
        if not str(raw.get("remote_dir", "")).strip() and raw.get("project_name") and netid:
            raw["remote_dir"] = "/scratch/%s/%s" % (netid, raw["project_name"])
        try:
            config.save(raw)
        except config.ConfigError as exc:
            raise ApiError(str(exc))
        self.load()
        return self.state()

    # -- sign-in ------------------------------------------------------------------------------
    def start_login(self) -> Dict[str, Any]:
        self.need_config()
        assert self.ssh is not None
        with self._lock:
            if self.login and self.login.step not in ("idle", "connected", "failed", "vpn", "cancelled"):
                return self.login.state()
            if not sshconf.status(self.cfg)["ok"]:
                raise ApiError("The connection settings step is not finished yet.", 409)
            kwargs: Dict[str, Any] = {}
            if self.demo:   # demo mode never opens a real sign-in page or touches the clipboard
                kwargs = {"opener": lambda url: True, "copier": lambda text: True,
                          "check_reachable": lambda host: os.environ.get("FAKE_SSH_LOGIN") != "unreachable"}
            self.login = LoginSession(self.ssh, self.cfg["remote_host"], self.log, self.redactor, **kwargs)
            session = self.login
        session.start()
        if self.dashboard:
            self.dashboard.forget_connection()
        return session.state()

    # -- terminal -----------------------------------------------------------------------------
    def start_terminal(self, rows: int, cols: int) -> Dict[str, Any]:
        self.need_config()
        assert self.ssh is not None
        with self._lock:
            if self.term and not self.term.closed:
                return {"ok": True, "reattached": True, "since": 0}
            command = commands.terminal(self.cfg.get("remote_dir", ""))
            argv = self.ssh.terminal_argv(command)
            if argv is None:
                raise ApiError("Sign in to Torch first: the terminal uses the shared sign-in.", 409)
            shown = "ssh -tt %s%s  (built-in terminal)" % (self.cfg["ssh_alias"], (" '%s'" % command.text) if command.text else "")
            self.term = TerminalSession(argv, self.log, self.redactor, shown)
            try:
                self.term.start(rows, cols)
            except OSError as exc:
                self.term = None
                raise ApiError("The terminal could not start on this Mac (%s)." % exc)
            return {"ok": True, "reattached": False, "since": 0}

    def close_terminal(self) -> None:
        if self.term:
            self.term.close()

    def login_state(self) -> Dict[str, Any]:
        if not self.login:
            return {"step": "idle", "message": "", "raw": ""}
        state = self.login.state()
        if state["step"] == "connected" and self.dashboard:
            self.dashboard.kick()
        return state


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "TorchBench/" + __version__
    protocol_version = "HTTP/1.1"
    app: App  # set on the class by serve()

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the terminal quiet
        return

    # -- plumbing -----------------------------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        self._send(status, _json_bytes(obj), "application/json; charset=utf-8")

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def _authorised(self) -> bool:
        sent = self.headers.get("X-Torchbench-Token", "")
        return self._host_ok() and secrets.compare_digest(sent, self.app.token)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError("That request is too large.", 413)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError("Could not read the request.")
        if not isinstance(data, dict):
            raise ApiError("Could not read the request.")
        return data

    # -- routes -------------------------------------------------------------------------------
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if not self._host_ok():
            self._send(403, b"Forbidden", "text/plain")
            return
        if url.path in ("/", "/index.html"):
            page = (paths.static_dir() / "index.html").read_text(encoding="utf-8")
            self._send(200, page.replace("__TORCHBENCH_TOKEN__", self.app.token).encode("utf-8"),
                       STATIC_TYPES[".html"])
            return
        if url.path.startswith("/static/"):
            name = url.path[len("/static/"):]
            target = paths.static_dir() / name
            ext = os.path.splitext(name)[1]
            if not STATIC_NAME_RE.match(name) or ".." in name or ext not in STATIC_TYPES or not target.is_file():
                self._send(404, b"Not found", "text/plain")
                return
            self._send(200, target.read_bytes(), STATIC_TYPES[ext])
            return
        if url.path.startswith("/api/"):
            self._api("GET", url.path, parse_qs(url.query))
            return
        self._send(404, b"Not found", "text/plain")

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            self._api("POST", url.path, parse_qs(url.query))
        else:
            self._send(404, b"Not found", "text/plain")

    def _api(self, method: str, path: str, query: Dict[str, Any]) -> None:
        if not self._authorised():
            self._json({"error": "This page is out of date. Reload it."}, 403)
            return
        try:
            body = self._body() if method == "POST" else {}
            status, result = self._route(method, path, query, body)
            if isinstance(result, Raw):
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", result.filename) or "download"
                self._send(status, result.body, result.content_type,
                           {"Content-Disposition": "attachment; filename=\"%s\"" % safe})
            else:
                self._json(result, status)
        except ApiError as exc:
            self._json({"error": exc.message}, exc.status)
        except commands.CommandRefused as exc:
            self._json({"error": str(exc)}, 400)
        except Exception as exc:  # never show a stack trace to a newcomer; keep the detail in the server log
            import traceback
            traceback.print_exc()
            self._json({"error": "Something went wrong inside Torch Bench (%s)." % exc.__class__.__name__}, 500)

    def _route(self, method: str, path: str, query: Dict[str, Any], body: Dict[str, Any]) -> Tuple[int, Any]:
        app = self.app
        q = lambda name, default="": (query.get(name) or [default])[0]
        route = (method, path)

        if route == ("GET", "/api/state"):
            return 200, app.state()
        if route == ("POST", "/api/setup/save"):
            return 200, app.save_setup(body)
        if route == ("GET", "/api/setup/ssh"):
            app.need_config()
            return 200, sshconf.status(app.cfg)
        if route == ("POST", "/api/setup/ssh"):
            app.need_config()
            try:
                st = sshconf.install(app.cfg)
            except (RuntimeError, OSError) as exc:
                raise ApiError(str(exc))
            app.log.record("edit %s" % st["file"], 0, label="Add the Torch entry to your SSH settings",
                           routine=False, where="mac")
            return 200, st

        if route == ("POST", "/api/login/start"):
            return 200, app.start_login()
        if route == ("GET", "/api/login"):
            return 200, app.login_state()
        if path.startswith("/api/login/") and method == "POST":
            if not app.login:
                raise ApiError("No sign-in is in progress.", 409)
            action = path[len("/api/login/"):]
            if action == "continue":          # the person says they finished in the browser: press Enter for them
                app.login.send("", secret=False)
            elif action == "trust-host":      # the person read the fingerprint and chose to trust the host
                app.login.send("yes", secret=False)
            elif action == "input":           # a line typed into a prompt; goes straight to ssh, never stored
                text = body.get("text", "")
                if not isinstance(text, str) or len(text) > 1024 or "\n" in text or "\r" in text:
                    raise ApiError("That reply cannot be sent.")
                app.login.send(text, secret=True)
            elif action == "cancel":
                app.login.cancel()
            elif action == "transcript":
                return 200, {"saved": app.login.save_transcript()}
            else:
                raise ApiError("Unknown sign-in action.", 404)
            return 200, app.login.state()

        if route == ("GET", "/api/dashboard"):
            app.need_config()
            assert app.dashboard is not None
            app.dashboard.kick(force=q("force") == "1")
            return 200, app.dashboard.snapshot()
        if route == ("GET", "/api/job"):
            app.need_config()
            assert app.dashboard is not None
            return 200, app.dashboard.job_detail(q("id"))
        if route == ("POST", "/api/disk"):
            app.need_config()
            assert app.dashboard is not None
            return 200, app.dashboard.measure_disk()
        # the project and its two folders
        if route == ("GET", "/api/project"):
            app.need_config()
            return 200, project.status(app.cfg, app.ssh)
        if route == ("POST", "/api/project/save"):
            app.need_config()
            if app.sync and app.sync.job_state().get("active") and not app.sync.job_state().get("done"):
                raise ApiError("Wait for the copy that is running to finish before changing the project.", 409)
            try:
                saved = project.save(app.cfg, body)
            except config.ConfigError as exc:
                raise ApiError(str(exc))
            app.load()
            out = project.status(app.cfg, app.ssh)
            out.update({"created_local": saved["created_local"], "imported_excludes": saved["imported_excludes"]})
            return 200, out
        if route == ("POST", "/api/project/choose-folder"):
            app.need_config()
            if app.demo:
                return 200, {"ok": False, "error": "The folder picker is switched off in the demo. Type a path instead."}
            return 200, project.choose_folder(str(body.get("start", "")))

        # push and pull: always a preview first, then a confirmed copy
        if path.startswith("/api/sync/"):
            app.need_config()
            assert app.sync is not None
            if route == ("POST", "/api/sync/preview"):
                return 200, app.sync.preview(str(body.get("direction", "")), str(body.get("rel", "")))
            if route == ("POST", "/api/sync/start"):
                return 200, app.sync.start(str(body.get("plan", "")))
            if route == ("GET", "/api/sync/job"):
                return 200, app.sync.job_state()
            if route == ("POST", "/api/sync/cancel"):
                app.sync.cancel()
                return 200, app.sync.job_state()

        # files and logs: read-only, inside the project folder
        if route == ("GET", "/api/files/list"):
            app.need_config()
            assert app.files is not None
            return 200, app.files.list(q("root"), q("path"))
        if route == ("GET", "/api/files/read"):
            app.need_config()
            assert app.files is not None
            try:
                start = int(q("start", "0"))
            except ValueError:
                raise ApiError("Not a valid position in the file.")
            return 200, app.files.read(q("root"), q("path"), start, following=q("follow") == "1")
        if route == ("GET", "/api/files/download"):
            app.need_config()
            assert app.files is not None
            got = app.files.download(q("root"), q("path"))
            if not got["ok"]:
                return (409 if got.get("not_connected") else 400), got
            return 200, Raw(got["data"], got["content_type"], got["name"])
        if route == ("GET", "/api/job/logs"):
            app.need_config()
            assert app.files is not None
            return 200, app.files.job_logs(q("id"))

        # the built-in terminal
        if route == ("POST", "/api/term/start"):
            try:
                rows, cols = int(body.get("rows", 30)), int(body.get("cols", 110))
            except (TypeError, ValueError):
                rows, cols = 30, 110
            return 200, app.start_terminal(rows, cols)
        if path.startswith("/api/term/"):
            term = app.term
            if term is None:
                raise ApiError("No terminal is open.", 409)
            if route == ("GET", "/api/term/read"):
                try:
                    since = max(0, int(q("since", "0")))
                except ValueError:
                    since = 0
                got = term.read(since)
                got["data"] = base64.b64encode(got["data"]).decode("ascii")
                return 200, got
            if route == ("POST", "/api/term/write"):
                data = body.get("data", "")
                if not isinstance(data, str) or not term.write(data):
                    raise ApiError("The terminal is closed.", 409)
                return 200, {"ok": True}
            if route == ("POST", "/api/term/resize"):
                try:
                    ok = term.resize(int(body.get("rows", 0)), int(body.get("cols", 0)))
                except (TypeError, ValueError):
                    ok = False
                return 200, {"ok": ok}
            if route == ("POST", "/api/term/close"):
                term.close()
                return 200, {"ok": True}

        if route == ("POST", "/api/disconnect"):
            app.need_config()
            assert app.ssh is not None and app.dashboard is not None
            app.close_terminal()
            app.ssh.close_login()
            app.dashboard.forget_connection()
            return 200, {"connected": app.ssh.check()}

        if route == ("GET", "/api/commands"):
            try:
                after = int(q("after", "0"))
            except ValueError:
                after = 0
            return 200, {"entries": app.log.entries(after, include_routine=q("routine") == "1"),
                         "last_id": app.log.last_id(), "log_folder": str(paths.shell_log_dir())}
        if route == ("GET", "/api/project-file"):
            app.need_config()
            return 200, config.shareable(app.cfg)
        if route == ("POST", "/api/quit"):
            app.close_terminal()
            threading.Thread(target=app.stop_server, daemon=True).start()
            return 200, {"stopping": True}
        raise ApiError("Unknown request.", 404)


def serve(port: int = DEFAULT_PORT, on_ready=None) -> None:
    app = App()
    Handler.app = app
    httpd = None
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)   # this Mac only
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("Torch Bench could not find a free port near %d." % port)
    httpd.daemon_threads = True
    app.stop_server = httpd.shutdown
    actual = httpd.server_address[1]
    info = {"pid": os.getpid(), "port": actual, "url": "http://127.0.0.1:%d/" % actual, "started": time.time()}
    paths.atomic_write(paths.run_dir() / "server.json", json.dumps(info))
    try:   # `torchbench stop` sends SIGTERM: shut down cleanly
        import signal
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
    except ValueError:
        pass   # not the main thread (tests)
    if on_ready:
        on_ready(info)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        try:
            current = json.loads((paths.run_dir() / "server.json").read_text())
            if current.get("pid") == os.getpid():
                (paths.run_dir() / "server.json").unlink()
        except (OSError, ValueError):
            pass
