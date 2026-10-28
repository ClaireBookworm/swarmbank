# macOS port

The original orchestrator needs systemd and bubblewrap. On macOS we use `sandbox-exec` (Seatbelt) and a tmux
restart loop. Install with `orchestrator/install-macos.sh`.

## Sandbox profile (`macos.py:profile`)

Seatbelt cannot remap paths, so agents see real host paths. Order matters: later rules win.

```scheme
(version 1)
(allow default)
(deny file-read-data file-write* (subpath "$HOME"))
(deny file-read-data file-write* (subpath "/private/tmp") (subpath "/Volumes"))
(deny file-write* (subpath "/"))
(allow file-write* (subpath "/dev") (subpath "/private/var/folders"))
(allow file-read* file-read-data file-write* (subpath RUN_DIR) (subpath ACCOUNT_PROFILE))
(allow file-read* file-read-data (subpath TOOLS) (subpath CLI) (subpath SHARED_RO...))
(allow file-read* file-read-data (subpath DATA/objects))                 ; shared downloads
(deny file-read-data (literal DATA) (literal DATA/objects))              ; ...but not listable
(allow file-read* file-read-data (literal SIGNING_KEY))
```

Network is allowed (agents need the model API, SCI, and data sources). Agents can reach loopback services, so keep
anything sensitive off 127.0.0.1 or authenticate it. Verify with `tests/macos_sandbox_check.py` (16 checks).

No per-run CPU/memory limits exist (no cgroups). Instead: size `concurrency` for RAM, set BLAS threads to 1, tell
agents a per-process memory budget in the prompt, and `memory_reserve_percent` holds new starts and resumes when
`memory_pressure -Q` reports low free memory.

## Pitfalls and fixes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Allowed path still unreadable | `(allow file-read* …)` does not override `(deny file-read-data …)` | Name `file-read-data` explicitly in allows. |
| Claude login: "unknown error … low max file descriptors" | cwd was inside the hidden home | Run login/status with cwd and `HOME` = account folder, under an account-only profile. |
| Agent fails at once: `EPERM … /tmp/claude-<uid>` | Claude Code ignores `TMPDIR` for its own temp folder | Set `CLAUDE_CODE_TMPDIR`; allow it in `guard.clean_env`. |
| No keychain over SSH | No GUI login session | With `CLAUDE_CONFIG_DIR` set, Claude Code stores credentials in `.credentials.json` there. Codex: `cli_auth_credentials_store="file"`. |
| No launchd GUI domain over SSH, no root | — | `quo-swarm-service` runs `serve` in tmux with a restart loop. Not reboot-persistent. |
| Runs marked `failed` after restart | Signal sent before state change | `interrupt_all()` marks `interrupting` first, then SIGINTs process groups. |
| Codex binary check fails | npm path differs on macOS; Homebrew cask is also official | `guard.codex_binary` accepts the cask binary or the npm darwin-arm64 vendor binary; sandbox reads the whole release folder. |
| Console status crashes | no `systemctl` | Proxy check uses `launchctl list`. |

## Sizing on an 8 GB M1

RAM was the only binding limit. At 7 agents memory was full with some swap while one Claude Max account was at
~2% of its weekly window after several hours (28% after ~24 h). Disk grew ~70 GB/day before the shared store.
Plan ~1–1.5 GB RAM per agent.
