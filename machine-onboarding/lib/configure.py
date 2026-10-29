#!/usr/bin/env python3
"""Small, testable edits to the current user's files. Never reads private keys."""
import argparse
import os
from pathlib import Path
import re
import subprocess

PATH_MARKER = "# swarmbank: tools for login and non-interactive SSH shells"
SSH_BEGIN = "# BEGIN swarmbank GitHub identity"
SSH_END = "# END swarmbank GitHub identity"


def write_private(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Restrictive from creation, even with a permissive caller umask.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)
    path.chmod(0o600)


def shell_path(home: Path):
    env = home / ".config/swarmbank-onboarding/env.sh"
    write_private(env, 'export PATH="$HOME/.local/opt/swarmbank-node/bin:$HOME/.local/bin:$PATH"\n'
                  'export NPM_CONFIG_PREFIX="$HOME/.local"\n')
    block = (PATH_MARKER + '\nif [ -r "$HOME/.config/swarmbank-onboarding/env.sh" ]; then\n'
             '    . "$HOME/.config/swarmbank-onboarding/env.sh"\nfi\n\n')
    profiles = [home / ".profile", home / ".bashrc"]
    # Bash ignores .profile if either of these is present.
    profiles += [p for p in (home / ".bash_profile", home / ".bash_login") if p.exists()]
    for path in profiles:
        old = path.read_text() if path.exists() else ""
        if PATH_MARKER not in old:
            # This must precede Ubuntu's .bashrc early return for non-interactive SSH.
            write_private(path, block + old)


def public_key(path: Path):
    text = path.read_text().strip()
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1 or not re.match(r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-\S+|sk-\S+) \S+", text):
        raise ValueError("Expected one OpenSSH public key (.pub), without authorized_keys options. Never supply a private key.")
    subprocess.run(["ssh-keygen", "-lf", str(path)], check=True, capture_output=True)
    return text


def authorize(home: Path, source: Path):
    key = public_key(source)  # Validate before changing anything.
    ssh = home / ".ssh"
    ssh.mkdir(mode=0o700, exist_ok=True)
    ssh.chmod(0o700)
    path = ssh / "authorized_keys"
    old = path.read_text() if path.exists() else ""
    # Match key material even when the same key has a different comment/options.
    material = " ".join(key.split()[:2])
    if not any(material in line for line in old.splitlines()):
        write_private(path, old.rstrip() + "\n" + key + "\n")
    path.chmod(0o600)


def github_config(home: Path):
    ssh = home / ".ssh"
    ssh.mkdir(mode=0o700, exist_ok=True)
    ssh.chmod(0o700)
    path = ssh / "config"
    old = path.read_text() if path.exists() else ""
    block = (SSH_BEGIN + "\nHost github.com\n    HostName github.com\n    User git\n"
             "    IdentityFile ~/.ssh/id_ed25519_swarmbank_github\n    IdentitiesOnly yes\n" + SSH_END + "\n")
    if SSH_BEGIN in old or SSH_END in old:
        pattern = re.compile(re.escape(SSH_BEGIN) + r"\n.*?" + re.escape(SSH_END) + r"\n?", re.S)
        if old.count(SSH_BEGIN) != 1 or old.count(SSH_END) != 1 or not pattern.search(old):
            raise ValueError("Malformed managed SSH block; inspect ~/.ssh/config before retrying.")
        write_private(path, pattern.sub(lambda _: block, old))
        return
    for line in old.splitlines():
        parts = line.split()
        if parts and parts[0].lower() == "host" and "github.com" in [s.lower() for s in parts[1:]]:
            raise ValueError("Existing github.com SSH configuration: rename its Host alias (for example, github-previous) before retrying. Nothing was overwritten. See README.")
    # Host * defaults must not take precedence over this specific identity.
    # Reset the match context so formerly global options still apply to all hosts.
    write_private(path, block + "\nHost *\n" + old)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["shell-path", "authorize-key", "github-config"])
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    if args.action == "shell-path":
        shell_path(Path.home())
    elif args.action == "github-config":
        github_config(Path.home())
    else:
        if not args.path:
            parser.error("authorize-key requires the controller's .pub file")
        authorize(Path.home(), args.path)


if __name__ == "__main__":
    main()
