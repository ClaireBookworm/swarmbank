# Operations

## Install (macOS)

```sh
cd orchestrator && ./install-macos.sh          # app, tools venv (sci + science stack), fetch, wrappers; never enables launching
cp ../config/config.example.json ~/.local/share/quo-swarm/config.json   # then edit accounts, prompt, key path
quo-swarm init
quo-swarm login claude-1                        # official CLI login into the account folder
quo-swarm check                                 # identity, plan, guards; no model calls
quo-swarm-service start                         # tmux session "quo-swarm", restart loop, log state/serve.log
quo-swarm resume --pool science                 # enables the pool
```

Linux: `./install.sh` and systemd, per `orchestrator/README.md`. The data service is macOS-only for now.

## Daily commands

| Command | Use |
| --- | --- |
| `quo-swarm status` | Accounts, usage %, pool targets, run counts. |
| `quo-swarm-service restart` | After code/config changes. Runs are interrupted and resume their sessions. |
| `quo-swarm stop-runs` / `pause` | Stop agents / stop new starts. |
| `quo-swarm note RUN "text" [--now]` | Message one agent at its next resume (`--now` interrupts it). |
| `"drain": true` on an account | No new runs; existing sessions finish. Use before moving an account. |
| `python scripts/progress.py [N]` | Per run: tool calls, sci sign/push commands, last N messages. |
| `python scripts/dedup_runs.py [--apply]` | Reclaim disk (see data-sharing.md). |
| `tail -f ~/.local/share/quo-swarm/state/data.log` | Shared downloads. |

Console: `http://127.0.0.1:8318/` (usage) and `/runs` (operator-only run summaries; agents never see it). From
another device use an SSH tunnel: `ssh -N -L 8318:127.0.0.1:8318 USER@HOST`.

## Guards that stop work

| Guard | Default | Effect |
| --- | --- | --- |
| Usage cap per account | 80% | No new turns; running turns interrupted until reset. |
| `disk_reserve_gib` | 50 | No new runs; `fetch` refuses downloads. |
| `disk_critical_gib` | 25 | All runs interrupted (sessions kept). |
| `memory_reserve_percent` (macOS) | 15 | No new starts/resumes while free memory is lower. |

## Recovering

- Runs wrongly `failed` by a crash can be requeued: `update runs set state='waiting_resume' where id=…` in
  `state/swarm.sqlite`, then let the scheduler resume them.
- Wait loops in scripts: use `until <check>; do sleep 5; done`, not fixed sleeps.
