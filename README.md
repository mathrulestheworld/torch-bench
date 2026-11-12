# Torch Bench

A small Mac app for using NYU's Torch HPC cluster without the command line. It runs on your Mac, opens in
your browser, and talks to Torch over one shared SSH sign-in. Nothing is installed on Torch.

**Status: milestone M0 (connection + dashboard). This version only looks; it cannot submit, cancel, change
or delete anything on the cluster.** It has been tested offline against a stand-in for ssh and made-up
data. It has not yet been run on a Mac against the real cluster: see "Before you rely on it" below.

## What M0 does

- **Guided setup**: one page with the NYU links a newcomer needs (account, project, VPN, Duo, docs), then
  NetID, the SSH settings entry, and sign-in.
- **Guided sign-in**: Torch Bench starts `ssh torch` itself, opens Microsoft's device-login page, shows the
  one-time code (already copied), and waits. You sign in and approve Duo yourself. If the login host is
  unreachable you get a "Connect to the NYU VPN first" page. Anything it does not recognise is shown exactly
  as Torch printed it, with a reply box. A plain `ssh torch` in Terminal also works and is detected.
- **Jobs dashboard**: what is running and waiting, in a sentence; each waiting job with a plain-language
  reason ("You've used a lot of GPU hours recently, so other jobs go first"), the scheduler's start estimate
  when it gives one, and Torch Bench's own estimate from how long your recent jobs really waited. Fair-share
  score, storage quota with a loud warning above 90%, free GPUs per partition, and the last 7 days of
  finished jobs.
- **Commands page**: every command sent to Torch with its output, also saved under
  `~/torchbench/logs/torch_shell/` (`history.log`, `last.txt`, `commands.jsonl`).
- **Help page**: the same NYU links, kept in the settings file so they can be changed.

Planned next, in order: M1 logs, read-only file browser, push/pull, built-in terminal; M2 launchers with a
dry run, guarded cancel, notifications; M3 disk manager, GPU-utilisation check, "copy for Claude".

## Install

Open Terminal on your Mac and paste this one line:

    curl -fsSL https://raw.githubusercontent.com/mathrulestheworld/torch-bench/main/install.sh | bash

The installer:

- checks for Apple's Command Line Tools (they include Python) and offers to install them if missing;
- puts Torch Bench in Applications and opens it;
- adds a `torchbench` command for Terminal;
- needs no sudo and no pip, and installs nothing on Torch.

Run the same line again to update; your settings are kept. The app is built on your Mac rather than
downloaded as an app, so macOS shows no security warning.

    torchbench            start it and open the window
    torchbench stop       stop it (your Torch sign-in stays open)
    torchbench status     is it running, and is a Torch sign-in open?
    torchbench demo       made-up data; touches neither Torch nor your settings
    torchbench record-fixtures [folder]    save Torch's real answers for the tests

To look around before you have a Torch account, run `torchbench demo`.

To uninstall: `torchbench stop`, delete `~/torchbench` and Torch Bench in Applications, remove the
`# torchbench` line from `~/.zprofile`, and remove the block between `# >>> torchbench >>>` and
`# <<< torchbench <<<` in `~/.ssh/config`.

For development, clone the repo and run `./torchbench` (or `bash install.sh`) from it.

## Before you rely on it (owner checklist for M0)

These need a Mac and a real Torch sign-in, which the build environment did not have.

1. **Run it on a Mac**: paste the install line, go through setup with your own NetID.
2. **Do one real sign-in through the app.** Step detection follows the prompt NYU documents
   ("Authenticate with PIN ... at https://microsoft.com/devicelogin and press ENTER"). If Torch prints
   something else, the raw-output fallback still gets you in. Either way, afterwards go to Help and choose
   "Save what Torch printed at the last sign-in" (codes and typed text are blanked), so the patterns in
   `app/torchbench/login.py` can be confirmed and a real transcript added to `tests/test_login.py`.
3. **Record real command output**: signed in, run `torchbench record-fixtures`, look the files over (they
   hold job names and your NetID), copy the folder to `tests/fixtures/real/<date>/`, and run the tests.
   `tests/test_real_fixtures.py` runs every parser over them. `myquota` is the likeliest to differ: its
   layout here is a guess from NYU's earlier cluster. If it differs, the window shows the raw text rather
   than a wrong number.
4. **Kill the shared login** (`ssh -O exit torch`) with the window open: the "Not connected" notice should
   appear within one refresh, with no hang and no prompt.
5. Check the **help links** in `~/torchbench/config.json` (the VPN one points at a Stern IT page).

## Safety rules, and where each is encoded

