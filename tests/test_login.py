"""The guided sign-in, driven end to end against the stand-in ssh under a real pty.

The transcripts here are synthetic (see fake_ssh.py). The Torch prompt wording follows NYU's
documentation; replace/extend with a recorded real sign-in once one exists.
"""
import os
import unittest

from helpers import FakeTorchCase
from torchbench import login, sshconf
from torchbench.cmdlog import CommandLog
from torchbench.redact import Redactor
from torchbench.ssh import SSH

try:
    import pty  # noqa: F401
    HAVE_PTY = True
except ImportError:
    HAVE_PTY = False


class Patterns(unittest.TestCase):
    def detect(self, text):
        for p in login.DEVICE_PATTERNS:
            m = p.search(text)
            if m:
                return m.group("code"), m.group("url").rstrip(".,;)")
        return None

    def test_torch_wording_from_nyu_docs(self):
        text = "(abc1234@log-2) Authenticate with PIN BUUM5YHJX at https://microsoft.com/devicelogin and press ENTER."
        self.assertEqual(self.detect(text), ("BUUM5YHJX", "https://microsoft.com/devicelogin"))
        self.assertTrue(login.PRESS_ENTER_RE.search(text))

    def test_microsoft_generic_wording(self):
        text = ("To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code "
                "ABCD12345 to authenticate.")
        self.assertEqual(self.detect(text), ("ABCD12345", "https://microsoft.com/devicelogin"))
        self.assertFalse(login.PRESS_ENTER_RE.search(text))

    def test_ordinary_text_is_not_a_code(self):
        for text in ("Last login: Wed Nov 11 from 10.0.0.1", "error code: authenticate again at https://device.example",
                     "Warning: Permanently added 'login.torch.hpc.nyu.edu' to the list of known hosts."):
            self.assertIsNone(self.detect(text), text)

    def test_only_microsoft_pages_open_by_themselves(self):
        for good in ("https://microsoft.com/devicelogin", "https://login.microsoftonline.com/common/oauth2/deviceauth",
                     "https://aka.ms/devicelogin"):
            self.assertTrue(login.trusted_login_url(good), good)
        for bad in ("http://microsoft.com/devicelogin", "https://microsoft.com.evil.example/devicelogin",
                    "https://evil.example/microsoft.com/devicelogin", "https://login.torch.hpc.nyu.edu/x", "file:///etc/passwd"):
            self.assertFalse(login.trusted_login_url(bad), bad)

    def test_prompts(self):
        self.assertTrue(login.PASSWORD_RE.search("(abc1234@log-1) Password: "))
        self.assertTrue(login.HOSTKEY_RE.search("Are you sure you want to continue connecting (yes/no/[fingerprint])? "))
        self.assertTrue(login.NETWORK_ERRORS_RE.search("ssh: connect to host x port 22: Operation timed out"))


@unittest.skipUnless(HAVE_PTY, "needs a pty")
class GuidedSignIn(FakeTorchCase):
    def setUp(self):
        super().setUp()
        self.cfg = self.config()
        sshconf.install(self.cfg)
        self.redactor = Redactor()
        self.log = CommandLog(self.redactor)
        self.ssh = SSH("torch", self.log)
        self.opened, self.copied = [], []

    def session(self, scenario, reachable=True):
        os.environ["FAKE_SSH_LOGIN"] = scenario
        s = login.LoginSession(self.ssh, "login.torch.hpc.nyu.edu", self.log, self.redactor,
                               opener=lambda url: self.opened.append(url) or True,
                               copier=lambda text: self.copied.append(text) or True,
                               check_reachable=lambda host: reachable)
        self.addCleanup(s.cancel)
        s.start()
        return s

    def until(self, s, step):
        return self.wait_for(lambda: s.state() if s.state()["step"] == step else None, what="step %r (now %r: %r)" % (step, s.step, s.state()["raw"]))

    def test_device_login_then_enter(self):
        s = self.session("device")
        st = self.until(s, "device")
        self.assertEqual((st["code"], st["url"]), ("DEMO12345", "https://microsoft.com/devicelogin"))
        self.assertTrue(st["needs_enter"] and st["url_trusted"] and st["browser_opened"] and st["copied"])
        self.assertEqual(self.opened, ["https://microsoft.com/devicelogin"])
        self.assertEqual(self.copied, ["DEMO12345"])
        self.assertFalse(self.ssh.check())                 # not connected until the person says they are done
        self.assertTrue(s.send("", secret=False))          # "I have signed in"
        self.until(s, "connected")
        self.assertTrue(self.ssh.check())
        self.assertEqual(len(self.opened), 1)              # the page is opened once, not on every poll

    def test_polling_flow_needs_no_button(self):
        s = self.session("polling")
        st = self.until(s, "device")
        self.assertFalse(st["needs_enter"])
        self.until(s, "connected")

    def test_password_prompt_is_piped_and_never_kept(self):
        s = self.session("password")
        self.until(s, "password")
        secret = "correct-horse-battery-staple"
        s.send(secret)
        self.until(s, "device")
        s.send("", secret=False)
        self.until(s, "connected")
        # ssh received it ...
        self.assertEqual((self.tmp / "state" / "password_length").read_text(), str(len(secret)))
        # ... and it is nowhere on disk, nor is the one-time code.
        saved = s.save_transcript()
        from pathlib import Path
        self.assertNotIn(secret, Path(saved).read_text())
        self.assertNotIn("DEMO12345", Path(saved).read_text())
        for path in (self.tmp / "torchbench").rglob("*"):
            if path.is_file():
                text = path.read_text(errors="replace")
                self.assertNotIn(secret, text, path)
                self.assertNotIn("DEMO12345", text, path)
        self.assertNotIn(secret, str(self.log.entries()))

    def test_first_connection_asks_before_trusting_the_host(self):
        s = self.session("hostkey")
        st = self.until(s, "hostkey")
        self.assertTrue(st["fingerprint"].startswith("SHA256:"))
        s.send("yes", secret=False)
        self.until(s, "device")

    def test_unrecognised_prompt_is_shown_raw(self):
        s = self.session("odd")
        st = self.until(s, "prompt")
        self.assertIn("Select a sign-in method [1-3]:", st["raw"])
        s.send("1")
        self.until(s, "device")

    def test_unreachable_host_is_the_vpn_page_and_ssh_never_starts(self):
        s = self.session("device", reachable=False)
        self.assertEqual(s.state()["step"], "vpn")
        self.assertEqual([a for a in self.ssh_argvs() if "-O" not in a and "-G" not in a], [])

    def test_network_error_from_ssh_is_the_vpn_page(self):
        s = self.session("unreachable")
        self.until(s, "vpn")

    def test_denied(self):
        s = self.session("denied")
        self.until(s, "device")
        s.send("", secret=False)
        st = self.until(s, "failed")
        self.assertIn("did not accept", st["message"])
        self.assertFalse(self.ssh.check())

    def test_cancel(self):
        s = self.session("device")
        self.until(s, "device")
        s.cancel()
        self.assertEqual(s.state()["step"], "cancelled")
        self.assertFalse(s.send("x"))
        self.assertFalse(self.ssh.check())

    def test_already_connected_skips_everything(self):
        self.open_login()
        s = self.session("device")
        self.assertEqual(s.state()["step"], "connected")
        self.assertEqual(self.opened, [])


if __name__ == "__main__":
    unittest.main()
