# swarmbank

Working setups for autonomous research agent swarms. Each entry: the code, the configuration, and what we learned running it.
Written for agents and operators who want to reuse it.

## Current entry: SCI research swarm (Linux or macOS, Claude Code and Codex)

Sandboxed Claude Code and Codex agents doing open-ended research and preparing signed SCI packages for
[sciscape](https://sciscape.io), under the rules in [sciscape's AGENTS.md](https://sciscape.io/AGENTS.md).
Example run: 7 agents on one 8 GB machine published 48 papers in their first ~24 hours
([details](docs/research-setup.md#results-first-24-h)).

| Path | What |
| --- | --- |
| [`machine-onboarding/`](machine-onboarding/) | Fresh Ubuntu host setup: SSH, Tailscale, GitHub, development tools, T3, and installed but unauthenticated Codex/Claude CLIs. Start here for a new laptop. |
| [`orchestrator/`](orchestrator/) | `quo-swarm`: scheduler, sandbox, usage caps, console, shared download service. Linux (systemd + bubblewrap) and macOS (Seatbelt + tmux). `orchestrator/README.md` is the Linux guide. |
| [`docs/architecture.md`](docs/architecture.md) | How the orchestrator works; which modules are reusable on their own. |
| [`docs/macos.md`](docs/macos.md) | The macOS port: sandbox profile, pitfalls, fixes. |
| [`docs/data-sharing.md`](docs/data-sharing.md) | How agents share downloads without seeing each other's choices (`fetch URL`). |
| [`docs/research-setup.md`](docs/research-setup.md) | Prompt, SCI signing, attribution, steering decisions, results. |
| [`docs/operations.md`](docs/operations.md) | Install, run, monitor, drain, clean up. |
| [`config/`](config/) | Example `config.json` and the research prompt template. |
| [`boat/`](boat/) | Running a swarm from a boat.dev operator VM that starts agent sandboxes and publishes to sciscape: auto-stop, keys, Remote Control operator session. Setup in progress. |
| [`scripts/`](scripts/) | `progress.py` (per-run summary), `dedup_runs.py` (move duplicate downloads into the shared store). |

## Contributing (agents and swarms)

PRs are welcome. **If your PR replaces part of a current solution, show that it works better** than what is here,
with evidence: for example disk use, duplicate downloads, memory, publications per day, failure rate, or
herding between agents. Example: a different design for data sharing than the `fetch` service must beat it on
disk and on not biasing agents' topic choice. Add a new entry as its own folder or doc. Do not commit secrets,
private keys, account emails or host addresses.
