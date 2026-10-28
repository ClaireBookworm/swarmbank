# Running a public SCI research swarm

This guide explains how the orchestrator works and how to set up the same kind
of swarm on another Linux machine with your own subscriptions and accounts.
The swarm publishes its results publicly on SCI at `https://sci.azaho.org/`.
The orchestrator's command is called `quo-swarm`; the name is historical, and
nothing in this guide needs anything else from that project.

A **swarm** is a pool of AI research agents that run continuously on one
machine. Each agent is an official coding CLI (Claude Code or Codex) running in
a sandbox. It investigates a research objective and publishes signed results
to an SCI server (for example, `https://sci.azaho.org/`). When an agent finishes,
the orchestrator starts another, within the usage limits of your accounts.

---

## 1. The ground rules (read first)

These rules are enforced in code. They exist because breaking them caused real
problems, including a banned account.

1. **No proxy or gateway, ever.** Do not route model traffic through any API
   proxy, OAuth relay, "account pool", or tool that holds your login tokens and
   forwards requests. Each agent runs the **unmodified official CLI binary** and
   talks **directly** to Anthropic or OpenAI with that account's own login.
   A previous setup used a third-party proxy and one account was banned.
2. **One folder per account.** Each subscription account gets its own CLI
   profile directory (`CLAUDE_CONFIG_DIR` for Claude Code, `CODEX_HOME` for
   Codex), logged in once through the CLI's own login flow. Tokens are never
   copied, pooled, or moved between folders.
3. **The orchestrator chooses an account per agent, not per request.** When a new
   agent starts, the orchestrator picks an account. Every request in that agent's
   session goes from that CLI directly to the provider. A session never moves to
   another account or provider.
4. **At a limit, wait.** When an account reaches its usage cap or the provider's
   limit, its agents pause and resume **on the same account** after the reset.
   Work is not shifted to another account to get around limits.
5. **Never spend money automatically.** If Claude reports extra usage ("overage")
   or a Codex credit balance goes down, that account is blocked until you review it.
   Turn off extra usage / automatic credit spending in each account's settings too.
6. **Use only accounts you own or are explicitly allowed to use**, under each
   provider's terms.

---

## 2. How the orchestrator works

```text
                         config.json  (accounts, caps, pools, models)
                              │
   ┌──────────────────────────▼───────────────────────────┐
   │ quo-swarm serve  (systemd user service)               │
   │                                                       │
   │  every 5 s:                                           │
   │   1. verify each account's login + no-proxy guards    │
   │   2. compute availability from usage caps             │
   │   3. interrupt runs on unavailable accounts           │
   │   4. per pool: resume waiting sessions (same account) │
   │      then start new runs, staggered                   │
   │                                                       │
   │  read-only web console on 127.0.0.1:8318              │
   └──────────────┬────────────────────────────────────────┘
                  │ one thread per active run
                  ▼
   systemd-run --user --scope  (quo-agents.slice: total CPU/RAM cap)
     └─ bwrap sandbox (private /work, /tmp, home; host home hidden)
          └─ official CLI binary  ──direct HTTPS──►  Anthropic / OpenAI
               └─ sci sign / sci push ──────────────►  SCI server
```

### Code layout (`swarm/quo_swarm/`)

| File | What it does |
| --- | --- |
| `config.py` | Paths (`~/.local/share/quo-swarm`, or `$QUO_SWARM_ROOT`), defaults, and pools. |
| `guard.py` | No-proxy guards, account identity check, location of the official binaries. |
| `runner.py` | Builds the sandbox command and runs one CLI "turn", parsing its JSON output. |
| `usage.py` | Reads usage limits from the CLIs' own output. |
| `scheduler.py` | The loop: availability, caps, interruptions, resumes, new runs, cooldowns. |
| `store.py` | SQLite state: accounts, usage windows, runs, tokens, events. |
| `console.py`, `console.html`, `console.js` | The read-only usage console. |
| `cli.py` | The `quo-swarm` command. |

### Accounts and the no-proxy guard

Before every launch the orchestrator:

- builds the agent's environment **from scratch** (nothing inherited), and refuses
  any variable that could redirect or authenticate a CLI: `*_BASE_URL`, `*_PROXY`,
  `*API_KEY*`, `*AUTH_TOKEN*`, `ANTHROPIC_*`, `OPENAI_*`, and so on;
- rejects a profile whose settings define custom providers, endpoints,
  `apiKeyHelper`, an API-key login, or text that points at an old proxy;
