# Old ThinkPads: from a rescue USB to a remotely managed Ubuntu machine

This is the record of onboarding one X220 and two T420s on October 29, 2026.
Use it when another old laptop arrives. The most useful preparation was a USB
that could boot Ubuntu, join Wi-Fi, and accept SSH from our controllers without
reinstalling packages or typing passwords every time.

The boot fix that worked on these machines was **Legacy BIOS boot, an MBR
partition table (`msdos` in partitioning tools), an active boot partition, and
BIOS GRUB installed on the disk the firmware actually boots**. Repeated Ubuntu
reinstalls were not the answer to the boot failure we observed.

This is a hardware-tested runbook, not a universal disk-conversion script.
`setup.sh` still starts **after** Ubuntu is installed. It does not partition
disks, install Ubuntu, edit BIOS settings, or build the rescue USB.

## Start a future session with this

> Read `machine-onboarding/OLD-THINKPAD-RUNBOOK.md` and the folder README.
> I have another old laptop and the prepared Ubuntu rescue USB. Help me boot
> the persistent rescue entry and establish SSH. Inventory the machine and
> identify every disk by model, serial, size, and role before proposing changes.
> Tell me which internal disk you intend to change and whether its data must
> be erased; do not infer permission to wipe it from this document. Reuse the
> tested Legacy/MBR approach only if appropriate for this machine. Preserve the
> USB, existing boot partitions, and data unless I explicitly authorize changing
> them. Install or repair Ubuntu, transfer controller public keys and Wi-Fi
> settings, then run the onboarding scripts with a new unique hostname.
> Continue autonomously where SSH permits it. Ask me for physical boot/BIOS
> steps and account approvals when needed. Verify a USB-free reboot, Tailscale
> health, T3, and lid-closed operation on AC before calling it done.

## What actually worked

| Machine | Internal boot arrangement | Result |
| --- | --- | --- |
| `syam1`, X220 | 160 GB Intel SSD: MBR, active ~1 GiB ext4 `/boot`, BIOS GRUB. 256 GB Samsung SSD: Ubuntu root filesystem. Firmware: Legacy Only, Intel disk first. | Booted successfully. The user's earlier repair established this layout; this session preserved it and added storage. |
| `syam2`, first T420 | 500 GB Crucial SSD. Existing Ubuntu was retained while the boot layout was repaired to MBR with an active Linux partition and BIOS GRUB. | Booted without the installer USB. |
| `syam3`, second T420 | 500 GB Crucial SSD. Fresh Ubuntu installation after the user explicitly authorized erasing the target disk, using the same Legacy/MBR approach. | Booted without the installer USB. |

All three ran Ubuntu 26.04 LTS and completed tool installation, SSH, Tailscale,
T3 startup, reboot, and closed-lid-on-AC checks. ARM64 was not tested. The names
above identify the historical machines; choose a new name for the next one.

On the X220, the remaining Intel SSD space became a separate ext4 filesystem
mounted by UUID at `/data`, owned by the normal user. It provides approximately
144 GiB usable space. We did **not** enlarge the Samsung root filesystem or move
the working `/boot`. Both internal SSDs remain necessary to boot that X220.

## The rescue USB is the part to prepare first

The tested stick is a **32 GB Transcend JetFlash** containing the Ubuntu installer
and a persistent rescue environment. Its menu includes:

- `Ubuntu rescue - saved Wi-Fi and SSH`
- A persistent rescue entry with safe graphics
- `CLEAN Ubuntu installer - no saved settings`, plus its safe-graphics entry

Use the first entry for remote inspection and repair. The clean entry is a
fallback and intentionally does not load the saved rescue settings.

The rescue environment contains:

- OpenSSH server enabled at boot and Avahi for `ubuntu-rescue.local` discovery.
- The Mac and HP's **public** keys in `authorized_keys`. Private keys stay on
  their originating controllers.
- A saved, automatically connecting Wi-Fi profile, without a restriction to
  HP's interface name or Wi-Fi MAC address.
- SSH access as `ubuntu`, using keys. Password and root SSH login are disabled.
- A **3.75 GiB ext4 `writable` persistence file** on the FAT32 installer volume,
  with the `persistent` kernel argument in the rescue boot entry.

