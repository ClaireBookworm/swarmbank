# Per-machine power monitoring with a smart power strip

Plug every swarm host into one energy-monitoring smart strip and log each outlet's
watts, volts, amps and kWh once a minute on an always-on Linux host. You get real
numbers for idle draw, load draw and monthly electricity cost per machine, without
installing anything on the machines being measured.

Tested with a **TP-Link Tapo P316M** (6 outlets, per-outlet energy monitoring,
firmware 1.4.1) and [python-kasa](https://github.com/python-kasa/python-kasa) 0.12.0,
with the logger on Ubuntu 26.04 LTS. Other Tapo strips that python-kasa supports
with per-outlet energy (for example the P304M) should work, but are untested.

| File | What |
| --- | --- |
| `tapo_logger.py` | Read-only logger: one CSV row per outlet per interval, plus `latest.json`. |
| `tapo-logger.service` | Hardened systemd unit running as a dedicated `tapo-logger` user. |
| `install.sh` | Installs the venv, service, root-only credentials and the `tapo-power` viewer. |
| `tapo-power` | `tapo-power` shows the latest reading; `tapo-power OUTLET 120` shows history. |

## The strip powers everything, so configure it before trusting it

A strip that powers every host is a single point of failure. Two problems we
actually hit:

- **Firmware auto-update rebooted the strip and cut power to every outlet.** It was
  enabled by default and fired within an hour of setup. Desktops without a battery
  turned off. **Turn auto-update off** and update manually when hosts are idle.
- **Laptops with closed lids went to sleep** when AC dropped for a second. The
  onboarding scripts deliberately only change *AC* lid behavior, so on battery
  Ubuntu's default "lid closed → suspend" applied. See
  [Power interruptions](#what-happens-to-the-hosts-during-a-power-interruption).

### 1. Pair it (phone required)

Initial pairing needs the Tapo phone app (Bluetooth plus the strip's temporary
setup Wi-Fi); it cannot be done from a server. The strip uses **2.4 GHz** Wi-Fi.

1. Tapo app → **+** → choose the model → join your Wi-Fi.
2. **Rename each outlet after the host plugged into it.** These names become the
   `outlet` column in the log.
3. In the strip's settings (tap an outlet's *name*, not its power button, then the
   gear icon):
   - **Firmware Update → Auto Update: off.** (The logger host can also check this:
     python-kasa reports `auto_update_enabled`.)
   - **Power-on state after an outage: On or Last state**, never Off.
   - No schedules, timers, away mode, auto-off, or overload thresholds on host outlets.
4. **Me → Third-Party Services → Third-Party Compatibility: on.** Without it, newer
   firmware rejects local connections with HTTP **403** at `handshake1`. Firmware
   updates can reset this, so check it after any update.

Each outlet's power button in the app is a hard power cut for that host.

### 2. Understand the privacy and security trade-off

Third-Party Compatibility enables the strip's **local LAN API**. It still requires
the TP-Link account email and password; it does not expose the strip to the
internet or send data to third parties. The real risks:

- Anything on the LAN with the account credentials can control the strip directly,
  and the local protocol has historically been weaker than the cloud path.
- The worst outcome is **someone switching hosts off**. The strip holds no secrets
  besides usage patterns.
- The larger risk is **storing the TP-Link password on a host**: it controls every
  device on that account from anywhere via the cloud.

Mitigations used here:

- A **unique** TP-Link password and **2FA** on the account. 2FA protects cloud logins;
  the local API keeps working with it on.
- The password is stored at `/etc/tapo-logger/credentials`, **root-only (0600)**, and
  handed to the service via systemd `LoadCredential`. The service runs as a separate
  system user with no shell, no home, a read-only filesystem except its state
  directory, and no capabilities.
- The normal user and agents running as that user **cannot read it**. This only holds
  if that user does **not** have passwordless sudo on the logger host. Pick the
  logger host accordingly, or accept the trade-off knowingly.
- The logger is **read-only by construction**: it only calls `update()`. A unit test
  fails if any `turn_*`/`set_*`/`reboot`/firmware call is added.

## Install the logger

On an always-on Ubuntu host on the same LAN, after `machine-onboarding/setup.sh`
(for `uv`):

```bash
# Find the strip's IP and MAC (it reports an authentication failure until credentials are given; that's fine)
uvx --from python-kasa kasa discover

cd power-monitoring
bash install.sh --host STRIP_IP --mac STRIP_MAC     # prompts for sudo, then the TP-Link email/password
```

`--mac` is optional but recommended: the logger verifies the device and
rediscovers it by MAC if its IP changes. A DHCP reservation for the strip is
simpler still. `--interval` sets seconds between readings (default 60, min 10).

The installer never writes the password to a user-readable file, never echoes it,
and keeps existing credentials on rerun. To change them:
`sudo rm /etc/tapo-logger/credentials` and rerun.

## Use it

```bash
tapo-power                  # latest reading per outlet + total
tapo-power HOSTNAME 120     # last 120 readings for one outlet
ssh logger-host tapo-power  # from the controller
```

Data lives in `/var/lib/tapo-logger/` (world-readable; power numbers only):

- `power.csv`: `time,outlet,on,watts,volts,amps,kwh_today,kwh_month`, one row per
  outlet per interval. A six-outlet strip at 60 s is ~8,600 rows/day, ~220 MB/year.
- `latest.json`: the most recent snapshot, replaced atomically.

`kwh_today`/`kwh_month` are the strip's own counters. A missing reading is blank,
not zero. If the strip is unreachable the logger logs the error to the journal
(`journalctl -u tapo-logger`) and retries every interval; no rows are written for
that time.

## What happens to the hosts during a power interruption

| Host type | Short blip | Long outage | When power returns |
| --- | --- | --- | --- |
| Desktop, no battery | Turns off | Off | Starts only if the BIOS "AC recovery / power on after power loss" setting is on. Check it before relying on remote access. |
| Laptop, lid closed, default Ubuntu | **Suspends** (lid-closed-on-battery rule) | Suspended until battery critical | Stays asleep until someone presses power |
| Laptop with the settings below | Keeps running | Runs until battery critical, then critical action | Stays off/asleep unless BIOS "Power On with AC Attach" (ThinkPad) / "Power On When AC Detected" (HP) is enabled |

To keep laptops running through blips (in addition to `power.sh`'s AC settings):

```bash
# GNOME: no idle sleep on battery
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing
# logind: ignore the lid on battery too; SIGHUP reloads logind without ending sessions
printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchDocked=ignore\n' |
  sudo tee /etc/systemd/logind.conf.d/81-lid-battery.conf
sudo systemctl kill -s HUP systemd-logind
busctl get-property org.freedesktop.login1 /org/freedesktop/login1 \
  org.freedesktop.login1.Manager HandleLidSwitch      # expect "ignore"
```

For unattended recovery from long outages, also consider setting UPower's
`CriticalPowerAction=PowerOff` (a suspended laptop is not woken by AC returning)
together with the BIOS power-on-with-AC option. Old laptops' worn batteries may
only last minutes; check `/sys/class/power_supply/BAT*/energy_full` against
`energy_full_design`.

## Verify

```bash
python3 -m unittest discover -s power-monitoring/tests -v   # offline; no device or python-kasa needed
shellcheck power-monitoring/install.sh power-monitoring/tapo-power
```

On the logger host: `systemctl is-active tapo-logger`, `tapo-power` shows every
outlet, `wc -l /var/lib/tapo-logger/power.csv` grows each interval, and
`cat /etc/tapo-logger/credentials` as the normal user fails with *Permission denied*.

## Limits

- One strip per logger instance; run a second unit with its own config for more.
- No dashboard; `power.csv` is meant for ad-hoc analysis or a later plotting tool.
- No CSV rotation. At ~220 MB/year this has not mattered.
- Tapo's local API and Third-Party Compatibility can change with firmware; pin and
  test python-kasa versions before upgrading.