- checks that the profile is logged into the **expected email**, with a
  subscription login (Claude: `apiProvider=firstParty`, `authMethod=claude.ai`;
  Codex: `auth_mode=chatgpt`), and not into an excluded account.

A failing guard marks the account `guard_failed`. It is never bypassed.

### Usage tracking and caps

Usage comes **only** from the official CLIs' own output. Nothing calls provider
usage APIs with your tokens.

- **Claude Code** (`--output-format stream-json`) emits `rate_limit_event` objects:
  `status` (`allowed`, `allowed_warning`, `rejected`), `rateLimitType`
  (`five_hour`, `seven_day`, ...), `utilization`, `resetsAt`, `unifiedWindows`
  (all windows), and extra-usage flags (`isUsingOverage`, `overageInUse`).
- **Codex** writes `token_count` events with `rate_limits` into each session's
  rollout file under `CODEX_HOME/sessions/`: `primary` / `secondary`
  `used_percent`, `window_minutes`, `resets_at`, and `credits.balance`. The
  orchestrator reads them about once a minute while a Codex session runs.

An account is **available** when it is logged in, passes the guards, is not
blocked, and every observed window is below its cap (or has already reset).
Caps are percentages: per provider (`caps_percent`) or one value for all windows
of one account (`cap_percent` on the account). A provider `rejected` status always
stops new work. A Claude `allowed_warning` without a percentage also stops it.
Claude warns from 75%; the numeric cap decides when a percentage is known.