| Rule | Where |
| --- | --- |
| No password or code is ever stored or logged. The app starts ssh and shows its prompts; the person authenticates. | `login.py` (typed text goes straight to the pty), `redact.py`, `tests/test_login.py::test_password_prompt_is_piped_and_never_kept` |
| Read-only by default; this version has no mutating command at all. | `commands.py` is the only place commands are built; `ssh.py` refuses anything else and anything marked mutating; `policy.py` |
| Nothing heavy on the login node. | The registry holds only `squeue`, `sacct`, `sshare`, `sprio`, `sinfo`, `myquota`, `scontrol show job`, `du -sh` (manual button only), `hostname`. Refreshes are stale-driven and stop when the window is hidden. |
| Nothing is deleted on the cluster. | No such command exists in the registry. |
| Every remote command and its output is logged and visible. | `cmdlog.py`, the Commands page |
| If `ssh -O check` fails, no command runs. | `ssh.py`; `tests/test_safety.py::test_no_login_means_nothing_runs` |
| Commands can never prompt or hang. | `BatchMode=yes`, `ControlMaster=no`, closed stdin, connect and hard timeouts in `ssh.py` |
| Only this Mac can talk to the app. | `server.py`: binds 127.0.0.1, per-launch page token, Host check, strict Content-Security-Policy |

Two deliberate choices to know about:

- The SSH entry uses NYU's recommended `StrictHostKeyChecking no` / `UserKnownHostsFile /dev/null`, because
  `login.torch.hpc.nyu.edu` points at several machines with different host keys. That trades host-key
  checking for not being blocked by key-mismatch errors. If you would rather keep checking, edit the block.
- If you already have your own working `Host torch` entry, Torch Bench uses it and writes nothing.

## Settings (`~/torchbench/config.json`)

Created by setup. Everything user- or project-specific lives here; nothing from any research project is
hard-coded in the program.

| Key | Meaning |
| --- | --- |
| `netid` | Your NYU NetID |
| `mode` | `colleague` (default: watch and read) or `owner` (later: also submit, cancel, clean up) |
| `ssh_alias`, `remote_host` | `torch`, `login.torch.hpc.nyu.edu` |
| `project_name`, `remote_dir`, `local_dir` | The project, its folder on Torch, its folder on this Mac |
| `sbatch_account` | The SLURM account jobs are charged to |
| `default_partition`, `gpu_partitions`, `cpu_partitions` | `l40s_public`; `l40s_public`, `h200_public`; `cs` |
| `launchers` | Registered submit scripts (used from M2) |
| `help_links` | The list on the Help page: `title`, `url`, `note` |
| `quota_warn_percent`, `history_days` | 90, 7 |

An owner can save a **project file** from the Help page; a colleague loads it during setup and only has to
type their NetID.

## Layout

    install.sh, torchbench        installer and command
    app/torchbench/               the program (Python 3.9+, standard library only)
      commands.py                 the registry: the only commands that can reach Torch
      ssh.py                      ControlMaster + BatchMode wrapper (the scripts/torch.sh pattern)
      login.py, ptyexec.py        guided sign-in under a pty
      sshconf.py                  the marked block in ~/.ssh/config
      slurm.py, reasons.py, waits.py, dashboard.py    parsing, plain-language reasons, wait estimates
      cmdlog.py, redact.py        transparency log, secret blanking
      config.py, policy.py, paths.py, server.py, __main__.py
    app/static/                   the window (one page, no libraries)
    tests/                        unittest suite; fake_ssh.py stands in for ssh; fixtures/demo is made up

## Tests

    python3 -m unittest discover -s tests

92 tests (6 of them wait for real recordings), all offline: the SSH wrapper, the parsers, the registry, redaction, the SSH-settings writer, the
guided sign-in end to end under a real pty, the dashboard, and the server. They pass on Python 3.9 (what
macOS ships) and 3.13.

Acceptance tests from the build brief that M0 covers, and how:

| Acceptance test | State |
| --- | --- |
| Fresh Mac, no ssh config: installer + wizard reaches a connected dashboard with one guided sign-in | Passes against the stand-in (`test_server.py::test_fresh_mac_to_green_dashboard`); needs the real run above |
| Kill the ControlMaster: "Not connected" within one refresh, no hangs, no prompts | Passes against the stand-in (`test_dashboard.py::test_losing_the_login_degrades_within_one_refresh`, `test_safety.py`) |
| Pending array job shows a human-readable reason and the fair-share number | Passes (`test_dashboard.py`) |
| Quota above 90% warns loudly; the submit block is ready for M2 | Passes (`test_dashboard.py`, `test_safety.py::test_full_scratch_blocks_submit`) |
| Unit tests for the SSH wrapper, parsers and registry against fixtures | Passes, on synthetic fixtures; real ones to be recorded |

The brief's wording "only one Terminal login" was changed to "one guided sign-in" and rule 1's "never
automate the login" to "never store or enter credentials for the person", as agreed with the owner.
