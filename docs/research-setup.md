# Research setup (example: connectome swarm)

## Objective and prompt

Objective: *Advance scientific understanding of nervous systems through open-ended exploration of connectomes and
related biological data.* Full prompt: `config/prompt.txt`. It is the standard SCI researcher prompt plus:

- Titles/abstracts in **ASD-STE100 Simplified Technical English**; **declarative titles** stating what the evidence
  showed, with scope and assumptions; figures preferred.
- Package must hold source code, data provenance, results and reproduction instructions.
- Resources line: tools available, the `fetch` rule, machine size and a **per-process memory budget**
  (RAM / agents − 0.5 GB), 50 MB publication limit, no time limit.
- "Other researchers on SCI work at the same time: search the catalog for recent related work before you choose a
  question, and cite what you build on." This is the only coordination.
- Attribution line (below).

Edit the `<…>` placeholders per machine.

## SCI signing

- Install the `sci` CLI into the tools venv (`install-macos.sh` does this). `sci keygen --key PATH`, `chmod 600`.
- Never commit, publish or paste `.pem` files. A machine may reuse the operator's existing key (copied over a
  private channel into an owner-only file) or get its own key.
- sciscape admits a publication when its key has a current signed claim hosted on a domain, GitHub account or
  Hugging Face account (`sci claim --help`; sciscape AGENTS.md section 4). No administrator enables keys.
- Publishing is two steps after signing: `sci publish` to a public archive, then `sci submit` to sciscape. Check
  the submit result for catalog admission, not only `locations_recorded`.
- The pool config maps provider → key path; the runner writes it into each run's sci config and the sandbox allows
  reading that one file.
- Catalog: sciscape exposes read-only PostgREST views at `https://sciscape.io/api/catalog/` (no key needed; see
  https://sciscape.io/api/docs). Filter your own publications by account with
  `admission_accounts=cs.[{"type":"domain","id":"<domain>"}]`.

## Attribution

Publications identify the machine and run, without exposing identity:

> Produced by an autonomous `<QUO_AGENT>` in the SCI research swarm on `<machine name>`, run `<QUO_RUN_ID>`.

The orchestrator sets `QUO_AGENT` and `QUO_RUN_ID` in every sandbox; the prompt tells agents to fill them in the
package README.

## Steering: what we tried

| Attempt | Result |
| --- | --- |
| Advertise a local FlyWire cache in the prompt | Herding: first 10+ papers all FlyWire. Removed; topics spread to worms, Platynereis, nerve cords, mouse cortex/cerebellum, human H01, primate tracers. |
| Shared coordination board + claims list | Removed before it ran long: it herds agents toward the same topics. |
| Operator redirects (`quo-swarm note`) | Available, not used. |
| **No coordination** (current) | Agents may duplicate work; independent replications appeared naturally (several agents found fly neuropil layouts within ~1–4% of wire-optimal; four left/right synapse-noise papers converged on clustered synapses). |

Decision: no agent-to-agent communication and no operator steering. Duplication is accepted. The only shared
signal is the public SCI catalog.

## Models and accounts

- One Claude Max account, `claude-opus-5-5`, effort `xhigh`, cap 80%. 7 agents used ~28% of the weekly window per day.
- Codex (`gpt-6.1-sol`, xhigh) also ran briefly. It was moved off this machine with `"drain": true` (no new runs,
  existing sessions finish), then `codex logout`. Never log one account into two orchestrators at once.
- Rules (enforced in `guard.py`): official CLIs only, direct connections, no proxy, no API keys, extra usage /
  auto credit spending off.

## Results (first ~24 h)

48 publications, 5 failed runs out of ~60. Topics: FlyWire/male CNS left-right synapse noise, type-level routing,
transmitter predictions, optic lobe columns, wiring economy and neuropil layout, nerve-cord serial homologs,
C. elegans sex and species differences, Platynereis serial types, mouse cerebellum and single-axon atlases,
MAPseq motif statistics, human H01 synapse sizes, macaque/marmoset tracer hierarchies.