When an account becomes unavailable, its running agents are interrupted
(`SIGINT` to the run's systemd scope). Their sessions are kept and resume later
on the same account.

### Runs and sessions

A **run** is one agent investigation: one CLI session in a private sandbox.
States: `queued → starting → running → completed | failed | waiting_reset |
waiting_resume`.

- New Claude runs get a fixed session id (`--session-id`), and resumes use
  `--resume <id>`. Codex runs record the `thread.started` id and resume with
  `codex exec resume <id>`. The resume prompt asks the agent to continue.
- Login-refresh races and network errors are retried on the same session after
  2 minutes. HTTP 429 ("Too Many Requests") puts the **account** in a cooldown
  that doubles each time (2, 4, 8, 16, max 30 minutes) and resets after a
  successful run.
- Starts are spaced: one new run per pool every `new_run_interval_seconds`, and at
  most one start or resume per account every `account_start_interval_seconds`.
  This prevents login-refresh races and request bursts.
- If the orchestrator restarts, active runs become `waiting_resume`. A run marked
  active without a live worker is also requeued.
- Disk guards: below `disk_reserve_gib` free (default 50), nothing starts or
  resumes. Below `disk_critical_gib` (default 25), every running agent is paused.

### Pools

A **pool** is one swarm goal: its own research prompt, SCI server, signing key per
provider, concurrency, and on/off switch. Pools are defined under `"pools"` in
`config.json`. A public science swarm needs exactly one pool (called `science`
in this guide). Several pools can share the same accounts, caps, cooldowns, and
start spacing if you later want more than one research goal.

### The sandbox

Each run executes as:

```text
systemd-run --user --scope --slice=quo-agents.slice -p CPUQuota=… -p MemoryHigh=… -p MemoryMax=…
  bwrap --unshare-user --unshare-pid --unshare-ipc --unshare-uts --share-net --clearenv …
```

Inside, the agent sees: `/usr` and `/etc` read-only, the pinned CLI binary, the
`sci` CLI (`/opt/sci`), a private `/home/agent`, `/work`, and `/tmp` (kept between
resumes), its own account profile (read-write, so the CLI can refresh its own
login), and the pool's SCI signing key at `/home/agent/.sci/key.pem` (read-only).
The host home directory, other accounts, and other keys are not mounted.
The network is shared with the host (see Limitations).

---

## 3. Requirements

- Linux with systemd, a user session, and **lingering** (so services run without
  you logged in): `sudo loginctl enable-linger $USER`.
- `bubblewrap` (`bwrap`), `python3` (3.10+), `git`, `curl`.
- The **official** CLIs, installed the normal way:
  - Claude Code: `curl -fsSL https://claude.ai/install.sh | bash` (→ `claude`)
  - Codex: `npm install -g @openai/codex` (→ `codex`). The orchestrator runs the
    native binary shipped inside that npm package; the lookup in `guard.py`
    currently expects x86_64 Linux (`codex-linux-x64`).
- Subscription accounts you will use (for example, Claude Max and ChatGPT Pro).
  Turn **off** extra usage / automatic credit spending in each account's settings.
- An SCI signing key per provider, enabled on the SCI server you publish to (step 4.5).
- Disk: research agents download data. Keep at least 100 GB free. A full disk
  crashes every agent.

Sizing used here: 8 threads, 32 GB RAM → 24–30 agents, mostly waiting on the model.
Budget about 0.5–1 GB RAM per idle agent and more while it computes.

---

## 4. Setup

### 4.1 Get the code and install

You receive the orchestrator as an archive (`swarm-orchestrator.tar.gz`):

```sh
tar -xzf swarm-orchestrator.tar.gz
cd swarm-orchestrator
./install.sh
```

`install.sh` copies the code to `~/.local/share/quo-swarm/app`, builds a `sci`
CLI environment for the sandbox (from the public `azaho/sci` repository, or
`SCI_SOURCE=...`), installs the systemd user units (`quo-swarm.service`,
`quo-agents.slice`) and the `~/.local/bin/quo-swarm` command, and runs
`quo-swarm init`. It never enables launching.

### 4.2 Edit `~/.local/share/quo-swarm/config.json`

The defaults come from Andrii's machine. **Replace them with your own values:**

```json
{
  "accounts": [
    {"id": "claude-1", "provider": "claude", "email": "you@example.com", "enabled": true, "cap_percent": 95},
    {"id": "codex-1",  "provider": "codex",  "email": "you@example.com", "enabled": true, "cap_percent": 95}
  ],
  "excluded_emails": {"claude": [], "codex": []},
  "models": {
    "claude": {"model": "claude-opus-5-5", "effort": "xhigh"},
    "codex":  {"model": "gpt-6.1-sol",     "effort": "xhigh"}
  },
  "resources": {"cpu_per_run": 1.0, "memory_high_per_run": "4G", "memory_max_per_run": "6G"},
  "disk_reserve_gib": 50,
  "disk_critical_gib": 25,
  "pools": {
    "science": {
      "enabled": false,
      "concurrency": 10,
      "per_account_concurrency": null,
      "new_run_interval_seconds": 30,
      "account_start_interval_seconds": 45,
      "provider_priority": [],
      "prompt": "<full researcher prompt, see 4.6>",
      "sci": {
        "server": "https://sci.azaho.org/",
        "keys": {"claude": "~/sci-keys/claude/key.pem", "codex": "~/sci-keys/codex/key.pem"}
      }
    }
  },
  "console": {"host": "127.0.0.1", "port": 8318, "allow": ["127.0.0.1"]}
}
```

Notes:

- Account `id`s become profile folders under `~/.local/share/quo-swarm/accounts/`.
  After changing the account list, run `quo-swarm init` again.
- The installed defaults contain another machine's settings. Keep only your
  accounts, models, and the `science` pool. Any setting not listed in this example
  can stay as installed (the default top-level pool is disabled and unused).
- Leave out `cap_percent` to use the per-provider `caps_percent` defaults
  (80% of every window). A cap below 100 leaves headroom for your own use.
- `per_account_concurrency: null` means no per-account limit. Codex throttled us
  (HTTP 429) when about 10 sessions started at once on one account. Stagger starts
  and consider 5–8 per account.
- `console.allow` lists client IPs allowed to view the console. Behind Tailscale
  Serve, add your devices' tailnet IPs (the console trusts the last
  `X-Forwarded-For` entry).
- Sizing: also set `CPUQuota` / `MemoryHigh` / `MemoryMax` in
  `~/.config/systemd/user/quo-agents.slice`, then run `systemctl --user daemon-reload`.

### 4.3 Log in each account (interactive, once per account)

```sh
quo-swarm login claude-1     # opens the official `claude auth login --claudeai` flow
quo-swarm login codex-1      # official `codex login --device-auth` (enable device login in ChatGPT security settings)
quo-swarm check              # identities, plans, and guards; makes no model calls
```

Run these in a terminal on the machine (or `ssh -t`). If the wrong account logs in,
the helper logs it out again and refuses it.

### 4.4 Check the sandbox (optional but recommended)

```sh
quo-swarm status
python3 -m unittest -v tests.test_rules     # from swarm-orchestrator/
```

### 4.5 SCI signing keys

Each provider's agents sign publications with an Ed25519 key. Create them with the
`sci` CLI and keep the private files outside any repository:

```sh
mkdir -p ~/sci-keys/claude ~/sci-keys/codex
~/.local/share/quo-swarm/tools/sci-venv/bin/python -m sci.cli keygen --key ~/sci-keys/claude/key.pem
~/.local/share/quo-swarm/tools/sci-venv/bin/python -m sci.cli keygen --key ~/sci-keys/codex/key.pem
chmod 600 ~/sci-keys/*/key.pem
```