The persistence file is smaller than FAT32's per-file size limit. The overall
32 GB stick size is not the amount of writable space inside the live system.
Large downloads and upgrades can fill that overlay. Automatic apt upgrade timers
were masked **inside the rescue environment**; installed Ubuntu machines have
security updates enabled separately.

To connect after booting the rescue entry:

```bash
ssh ubuntu@ubuntu-rescue.local
# If discovery fails, use the address shown by `hostname -I` on the laptop:
ssh ubuntu@LAPTOP_LAN_IP
```

Use an external USB keyboard if the built-in keyboard makes entry unreliable.
Neither this USB nor SSH can operate BIOS or a machine that has not booted Linux.
Someone still needs to select the USB, adjust firmware settings, and remove the
stick when requested. Once the rescue system is up, Codex can perform most of
the inspection, installation, boot repair, and verification through SSH.

### Reusing or rebuilding the stick

First inspect the existing stick and test its persistent entry. Do not rewrite
it with an ISO writer just to add files: that can erase the persistence and the
prepared installer. Keep a backup of its boot menu and persistence image before
modifying them, with the image unmounted for a consistent copy.

The original preparation was done on HP. It inspected the specific installer
image, built an ext4 overlay over its Casper squashfs layers, installed SSH and
Avahi into that overlay, added the Wi-Fi profile and public keys, then added the
rescue menu while preserving the original installer entries. This was a custom
preparation for that image, **not a tested general-purpose USB builder**. A newer
ISO may use different squashfs layers, persistence conventions, or boot menus;
inspect its Casper documentation and files rather than assuming the same paths.

Two preparation details mattered:

1. HP's active Wi-Fi secret came from its Netplan-managed profile. NetworkManager's
   API alone did not expose it. Read the authorized local configuration without
   printing the password; write the portable profile with root-only permissions.
2. The initial inspection mount was read-only. The preparation had to explicitly
   remount the verified USB writable before committing the image and boot menu.

The USB contains a saved Wi-Fi secret even though its SSH keys are public. Keep
the stick private. Do not commit its image, Wi-Fi profiles, password files, SSH
private keys, or authenticated Tailscale/T3/provider state to this repository.
If using multiple rescue sticks simultaneously, give them distinct hostnames
and SSH host identities. The installed laptops must also have their own SSH
host keys and Tailscale identities; do not clone the live system's identities.

After rebuilding, boot it on actual hardware, verify Wi-Fi and key-only SSH,
reboot it, and verify again. Also verify the clean installer entry still works.

## Diagnose the boot failure before reinstalling

From the live USB, record the layout and firmware mode:

```bash
hostnamectl
lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,PTTYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS
ls -l /dev/disk/by-id/
sudo fdisk -l
sudo blkid
test -d /sys/firmware/efi && echo 'Live USB booted in UEFI mode' || echo 'Live USB booted in BIOS mode'
```

Confirm which device is the USB and which internal disk the BIOS calls HDD0.
Linux names such as `/dev/sda` can change when USB devices are attached. Use a
verified `/dev/disk/by-id/…` identity for disk-changing operations and verify
where it resolves immediately before writing.

For the observed “Missing operating system” failure, Ubuntu could be present
and readable while the firmware failed to start its bootloader. That is a
different problem from GRUB starting and reporting a missing root UUID, or the
kernel starting and failing to mount root. Follow the actual failure stage.

### What “MS-DOS boot” means here

These are separate settings that must agree:

| Layer | Working choice on these ThinkPads |
| --- | --- |
| Firmware boot mode | Legacy Only |
| Boot disk partition-table type | DOS/MBR, displayed as `msdos` by some tools |
| Boot partition flag | Active/boot on the intended boot partition |
| Linux filesystem | ext4; `msdos` describes the partition table, not the filesystem |
| Bootloader | GRUB's BIOS target, `i386-pc`, installed to the whole boot disk |

`i386-pc` is GRUB's BIOS target name even for a 64-bit Ubuntu installation.
Installing GRUB only into a partition, or onto a different disk from the BIOS
boot target, does not establish this arrangement.

Legacy GRUB can also boot GPT when correctly configured with a BIOS Boot
Partition. Our result does **not** establish that every X220/T420 rejects GPT or
that MBR is the right choice for all machines. The earlier GPT/GRUB repairs on
this hardware did not resolve the observed failure; the MBR arrangement did.
Do not convert a working modern UEFI/GPT installation just because of this note.

### Repair an existing installation

