# Linux GPU swarm in a Proxmox LXC container

This is an additional Linux deployment path for four independent researchers
using official subscription CLIs, systemd user scopes and bubblewrap. It extends
the existing `fetch` service to Linux. No researcher coordination board or
operator messages are needed. Keep account identities and machine attribution
in the private installed config, outside this checkout.

## Prerequisites and the sandbox gate

On the container, as root, install the prerequisites and create a dedicated user
(choose `SWARM_USER` locally; do not commit that value):

```sh
apt-get update
apt-get install -y bubblewrap python3 python3-venv git curl dbus-user-session nodejs npm acl poppler-utils
useradd --create-home --shell /bin/bash "$SWARM_USER"
loginctl enable-linger "$SWARM_USER"
runuser -u "$SWARM_USER" -- bwrap \
  --unshare-user --unshare-pid --unshare-ipc --unshare-uts --share-net \
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib \
  --symlink usr/lib64 /lib64 --proc /proc --dev /dev --tmpfs /tmp \
  --clearenv --setenv PATH /usr/bin:/bin /bin/sh -c 'id; test ! -e /root; echo sandbox-ok'
```

Stop if this fails. Do not replace bubblewrap with an unsandboxed CLI or make the
container privileged. The tested container allowed unprivileged user namespaces.
If another container rejects namespace creation, inspect its Proxmox feature and
AppArmor configuration from the host before changing it; this deployment does
not require disabling AppArmor. GPU device passthrough and cgroup delegation must
also be configured by the container host administrator.

## Install once, as the dedicated user

Put a checkout somewhere the dedicated user can traverse, then log into that
user's shell. Run the GPU helper **before** the main Linux installer:

```sh
cd /path/to/checkout
bash orchestrator/install-linux-gpu-tools.sh
bash orchestrator/install.sh
npm install --prefix "$HOME/.local" -g @openai/codex
export PATH="$HOME/.local/bin:$PATH"
```

The helper installs uv, CPython 3.12, SCI and the pinned physics stack in one
shared tools environment. Researchers mount it read-only and do not each install
their own copy. It does not log into a model account or start researchers.
If an existing tools environment uses a different Python version, stop the
service and move it aside before running the helper; it refuses an in-place
interpreter replacement.

Use `requirements-linux-gpu.txt` rather than unconstrained versions. In the
tested environment an unconstrained resolver selected `k-wave-python` 0.1.0,
which failed to import; 0.6.2 and its pinned dependencies import correctly.
j-Wave 0.2.1 selects JAX 0.4.38. CUDA 12 pip libraries work with the tested
595.84 driver, whose reported maximum CUDA version is 13.2. Do not install or
replace a kernel NVIDIA driver inside an LXC container.

The runner mounts both the venv and its uv-managed interpreter read-only,
including uv's minor-version alias. Mounting only the venv leaves `python`
pointing outside the sandbox and produces an `execvp` failure.

The official CLI runs shell tools through a login shell. Debian's `/etc/profile`
resets PATH, which originally hid `sci`, `python`, `fetch` and `gpu-run` despite
their presence in the launch environment. The installer builds a private profile
copy with the tools PATH appended; bubblewrap mounts it read-only over
`/etc/profile`. The host's profile is unchanged. The integration check uses
`bash -lc` for the physics and GPU probes to cover this execution path.

## Private configuration and sizing

Edit `~/.local/share/quo-swarm/config.json`, using the example config as a
starting point. Keep pools disabled during setup. Set these values:

```json
{
  "resources": {
    "cpu_per_run": 4,
    "threads_per_run": 4,
    "memory_high_per_run": "3584M",
    "memory_max_per_run": "4G"
  },
  "gpu": {"enabled": true},
  "disk_reserve_gib": 30,
  "disk_critical_gib": 15
}
```

