"use strict";
const $ = id => document.getElementById(id);
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v; else if (k === "style") node.style.cssText = v; else node.setAttribute(k, v);
  }
  for (const c of children) if (c !== null && c !== undefined) node.append(c instanceof Node ? c : String(c));
  return node;
}
const num = n => (n || 0).toLocaleString();
function ago(t, now) {
  if (!t) return "never";
  const s = Math.round(now - t), a = Math.abs(s);
  const f = a < 90 ? `${a}s` : a < 5400 ? `${Math.round(a / 60)}m` : a < 172800 ? `${(a / 3600).toFixed(1)}h` : `${(a / 86400).toFixed(1)}d`;
  return s >= 0 ? `${f} ago` : `in ${f}`;
}
const stateClass = s => ({ready: "ok", limited: "warn", logged_out: "warn", unknown: "warn"}[s] || "bad");

function poolCards(d) {
  const counts = d.run_counts || {};
  const active = (counts.running || 0) + (counts.starting || 0) + (counts.interrupting || 0);
  const waiting = (counts.waiting_reset || 0) + (counts.waiting_resume || 0);
  const card = (title, rows) => el("div", {class: "card"}, el("h3", {}, title),
    el("div", {class: "kv"}, ...rows.flatMap(([k, v]) => [el("div", {}, k), el("div", {}, v)])));
  const poolRows = (d.pools || []).map(p => [p.name, `${p.enabled ? "ON" : "paused"} · ${p.active}/${p.concurrency} active · ${p.waiting} waiting · ${p.completed} done`]);
  return [
    card("Pools", poolRows),
    card("Pool (quo)", [["Launch", d.launch_enabled ? "ENABLED" : "disabled"], ["Concurrency", String(d.pool.concurrency)],
                  ["Per account", String(d.pool.per_account_concurrency)], ["Task", d.task_configured ? "configured" : "not set"]]),
    card("Runs", [["Active", String(active)], ["Waiting", String(waiting)], ["Completed", String(counts.completed || 0)],
                  ["Failed", String(counts.failed || 0)]]),
    card("Models", [["Claude", `${d.models.claude.model || "not set"} ${d.models.claude.effort || ""}`],
                    ["Codex", `${d.models.codex.model || "not set"} ${d.models.codex.effort || ""}`],
                    ["Excluded", (d.excluded.claude || []).concat(d.excluded.codex || []).join(", ") || "none"],
                    ["Limit policy", "wait for reset; never switch"]]),
  ];
}

function accountCard(a, now) {
  const bars = a.usage.length ? a.usage.map(u => {
    const pct = u.used_percent;
    const cap = a.caps[u.window] ?? a.caps.default ?? Math.min(...Object.values(a.caps));
    const fill = el("div", {class: "fill" + (pct >= cap ? " hi" : pct >= cap * 0.8 ? " mid" : ""),
                            style: `width:${Math.min(100, pct || 0)}%`});
    return el("div", {class: "bar"},
      el("div", {class: "lbl"}, el("span", {}, `${u.window}${u.status ? " · " + u.status : ""}`),
        el("span", {}, pct === null || pct === undefined ? "n/a" : `${pct.toFixed(0)}% / cap ${cap}%`)),
      el("div", {class: "track"}, fill, el("div", {class: "cap", style: `left:${cap}%`})),
      el("div", {class: "muted small"}, `resets ${ago(u.resets_at, now)} · seen ${ago(u.observed_at, now)}`));
  }) : [el("div", {class: "muted small", style: "margin-top:10px"}, "No usage observed yet (reported by the CLI during runs).")];
  const t24 = a.totals["24h"] || {}, t7 = a.totals["7d"] || {};
  const credits = a.credits_latest === null || a.credits_latest === undefined ? "—"
    : `${a.credits_latest}${a.credits_baseline !== null && a.credits_latest < a.credits_baseline ? " (SPENT!)" : ""}`;
  return el("div", {class: "card"},
    el("h3", {}, el("span", {}, a.id), el("span", {class: "pill " + stateClass(a.state)}, a.enabled ? a.state : "disabled")),
    el("div", {class: "kv"},
      el("div", {}, "Account"), el("div", {}, a.email),
      el("div", {}, "Logged in"), el("div", {}, a.logged_in ? `yes · ${a.verified_email || "?"}` : "no"),
      el("div", {}, "Plan"), el("div", {}, a.plan || "—"),
      el("div", {}, "Verified"), el("div", {}, ago(a.checked_at, now)),
      el("div", {}, "Note"), el("div", {}, a.reason ? `${a.reason}${a.until ? " until " + new Date(a.until * 1000).toLocaleString() : ""}` : "—"),
      el("div", {}, "Credits"), el("div", {}, credits),
      el("div", {}, "Tokens 24h"), el("div", {}, `${num(t24.input)} in · ${num(t24.cached)} cached · ${num(t24.output)} out`),
      el("div", {}, "Tokens 7d"), el("div", {}, `${num(t7.input)} in · ${num(t7.output)} out · ${t7.runs || 0} runs`)),
    ...bars);
}

async function refresh() {
  try {
    const r = await fetch("api/status", {cache: "no-store"});
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    $("problem").hidden = true;
    const p = $("proxy");
    p.textContent = d.proxy.ok ? "No proxy ✓" : "PROXY DETECTED";
    p.className = "pill " + (d.proxy.ok ? "ok" : "bad");
    const l = $("launch");
    l.textContent = d.launch_enabled ? "Launch ENABLED" : "Launch disabled";
    l.className = "pill " + (d.launch_enabled ? "warn" : "ok");
    $("pool").replaceChildren(...poolCards(d));
    $("accounts").replaceChildren(...d.accounts.map(a => accountCard(a, d.now)));
    $("runs").replaceChildren(...(d.runs.length ? d.runs.map(r => el("tr", {},
      el("td", {}, el("code", {}, r.id.slice(0, 8)), " ", el("span", {class: "muted small"}, r.pool || "")), el("td", {}, r.account), el("td", {}, r.state),
      el("td", {}, r.model || ""), el("td", {class: "num"}, String(r.turns)), el("td", {class: "num"}, num(r.input_tokens)),
      el("td", {class: "num"}, num(r.cached_input_tokens)), el("td", {class: "num"}, num(r.output_tokens)),
      el("td", {}, ago(r.updated_at, d.now)), el("td", {}, r.reason || (r.resume_at ? "resume " + ago(r.resume_at, d.now) : ""))))
      : [el("tr", {}, el("td", {colspan: "10", class: "muted"}, "No runs yet."))]));
    $("events").replaceChildren(...(d.events.length ? d.events.map(e => el("tr", {},
      el("td", {}, new Date(e.at * 1000).toLocaleString()), el("td", {}, e.level), el("td", {}, e.account || ""),
      el("td", {}, e.message))) : [el("tr", {}, el("td", {colspan: "4", class: "muted"}, "No events."))]));
  } catch (err) {
    $("problem").hidden = false;
    $("problem").textContent = `Console data unavailable: ${err.message}`;
  }
}
refresh();
setInterval(refresh, 10000);
