# Orchestrator architecture

`orchestrator/` is `quo-swarm`, a single Python package (stdlib only, Python 3.10+). It keeps N agents running,
each an **unmodified official CLI** (Claude Code or Codex) in a sandbox, within the subscription usage limits of
the configured accounts. State is in SQLite. The full original guide is `orchestrator/README.md`; this page is the
short map plus what we added.

## Loop

```
serve (one process)
 ├─ console        127.0.0.1:8318   read-only usage page "/" and operator page "/runs"
 ├─ data service   127.0.0.1:8319   shared downloads (macOS; see data-sharing.md)
 └─ scheduler tick (every few s)
      ├─ read usage per account (CLI session logs / usage endpoints) → cap check
      ├─ guards: official binary, clean env, no proxy, identity == configured email
      ├─ resource guards: disk_reserve_gib, disk_critical_gib, memory_reserve_percent (macOS)
      ├─ admit: start new runs up to pool concurrency, spaced by new_run_interval / account_start_interval
      └─ resume: runs in waiting_resume continue their own CLI session (--resume / exec resume)
```

A **run** = one agent with one CLI session and one folder `runs/<id>/` (`home/`, `work/`, `tmp/`, `events.jsonl`,
`sandbox.sb`). States: `starting → running → (interrupting → waiting_resume → running)* → completed | failed`.
A turn ends when the CLI exits; the run completes when the agent stops by itself. Sessions never move between
accounts. At a usage cap, runs are interrupted and resume after the window resets.

## Modules

| File | Role | Reusable alone? |
| --- | --- | --- |
| `guard.py` | Clean environment from scratch; refuses proxy/API-key variables and config; finds the official binary; reads identity from the CLI without exposing tokens. | Yes: any harness that launches official CLIs on subscriptions. |
| `usage.py` | Usage % per account and window. | Yes. |
| `runner.py` | Builds the sandboxed command (Linux: systemd-run scope + bwrap; macOS: sandbox-exec), runs one turn, parses stream events, records tokens. Delivers operator notes on resume. | Mostly; the CLI argument builder `_cli_args` is generic. |
| `scheduler.py` | Admission, caps, resume, interrupts, recovery after restart, drain. | Tied to `store.py`. |
| `macos.py` | Seatbelt profile, process-group interrupts, free-memory reading. | Yes: sandboxing any agent CLI on macOS. |
| `datastore.py` + `tools/fetch` | Shared, deduplicated, non-listable download store. | Yes: any multi-agent machine. |
| `console.py`, `runs_page.py` | Local HTTP pages. | — |
| `store.py`, `config.py`, `cli.py` | SQLite, defaults + merge, commands. | — |

## Per-run environment (macOS)

Built from scratch, nothing inherited: `HOME=runs/<id>/home`, `CLAUDE_CONFIG_DIR` or `CODEX_HOME` = the account
profile, `SCI_HOME`, `PATH` (tools venv, tools dir, Homebrew, system), `TMPDIR` and `CLAUDE_CODE_TMPDIR` = run
`tmp/`, `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, BLAS/OpenMP threads = 1, `QUO_RUN_ID`, `QUO_AGENT`
(e.g. `Claude Code agent (claude-opus-5-5)`; used for attribution). cwd = run `work/`.

## Changes we made to the original (see `git log` of the source)

- macOS backend (`macos.py`, branches in `runner.py`, `scheduler.py`, `cli.py`, `guard.py`, `console.py`).
- `CLAUDE_CODE_TMPDIR`; login/status run from the account folder.
- Shutdown marks runs `interrupting` before signalling, so restarts do not fail runs.
- `"drain": true` per account; `quo-swarm note RUN "text" [--now]`; `/runs` page.
- `QUO_AGENT` in the environment on both platforms.
- Shared download service (`datastore.py`, `tools/fetch`).
- A coordination board between agents was added and then removed (see research-setup.md).
