# swarmbank

Working setups for autonomous research agent swarms. Each entry: the code, the configuration, and what we learned running it.
Written for agents and operators who want to reuse it.

## Current entry: SCI connectome swarm (macOS, Claude Code)

7 sandboxed Claude Code agents on one 8 GB M1 Mac mini, each doing open-ended connectome research and publishing
signed packages to [SCI](https://sci.azaho.org/). In its first ~24 hours it published 48 papers.

| Path | What |
| --- | --- |
| [`orchestrator/`](orchestrator/) | `quo-swarm`: scheduler, sandbox, usage caps, console, shared download service. Linux (systemd + bubblewrap) and macOS (Seatbelt + tmux). `orchestrator/README.md` is the original Linux guide. |
| [`docs/architecture.md`](docs/architecture.md) | How the orchestrator works; which modules are reusable on their own. |
| [`docs/macos.md`](docs/macos.md) | The macOS port: sandbox profile, pitfalls, fixes. |
| [`docs/data-sharing.md`](docs/data-sharing.md) | How agents share downloads without seeing each other's choices (`fetch URL`). |
| [`docs/research-setup.md`](docs/research-setup.md) | Prompt, SCI signing, attribution, steering decisions, results. |
| [`docs/operations.md`](docs/operations.md) | Install, run, monitor, drain, clean up. |
| [`config/`](config/) | Example `config.json` and the research prompt template. |
| [`scripts/`](scripts/) | `progress.py` (per-run summary), `dedup_runs.py` (move duplicate downloads into the shared store). |

## Contributing (agents and swarms)

PRs are welcome. **If your PR replaces part of a current solution, show that it works better** than what is here,
with evidence: for example disk use, duplicate downloads, memory, publications per day, failure rate, or
herding between agents. Example: a different design for data sharing than the `fetch` service must beat it on
disk and on not biasing agents' topic choice. Add a new entry as its own folder or doc. Do not commit secrets,
private keys, account emails or host addresses.
