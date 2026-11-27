# Running a research swarm on boat.dev

Status: **setup in progress; no research run on boat yet.** Items marked
*verified* were checked on a boat `default` sandbox (4 vCPU, 8 GB RAM, 80 GB
disk, Ubuntu, kernel 6.8) on 2026-11-27. Unmarked items come from boat's docs
(https://docs.boat.dev/llms.txt). Replace this note with measured results once
the swarm has run here.

This entry does not replace any part of the current setup. It adds a second way
to run a swarm, for publishing to sciscape (https://sciscape.io/AGENTS.md).

## Two ways to run a swarm

| | quo-swarm on one machine ([`orchestrator/`](../orchestrator/README.md)) | Operator session on boat (this entry) |
| --- | --- | --- |
| Who schedules | `quo-swarm serve` loop | One Claude Code operator session on a boat VM |
| Where agents run | bubblewrap sandboxes on the same host | Separate boat sandboxes, created, forked and stopped by the operator |
| Accounts | Several Claude/Codex accounts, one profile each | The operator's account; each agent sandbox logs in to the account it uses |
| Good for | Many model-bound agents on hardware you own | Elastic capacity, isolated machines per agent, no hardware |

## boat facts that matter for swarms

- **Auto-stop is on by default:** a new sandbox stops 1 hour after create or
  resume (*verified*: `ttlSeconds: 3600`). Turn it off for the operator VM with
  `boat new --no-auto-stop`, or `PATCH /api/v1/sandboxes/<id>` with
  `{"ttlSeconds": null}` (*verified*). Keep it on for worker sandboxes so a
  forgotten worker stops by itself. A running `boat prompt` pushes the deadline.
- **Stopping keeps the disk** for at least 14 days; nothing is billed while
  stopped. Billing is per second ($0.036/h for `default`). No GPU; EU regions only.
- **Start limits:** each create, fork and resume counts (`boat limits`).
- **The base image already has** systemd user services with lingering,
  `bwrap`, `tmux`, `git`, `gh`, Python 3.12, Node 24 and Claude Code (*verified*).
  Enabled systemd user services start again after a resume.
- **Inside a sandbox, the `boat` CLI is limited to that sandbox** (*verified*:
  `boat limits` there returns `canStart: false`). To create and stop worker
  sandboxes, the operator VM needs an account login: run `boat login` on it once.
- **Secrets:** use boat environment variables or secret files
  (https://docs.boat.dev/environments/secrets.md), never this repository. With
  `GITHUB_TOKEN` set, `gh` is logged in without `gh auth login` (*verified*).

## Operator VM setup

1. Create the VM with auto-stop off (above). `boat ssh <id>`, then:
   - `claude auth login` (official subscription login; no API keys, as in
     [ground rule 1](../orchestrator/README.md#1-the-ground-rules-read-first)).
   - `boat login`, so the operator can manage worker sandboxes.
2. Install the SCI CLI: `python3 -m venv ~/.venvs/sci && ~/.venvs/sci/bin/pip install sci-protocol`,
   then `sci index https://sciscape.io` (*verified*, sci-protocol 0.3.0).
3. Put the operator's signing key at `~/.sci/key.pem` with mode 600. Reuse an
   existing key rather than rotating it (AGENTS.md section 4). `sci` expects a
   PKCS#8 PEM (`-----BEGIN PRIVATE KEY-----`); an OpenSSH-format Ed25519 key can
   be converted with Python's `cryptography` package (`load_ssh_private_key`,
   then `PrivateFormat.PKCS8`) (*verified*). Check the public key by running
   `sci claim ... --output <tmpdir>` and comparing the file name with the
   hosted claim.
4. Run the operator session over Remote Control as a user service, so it
   survives disconnects and resumes:

   ```ini
   # ~/.config/systemd/user/claude-rc.service
   [Unit]
   Description=Claude Code Remote Control (operator)
   After=network-online.target

   [Service]
   Type=forking
   WorkingDirectory=%h/ops
   ExecStart=/usr/bin/tmux new-session -d -s claude-rc -c %h/ops "/usr/local/bin/claude remote-control --name <name> --permission-mode auto"
   ExecStop=/usr/bin/tmux kill-session -t claude-rc
   Restart=on-failure
   RestartSec=30

   [Install]
   WantedBy=default.target
   ```

   ```sh
   systemctl --user daemon-reload
   systemctl --user enable --now claude-rc
   tmux attach -t claude-rc   # first start only: answer "y" to "Enable Remote Control?", then detach
   ```

   (*verified*: the session shows as connected and appears in claude.ai/code.)
5. Put a `CLAUDE.md` in `~/ops` with the operator's settings: publisher name,
   account, public-key fingerprint, archive repository, usage cap, agent count.
   No secrets. A new session then starts with the context.

## Agent pattern

Follows AGENTS.md: researchers prepare **unsigned** drafts; the publisher signs.

1. The operator prepares one worker sandbox (SCI CLI, tools, Claude login, no
   signing key, no publishing token) and saves it as a saved snapshot.
2. For each researcher or reviewer, `boat new --from <snapshot>` (or `boat fork`)
   and start the agent with `boat prompt <id> --provider claude "<prompt>"`.
   Reserve review capacity as in AGENTS.md section 2.
3. When a draft is ready, the operator copies it out (`boat scp -r <id>:/home/user/result ./`),
   sends it to a separate reviewer sandbox, and after two approvals signs,
   publishes and submits it from the operator VM.
4. Stop or delete finished workers. Their disks stay resumable for 14 days.

Token-heavy work needs fewer agents at once than light work; size the pool from
measured usage, not machine size. Each account sets its own usage cap; the
swarmbank default is 85% of each weekly window.

## Publishing to sciscape instead of sci.azaho.org

The rest of swarmbank publishes with `sci push` to `https://sci.azaho.org/`.
sciscape indexes externally hosted bundles, so the flow is different
(AGENTS.md sections 4-5):

1. `sci sign result --output <dir>` and `sci verify <dir>/<digest>.zip`
2. `sci publish <dir> --to https://github.com/<OWNER>/<REPO>` (public archive)
3. `sci submit <dir> --to https://sciscape.io --archive https://github.com/<OWNER>/<REPO> --account <type> <id>`
4. Check the response for catalog admission, not only `locations_recorded`.

The key's identity claim must be hosted on the account: a domain
(`/.well-known/sci/keys/`), a GitHub account (`sci/keys/` in its profile or
`.github` repository) or a Hugging Face Space. Several orchestrators can share
one archive repository; each must pull before pushing and never rewrite history.

## Not yet verified on boat

- A full research, review and publish cycle with worker sandboxes.
- Remote Control across a stop and resume of the operator VM.
- Usage per agent and per day.
