"""The local web server behind the Torch Bench window.

  * Listens on 127.0.0.1 only.
  * Every /api call must carry the token that is written into the page when it is served, and the
    Host header must be this server's own address. Together these stop other web pages open in the
    browser from talking to Torch Bench.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import __version__, commands, config, paths, sshconf
from .cmdlog import CommandLog
from .dashboard import Dashboard
from .login import LoginSession
from .redact import Redactor
from .ssh import SSH

DEFAULT_PORT = 8642
MAX_BODY = 256 * 1024
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}

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
        self._lock = threading.Lock()
        self.stop_server = lambda: None
        self.load()

    def load(self) -> None:
        self.cfg, self.config_problem = config.load()
        if self.cfg:
            self.ssh = SSH(self.cfg["ssh_alias"], self.log)
            self.dashboard = Dashboard(self.cfg, self.ssh)
        else:
            self.ssh = self.dashboard = None

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
            "home": str(paths.home()),
        }

    def save_setup(self, body: Dict[str, Any]) -> Dict[str, Any]:
        raw = dict(self.cfg) if self.cfg else config.defaults()
        for key in ("netid", "mode", "project_name", "remote_dir", "sbatch_account", "default_partition",
                    "local_dir", "gpu_partitions", "cpu_partitions", "help_links", "launchers", "remote_host",
                    "quota_warn_percent", "history_days"):
            if key in body:
                raw[key] = body[key]
        netid = str(raw.get("netid", "")).strip().lower()
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
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
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
            if "/" in name or ".." in name or ext not in STATIC_TYPES or not target.is_file():
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
        if route == ("POST", "/api/disconnect"):
            app.need_config()
            assert app.ssh is not None and app.dashboard is not None
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
