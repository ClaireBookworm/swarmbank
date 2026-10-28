# Data sharing between agents: `fetch URL`

## Problem

Each agent downloaded its own copy of the same datasets (FlyWire connections: 6 copies of 813 MB; male CNS,
BANC, hemibrain, …). Disk went from 129 to 60 GB free in a day. A visible shared cache fixes disk but **biases
topic choice**: when we advertised a local FlyWire copy in the prompt, nearly every early paper used FlyWire.

## Design

A download service in the orchestrator process (`orchestrator/quo_swarm/datastore.py`, port 8319), and a client
`fetch` on every agent's `PATH` (`orchestrator/tools/fetch`, stdlib only).

```
agent:   fetch https://zenodo.org/.../proofread_connections_783.feather
         → /…/quo-swarm/data/objects/<sha256(url)[:32]>/proofread_connections_783.feather   (read-only)
```

Rules:

- **Same response whether cached or not.** Always a path. (Only latency differs.)
- **Not listable.** The sandbox allows reading files under `data/objects/` but denies listing `data/` and
  `data/objects/`. Object folders are named by a hash of the URL, so an agent reaches only files it asked for.
  `data/index.json` is not readable. The prompt does not say what is cached.
- **Read-only** to agents (sandbox write deny + mode 0444).
- **One download per URL** (per-URL lock; concurrent requests wait and share the result).
- **Content dedup.** SHA-256 is computed while streaming; if the bytes already exist under another URL, the new copy
  is discarded and both URLs map to one file (`index.json`: `url:<url>` and `sha256:<hex>` → path).
- **Safety.** http/https only; the host and every redirect must resolve to public (global) IPs, so agents cannot
  use it to reach loopback, LAN or metadata services. Stops when free disk < `disk_reserve_gib`; per-file limit
  `data_service.max_file_gib`. Log: `state/data.log` (run id, URL, hit, bytes, sha256).
- Not for authenticated sources (tokens, cookies, headers): agents download those themselves.

Prompt text (in `config/prompt.txt`):

> Download every public data file over about 20 MB with `fetch <url>` (one URL per call; it waits until the file is
> ready). It prints the path of a read-only local copy; read the file from that path and do not copy it into your
> directory. Write extracted or derived files into your directory. Use your own downloads only for sources that need
> login or special headers.

## Backfill: `scripts/dedup_runs.py`

One-time cleanup for runs that downloaded before `fetch` existed. Dry run by default; `--apply` to act.

1. Hash every file > 20 MB under `runs/*/work` (skips `.venv`, `.so/.dylib`, signed publication folders
   containing `manifest.json.sig`, and files modified in the last 15 min in running runs).
2. A file is kept in the store if it is duplicated or its URL is found in the run's own scripts/notes; URLs are
   added to `index.json`, so `fetch` on them is an instant hit.
3. Finished runs (`completed`/`failed`): other large files and `.venv` folders are deleted (publications are on SCI).
4. Running runs: duplicates are replaced by APFS clones (`cp -c`) of the stored copy, which share disk blocks but
   remain separate, writable files, so a running agent cannot corrupt the shared copy.

Result on our machine: 28 files (5.2 GB) in the store, 17 with URLs; runs 82 → 40 GB; free disk 60 → 94 GB.

## Alternatives considered

- **A data-librarian agent** answering requests: costs a model session, and an agent could steer others ("X is
  already here"). The service is deterministic and cannot steer.
- **Advertised shared folder:** herding (observed).
- **Hardlinks** for running runs' duplicates: an agent could `chmod`/write and change the shared inode. Clones are safe.

Open problems (good PR targets): archives (`.tar.gz`) are extracted into each run's folder, so extracted data is
still duplicated; no eviction policy; latency reveals cache hits.
