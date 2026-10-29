# New laptop setup

For a freshly installed **Ubuntu 24.04 or 26.04 LTS** laptop (Intel/AMD 64-bit or
ARM64). Use your normal administrator account, Wi-Fi/Ethernet, and AC power.
Start in an Ubuntu terminal, or use `ssh -t` for remote setup. Live USB sessions,
containers, and other Linux distributions are unsupported.

For an old laptop that still needs Ubuntu installed or will not boot, start with
the [old ThinkPad runbook](OLD-THINKPAD-RUNBOOK.md). It records the tested
Legacy/MBR repair, persistent Wi-Fi/SSH rescue USB, offline transfer fallback,
and verification sequence from one X220 and two T420s. The scripts here do not
install Ubuntu or change disk partition tables.

## 1. Download and install

This repository is private. On your Mac/controller, sign in to GitHub, select
the branch you want (for the unmerged PR, `codex/machine-onboarding`), and use
**Code → Download ZIP**. Extract it and copy the entire `machine-onboarding`
folder to a USB drive. Keep its `lib/` directory and other files together.

On the new Ubuntu laptop, copy that folder from USB into **Downloads**, then
open Terminal and run:

```bash
cd ~/Downloads/machine-onboarding
bash setup.sh --name swarm-01
```

The new laptop needs internet for package downloads, but no GitHub login to run
the copied installer. Use `swarm-02` and `swarm-03` on the other laptops. Setup
asks for your Ubuntu sudo password. GitHub setup is optional:

```bash
bash setup.sh --name swarm-01 --github   # --gh also works
```

This also asks for your Git name/email, creates a GitHub key, and starts the GitHub
login prompts after installation. Without the flag, no Git identity is requested,
no GitHub key/config is created, and no GitHub login is started. Git and `gh` are
still installed. You can rerun setup with `--github` later.

It installs SSH, Tailscale, Git/GitHub CLI, Node 24 when needed, Python/uv,
build tools, tmux, T3, Codex CLI, and Claude Code. **Codex and Claude remain logged
out.** T3 runs as a user service that survives logout and starts at boot; browser
automation and research workloads are left for later.

Setup enables security updates without automatic reboots, configures tool paths
for SSH sessions, and disables GNOME idle sleep/lid suspend on AC. The lid setting
requires a reboot. Battery sleep settings stay unchanged. Existing CLI installs
and GitHub keys are retained when you rerun setup after a failure.

With `--github`, new GitHub keys have **no passphrase by default** so they work
after reboot. Add `--key-passphrase` to prompt for one instead; that requires an
unlocked `ssh-agent`. Existing keys and their passphrases are preserved. Each
laptop gets its own key; never copy private keys between machines.

Other options:

- `--git-name 'Your Name' --git-email 'YOUR_EMAIL'`: skip the identity prompts.
- `--authorized-key FILE.pub`: allow your controller's public key to SSH in.
- `--keep-sleep`: preserve current sleep/lid settings.
- `--no-t3`: skip T3 and its service.

## 2. Connect Tailscale

```bash
bash connect.sh
```

Approve the printed Tailscale URL using the same network/account as your
controller. By default, `connect.sh` connects only Tailscale.

### Optional GitHub login

When you run setup with `--github`, it starts these prompts automatically:

