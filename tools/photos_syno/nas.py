#!/usr/bin/env python3
"""nas.py - run commands on the Synology NAS over SSH as Jason's DSM user, password from Pass via SSH_ASKPASS.

The password is held only in this process's environment (NAS_SSH_PW) and handed to ssh by askpass.cmd,
which echoes that variable; nothing is written to disk or printed. Host key: the NAS ED25519 key
SHA256:9PKLehgl9LKkU+yn3adZZPTWAnXq7/WyLL1rBEoGQaE, already in known_hosts as [192.168.1.68]:1143.
DSM SSH is on port 22 (SYNO.Core.Terminal 11-24); same host key as the old :1143 entry, so HostKeyAlias reuses it.
Use:  from nas import run, put_text;  run("ls /volume1")  -> (rc, stdout, stderr)
      python nas.py "command"        -> prints stdout/stderr of the command
OUTCOME 2026-11-24: password auth via askpass works with OpenSSH_for_Windows_9.5 (SSH_ASKPASS_REQUIRE=force).
"""
import os
import subprocess
import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

from creds import dsm_login

HERE = Path(__file__).resolve().parent
HOST = os.environ.get("NAS_HOST", "192.168.1.68")
PORT = "22"
_ENV = None
_USER = None


def _env():
    """Environment for ssh with askpass set up; creds read once."""
    global _ENV, _USER
    if _ENV is None:
        user, pw, _ = dsm_login()
        _USER = user
        _ENV = dict(os.environ, NAS_SSH_PW=pw, SSH_ASKPASS=str(HERE / "askpass.cmd"),
                    SSH_ASKPASS_REQUIRE="force", DISPLAY="none")
    return _ENV


def user():
    """DSM user name (not printed by this module)."""
    _env()
    return _USER


def _ssh_args():
    return ["ssh", "-p", PORT, "-o", "ConnectTimeout=15", "-o", "HostKeyAlias=[192.168.1.68]:1143", "-o", "PreferredAuthentications=password,keyboard-interactive",
            "-o", "NumberOfPasswordPrompts=1", "-o", "ServerAliveInterval=30", f"{user()}@{HOST}"]


def run(cmd, stdin=None, timeout=None):
    """Run one shell command on the NAS. Returns (rc, stdout, stderr)."""
    # bytes, not text mode: Windows text mode turns \n into \r\n and breaks sh scripts (seen 11-24)
    data = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
    r = subprocess.run(_ssh_args() + [cmd], input=data, capture_output=True, env=_env(), timeout=timeout)
    err = "\n".join(l for l in r.stderr.decode("utf-8", "replace").splitlines()
                    if not l.startswith("** "))  # drop OpenSSH post-quantum notice
    return r.returncode, r.stdout.decode("utf-8", "replace"), err


def popen(cmd, stdout):
    """Start a long NAS command, stdout to an open file. Returns the Popen."""
    return subprocess.Popen(_ssh_args() + [cmd], stdout=stdout, stderr=subprocess.STDOUT, env=_env())


def put_text(remote_path, text):
    """Write text to a NAS file through stdin (cat >). remote_path must be a literal, safe path."""
    rc, out, err = run(f"cat > '{remote_path}'", stdin=text)
    if rc != 0:
        raise RuntimeError(f"put_text failed rc {rc}: {err.strip()[:200]}")


def sudo(cmd, timeout=None):
    """Run cmd with sudo -S on the NAS; the password goes on stdin only."""
    pw = _env()["NAS_SSH_PW"]
    return run(f"sudo -S -p '' {cmd}", stdin=pw + "\n", timeout=timeout)


def script(text, as_root=False, timeout=None):
    """Run a multi-line sh script on the NAS via stdin (no quoting through ssh). Root: sudo -S, password first line."""
    if as_root:
        return run("sudo -S -p '' sh -s", stdin=_env()["NAS_SSH_PW"] + "\n" + text, timeout=timeout)
    return run("sh -s", stdin=text, timeout=timeout)


if __name__ == "__main__":
    if sys.argv[1] == "--py":  # run a local Python file on the NAS (python3 -), extra args passed through
        rc, out, err = run("python3 - " + " ".join(sys.argv[3:]), stdin=Path(sys.argv[2]).read_text(encoding="utf-8"))
        sys.stdout.write(out)
        sys.stderr.write(err)
        sys.exit(rc)
    if sys.argv[1] in ("--script", "--root-script"):
        rc, out, err = script(Path(sys.argv[2]).read_text(encoding="utf-8"), as_root=sys.argv[1] == "--root-script")
        sys.stdout.write(out)
        sys.stderr.write(err)
        sys.exit(rc)
    rc, out, err = run(" ".join(sys.argv[1:]))
    sys.stdout.write(out)
    sys.stderr.write(err)
    sys.exit(rc)
