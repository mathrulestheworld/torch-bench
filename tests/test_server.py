import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from helpers import FakeTorchCase
from torchbench import server


class ServerCase(FakeTorchCase):
    def setUp(self):
        super().setUp()
        os.environ["TORCHBENCH_DEMO"] = "1"        # never open a real browser or touch the clipboard
        self.app = server.App()
        server.Handler.app = self.app
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        if self.app.login:
            self.app.login.cancel()
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, method, path, body=None, token=True, host=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        if token:
            req.add_header("X-Torchbench-Token", self.app.token if token is True else token)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw.decode())
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode()

    def setup_user(self):
        status, st = self.call("POST", "/api/setup/save", {"netid": "abc1234", "project_name": "demo-project",
                                                           "sbatch_account": "torch_pr_000_general"})
        self.assertEqual(status, 200, st)
        return st


class Security(ServerCase):
    def test_listens_on_this_mac_only(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")

    def test_api_needs_the_page_token(self):
        self.assertEqual(self.call("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/state", token="wrong")[0], 403)
        self.assertEqual(self.call("POST", "/api/quit", token=False)[0], 403)
        self.assertEqual(self.call("GET", "/api/state")[0], 200)

    def test_other_host_names_are_refused(self):
        self.assertEqual(self.call("GET", "/api/state", host="evil.example:%d" % self.port)[0], 403)
        self.assertEqual(self.call("GET", "/", token=False, host="evil.example")[0], 403)

    def test_page_carries_the_token_and_static_files_stay_in_their_folder(self):
        status, page = self.call("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.app.token, page)
        self.assertEqual(self.call("GET", "/static/app.js", token=False)[0], 200)
        for bad in ("/static/../torchbench/server.py", "/static/..%2f..%2fREADME.md", "/static/nothing.js", "/etc/passwd"):
            self.assertEqual(self.call("GET", bad, token=False)[0], 404, bad)


class Flow(ServerCase):
    def test_fresh_mac_to_green_dashboard(self):
        status, st = self.call("GET", "/api/state")
        self.assertFalse(st["configured"])
        self.assertEqual(self.call("GET", "/api/dashboard")[0], 409)          # not set up yet

        status, err = self.call("POST", "/api/setup/save", {"netid": "Not A NetID"})
        self.assertEqual(status, 400)
        self.assertIn("NetID", err["error"])

        st = self.setup_user()
        self.assertTrue(st["configured"])
        self.assertEqual(st["config"]["mode"], "colleague")
        self.assertEqual(st["config"]["remote_dir"], "/scratch/abc1234/demo-project")
        self.assertFalse(st["ssh_ready"])

        self.assertEqual(self.call("POST", "/api/login/start")[0], 409)        # connection settings come first
        status, ss = self.call("POST", "/api/setup/ssh")
        self.assertTrue(ss["ok"], ss)

        status, login = self.call("POST", "/api/login/start")
        self.assertEqual(status, 200, login)
        login = self.wait_for(lambda: (lambda s: s if s["step"] == "device" else None)(self.call("GET", "/api/login")[1]), what="device step")
        self.assertEqual(login["code"], "DEMO12345")
        self.call("POST", "/api/login/continue")
        self.wait_for(lambda: self.call("GET", "/api/login")[1]["step"] == "connected", what="connected")

        d = self.wait_for(lambda: (lambda s: s if s["sources"]["quota"]["loaded"] and not s["refreshing"] else None)(self.call("GET", "/api/dashboard")[1]), what="dashboard data")
        self.assertTrue(d["connected"])
        self.assertEqual(d["summary"]["headline"], "5 jobs are running and 17 are waiting.")

        status, cmds = self.call("GET", "/api/commands?routine=1")
        texts = [e["command"] for e in cmds["entries"]]
        self.assertTrue(any(t.startswith("squeue --me") for t in texts))
        self.assertTrue(all("DEMO12345" not in json.dumps(e) for e in cmds["entries"]))
        status, quiet = self.call("GET", "/api/commands")
        self.assertFalse(any(e["routine"] for e in quiet["entries"]))

        status, detail = self.call("GET", "/api/job?id=22601977")
        self.assertTrue(detail["ok"])
        self.assertEqual(self.call("GET", "/api/job?id=1;id")[0], 400)

        status, out = self.call("POST", "/api/disconnect")
        self.assertFalse(out["connected"])
        d = self.wait_for(lambda: (lambda s: s if s["connected"] is False else None)(self.call("GET", "/api/dashboard")[1]), what="not connected")
        self.assertEqual(d["summary"]["running"], 5)

    def test_login_input_is_validated(self):
        self.setup_user()
        self.call("POST", "/api/setup/ssh")
        self.assertEqual(self.call("POST", "/api/login/input", {"text": "x"})[0], 409)   # no sign-in in progress
        os.environ["FAKE_SSH_LOGIN"] = "password"
        self.call("POST", "/api/login/start")
        self.wait_for(lambda: self.call("GET", "/api/login")[1]["step"] == "password", what="password step")
        self.assertEqual(self.call("POST", "/api/login/input", {"text": "two\nlines"})[0], 400)
        self.assertEqual(self.call("POST", "/api/login/input", {"text": 5})[0], 400)
        self.assertEqual(self.call("POST", "/api/login/cancel")[1]["step"], "cancelled")

    def test_vpn_gate(self):
        self.setup_user()
        self.call("POST", "/api/setup/ssh")
        os.environ["FAKE_SSH_LOGIN"] = "unreachable"
        status, st = self.call("POST", "/api/login/start")
        self.assertEqual(st["step"], "vpn")

    def test_project_and_sync_routes(self):
        import shutil
        self.call("POST", "/api/setup/save", {"netid": "abc1234", "mode": "owner"})
        status, st = self.call("GET", "/api/project")
        self.assertFalse(st["configured"])
        self.assertEqual(st["defaults"]["remote_parent"], "/scratch/abc1234")
        self.assertEqual(self.call("POST", "/api/project/save", {"project_name": "p", "remote_dir": "/scratch/other/p"})[0], 400)
        status, st = self.call("POST", "/api/project/save", {"project_name": "study"})
        self.assertEqual(status, 200, st)
        self.assertTrue(st["configured"] and st["created_local"] and st["can_push"])
        self.assertEqual(st["remote_dir"], "/scratch/abc1234/study")
        self.assertEqual([p["key"] for p in self.call("GET", "/api/state")[1]["places"]][0], "project")
        self.assertTrue(self.call("POST", "/api/sync/preview", {"direction": "push"})[1]["not_connected"])
        self.assertEqual(self.call("POST", "/api/sync/preview", {"direction": "push"}, token=False)[0], 403)
        self.assertFalse(self.call("POST", "/api/project/choose-folder", {})[1]["ok"])      # switched off in demo mode
        if not shutil.which("rsync"):
            return
        self.open_login()
        with open(os.path.join(st["local_dir"], "train.py"), "w") as f:
            f.write("print(1)\n")
        status, plan = self.call("POST", "/api/sync/preview", {"direction": "push"})
        self.assertEqual(plan["files"], ["train.py"])
        self.assertTrue(self.call("POST", "/api/sync/start", {"plan": "nope"})[1]["stale"])
        self.assertTrue(self.call("POST", "/api/sync/start", {"plan": plan["plan"]})[1]["started"])
        job = self.wait_for(lambda: (lambda j: j if j.get("done") else None)(self.call("GET", "/api/sync/job")[1]), what="push to finish")
        self.assertEqual(job["exit"], 0)
        self.assertTrue((self.tmp / "fs" / "scratch" / "study" / "train.py").exists())
        self.assertTrue(self.call("GET", "/api/project")[1]["remote_exists"])

    def test_project_file_import(self):
        status, st = self.call("POST", "/api/setup/save", {
            "netid": "xyz9876", "project_name": "shared-proj", "remote_dir": "",
            "remote_dir_template": "/scratch/{netid}/shared-proj", "sbatch_account": "torch_pr_000_general"})
        self.assertEqual(st["config"]["remote_dir"], "/scratch/xyz9876/shared-proj")
        status, shared = self.call("GET", "/api/project-file")
        self.assertNotIn("netid", shared)


if __name__ == "__main__":
    unittest.main()
