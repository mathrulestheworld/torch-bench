"""The built-in terminal against the stand-in ssh (which opens a local shell in the made-up project)."""
import base64
import json
import os
import unittest

from helpers import FakeTorchCase
from torchbench import commands
from torchbench.cmdlog import CommandLog
from torchbench.redact import Redactor
from torchbench.ssh import SSH
from torchbench.terminal import TerminalSession


class Terminal(FakeTorchCase):
    def setUp(self):
        super().setUp()
        self.redactor = Redactor()
        self.log = CommandLog(self.redactor)
        self.ssh = SSH("torch", self.log)
        self.open_login()

    def start(self):
        argv = self.ssh.terminal_argv(commands.terminal(self.config()["remote_dir"]))
        t = TerminalSession(argv, self.log, self.redactor, "ssh -tt torch (built-in terminal)")
        t.start(24, 100)
        self.addCleanup(t.close)
        return t

    def screen(self, t, until, seconds=8.0):
        """Everything shown so far, once `until` has appeared."""
        seen = {"n": 0, "text": ""}

        def poll():
            got = t.read(seen["n"], wait=0.3)
            seen["text"] += got["data"].decode("utf-8", "replace")
            seen["n"] = got["next"]
            return until in seen["text"] or None
        self.wait_for(poll, seconds, what="%r on screen (have %r)" % (until, seen["text"][-300:]))
        return seen["text"]

    def test_real_shell_round_trip_and_record(self):
        t = self.start()
        self.screen(t, "demo-project]$")
        self.assertTrue(t.write("echo torch$((40+2))bench; pwd\n"))
        text = self.screen(t, "torch42bench")
        self.assertIn("project", text)                      # it started in the project folder
        t.write("stty size\n")
        self.assertIn("24 100", self.screen(t, "24 100"))
        self.assertTrue(t.resize(40, 132))
        t.write("stty size\n")
        self.assertIn("40 132", self.screen(t, "40 132"))   # resizing reaches the shell
        t.write("export HF_TOKEN=hf_abcdefghijklmnopqrstuvwxyz012345\n")
        t.write("exit\n")
        self.wait_for(lambda: t.closed, what="terminal to close")
        self.assertEqual(t.exit_code, 0)
        self.assertFalse(t.write("echo late\n"))
        from pathlib import Path
        record = Path(t.record_path).read_text(encoding="utf-8")
        self.assertIn("torch42bench", record)               # what appeared on screen is recorded ...
        self.assertNotIn("hf_abcdefghijklmnop", record)     # ... with token-shaped text blanked
        self.assertEqual(oct(os.stat(t.record_path).st_mode & 0o777), "0o600")
        labels = [e["label"] for e in self.log.entries()]
        self.assertIn("Terminal opened on the Torch login node", labels)
        closed = [e for e in self.log.entries() if e["label"].startswith("Terminal closed")][0]
        self.assertIn("torch42bench", closed["stdout"])     # and the session shows up on the Commands page
        self.assertNotIn("hf_abcdefghijklmnop", json.dumps(closed))

    def test_reattach_gets_earlier_output(self):
        t = self.start()
        self.screen(t, "demo-project]$")
        t.write("echo first-line\n")
        self.screen(t, "first-line")
        again = t.read(0, wait=0.1)                          # a reloaded window asks from the start
        self.assertIn("first-line", again["data"].decode())

    def test_close_ends_the_shell(self):
        t = self.start()
        self.screen(t, "demo-project]$")
        t.close()
        self.wait_for(lambda: t.closed, what="terminal to close")
        self.assertTrue(t.read(0, wait=0.1)["closed"])

    def test_oversized_write_is_refused(self):
        t = self.start()
        self.assertFalse(t.write("x" * 70000))


class TerminalOverHttp(unittest.TestCase):
    """Server routes; reuses the server test harness."""

    def test_routes(self):
        import test_server
        case = test_server.ServerCase("setUp")
        case.setUp()
        try:
            case.setup_user()
            case.call("POST", "/api/setup/ssh")
            self.assertEqual(case.call("POST", "/api/term/start", {"rows": 24, "cols": 90})[0], 409)   # not signed in
            self.assertEqual(case.call("GET", "/api/term/read?since=0")[0], 409)
            case.open_login()
            status, started = case.call("POST", "/api/term/start", {"rows": 24, "cols": 90})
            self.assertEqual((status, started["reattached"]), (200, False))
            self.assertTrue(case.call("POST", "/api/term/start", {})[1]["reattached"])                  # one shell, reattached
            state = {"n": 0, "text": ""}

            def shown(marker):
                def poll():
                    got = case.call("GET", "/api/term/read?since=%d" % state["n"])[1]
                    state["text"] += base64.b64decode(got["data"]).decode("utf-8", "replace")
                    state["n"] = got["next"]
                    return marker in state["text"] or None
                return poll
            case.wait_for(shown("demo-project]$"), what="prompt")
            self.assertEqual(case.call("POST", "/api/term/write", {"data": "echo via-$((6*7))-http\n"})[0], 200)
            case.wait_for(shown("via-42-http"), what="echo")
            self.assertTrue(case.call("POST", "/api/term/resize", {"rows": 30, "cols": 120})[1]["ok"])
            self.assertEqual(case.call("POST", "/api/term/write", {"data": 5})[0], 409)
            self.assertEqual(case.call("POST", "/api/term/write", {"data": "x"}, token=False)[0], 403)
            case.call("POST", "/api/disconnect")                                                        # disconnect closes the shell
            case.wait_for(lambda: case.app.term.closed, what="terminal closed on disconnect")

            # files over HTTP
            case.open_login()
            status, listing = case.call("GET", "/api/files/list?root=project&path=results/demo_v1/analyze")
            self.assertTrue(listing["ok"], listing)
            status, doc = case.call("GET", "/api/files/read?root=project&path=results/demo_v1/analyze/SUMMARY.md")
            self.assertTrue(doc["text"].startswith("# Demo run"))
            self.assertEqual(case.call("GET", "/api/files/read?root=project&path=../x")[0], 400)
            self.assertEqual(case.call("GET", "/api/files/read?root=project&path=README.md&start=abc")[0], 400)
            status, logs = case.call("GET", "/api/job/logs?id=22590102_2")
            self.assertEqual(len(logs["files"]), 3)
            self.assertEqual(case.call("GET", "/api/job/logs?id=1;id")[0], 400)
            self.assertEqual(case.call("GET", "/api/files/download?root=home&path=nope.bin")[0], 400)
            self.assertEqual(case.call("GET", "/api/files/list?root=home&path=", token=False)[0], 403)
            self.assertTrue(case.call("GET", "/api/files/list?root=scratch&path=")[1]["ok"])
            self.assertEqual(case.call("GET", "/api/files/list?root=elsewhere&path=")[0], 400)
            self.assertEqual([p["key"] for p in case.call("GET", "/api/state")[1]["places"]], ["project", "home", "scratch", "archive"])
            self.assertEqual(case.call("GET", "/static/vendor/xterm.js", token=False)[0], 200)
            self.assertEqual(case.call("GET", "/static/vendor/../app.js", token=False)[0], 404)
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