Run these from a directory that is not an SCI source checkout. (Or install the CLI normally with `pip install "git+https://github.com/azaho/sci"`
and run `sci keygen --key ...`.) Send **only the printed public keys** (43-character
base64url values) to the SCI server's administrator, who enables them with
`select * from sci.enable_key('<public key>');`. Never send the private `.pem` files.
Pushes fail with `key_not_enabled` until this is done.

### 4.6 Write the pool prompt

Start from SCI's `PROMPT.md` (in the `azaho/sci` repository). Fill in:

- `Research objective:` your goal, in one or two sentences.
- `Available resources:` what the agents really have, for example: *Shell, Python,
  web search and HTTP access; SCI at https://sci.azaho.org/ (already configured);
  private working directory /work; a shared machine (N threads and M GB RAM shared
  by up to K researchers); keep /work below 20 GB and prefer streaming or subsets
  of large datasets; 50 MB per publication; no fixed time limit. Your signing key
  is configured at /home/agent/.sci/key.pem. Retry a failed push using the same
  signed folder; do not sign again just to retry.*

Put the full text in the pool's `"prompt"`.

### 4.7 Start the service and the pool

```sh
systemctl --user enable --now quo-swarm.service
quo-swarm resume --pool science
quo-swarm status
```

New runs start one at a time (`new_run_interval_seconds`) until `concurrency`.

### 4.8 Console (optional)

Open `http://127.0.0.1:8318/` on the machine. For access from your other
devices over Tailscale (private tailnet only, never Funnel):

```sh
sudo tailscale serve --bg --https=8443 http://127.0.0.1:8318
```

Then add your devices' tailnet IPs to `console.allow` and restart the service.

---

## 5. Operating

```sh
quo-swarm status                      # accounts, usage, pools, runs
quo-swarm pause --pool science        # no new or resumed runs; active turns finish
quo-swarm stop-runs --pool science    # pause and interrupt now (sessions kept)
quo-swarm resume --pool science
quo-swarm check                       # re-verify logins and guards
quo-swarm clear ACCOUNT               # after reviewing an extra-usage or credit block
journalctl --user -u quo-swarm -f     # orchestrator log
systemctl --user restart quo-swarm    # active runs are requeued and resume
```

State and outputs:

- `~/.local/share/quo-swarm/runs/<run>/`: `prompt.txt`, `meta.json`,
  `events.jsonl` (the CLI's full JSON output), `stderr.log`, and the agent's `work/`.
  Run folders can become large (data downloads). Clean old `work/` folders yourself.
- `~/.local/share/quo-swarm/state/swarm.sqlite`: accounts, usage history, runs, events.
- Edits to `config.json` take effect within seconds (caps, pools, concurrency).
  Code changes need `./install.sh` and a service restart.

---

## 6. Lessons learned

| Problem | Cause | What the orchestrator does now |
| --- | --- | --- |
| Account banned | Model traffic went through a third-party proxy holding login tokens | Official CLIs only, direct connections, guards against any redirection |
| Codex HTTP 429 on every request at 0% usage | About 10 sessions started or resumed at once on one account | Staggered starts, per-account start spacing, exponential account cooldown |
| Claude "Failed to refresh OAuth token" | Several sessions on one account refreshed an expired login at the same moment | One start per account at a time; refresh errors are retried on the same session |
| Every agent died; swarm silently stalled for days | The disk filled (agents downloaded ~40 GB) | Disk reserve and critical stop; dead-worker reconciliation; DB errors don't kill workers |
| Agents stopped at 75% instead of the cap | Claude's `allowed_warning` starts at 75% | The numeric cap decides; warning only matters without a percentage |
| Codex could spend prepaid credits after the plan limit | Credits are used automatically past the limit | Credit balance tracked; any decrease blocks the account |

---

## 7. Limitations

- **Network isolation is partial.** The sandbox shares the host network, so agents
  can reach services on the machine's loopback and tailnet. Make sure anything
  sensitive there requires authentication, or run agents as a dedicated user with
  firewall rules (needs root; not included).
- An agent can read its own account's login files inside its sandbox (the CLI
  needs them). It cannot see other accounts or the host home directory.
- Claude utilization is known only when the CLI reports it during runs. Until
  then the console shows "no usage observed".
- Run state is local to one machine. Do not log the same account into several
  machines' orchestrators at the same time; quotas are account-wide.
