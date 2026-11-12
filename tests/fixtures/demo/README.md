# Demo fixtures (made up)

These files are **synthetic**: written by hand to match the output formats of the exact commands in
`app/torchbench/commands.py`, not recorded from Torch. `myquota` in particular follows the layout NYU
documented for its earlier cluster and is a guess for Torch.

Replace or supplement them with real recordings: sign in, run `torchbench record-fixtures`, review the
files, and copy them to `tests/fixtures/real/<date>/`. `tests/test_real_fixtures.py` then runs every
parser over them.

Tokens such as `{T-90m}` are replaced with times relative to "now" by `tests/fake_ssh.py`.
