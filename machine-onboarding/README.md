# Onboard a new Ubuntu machine

Turn a fresh laptop into a research host you can reach from your controller
(Mac, Linux, or Windows with OpenSSH). Repeat these steps on **each** machine,
using unique names such as `swarm-01`, `swarm-02`, and `swarm-03`.

This prepares the host. It installs **Codex CLI and Claude Code without logging
either in**, making model calls, or starting a swarm. Tailscale and GitHub account
approvals happen separately, in a browser on any device.

## 1. Install Ubuntu, then download this folder

Supported: **Ubuntu 24.04 or 26.04 LTS**, Intel/AMD 64-bit or ARM64, with systemd.
Ubuntu Desktop with the default Bash shell is the easiest laptop setup. Other
Linux distributions, WSL, live USB sessions, and containers are not supported by
this installer. ARM64 follows vendor installer support; it has not been tested
on physical hardware here.

Finish installing Ubuntu onto the internal disk, connect Wi-Fi/Ethernet, and plug
in AC power. Create a normal administrator account with a password. Do not run
these scripts as root. Start in that account's desktop terminal; if setting up
over SSH, allocate a terminal with `ssh -t`.

Download the public repository without needing GitHub authentication first:

```bash
sudo apt update
sudo apt install -y curl tar
mkdir -p ~/Downloads
cd ~/Downloads
curl -fL --retry 3 https://github.com/ClaireBookworm/swarmbank/archive/refs/heads/main.tar.gz -o swarmbank.tar.gz
tar -xzf swarmbank.tar.gz
cd swarmbank-main/machine-onboarding
```

Alternatively, use GitHub's **Code → Download ZIP**, extract it, and open a
terminal in `machine-onboarding`. The folder is self-contained; you can also copy
it by USB. Keep `lib/` and the other files together. Review the scripts before
running. For a release you intend to reuse, archive a specific reviewed commit
instead of a moving `main` branch.

## 2. Install and configure the machine

On the first laptop:

```bash
bash setup.sh --name swarm-01 --unattended-key
```

Use `swarm-02` and `swarm-03` on the other two. The script asks for your Git author
name and email (a GitHub noreply email is fine), then your Ubuntu sudo password.
Allow time for package downloads. Keep the terminal open until it prints the
next command. Re-run the same command after a failure; existing CLI installations
and GitHub keys are retained, and a healthy T3 service is not restarted.

`--unattended-key` creates a per-machine GitHub SSH key **without a passphrase**
so agents can use it after reboot without unlocking an SSH agent. The private
file is readable only by your account. Omit this flag to enter a passphrase at
`ssh-keygen`'s prompts; you will then need an unlocked `ssh-agent` for Git operations.
Never copy a private key from the controller or another laptop.

Optional flags:

```bash
bash setup.sh --name swarm-02 \
  --git-name 'Your Name' --git-email 'YOUR_GIT_EMAIL' \
  --authorized-key ~/Downloads/controller.pub --unattended-key
```

- `--authorized-key FILE`: authorize your controller's existing **public `.pub`**
  key for incoming SSH. Without it, use `ssh-copy-id` in step 3. No personal key is
  bundled in this repository.
- `--keep-sleep`: keep your current idle-sleep and lid settings.
- `--no-t3`: omit T3 and its service; keep both agent CLIs installed.
- `--help`: print options without installing anything.

If a `github.com` entry already exists in `~/.ssh/config`, the script stops
instead of replacing it. To use the new per-machine key, rename the old entry's
`Host github.com` to an alias such as `Host github-previous`, retaining
`HostName github.com` and its existing identity, then rerun setup. Existing
repositories that need the previous identity can use that alias in their remote
URLs. The script owns only its comment-delimited GitHub block and preserves
other SSH hosts.

### Installed components

| Component | Result |
| --- | --- |
| OpenSSH server | Starts at boot; controller access via ordinary SSH |
| Tailscale | Installed and enabled; join your network in step 3 |
| Git and GitHub CLI | Git identity set; API and SSH authentication in step 3 |
| Node.js 24 LTS / npm | User-owned, checksum-verified Node if an existing Node ≥24 is unavailable |
| Python / venv / pip / pipx / uv | Project environments and Python CLI tools |
| Build tools, bubblewrap, jq, ripgrep, rsync | Development and prerequisites for later research setup |
| tmux, htop, archive tools | Persistent terminals, monitoring, transfers |
| Codex CLI / Claude Code | Installed; **no provider login** |
| T3 Code | User service enabled at boot and after logout; no cloud account required |
| Ubuntu security updates | Automatic security updates enabled, automatic reboots disabled |

Tailscale, Claude, and T3 use their official installers; Codex uses its official
npm package; uv uses pipx/PyPI. Network downloads occur at installation time, so
versions are not frozen. Existing installed CLIs are not automatically upgraded.
Saved vendor installers live in `~/.local/state/swarmbank-onboarding`.

The scripts add the tools' PATH **before** Ubuntu's `.bashrc` non-interactive
return. This matters when T3 launches tools over SSH. Custom non-Bash shells may
need their own startup file to source `~/.config/swarmbank-onboarding/env.sh`.