Configure the science pool with concurrency 4, per-account concurrency 4,
120 seconds between new runs and 60 seconds between starts on one account.
Set the intended model explicitly; set account caps to 80% to leave headroom.
For a 24-CPU, 24-GB-RAM container with no swap, four 4-GiB run limits leave RAM
for the orchestrator, downloads and the operating system. The limit covers the
CLI and **all descendants**, rather than each Python process separately.
Threads default to 4 for BLAS/OpenMP; CPU quota is 400% per run, with swap
disabled per scope. GPU memory is separate from the cgroup's host-RAM budget.

For aggregate limits create
`~/.config/systemd/user/quo-agents.slice.d/resources.conf`:

```ini
[Slice]
CPUQuota=1600%
MemoryHigh=14G
MemoryMax=16G
MemorySwapMax=0
```

For a user-local official CLI install, also create
`~/.config/systemd/user/quo-swarm.service.d/path.conf`:

```ini
[Service]
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
```

Drop-ins survive installer updates. Run `systemctl --user daemon-reload`.
Set the data service's `max_file_gib` to 20 for this disk budget.

Copy `config/prompt.txt` into the private installed config. Change only the
Available resources paragraph and the machine placeholder in the attribution
line. Describe the installed stack, four CPU cores and four threads per
researcher, a 4-GiB **whole-run** limit (aim below 3 GiB working memory), and
the shared 24-GB GPU through `gpu-run`. Preserve the `fetch` instructions and
the rest of the research prompt. Do not describe cached datasets or other runs.

## GPU and download isolation

`gpu.enabled` exposes only accessible NVIDIA character devices, read-only
sysfs and NVIDIA driver proc metadata. System driver libraries are already in
the read-only `/usr` mount. The tested LXC needed the proc metadata and sysfs
in addition to `/dev/nvidia0`, `/dev/nvidiactl` and `/dev/nvidia-uvm`; without
them CUDA initialization failed even though the GPU worked outside bubblewrap.
Hosts with driver libraries outside `/usr` need explicit read-only mounts for
those libraries before enabling GPU work.

Test permissions after a real container reboot. In this LXC, Proxmox recreated
the NVIDIA nodes with mode 0660 and root ownership, removing the non-root user's
access even though the service started successfully. As root, grant the dedicated
user access to the compute nodes through a persistent tmpfiles rule:

```sh
SWARM_UID="$(id -u "$SWARM_USER")"
for device in nvidia0 nvidiactl nvidia-uvm; do
  printf 'a+ /dev/%s - - - - u:%s:rw-\n' "$device" "$SWARM_UID"
done > /etc/tmpfiles.d/quo-swarm-gpu.conf
systemd-tmpfiles --create /etc/tmpfiles.d/quo-swarm-gpu.conf
```

This grants one host user access, and the agent still receives only the explicit
device mounts. If device nodes appear after tmpfiles setup on another host,
order a root oneshot applying this rule after device creation and before the
user manager. Do not make all devices world-writable.

Run GPU work with `gpu-run python simulation.py`. It takes an exclusive flock
on one empty, read-only mounted inode and then execs the command, retaining
the lock until that command exits. Researchers cannot write messages into or
replace that lock file. JAX defaults to CPU outside the wrapper; inside it,
CUDA is enabled and allocator preallocation is disabled. The wrapper serializes
cooperating jobs; it cannot make an individual oversized simulation fit VRAM,
and direct device use can bypass this admission convention.

The serve process now starts `datastore.py` on both Linux and macOS. Linux
returns paths under `/opt/quo-data/objects`. Only objects are mounted into the
researcher, read-only; the private index, partial downloads, log, config and
other runs are absent. Object directories have owner write/traverse permission
without read permission (0311). The service can create known paths but neither
it nor the sandbox can list those directories. The sandbox cannot chmod the
read-only mount, and it drops all capabilities. Eviction uses the private index
instead of walking directories. Content deduplication and per-URL locking are
unchanged; the client receives the same path whether a download was cached.

