#!/usr/bin/env python3
"""A stand-in for ssh, used by the tests and by `torchbench demo`. It never opens a network connection.

It imitates just what Torch Bench relies on:
  ssh -G <alias>                      print the effective settings from $TORCHBENCH_SSH_CONFIG
  ssh -O check|exit <alias>           is a "shared login" open? ($FAKE_SSH_STATE/master exists)
  ssh -o BatchMode=yes ... <alias> <command>
                                      answer from $FAKE_SSH_FIXTURES/<first word>.txt, only if a login is open
  ssh <alias> echo MARKER             an interactive sign-in, scripted by $FAKE_SSH_LOGIN:
        device (default) | polling | password | hostkey | odd | denied | unreachable

Fixture files may contain time tokens such as {T-90m}, {T+5h}, {T-2d}, {D-7d}; they are replaced
with times relative to now (or to $FAKE_SSH_NOW, epoch seconds), so demo data never looks stale.
"""
import os
import re
import sys
import time

STATE = os.environ.get("FAKE_SSH_STATE", "/tmp/fake_ssh_state")
FIXTURES = os.environ.get("FAKE_SSH_FIXTURES", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "demo"))
MASTER = os.path.join(STATE, "master")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def now():
    return float(os.environ.get("FAKE_SSH_NOW") or time.time())


def render(text, at=None):
    at = now() if at is None else at

    def sub(m):
        kind, sign, amount, unit = m.group(1), m.group(2), int(m.group(3)), m.group(4)
        t = at + (1 if sign == "+" else -1) * amount * UNITS[unit]
        return time.strftime("%Y-%m-%dT%H:%M:%S" if kind == "T" else "%Y-%m-%d", time.localtime(t))
    return re.sub(r"\{([TD])([+-])(\d+)([smhd])\}", sub, text)


def effective_config(alias):
    out = {"hostname": alias, "user": "localuser", "controlmaster": "false", "controlpath": "none"}
    path = os.environ.get("TORCHBENCH_SSH_CONFIG")
    if not path or not os.path.exists(path):
        return out
    active = False
    seen = set()
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        key, value = key.lower(), value.strip()
        if key == "host":
            active = alias in value.split() or "*" in value.split()
        elif active and key not in seen:
            seen.add(key)
            out[key] = value
    return out


def parse(argv):
    opts, control, show, rest = {}, None, False, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "-o" and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("=")
            opts[k.lower()] = v
            i += 2
        elif a == "-O" and i + 1 < len(argv):
            control = argv[i + 1]
            i += 2
        elif a == "-G":
            show = True
            i += 1
        else:
            rest = argv[i:]
            break
    return opts, control, show, rest


def say(text, end="\n"):
    sys.stdout.write(text + end)
    sys.stdout.flush()


def ask():
    line = sys.stdin.readline()
    return line.rstrip("\n")


def open_master():
    os.makedirs(STATE, exist_ok=True)
    open(MASTER, "w").write(str(os.getpid()))


def login(alias, command):
    scenario = os.environ.get("FAKE_SSH_LOGIN", "device")
    cfg = effective_config(alias)
    who = "%s@log-1" % cfg.get("user", "abc1234")
    if scenario == "unreachable":
        sys.stderr.write("ssh: connect to host %s port 22: Operation timed out\n" % cfg.get("hostname", alias))
        return 255
    if scenario == "hostkey":
        say("The authenticity of host '%s (10.0.0.1)' can't be established." % cfg.get("hostname", alias))
        say("ED25519 key fingerprint is SHA256:Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MGFiY2RlZg.")
        say("This key is not known by any other names.")
        say("Are you sure you want to continue connecting (yes/no/[fingerprint])? ", end="")
        if ask().strip().lower() != "yes":
            say("Host key verification failed.")
            return 255
    if scenario == "password":
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
        try:
            say("(%s) Password: " % who, end="")
            typed = ask()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        say("")
        os.makedirs(STATE, exist_ok=True)
        open(os.path.join(STATE, "password_length"), "w").write(str(len(typed)))
    if scenario == "odd":
        say("Select a sign-in method [1-3]: ", end="")
        ask()
    if scenario == "polling":
        say("To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code "
            "DEMO12345 to authenticate.")
        time.sleep(float(os.environ.get("FAKE_SSH_POLL_SECONDS", "1")))
    else:
        say("(%s) Authenticate with PIN DEMO12345 at https://microsoft.com/devicelogin and press ENTER. " % who, end="")
        ask()
    if scenario == "denied":
        sys.stderr.write("%s@%s: Permission denied (keyboard-interactive).\n" % (cfg.get("user"), cfg.get("hostname")))
        return 255
    if cfg.get("controlmaster", "false") in ("auto", "yes", "true"):
        open_master()
    say(" ".join(command[1:]) if command and command[0] == "echo" else "")
    return 0


def run(command_text):
    word = command_text.split()[0] if command_text.split() else ""
    if os.environ.get("FAKE_SSH_SLEEP"):
        time.sleep(float(os.environ["FAKE_SSH_SLEEP"]))
    if os.environ.get("FAKE_SSH_FAIL") == word:
        sys.stderr.write("%s: error: simulated failure\n" % word)
        return 1
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "commands.log"), "a") as f:
        f.write(command_text + "\n")
    path = os.path.join(FIXTURES, word + ".txt")
    if not re.match(r"^[a-z]+$", word) or not os.path.exists(path):
        sys.stderr.write("bash: %s: command not found\n" % word)
        return 127
    sys.stdout.write(render(open(path, encoding="utf-8").read()))
    return 0


def main(argv):
    opts, control, show, rest = parse(argv)
    if os.environ.get("FAKE_SSH_ARGV_LOG"):
        with open(os.environ["FAKE_SSH_ARGV_LOG"], "a") as f:
            f.write("\x1f".join(argv) + "\n")
    alias = rest[0] if rest else ""
    if show:
        for k, v in effective_config(alias).items():
            print("%s %s" % (k, v))
        return 0
    if control == "check":
        if os.path.exists(MASTER):
            sys.stderr.write("Master running (pid=4242)\n")
            return 0
        sys.stderr.write("Control socket connect(%s): No such file or directory\n" % MASTER)
        return 255
    if control == "exit":
        if os.path.exists(MASTER):
            os.remove(MASTER)
            sys.stderr.write("Exit request sent.\n")
            return 0
        sys.stderr.write("Control socket connect(%s): No such file or directory\n" % MASTER)
        return 255
    command = rest[1:]
    if opts.get("batchmode") == "yes":
        if not os.path.exists(MASTER):
            sys.stderr.write("%s: Permission denied (publickey,keyboard-interactive).\n" % alias)
            return 255
        return run(" ".join(command))
    return login(alias, command)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
