"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const show = (s) => { $(s).hidden = false; };
const hide = (s) => { $(s).hidden = true; };
const fmt = (v) => (v == null ? "n/a" : Number(v).toFixed(2));

let presets = {}, health = {}, clarifications = [], lastQuestion = null, current = null, selTable = 1, selGuest = null;

// ---- guest list parsing: "Name: a; b; c [Group]" lines or CSV (name, favourites, group) -------------
function splitCSV(line, delim) {
  const out = []; let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) { if (c === '"') { if (line[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += c; }
    else if (c === '"') q = true;
    else if (c === delim) { out.push(cur); cur = ""; }
    else cur += c;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}
function detectDelim(line) {
  if (line.includes("	")) return "	";
  const noq = line.replace(/"[^"]*"/g, "");
  return noq.split(";").length > noq.split(",").length ? ";" : ",";
}
const splitFavs = (s) => s.split(/[;|]/).map((x) => x.trim()).filter(Boolean);

function parseGuests(text) {
  const guests = [], warnings = [], seen = new Map();
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
  let header = null;
  const csvLine = lines.find((l) => !/^[^:,;	"]+?\s*:\s*\S/.test(l)) || lines[0] || "";
  const delim = detectDelim(csvLine);
  lines.forEach((line, i) => {
    let name, favs, group = "";
    const colon = /^([^:,;\t"]+?)\s*:\s*(.+)$/.exec(line);
    if (colon) {
      name = colon[1].trim();
      let rest = colon[2];
      const g = /\[([^\]]+)\]\s*$/.exec(rest);
      if (g) { group = g[1].trim(); rest = rest.slice(0, g.index); }
      favs = rest.split(/[;|]/).length > 1 ? splitFavs(rest) : rest.split(",").map((x) => x.trim()).filter(Boolean);
    } else {
      const cols = splitCSV(line, delim);
      if (i === 0 && /^(guest|name)$/i.test(cols[0]) && cols.some((c) => /fav/i.test(c))) { header = cols.map((c) => c.toLowerCase()); return; }
      if (header) {
        name = cols[header.findIndex((h) => /^(guest|name)$/.test(h))] || cols[0];
        const gi = header.findIndex((h) => /group|side|team|table/.test(h));
        group = gi >= 0 ? cols[gi] || "" : "";
        favs = [];
        header.forEach((h, k) => { if (/fav/.test(h) && cols[k]) favs.push(...(h.includes("s") && /[;|]/.test(cols[k]) ? splitFavs(cols[k]) : [cols[k]])); });
      } else if (cols.length <= 3) {
        name = cols[0]; favs = splitFavs(cols[1] || ""); group = cols[2] || "";
      } else {
        name = cols[0]; favs = cols.slice(1, 5).filter(Boolean);
      }
    }
    name = (name || "").replace(/\s+/g, " ").slice(0, 40);
    if (!name) return;
    favs = [...new Set(favs.map((f) => f.replace(/\s+/g, " ").slice(0, 80)))].slice(0, 4);
    if (!favs.length) warnings.push(`${name} has no favourites.`);
    const key = name.toLowerCase();
    if (seen.has(key)) { const n = seen.get(key) + 1; seen.set(key, n); warnings.push(`Duplicate name "${name}" renamed to "${name} (${n})".`); name = `${name} (${n})`; }
    else seen.set(key, 1);
    guests.push({ name, favorites: favs, group: group.replace(/\s+/g, " ").slice(0, 40) });
  });
  return { guests, warnings };
}
const formatGuests = (gs) => gs.map((g) => `${g.name}: ${g.favorites.join("; ")}${g.group ? ` [${g.group}]` : ""}`).join("\n");

function refreshParse() {
  const { guests, warnings } = parseGuests($("#guests").value);
  const favs = guests.reduce((a, g) => a + g.favorites.length, 0);
  const groups = new Set(guests.map((g) => g.group).filter(Boolean)).size;
  $("#parse-info").textContent = guests.length ? `${guests.length} guests, ${favs} favourites${groups ? `, ${groups} groups` : ""}` : "No guests yet";
  const w = $("#parse-warn");
  w.hidden = !warnings.length;
  w.textContent = warnings.slice(0, 3).join(" ") + (warnings.length > 3 ? ` (+${warnings.length - 3} more)` : "");
  return guests;
}
function loadPreset(key) {
  const p = presets[key];
  if (!p) return;
  $("#guests").value = formatGuests(p.guests);
  $("#constraints").value = p.constraints || "";
  $("#table-size").value = String(p.table_size);
  clarifications = []; hide("#question"); hide("#plan");
  refreshParse();
}

// ---- agent timeline ------------------------------------------------------------------------------
const pendingCalls = {};
function setStatus(text, cls) { const s = $("#status"); s.textContent = text; s.className = "status " + cls; }
const shortTag = (t) => String(t).split(":").pop().replace(/_/g, " ");
function argText(name, a) {
  a = a || {};
  if (name === "build_taste_profiles") return a.names ? `${a.names.length} guests` : "all guests";
  if (name === "apply_constraint") return `"${a.text || ""}"`;
  if (name === "seat_guests") return a.table_size ? `${a.table_size} seats per table` : "";
  if (name === "explain_table") return `table ${a.table}`;
  if (name === "table_card") return `table ${a.table}: ${(a.topics || []).map((t) => t.tag).join(", ")}`;
  if (name === "present_plan") return "";
  return JSON.stringify(a).slice(0, 80);
}
const PENDING = { build_taste_profiles: "asking Qloo", seat_guests: "optimising", table_card: "checking grounding", explain_table: "reading", apply_constraint: "validating" };
function addStep(ev) {
  const tl = $("#timeline");
  if (ev.type === "start") {
    tl.innerHTML = `<li class="thought"><span class="say">Agent started: ${esc(ev.llm)}, Qloo ${esc(ev.qloo)}, ${esc(ev.guests)} guests.</span></li>`;
    return;
  }
  if (ev.type === "thought") {
    tl.insertAdjacentHTML("beforeend", `<li class="thought"><span class="say">${esc(ev.text)}</span></li>`);
  } else if (ev.type === "tool_call") {
    const li = document.createElement("li");
    li.className = "tool";
    li.innerHTML = `<span class="tname">${esc(ev.name)}</span> <span class="args">${esc(argText(ev.name, ev.args))}</span><span class="sum pending">${esc(PENDING[ev.name] || "working")}</span>`;
    tl.appendChild(li);
    pendingCalls[ev.id] = { li, name: ev.name };
  } else if (ev.type === "progress") {
    const open = Object.values(pendingCalls).filter((p) => p.name === ev.name && $(".pending", p.li));
    const p = open[open.length - 1];
    if (p) $(".sum", p.li).textContent = `profiling guests ${ev.done}/${ev.total}`;
  } else if (ev.type === "tool_result") {
    const p = pendingCalls[ev.id];
    if (!p) return;
    if (ev.error) p.li.classList.add("bad");
    const sum = $(".sum", p.li);
    sum.className = "sum"; sum.textContent = ev.summary || "done";
    if (ev.data) {
      const d = document.createElement("details");
      d.innerHTML = `<summary>raw tool result</summary><pre>${esc(JSON.stringify(ev.data, null, 1))}</pre>`;
      p.li.appendChild(d);
    }
  }
  tl.scrollTop = tl.scrollHeight;
}
function renderStats(r) {
  const s = r.stats || {};
  const tok = (s.input_tokens || 0) + (s.output_tokens || 0);
  $("#stats").innerHTML = [`${s.turns} model turns`, `${s.tool_calls} tool calls`, `${s.qloo_calls} Qloo requests`,
    tok ? `${tok.toLocaleString()} LLM tokens` : "no LLM tokens (mock)", `${s.seconds}s`].map((x) => `<span>${esc(x)}</span>`).join("");
  show("#stats");
}

// ---- the table map -------------------------------------------------------------------------------
const initials = (n) => { const p = n.replace(/\(.*?\)/g, "").replace(/^(Dr|Mr|Mrs|Ms|Prof)\.?\s+/i, "").trim().split(/\s+/); return ((p[0]?.[0] || "?") + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase(); };
function fitRange(plan) {
  const v = plan.guests.filter((g) => !g.no_data).map((g) => g.fit);
  return v.length ? [Math.min(...v), Math.max(...v)] : [0, 1];
}
function seatStyle(g, range) {
  if (g.no_data) return "";
  const [lo, hi] = range;
  const t = hi > lo ? (g.fit - lo) / (hi - lo) : 0.5;
  const p = Math.round(Math.min(Math.max(t, 0), 1) * 100);
  return `fill:color-mix(in srgb, var(--fit-hi) ${p}%, var(--fit-lo))`;
}
const seatText = (g, range) => { if (g.no_data) return "var(--muted)"; const [lo, hi] = range; const t = hi > lo ? (g.fit - lo) / (hi - lo) : 0.5; return t >= 0.5 ? "var(--on-accent)" : "#1c1b18"; };
function tableSvg(t, byName, range) {
  const n = t.guests.length, R = 74, r = n > 11 ? 13 : 15.5;
  let s = `<svg viewBox="0 0 200 200" role="img" aria-label="${esc(t.label)}: ${n} guests"><circle class="tbl-fill" cx="100" cy="100" r="47"/>`;
  s += `<text class="tnum" x="100" y="${t.head ? 98 : 105}">${t.head ? "&#9733; " : ""}${t.index}</text>`;
  if (t.head) s += `<text class="tsub" x="100" y="116">HEAD</text>`;
  t.guests.forEach((name, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n, x = 100 + R * Math.cos(a), y = 100 + R * Math.sin(a);
    const g = byName[name];
    const cls = ["seat", g.isolated ? "iso" : "", g.no_data ? "nodata" : "", selGuest === name ? "sel" : ""].join(" ");
    s += `<g class="${cls}" data-guest="${esc(name)}" tabindex="0" role="button" aria-label="${esc(name)}, fit ${g.no_data ? "unknown" : fmt(g.fit)}">
      <title>${esc(name)}: fit ${g.no_data ? "n/a (no taste data)" : fmt(g.fit)}${g.isolated ? " (weak match)" : ""}</title>
      <circle class="s" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${r}" style="${seatStyle(g, range)}"/>
      <text x="${x.toFixed(1)}" y="${(y + 4).toFixed(1)}" style="fill:${seatText(g, range)}">${g.no_data ? "?" : esc(initials(name))}</text></g>`;
  });
  return s + "</svg>";
}
function renderMap() {
  const p = current.plan, byName = Object.fromEntries(p.guests.map((g) => [g.name, g])), range = fitRange(p);
  $("#map").innerHTML = p.tables.map((t) => `<div class="tbl${t.head ? " head" : ""}${t.index === selTable ? " sel" : ""}" data-table="${t.index}" tabindex="0" role="button" aria-label="${esc(t.label)}, ${t.size} guests, cohesion ${fmt(t.cohesion)}">
      ${tableSvg(t, byName, range)}<div class="cap"><b>${esc(t.label)}</b> &middot; cohesion ${fmt(t.cohesion)}</div></div>`).join("");
  $$("#map .tbl").forEach((el) => {
    const pick = () => { selTable = +el.dataset.table; selGuest = null; renderMap(); renderDetail(); if (window.matchMedia("(max-width: 960px)").matches) $("#detail").scrollIntoView({ behavior: "smooth", block: "start" }); };
    el.addEventListener("click", pick);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  });
  $$("#map .seat").forEach((el) => {
    const pick = (e) => { e.stopPropagation(); selTable = +el.closest(".tbl").dataset.table; selGuest = el.dataset.guest; renderMap(); renderDetail(true); };
    el.addEventListener("click", pick);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(e); } });
  });
}
function fitBar(g, range) {
  const [lo, hi] = range;
  const t = g.no_data ? 0 : (hi > lo ? (g.fit - lo) / (hi - lo) : 0.5);
  const p = Math.round(Math.min(Math.max(t, 0), 1) * 100);
  return `<div class="bar"><i style="width:${g.no_data ? 0 : Math.max(4, Math.round(g.fit * 100 / Math.max(hi, 0.01) * 0.95))}%;background:color-mix(in srgb, var(--fit-hi) ${p}%, var(--fit-lo))"></i></div>`;
}
function renderDetail(scrollGuest) {
  const p = current.plan, t = p.tables[selTable - 1], card = p.cards[selTable - 1];
  const byName = Object.fromEntries(p.guests.map((g) => [g.name, g])), range = fitRange(p);
  let h = `<div class="kicker">${t.head ? "Head table" : "Table " + t.index}</div><h3>${esc(t.label)}: ${t.size} guests</h3>
    <div class="meta">Cohesion ${fmt(t.cohesion)} &middot; least-matched guest ${fmt(t.min_fit)}</div>`;
  h += `<div class="card"><div class="kicker">${esc(card.title)}</div><p class="opener">${esc(card.opener)}</p>`;
  if (card.topics.length) h += `<ul>${card.topics.map((tp) => `<li><b>${esc(tp.tag)}</b>: ${esc(tp.because)}</li>`).join("")}</ul>`;
  h += `<p class="ask">${esc(card.ask)}</p><div class="src">${card.source === "agent" ? "Written by the agent. Every tag, favourite and name was checked against this table's Qloo data." : "Automatic card built from this table's shared Qloo tags."}</div></div>`;
  if (t.shared_tags.length) h += `<div class="tags" aria-label="Shared Qloo tags">${t.shared_tags.map((s) => `<span class="tag">${esc(s.tag)} <i>${Math.round(s.coverage * 100)}%</i></span>`).join("")}</div>`;
  else h += `<p class="meta">No tag is shared by several guests here, so the card invites them to compare favourites.</p>`;
  h += `<div class="guests">`;
  for (const name of t.guests) {
    const g = byName[name];
    const flags = (g.isolated ? `<span class="flag iso">weak match</span>` : "") + (g.confidence === "low" ? `<span class="flag low">1 favourite matched</span>` : "") + (g.no_data ? `<span class="flag low">no taste data</span>` : "");
    h += `<div class="guest${selGuest === name ? " sel" : ""}" data-guest="${esc(name)}"><span class="gname">${esc(name)}${g.group ? ` <small>${esc(g.group)}</small>` : ""}${flags}</span>
      <span class="fitv">${g.no_data ? "n/a" : fmt(g.fit)}</span>${fitBar(g, range)}
      <span class="favs">${g.favorites.length ? esc(g.favorites.join(" · ")) : "<i>no favourites matched in Qloo</i>"}${g.unmatched.length ? ` <span class="meta">(not found: ${esc(g.unmatched.join(", "))})</span>` : ""}</span>
      <span class="why">${esc(g.why)}</span></div>`;
  }
  $("#detail").innerHTML = h + `</div>`;
  $$("#detail .guest").forEach((el) => el.addEventListener("click", () => { selGuest = el.dataset.guest; renderMap(); renderDetail(); }));
  if (scrollGuest && selGuest) { const el = $(`#detail .guest.sel`); if (el) el.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
}
function renderPlan(r) {
  current = r; selTable = 1; selGuest = null;
  const p = r.plan, m = p.metrics, sample = r.mode.sample_data;
  const ok = p.constraints.filter((c) => c.ok).length;
  const ratio = m.baseline.min_fit > 0 ? (m.min_fit / m.baseline.min_fit) : null;
  let h = `<div class="plan-head"><div><div class="kicker">Your seating plan</div><h2>${esc(p.headline)}</h2></div>
    <div class="plan-actions"><button type="button" class="chip-btn" id="csv">Download CSV</button><button type="button" class="chip-btn" id="print">Print</button></div></div>`;
  if (sample) h += `<div class="banner"><strong>SAMPLE DATA.</strong> These taste vectors come from the demo catalogue, not from Qloo.</div>`;
  h += `<div class="facts">
    <div class="fact good"><b>${fmt(m.min_fit)}</b><span>least-matched guest's fit${ratio ? ` (${ratio.toFixed(1)}x random seating's ${fmt(m.baseline.min_fit)})` : ""}</span></div>
    <div class="fact"><b>${fmt(m.avg_cohesion)}</b><span>average table cohesion (random: ${fmt(m.baseline.avg_cohesion)})</span></div>
    <div class="fact ${m.isolated.length ? "bad" : "good"}"><b>${m.isolated.length}</b><span>guests sitting with no close match${m.unavoidable_isolated.length ? ` (${m.unavoidable_isolated.length} more have none anywhere in this party)` : ""}</span></div>
    <div class="fact ${ok === p.constraints.length ? "good" : "bad"}"><b>${ok}<small>/${p.constraints.length}</small></b><span>requirements met</span></div></div>`;
  h += `<div class="mapgrid"><div><div class="panel"><div id="map" class="map"></div>
    <div class="legend" aria-hidden="true"><span>Seat colour = fit with table-mates:</span><span>lower</span><span class="ramp"></span><span>higher</span>
    <span class="key"><span class="dash"></span>weak match</span><span class="key"><span class="none"></span>no taste data</span></div></div></div>
    <aside class="panel detail" id="detail" aria-live="polite"></aside></div>`;
  h += `<div class="below"><div class="panel"><h3>Requirements</h3>`;
  h += p.constraints.length ? `<ul class="checks">${p.constraints.map((c) => `<li class="${c.ok ? "" : "no"}">${esc(c.text)}</li>`).join("")}</ul>` : `<p class="meta">None given.</p>`;
  h += `</div><div class="panel"><h3>Notes and assumptions</h3>`;
  const notes = [...(p.notes || []), ...(p.assumptions || [])];
  h += notes.length ? `<ul class="assume">${notes.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : `<p class="meta">None. Every favourite matched cleanly.</p>`;
  h += `<p class="source">Taste vectors: ${sample ? "SAMPLE catalogue (not Qloo)" : "Qloo taste analysis (/v2/insights) blended with the favourites' own Qloo tags (/search)"}. Reasoning: ${esc(r.mode.llm)}. Seed ${esc(m.seed)}. Fit is the average compatibility with a guest's table-mates; it reflects aggregate taste patterns, not a prediction about any one person.</p></div></div>`;
  $("#plan").innerHTML = h;
  show("#plan");
  renderMap(); renderDetail();
  $("#csv").onclick = downloadCSV;
  $("#print").onclick = () => window.print();
}
function downloadCSV() {
  const q = (s) => `"${String(s).replace(/"/g, '""')}"`;
  const rows = [["guest", "table", "fit", "group", "favourites"].join(",")];
  current.plan.guests.slice().sort((a, b) => a.table - b.table || b.fit - a.fit).forEach((g) => rows.push([q(g.name), g.table, g.no_data ? "" : g.fit, q(g.group), q(g.favorites.join("; "))].join(",")));
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([rows.join("\n")], { type: "text/csv" }));
  a.download = "placecard-seating.csv"; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---- question / run --------------------------------------------------------------------------------
function renderQuestion(r) {
  const q = r.question;
  lastQuestion = q.question;
  let h = `<div class="kicker">The agent needs one answer</div><p>${esc(q.question)}</p><div class="answers">`;
  for (const o of q.options || []) h += `<button type="button" class="opt">${esc(o)}</button>`;
  h += `<input id="answer" maxlength="200" placeholder="Or type your answer"><button type="button" class="solid" id="send-answer">Answer and re-run</button></div>`;
  $("#question").innerHTML = h;
  show("#question");
  $$("#question .opt").forEach((b) => (b.onclick = () => answer(b.textContent)));
  $("#send-answer").onclick = () => answer($("#answer").value);
  $("#answer").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); answer($("#answer").value); } });
  $("#question").scrollIntoView({ behavior: "smooth", block: "center" });
}
function answer(text) {
  text = (text || "").trim();
  if (!text) return;
  clarifications.push({ question: lastQuestion, answer: text });
  hide("#question");
  run();
}
async function run() {
  const guests = refreshParse();
  const fe = $("#form-error");
  const err = guests.length < 2 ? "Add at least two guests." : !guests.some((g) => g.favorites.length) ? "Add at least one favourite." : guests.length > (health.max_guests || 120) ? `At most ${health.max_guests || 120} guests.` : "";
  if (err) { fe.textContent = err; fe.hidden = false; return; }
  fe.hidden = true;
  const req = { guests, table_size: parseInt($("#table-size").value, 10), constraints_text: $("#constraints").value.trim(), clarifications };
  hide("#plan"); hide("#stats");
  $("#go").disabled = true; $("#go").textContent = "Agent is seating...";
  setStatus("Running", "run");
  $("#timeline").innerHTML = `<li class="thought"><span class="say pending">Starting the agent</span></li>`;
  Object.keys(pendingCalls).forEach((k) => delete pendingCalls[k]);
  if (window.matchMedia("(max-width: 860px)").matches) $(".agent").scrollIntoView({ behavior: "smooth" });
  let result = null;
  try {
    const resp = await fetch("/api/plan/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(req) });
    if (!resp.ok) { const j = await resp.json().catch(() => ({})); throw new Error(j.error || `Server error ${resp.status}`); }
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
        if (!line) continue;
        const msg = JSON.parse(line);
        if (msg.event) addStep(msg.event);
        if (msg.result) result = msg.result;
      }
    }
    if (buf.trim()) { const msg = JSON.parse(buf); if (msg.result) result = msg.result; }
    if (!result) throw new Error("The connection closed before the agent finished. Please try again.");
    renderStats(result);
    if (result.status === "plan") { setStatus("Plan ready", "ok"); renderPlan(result); $("#plan").scrollIntoView({ behavior: "smooth" }); }
    else if (result.status === "question") { setStatus("Needs an answer", "run"); renderQuestion(result); }
    else { setStatus("Error", "err"); $("#timeline").insertAdjacentHTML("beforeend", `<li class="bad"><span class="sum">${esc(result.error)}</span></li>`); }
  } catch (e) {
    setStatus("Error", "err");
    $("#timeline").insertAdjacentHTML("beforeend", `<li class="bad"><span class="sum">${esc(e.message)}</span></li>`);
  } finally {
    $("#go").disabled = false; $("#go").textContent = "Seat my guests";
  }
}

// ---- boot ---------------------------------------------------------------------------------------------
function renderModes(h) {
  const llm = h.llm_provider === "mock" ? `<span class="pill warn">Agent: <b>MOCK (no LLM key)</b></span>`
    : `<span class="pill">Agent: <b>${esc(h.llm_provider === "anthropic" ? "Claude" : "Gemini")}</b> ${esc(h.llm_model)}</span>`;
  const q = h.sample_data ? `<span class="pill warn">Qloo: <b>SAMPLE data</b></span>` : `<span class="pill">Qloo: <b>live</b></span>`;
  $("#modes").innerHTML = q + llm;
  $("#sample-banner").hidden = !h.sample_data;
  $("#max-guests").textContent = h.max_guests || 120;
}
$$("[data-preset]").forEach((b) => (b.onclick = () => loadPreset(b.dataset.preset)));
$("#guests").addEventListener("input", refreshParse);
$("#file").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (!f) return;
  if (f.size > 200000) { $("#form-error").textContent = "That file is too large (200 KB max)."; $("#form-error").hidden = false; return; }
  const rd = new FileReader();
  rd.onload = () => { $("#guests").value = String(rd.result); refreshParse(); };
  rd.readAsText(f);
  e.target.value = "";
});
$("#form").addEventListener("submit", (e) => { e.preventDefault(); clarifications = []; hide("#question"); run(); });
fetch("/api/health").then((r) => r.json()).then((h) => { health = h; renderModes(h); }).catch(() => {});
fetch("/api/presets").then((r) => r.json()).then((p) => {
  presets = p;
  $$("[data-preset]").forEach((b) => { const t = presets[b.dataset.preset]; if (t) b.textContent = t.title.replace(/(\d+) guests/, "$1"); });
  const auto = /^#run=(\w+)$/.exec(location.hash);
  const key = auto && presets[auto[1]] ? auto[1] : "wedding";
  loadPreset(key);
  if (auto && presets[auto[1]]) run();
}).catch(() => {});