1. Add the printed public key at [GitHub SSH keys](https://github.com/settings/ssh/new)
   as an Authentication Key, named after this laptop.
2. Enter the new GitHub CLI code at [GitHub device login](https://github.com/login/device)
   on your controller. Keep the laptop terminal open until it finishes. If asked
   to upload an SSH key, choose **Skip** because you just added it.

SSH handles clone/pull/push; GitHub CLI authentication enables PRs and other API
operations. Provider logins remain deferred. Retry an interrupted GitHub login
with `bash connect.sh github`.

`github_known_hosts` contains **GitHub's published server public key**, used to
verify the server. It contains no user credentials. Your own keys are generated
locally and never belong in this repository.

## 3. Connect from your Mac/controller

Install Tailscale on the controller and join the same network. The helper prints
the laptop's username, Tailscale address, and SSH host-key fingerprint.

If you did not supply `--authorized-key`, run these **on the controller**, replacing
`USER` and `TAILSCALE_IP` with those printed values:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub USER@TAILSCALE_IP
ssh USER@TAILSCALE_IP
```

Compare the host fingerprint on first connection. The initial key copy asks for
the laptop's Ubuntu password. If your controller has no key, create one with
`ssh-keygen -t ed25519`; keep any existing keys. Without `ssh-copy-id`, transfer
only the `.pub` file and run on the laptop:

```bash
python3 lib/configure.py authorize-key /path/to/controller.pub
```

For a short alias, add this to the controller's `~/.ssh/config` with real values:

```sshconfig
Host swarm-01
    HostName TAILSCALE_IP
    User UBUNTU_USERNAME
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

Then use `ssh swarm-01`. Repeat with distinct aliases for the other laptops.
For T3, add that SSH alias under **Settings → Connections → Add environment → SSH**.
Match controller/host T3 versions if connecting fails.

An SSH environment saved on the Mac is not automatically available on a phone.
For optional phone access, run `t3 connect link --headless` on the intended host,
approve it with the phone's T3 account, restart its T3 service when jobs are idle,
and check `t3 connect status`. Use the existing host's T3 data directory so the
phone sees the same threads. See the runbook and
[T3 remote access](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md)
for account limits and current mobile steps.

## 4. Verify and reboot

```bash
bash doctor.sh
```

GitHub checks run only if GitHub setup was requested; skipping it is fine.
Rerunning setup without `--github` does not undo a previous GitHub opt-in.
The checker requires Tailscale to report this host online without health warnings;
an assigned IP and working peer SSH connection alone can hide a lost coordination
connection. It also checks SSH and Tailscale are enabled at boot.

Fix any failed checks, then reboot with the lid open when ready. Reconnect before
closing the lid on AC, and run the checker again over SSH. Disk encryption may
need a physical boot passphrase before SSH becomes available. Keep Wi-Fi auto-connect enabled
and check Tailscale device expiry for unattended hosts.

SSH retains Ubuntu's authentication defaults. When UFW is active, setup allows
SSH on `tailscale0`; otherwise SSH also listens on the LAN. No router forwarding
or public T3 listener is configured. Test the actual lid behavior before leaving
home. Restore previous AC settings with `bash power.sh restore`, then reboot.

## If something fails

- Fix the reported error and rerun the same setup command. Don't run concurrent
  installers or delete apt/dpkg lock files.
- GitHub login works over SSH: approve on the controller and keep the waiting
  process running. A desktop keyring can be locked after reboot; verify with
  `gh auth status` and `gh api user` over SSH.
- With a passphrase-protected GitHub key, unlock it using `ssh-agent` and
  `ssh-add ~/.ssh/id_ed25519_swarmbank_github` in the session running Git.
- Setup stops if an existing `Host github.com` entry would conflict. Rename that
  entry to `Host github-previous`, retaining `HostName github.com` and its identity,
  then rerun. Existing repos can use that alias to retain their previous identity.
- If T3 cannot find tools, open a new SSH session. Inspect its service with
  `systemctl --user status t3code`.

The scripts use official installers and do not freeze versions or upgrade
existing CLIs. Keep Ubuntu and installed tools updated when jobs are idle.
For later agent login and workloads, use the [orchestrator guide](../orchestrator/README.md)
and its account-specific login instructions.

## Local checks

A small test suite protects existing keys, SSH configuration, and shell startup
when changing the scripts. There is no GitHub workflow or test dependency to install
on the laptops.

```bash
# From the repository root; ShellCheck is only needed when editing scripts.
shellcheck -x -P machine-onboarding machine-onboarding/*.sh machine-onboarding/lib/*.sh
python3 -m unittest discover -s machine-onboarding/tests -v
```

Installation/onboarding, reboot, and AC lid behavior were exercised on an X220
and two T420s running Ubuntu 26.04 LTS; see the runbook for layouts and limits.
The strengthened Tailscale check has regression coverage. ARM64 and a general
USB-building or unattended OS-installation workflow remain untested.
