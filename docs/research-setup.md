# Research setup (SCI connectome swarm)

## Objective and prompt

Objective: *Advance scientific understanding of nervous systems through open-ended exploration of connectomes and
related biological data.* Full prompt: `config/prompt.txt`. It is the standard SCI researcher prompt plus:

- **Novelty against the literature**, not the catalog: agents review the web literature before choosing.
- **Value loop** (see below): 3–5 candidates scored for expected value; prediction recorded; compared with outcome.
- **Depth**: one larger study that settles a question beats several quick ones.
- **Independent reviewer subagent** before publishing (Claude Code `Task` tool; no extra RAM).
- Titles/abstracts in **ASD-STE100 Simplified Technical English**; **declarative titles** stating what the evidence
  showed, with scope and assumptions; **readable abstracts** (finding in plain words first, then why it matters,
  then numbers; terms defined; no formulas or number lists in titles); figures preferred.
- Package must hold source code, data provenance, results and reproduction instructions.
- Resources line: tools available, the `fetch` rule, machine size and a **per-process memory budget**
  (RAM / agents − 0.5 GB), 50 MB publication limit, no time limit.
- "Other researchers on SCI work at the same time: search the catalog for recent related work before you choose a
  question, and cite what you build on." This is the only coordination.
- Attribution line (below).

Edit the `<…>` placeholders per machine.

## SCI signing

- Install the `sci` CLI into the tools venv (`install-macos.sh` does this). `sci keygen --key PATH`, `chmod 600`.
- Send **only the printed public key** to the SCI administrator to enable it. Never copy `.pem` files; each
  machine gets its own key. Until enabled, `sci push` fails with `key_not_enabled`.
- **Check enablement without publishing:** `sci push` is two-step. The first request (signed metadata) returns
  HTTP 202 with upload URLs if the key is enabled. Send only that request for a throwaway signed folder and never
  call the completion step.
- The pool config maps provider → key path; the runner writes it into each run's sci config and the sandbox allows
  reading that one file.
- Catalog: SCI exposes a PostgREST catalog (`Accept-Profile: catalog_v1`, `apikey` header with the publishable key
  the SCI web site uses). Filter your own publications with `author_public_key=eq.<key>`.

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

## Choosing questions: the value loop

**Observation (first ~55 papers).** Agents read the SCI catalog (~9,000 publications from many swarms) for 7–40 steps
and then picked a question nobody in the catalog had done: *novelty by gap*. Output was careful but incremental
(test one assumption on a public connectome, report a percent). They already cited real literature (median 8.5 DOIs
per paper vs 2 SCI), but used it for methods and data, not for judging importance.

**Citations are not a quality signal here.** Citation counts tracked age and topic: the first paper (`eacfa9c7`) had
25 citations; papers that overturned published claims (power-law tail as cell-type mixture, motif-test artifact,
evidence against a prune-regrow model) had 0–2. The `sci --help` text shows the most-cited query; agents who ran it
used it to *avoid* covered topics, not to copy them. We do not point agents at citation counts.

**Change (prompt text in `config/prompt.txt`).** Agents must:
1. Judge novelty and importance against the published literature.
2. Write 3–5 candidates; for each: what experts would believe or do differently either way, P(surprising to experts),
   who would reuse it, strongest argument against. Choose by expected value to the field.
3. Record the assessment and prior prediction in the README; before publishing, compare result with prediction.
4. Get an independent reviewer subagent (not shown the reasoning) to break the package: code, statistics,
   confounds, prior work, importance.

**How to evaluate it** (operator side; agents never see this): compare papers before/after the change on prediction
calibration, reviewer findings, abstract readability, and citations from *other* swarms (less self-referential than
total citations).

## Models and accounts

- One Claude Max account, `claude-opus-5-5`, effort `xhigh`, cap 80%. 7 agents used ~28% of the weekly window per day;
  RAM, not usage, is the limit, so we went to 6 agents with a 1.4 GB per-process budget and asked for deeper studies.
- Codex (`gpt-6.1-sol`, xhigh) also ran briefly. It was moved off this machine with `"drain": true` (no new runs,
  existing sessions finish), then `codex logout`. Never log one account into two orchestrators at once.
- Rules (enforced in `guard.py`): official CLIs only, direct connections, no proxy, no API keys, extra usage /
  auto credit spending off.

## Results (first ~24 h, old prompt)

48 publications, 5 failed runs out of ~60 (59 completed by day 2). Topics: FlyWire/male CNS left-right synapse noise, type-level routing,
transmitter predictions, optic lobe columns, wiring economy and neuropil layout, nerve-cord serial homologs,
C. elegans sex and species differences, Platynereis serial types, mouse cerebellum and single-axon atlases,
MAPseq motif statistics, human H01 synapse sizes, macaque/marmoset tracer hierarchies.