### Power and network behavior

By default, the script disables GNOME idle sleep on AC for your user and writes
an AC-only lid policy. The lid policy takes effect **after reboot**. Battery
sleep policy is left alone; sleep targets are not masked. On a different desktop
environment, configure its AC idle-sleep behavior manually. Screen blanking is fine.

OpenSSH retains Ubuntu's existing authentication policy. If UFW is active, the
installer adds an SSH allowance on `tailscale0` without enabling/resetting the
firewall or changing other rules. On a fresh machine with UFW inactive, SSH also
listens on the LAN. No router port forwarding, public T3 listener, or Tailscale
exit node is configured. Existing custom firewall policies may need adjustment.

## 3. Connect Tailscale and GitHub (human approvals)

```bash
bash connect.sh
```

You can resume just one part with `bash connect.sh tailscale` or
`bash connect.sh github`. Run only one login at a time. A lock prevents parallel
copies of this helper from creating competing device codes.

### Tailscale and controller SSH

Approve the printed Tailscale URL using the same account/network as your
controller. Install Tailscale on the controller too. The helper prints the
machine's actual address, username, and SSH host-key fingerprint. Compare the
fingerprint on the first connection.

If you did not supply `--authorized-key`, run the printed `ssh-copy-id` command
**on the controller**, using its public key path. For example:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub USER@TAILSCALE_IP
ssh USER@TAILSCALE_IP
```

Use the new laptop's Ubuntu username, not necessarily your controller username.
`ssh-copy-id` needs that laptop's account password for the initial connection.
If the controller has no key, create one there with `ssh-keygen -t ed25519`
(keep any existing keys). On controllers without `ssh-copy-id`, transfer only
the `.pub` file, then on the laptop run:

```bash
python3 lib/configure.py authorize-key ~/Downloads/controller.pub
```

Add aliases on your **controller**, one per laptop, using the printed values:

```sshconfig
Host swarm-01
    HostName TAILSCALE_IP_FOR_THIS_MACHINE
    User UBUNTU_USERNAME
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

Now `ssh swarm-01` and `scp file swarm-01:~/` work. Tailnet access policies still
apply. Keep Wi-Fi auto-connect enabled. Consider the device-key expiry setting
for an unattended host; a disabled or expired Tailscale device cannot be reached.

### GitHub: two distinct authentications

1. The helper prints this machine's **public** key and the GitHub SSH-key settings
   URL. Add it as an **Authentication Key**, with the machine name as its title.
   Press Enter in the helper after saving it. Each laptop has a different key.
2. GitHub CLI prints a **new one-time device code**. Open
   <https://github.com/login/device> on your controller, enter that exact code,
   and approve GitHub CLI. If an older CLI asks to upload a key, choose **Skip**
   because you just added it. The helper suppresses browser launching on the host
   with `GH_BROWSER=true`; no graphical display is needed over SSH.
3. Leave the terminal running until authentication finishes. The helper checks
   GitHub API access and SSH authentication, and selects SSH for Git operations.

SSH enables clone/pull/push. The separate `gh` login enables PR and issue API
operations subject to your account's repository permissions and organization SSO.
The helper uses GitHub's documented public server key for strict host verification;
it does not trust an unverified `ssh-keyscan` result or disable host checks.

`gh` uses the system credential store where available and can fall back to a
local configuration file. Check `gh auth status` for the location. A locked
desktop keyring after reboot can make API authentication unavailable over SSH;
test it after reboot instead of assuming the browser approval is sufficient.
No tokens or private keys belong in this repository or in copied onboarding ZIPs.

## 4. Verify, reboot, and test from the controller

```bash
bash doctor.sh
```

The checker returns nonzero if installed tools, GitHub access, or enabled services
need attention. Codex/Claude authentication is **not required**. Optional T3 browser
setup is reported separately.

When the checks pass, reboot at a convenient time. If disk encryption asks for
a boot passphrase, someone must enter it physically before Tailscale/SSH starts.
There is no remote disk-unlock mechanism in these scripts. Keep the machine on AC,
close the lid, wait a few minutes, and reconnect with `ssh swarm-01`. Run
`doctor.sh` again. Test once from another network (for example a phone hotspot).

In T3 on the controller: **Settings → Connections → Add environment → SSH**, then
enter `swarm-01` or `USER@TAILSCALE_IP`. Match the controller and host T3 versions if
the connection fails. The host service stays alive after the controller disconnects.
Provider authentication remains a later task, so T3 should show those providers
as needing sign-in on a fresh machine.

For a development server listening on port 3000 on the laptop:

```bash
ssh -N -L 3000:127.0.0.1:3000 swarm-01
```

Open `http://localhost:3000` on the controller while the tunnel runs. For persistent
terminal work, use `tmux new -As research`; detach with Ctrl+B then D. tmux survives
SSH disconnects, not reboots.

## Later: agents and research workloads