Both platforms now attempt eviction when free space drops within 10 GiB of
the reserve, at most every ten minutes. Objects fetched in the last two days
are protected. Eviction aims for reserve plus 20 GiB; disk admission and
critical-stop guards still apply if there is nothing old enough to evict.
An open file remains readable after unlink; a later open may need another fetch.

The existing host-network limitation still applies: bubblewrap shares the
container network. Protect local administrative services from researchers;
filesystem isolation is not network isolation. Do not use operator notes or
shared writable folders to coordinate researchers.

## Keys, accounts and launch

Generate a new key on this machine, outside the checkout:

```sh
mkdir -p "$HOME/.sci"
"$HOME/.local/share/quo-swarm/tools/sci-venv/bin/sci" keygen --key "$HOME/.sci/key.pem"
chmod 600 "$HOME/.sci/key.pem"
```

Give only the printed base64url public key to the SCI administrator. Never
print, copy or commit `key.pem`. Map the provider to this key in the pool's SCI
config. Wait for enablement before letting researchers publish.

The operator must perform `quo-swarm login codex-1` themselves, with a ChatGPT
subscription account that is not logged into another swarm. This uses the
official CLI's device login; never copy another profile's tokens. Disable extra
usage and automatic credit purchases in account settings before enabling the
pool. The orchestrator's credit-decrease guard is reactive, so it is not a
replacement for disabling paid overages in the account. No API key or model
proxy is required.

```sh
quo-swarm init
quo-swarm check
systemctl --user enable --now quo-swarm.service
PYTHONPATH="$HOME/.local/share/quo-swarm/app" \
  python3 /path/to/checkout/orchestrator/tests/linux_sandbox_check.py
quo-swarm resume --pool science
```

Observe the first research run for about twenty minutes without steering it:
inspect its events, successful tool calls, scope `MemoryCurrent`/`MemoryPeak`,
and failures. Test tools successfully during setup; do not claim a researcher
used them until its own event log shows that it did. An autonomous researcher
may choose CPU-only work, so natural GPU use is not guaranteed on a schedule.

The service has `Restart=on-failure` and is enabled under the lingering user's
default target. Before leaving it unattended, test an unexpected service
termination, then reboot the container and verify the service is active,
the pool is still enabled, and sessions resume. A service enablement check or
user-manager restart alone is not evidence of an actual successful reboot.
In LXC the kernel boot ID and uptime can belong to the host; use the container
PID 1 start time, its boot journal and the service's activation time to distinguish
a completed container reboot from shutdown still in progress.

## Validation evidence

Setup probes on Debian 13, x86_64 LXC, 24 online CPUs, 24 GB RAM without swap
and an RTX 4090 showed:

| Check | Result |
| --- | --- |
| Existing account/usage/admission safety tests plus datastore/recovery tests | 31 passed |
| Imports inside the production bubblewrap command | All ten requested packages passed |
| JAX through `gpu-run` | CUDA device 0; jitted 64-by-64 matrix product returned 64 |
| MCX through `gpu-run` | One 4090 detected; 10,000 photons simulated with finite positive flux |
| Two independent sandbox GPU jobs | Two-second intervals did not overlap |
| `fetch` cold/hit | Same path and bytes; 10,754-byte file retained once |
| Shared data protections | Listing, writing, chmod and lock-file writes denied |
| Memory enforcement | 512-MiB allocation under a 128-MiB test scope killed with exit 137 |
| Orphan run scope after orchestrator loss | Stopped before requeuing its original session |
| Unexpected serve-process termination | systemd restarted the service with a new PID |
| Actual LXC reboot | Lingering user and enabled service returned at container boot |

The Linux baseline had no running data service, no shared-store mount and no
GPU devices in the sandbox. These probes demonstrate the added capabilities
while preserving non-listability and single-copy downloads. They do not measure
publication throughput or scientific quality. Account login, first-run
observation and actual reboot recovery are deployment checks, separate from
these reproducible setup probes.