Back up the partition table, boot sectors, relevant filesystem metadata, and
`/etc/fstab` before changes. Keep the backups off the target disk. For GPT, include
its backup table at the end of the disk. Check the proposed layout and existing
partition start/end sectors before considering conversion. Never use
`mklabel msdos` as a supposedly non-destructive repair: it replaces the table.

The first T420's repair preserved the installed Linux partition and its sector
boundaries. Repeating that conversion needs a new disk-specific plan; this guide
deliberately supplies no blind GPT-to-MBR command.

For an identified installed root, the repair sequence is:

1. Mount the installed root at `/mnt` and any separate `/boot` at `/mnt/boot`.
2. Confirm `/mnt/etc/fstab`, filesystem UUIDs, and the actual mounted `/boot`.
3. Bind the required `/dev`, `/proc`, `/sys`, and `/run` mounts for the chroot;
   make recursive bind mounts slave mounts before later recursive unmounting.
4. In that installed-system chroot, install/repair BIOS GRUB on the **verified
   whole boot disk**, then regenerate its configuration with `update-grub`.
   A separate `/boot` must already be mounted before doing this.
5. Unmount the temporary tree cleanly and reboot. Remove the installer USB;
   choose Legacy Only and the intended internal boot disk in firmware.

For the X220 specifically, the firmware boots the Intel SSD's GRUB and `/boot`,
which then loads the Ubuntu root on the Samsung SSD. An apparently “mostly empty”
first SSD is not disposable. Preserve its working boot partition when allocating
its remaining space to a separate filesystem.

### Fresh installation when wiping is explicitly authorized

Identify the exact internal target, confirm what will be erased, and keep the
USB out of the installation target list. For a matching legacy ThinkPad, use
the tested MBR layout, an active Linux boot partition, space before the first
partition for BIOS GRUB embedding, and GRUB on the whole internal disk. A
single-disk installation need not copy the X220's two-disk `/boot` arrangement.

An agent can prepare and run much of the installation from the live SSH session,
but the available Ubuntu installer and its automation interface must be checked
for the particular release. There is no generic unattended Ubuntu installer in
this folder. Don't treat a successful installer exit as proof of bootability.
Inspect the resulting partition table, boot flag, GRUB target, root UUID, and
`fstab`, then perform a real boot without USB.

Before leaving the live session, arrange access to the **installed** OS: create
the normal administrator user, install/enable SSH, authorize the controller
public key, and configure Wi-Fi autoconnect there. Rescue persistence alone does
not transfer any of those settings. Do not copy rescue-only SSH restrictions,
the `ubuntu` live identity, or its masked update timers into the installed OS.

## Onboard the installed system

Copy this complete folder to `~/Downloads/machine-onboarding`. As the normal
installed Ubuntu user:

```bash
cd ~/Downloads/machine-onboarding
bash setup.sh --name UNIQUE_MACHINE_NAME
bash connect.sh
bash doctor.sh
```

Replace the name placeholder with a unique lowercase hostname. Complete the
printed Tailscale authorization in the controller's browser, using the same
tailnet account. An earlier “approved” reply was not enough: the final website
Connect/Authorize step had to finish before the device left `NeedsLogin`.

The scripts install development tools and T3, enable SSH and Tailscale, enable
the T3 user service with linger, and configure AC sleep/lid behavior. GitHub is
optional through `--github`; Codex/Claude account logins remain deferred.

The first real run exposed an installer bug: sourcing `/etc/os-release` replaced
the requested hostname variable `NAME` with `Ubuntu`. `common.sh` now localizes
that variable, and a regression test verifies the requested hostname survives.

## Slow Wi-Fi: switch methods sooner

The T420s looked alike but had different wireless adapters. The slow machine had
an Intel Centrino Wireless-N 1000; the other had a Realtek RTL8188CE. On the Intel
machine we saw strong signal, low negotiated rates, and many transmit retries.
Strong signal alone did not mean a healthy connection.

Disabling Wi-Fi power saving, reconnecting, and a temporary 802.11n test did not
produce a dependable fix. The temporary 802.11n change was rolled back. We spent
too long trying variations before switching to USB. Bluetooth was suggested but
not used or validated.