When ready, sign in explicitly on each host with `codex login` and
`claude auth login`. These commands are intentionally absent from setup/connect.
Follow the chosen account's usage rules; do not copy authentication directories
between machines. For this repository's swarm, use its separate
[Linux orchestrator guide](../orchestrator/README.md) and
[account/operations guidance](../docs/operations.md), including account-specific
login directories. Do not start duplicate orchestrators for one account.

Keep projects in `~/projects` and use `uv venv` or `python3 -m venv .venv` per
project. Install Docker, databases, GPU drivers, 1Password, or a university VPN
only for the projects that require them. VPNs can interfere with Tailscale;
test a VPN locally before depending on unattended remote access. Ubuntu Pro is
optional and can be enabled separately. Choose a backup destination for code,
uncommitted work, configurations, and data before relying on the host.

Browser automation needs verification on the installed T3 release. Some versions
(including the original 0.0.45 setup) lack the documented `t3 browser setup`
command. Setup detects this and continues without browser support. If supported
but unsuccessful, retry it manually and inspect the error; do not disable Chrome's
sandbox. Project-specific Playwright installations are a separate option.

## Maintenance and troubleshooting

- **Download/package failure:** fix connectivity or the reported package issue,
  then rerun setup with the same options. Don't run two apt installations at once.
  If a vendor doesn't support your Ubuntu/CPU combination, stop at that error;
  do not silently substitute another distribution's repositories.
- **Installer appears stuck:** open a second terminal; inspect `systemctl list-jobs`
  and `ps -eo pid,ppid,stat,etime,args`. A `T` process is suspended. Identify the
  cause before resuming it with `sudo kill -CONT PID`. Do not kill apt/dpkg or
  delete their lock files. Setup preserves terminal I/O rather than wrapping apt
  in a logging pipe.
- **`no DISPLAY` during login:** use `connect.sh github`; authorize on the
  controller and keep the waiting host process alive. If you canceled/expired a
  login, restart once and use only its new code. A browser success page alone
  does not prove credentials were saved; check `gh auth status` on the host.
- **TLS/network timeout:** fix connectivity and retry; do not disable TLS
  verification. Never start multiple device flows hoping an old code will work.
- **GitHub SSH fails:** verify this machine's key is registered and the effective
  identity with `ssh -G github.com`. With a passphrase, use `ssh-agent` and
  `ssh-add ~/.ssh/id_ed25519_swarmbank_github` in the session that runs Git.
  If the server host key changed, verify it against GitHub's documentation first.
- **T3 can't find Node/agents:** reconnect with a new SSH session; verify
  `ssh swarm-01 'command -v node; command -v codex; command -v claude'`.
- **T3 stops after logout/reboot:** inspect `t3 service status`,
  `systemctl --user status t3code`, and `loginctl show-user "$USER" -p Linger`.
- **Lid behavior:** the logind rule applies after reboot. Check AC power and
  desktop settings/inhibitors; test physical behavior before leaving home.

Update deliberately when no jobs are running:

```bash
sudo apt update && sudo apt upgrade
npm install -g @openai/codex@latest
claude update
t3 update                  # if installed; may restart service
pipx upgrade uv
```

These scripts install Node 24 when needed but do not maintain an existing Node
installation automatically; use your existing Node manager or a reviewed newer
Node release when upgrading. Schedule reboots when Ubuntu requests them.

To restore the previous AC idle-sleep setting and remove this folder's lid rule:

```bash
bash power.sh restore
```

Reboot afterward. `t3 service uninstall` removes its startup service while
retaining its data. To retire a machine, also revoke its Tailscale device,
GitHub SSH key, and CLI credentials in the corresponding account settings.

## Files and validation

| File | Role |
| --- | --- |
| `setup.sh` | Install and configure; no account login |
| `connect.sh` | Tailscale + GitHub approvals and verification |
| `doctor.sh` | Read-only readiness checks |
| `power.sh` | Apply/restore AC-only power settings |
| `lib/common.sh`, `lib/configure.py` | Guards, downloads, and idempotent user-file edits |
| `github_known_hosts` | GitHub's public host key (not a user key) |
| `tests/` | Offline tests for key preservation, shell startup, and capability checks |

Run checks from the repository root:

```bash
shellcheck -x -P machine-onboarding machine-onboarding/*.sh machine-onboarding/lib/*.sh
python3 -m unittest discover -s machine-onboarding/tests -v
```

CI runs these on Ubuntu 24.04. Tests do not install third-party tools, authenticate,
reboot, or simulate laptop firmware. The earlier HP setup established the workflow;
this generalized version still needs an end-to-end run on each new machine.

Official sources: [Codex](https://developers.openai.com/codex/cli/),
[Claude Code](https://code.claude.com/docs/en/setup),
[T3 install](https://github.com/pingdotgg/t3code/blob/main/docs/user/install.md),
[T3 service](https://github.com/pingdotgg/t3code/blob/main/docs/user/background-service.md),
[Tailscale](https://tailscale.com/docs/install/linux),
[GitHub CLI login](https://cli.github.com/manual/gh_auth_login),
[GitHub host keys](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints),
[uv](https://docs.astral.sh/uv/getting-started/installation/).