The practical fallback was an offline bundle copied from the fast laptop to
`/syam2-onboarding` on the rescue USB, without erasing its installer. It contained
apt packages and metadata plus the already downloaded Node, uv, Codex, Claude,
and T3 binaries. A dependency download using the slow machine's dpkg inventory
on the fast machine found the remaining missing packages. SHA-256 checks were
verified before installation. Downloads that still needed network access could
also use SSH through the fast laptop as a jump host.

For a future transfer, inspect that bundle rather than assuming it is current.
Packages must match the target's Ubuntu release, architecture, repositories,
and dependency state. Check the dependency closure before moving the USB.
Copy software and public repository keys, **not** another user's home directory,
Tailscale state, T3 userdata, SSH private keys, or provider credentials. The T3
binary can be reused; each host's T3 identity and thread store must be its own.

Do not run concurrent apt/dpkg installations or delete lock files. Stop a stuck
download cleanly, allow any package configuration to finish, install the cached
packages, and resume setup. Remove temporary apt proxies, tunnels, Wi-Fi test
timers, and sleep inhibitors afterward. Keep the USB safely unmounted before
moving it. This fallback completed provisioning; it did not prove the Intel
Wi-Fi performance problem permanently fixed.

## Verify the three different kinds of connection

**SSH:** A fresh connection from the controller must succeed with the correct
host key and user. Give each laptop a distinct alias. Verify host fingerprints;
do not disable host-key checking to work around reinstall warnings.

**Tailscale:** An active daemon and assigned IP are insufficient. On the X220,
SSH over an existing peer connection still worked while the admin console showed
it offline. `tailscale status --json` reported `Self.Online: false` and warnings
about the coordination server. Restarting `tailscaled` restored an online,
warning-free state. The underlying cause was not established. The updated
`doctor.sh` and `connect.sh` check control-plane status and health as well as IP.

If this recurs, inspect `tailscale status`, `journalctl -u tailscaled`, DNS, and
connectivity first. Arrange a LAN SSH fallback before restarting the daemon
that carries your remote session. Do not automatically log out, replace its
identity, or repeatedly reinstall Tailscale. Check device key expiry for hosts
expected to run unattended; onboarding does not disable expiry.

**T3:** Add each SSH alias in the controller's T3 Environments settings. Match
desktop and host T3 versions when connections fail. We updated the Mac and HP
to match the ThinkPads and verified all four as Connected.

Mac SSH environments do **not** automatically become available on a phone.
Phone access through T3 Connect is a separate, optional account link. We enabled
it on HP only, using the existing service and thread store:

```bash
t3 connect link --headless
# Approve the printed code in the browser using the phone's T3 account.
t3 service restart
t3 connect status
```

Schedule that restart around any work running in the T3 service. Initial status
can be pending while the tunnel provisions; wait for a successful reachability
check. HP eventually reported four connected QUIC tunnel connections. The
three ThinkPads were deliberately left without T3 Connect, at the user's request.
Threads remain on the environment where they were created. HP access does not
make Mac-local or ThinkPad threads appear on the phone. Account machine limits
and current mobile instructions are in the
[T3 remote-access documentation](https://github.com/pingdotgg/t3code/blob/main/docs/user/remote-access.md).

## Final test order: reboot first, then close the lid

1. Run `doctor.sh` in the installed OS. Check the intended root and any separate
   `/boot` or `/data` mount, with UUID-based `fstab` entries.
2. Unmount/eject the USB. Keep the machine on AC with its lid **open**.
3. Reboot from the internal disk. If a live GNOME session blocks a planned reboot
   after unmounting, `sudo systemctl reboot -i` was the successful override in
   this session; it is not a substitute for finishing disk writes/unmounts.
4. Confirm a fresh SSH connection, healthy Tailscale status, and T3 startup
   without a desktop login. Rerun `doctor.sh`.
5. Only now close the lid on AC and establish another SSH connection. Confirm
   the physical lid state and that the services remain available.

We closed syam2 too early during testing: its newly written logind policy had
not yet taken effect, and it suspended. Opening the lid, rebooting, and repeating
the closed-lid test fixed that. `power.sh` intentionally does not restart logind
under a live session. Battery sleep policy is left unchanged.

Record the final hostname, disk roles, filesystem UUIDs, firmware mode, SSH
fingerprint, Tailscale address, enabled services, and the tests actually passed.
Keep passwords and recovery images outside Git. A live session that is reachable
today is useful progress; a fresh USB-free boot with remote services and a
closed-lid test is the evidence that onboarding is finished.
