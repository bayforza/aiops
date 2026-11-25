/* AIOps Copilot UI: plain JavaScript, no build step and no external requests (works air-gapped). */
"use strict";

const $app = document.getElementById("app");
const state = { info: null, filters: { status: "", category: "", q: "" }, timer: null };

// ---------- helpers ----------
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x, d = 0) => (x == null ? "–" : `${(x * 100).toFixed(d)}%`);
const ms = (x) => (x == null ? "–" : x >= 10000 ? `${(x / 1000).toFixed(0)} s` : x >= 1000 ? `${(x / 1000).toFixed(1)} s` : `${Math.round(x)} ms`);
const when = (iso) => {
  if (!iso) return "–";
  const d = new Date(iso), s = (Date.now() - d.getTime()) / 1000;
  const rel = s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : d.toLocaleDateString();
  return `<span title="${esc(d.toLocaleString())}">${rel}</span>`;
};
const badge = (text, cls = "") => `<span class="badge ${cls}">${esc(text)}</span>`;
const statusBadge = (s) => badge(s, `b-${s}`);
const kindLabel = { local: "Local AI", cloud: "Cloud AI", rules: "Rules" };
const whoBadge = (w) => (w ? `${badge(kindLabel[w.kind] || w.kind, `b-${w.kind}`)} <span class="mono nowrap">${esc(w.kind === "rules" ? "deterministic" : w.name)}</span>` : "–");
const voteBadge = (v) => (v ? badge(v, `b-${v}`) : '<span class="muted">–</span>');
const meter = (x) => {
  if (x == null) return "–";
  const cls = x >= 0.75 ? "hi" : x >= 0.5 ? "mid" : "lo";
  return `<span class="meter ${cls}"><span class="bar"><i style="width:${Math.round(x * 100)}%"></i></span>${pct(x)}</span>`;
};
const target = (t) => [t.namespace, t.workload || t.pod].filter(Boolean).map((x) => `<span class="chip">${esc(x)}</span>`).join("") || "–";

async function api(path, opts) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}
function toast(msg) {
  let t = document.querySelector(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.appendChild(t); }
  t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 1800);
}
const store = {
  get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

// ---------- header ----------
async function loadInfo() {
  try { state.info = await api("/api/info"); } catch { return; }
  const i = state.info;
  const locals = i.local_models.map((m) => badge(`Local: ${m.name}`, "b-local")).join(" ");
  const cloud = i.cloud.enabled ? badge(`Cloud: ${i.cloud.model}`, "b-cloud") : badge("Cloud off", "");
  const mode = badge(`Level ${i.autonomy_level}${i.dry_run ? " · dry run" : ""}`, "");
  document.getElementById("topmeta").innerHTML = `${locals || badge("Rules only", "b-rules")} ${cloud} ${mode}`;
}

// ---------- overview ----------
function kpi(label, value, hint = "") {
  return `<div class="card kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="hint">${hint}</div></div>`;
}
function accHint(a) { return a.rated ? `${a.correct} correct · ${a.partial} partial · ${a.wrong} wrong` : "no votes yet"; }

async function viewDashboard() {
  const s = await api("/api/stats");
  const st = s.status || {};
  const lat = s.latency_ms;
  const days = Object.entries(s.per_day).slice(-14);
  const maxDay = Math.max(1, ...days.map(([, n]) => n));
  const maxCat = Math.max(1, ...s.categories.map((c) => c.incidents));
  $app.innerHTML = `
    <div class="pagehead"><h1>Overview</h1><span class="sub">${s.incidents} incidents analysed · ${s.alerts.received} alerts received</span></div>
    <div class="grid kpis">
      ${kpi("Open incidents", st.open || 0, `${st.acknowledged || 0} acknowledged · ${st.resolved || 0} resolved`)}
      ${kpi("Root cause accuracy", pct(s.root_cause.accuracy), accHint(s.root_cause))}
      ${kpi("Fix accuracy", pct(s.fix.accuracy), accHint(s.fix))}
      ${kpi("Rated by operators", s.incidents ? pct(s.rated / s.incidents) : "–", `${s.rated} of ${s.incidents} incidents`)}
      ${kpi("Time to analysis (p50)", ms(lat.total.p50), `p95 ${ms(lat.total.p95)} · max ${ms(lat.total.max)}`)}
      ${kpi("Cloud usage", pct(s.cloud.rate), `${s.cloud.asked} second opinions · ${s.cloud.agreed} agreed`)}
    </div>
    <div class="grid two">
      <div class="card"><h2>Who answered</h2>${answererTable(s.answered_by)}</div>
      <div class="card"><h2>Latency per stage</h2>${stageTable(lat)}</div>
      <div class="card"><h2>Incidents per day</h2>
        ${days.length ? `<div class="bars">${days.map(([d, n]) => `<div class="col" title="${esc(d)}: ${n}"><span>${n}</span><i style="height:${(n / maxDay) * 90}px"></i><span>${esc(d.slice(5))}</span></div>`).join("")}</div>` : '<div class="empty">No incidents yet</div>'}
      </div>
      <div class="card"><h2>Root causes by category</h2>
        ${s.categories.length ? `<div class="hbar">${s.categories.map((c) => `<span>${esc(c.category)}</span><span class="track"><i style="width:${(c.incidents / maxCat) * 100}%"></i></span><span class="num">${c.incidents}</span>`).join("")}</div>` : '<div class="empty">No incidents yet</div>'}
        <h3>Accuracy by category</h3>
        <table><thead><tr><th>Category</th><th class="num">Rated</th><th class="num">Root cause accuracy</th></tr></thead><tbody>
        ${s.categories.map((c) => `<tr><td>${esc(c.category)}</td><td class="num">${c.root_cause.rated}</td><td class="num">${meter(c.root_cause.accuracy)}</td></tr>`).join("")}
        </tbody></table>
      </div>
      <div class="card"><h2>Is the confidence honest?</h2>
        <p class="muted">Average confidence the copilot reported, grouped by how operators rated the root cause. A well calibrated copilot is confident when right and unsure when wrong.</p>
        <table><tbody>${["correct", "partial", "wrong"].map((k) => `<tr><td>${voteBadge(k)}</td><td class="num">${meter(s.confidence_by_vote[k])}</td></tr>`).join("")}</tbody></table>
      </div>
      <div class="card"><h2>Local or cloud</h2>
        <table><tbody>
          <tr><td>Answered locally</td><td class="num">${s.routes.local || 0}</td></tr>
          <tr><td>Local only (data must stay inside)</td><td class="num">${s.routes.local_only || 0}</td></tr>
          <tr><td>Cloud second opinion asked</td><td class="num">${s.cloud.asked}</td></tr>
          <tr><td>Cloud agreed with local result</td><td class="num">${s.cloud.agreed}</td></tr>
          <tr><td>Alerts deduplicated</td><td class="num">${s.alerts.deduplicated || 0}</td></tr>
        </tbody></table>
      </div>
    </div>`;
}

function answererTable(rows) {
  if (!rows.length) return '<div class="empty">No incidents yet</div>';
  return `<div class="tablewrap"><table><thead><tr><th>Answered by</th><th class="num">Incidents</th><th class="num">Root cause</th><th class="num">Fix</th><th class="num">Avg confidence</th><th class="num">p50 total</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${whoBadge(r)}${r.model && r.kind !== "rules" ? `<div class="muted mono">${esc(r.model)}</div>` : ""}</td><td class="num">${r.incidents}</td>
      <td class="num" title="${esc(accHint(r.root_cause))}">${meter(r.root_cause.accuracy)}</td><td class="num" title="${esc(accHint(r.fix))}">${meter(r.fix.accuracy)}</td>
      <td class="num">${pct(r.avg_confidence)}</td><td class="num">${ms(r.latency_ms.p50)}</td></tr>`).join("")}
  </tbody></table></div>`;
}
const STAGE_LABEL = { queue: "Waiting in queue", collect: "Collect context", preprocess: "Log summary + signals", llm: "Local AI", cloud: "Cloud AI", act: "Patch / PR / notify", total: "Total" };
function stageTable(lat) {
  return `<table><thead><tr><th>Stage</th><th class="num">n</th><th class="num">p50</th><th class="num">p95</th><th class="num">max</th></tr></thead><tbody>
    ${Object.entries(STAGE_LABEL).map(([k, label]) => { const l = lat[k] || {}; return `<tr><td>${label}</td><td class="num">${l.n || 0}</td><td class="num">${ms(l.p50)}</td><td class="num">${ms(l.p95)}</td><td class="num">${ms(l.max)}</td></tr>`; }).join("")}
  </tbody></table>`;
}

// ---------- incidents ----------
async function viewIncidents() {
  const f = state.filters;
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  const items = await api(`/api/incidents${qs ? `?${qs}` : ""}`);
  const cats = ["", "oom", "bad_deploy", "image_pull", "config_error", "dependency", "disk_full", "probe_failure", "unknown"];
  $app.innerHTML = `
    <div class="pagehead"><h1>Incidents</h1><span class="sub">${items.length} shown</span></div>
    <div class="filters">
      <div class="seg" id="statusSeg">${["", "open", "acknowledged", "resolved"].map((s) => `<button data-s="${s}" class="${f.status === s ? "on" : ""}">${s || "All"}</button>`).join("")}</div>
      <select id="catSel" aria-label="Category">${cats.map((c) => `<option value="${c}" ${f.category === c ? "selected" : ""}>${c || "All categories"}</option>`).join("")}</select>
      <input type="search" id="q" placeholder="Search alert, workload, root cause…" value="${esc(f.q)}" size="32">
    </div>
    <div class="card tablewrap">${items.length ? `<table><thead><tr>
      <th>When</th><th>Status</th><th>Alert</th><th>Target</th><th>Category</th><th>Root cause</th><th>Confidence</th><th>Answered by</th><th class="num">Took</th><th>Vote</th>
    </tr></thead><tbody>
      ${items.map((i) => `<tr class="click" data-id="${esc(i.id)}">
        <td>${when(i.created_at)}</td><td>${statusBadge(i.status)}</td>
        <td><strong>${esc(i.alert)}</strong>${i.severity ? ` ${badge(i.severity, `b-${i.severity}`)}` : ""}</td>
        <td>${target(i.target)}</td><td>${esc(i.category)}</td>
        <td><div class="clip">${esc(i.root_cause)}</div></td>
        <td>${meter(i.confidence)}</td><td>${whoBadge(i.answered_by)}</td><td class="num">${ms(i.total_ms)}</td>
        <td>${i.vote ? `${voteBadge(i.vote.root_cause)}` : '<span class="muted">not rated</span>'}</td>
      </tr>`).join("")}
    </tbody></table>` : '<div class="empty">No incidents match. Trigger a scenario (make scenario-oom) or send an alert to /webhook/alertmanager.</div>'}</div>`;
  document.querySelectorAll("#statusSeg button").forEach((b) => b.onclick = () => { f.status = b.dataset.s; viewIncidents(); });
  document.getElementById("catSel").onchange = (e) => { f.category = e.target.value; viewIncidents(); };
  const q = document.getElementById("q");
  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(() => { f.q = q.value; viewIncidents().then(() => { const n = document.getElementById("q"); n.focus(); n.setSelectionRange(n.value.length, n.value.length); }); }, 300); };
  document.querySelectorAll("tr.click").forEach((tr) => tr.onclick = () => { location.hash = `#/incident/${tr.dataset.id}`; });
}

// ---------- incident detail ----------
async function viewIncident(id) {
  const d = await api(`/api/incidents/${encodeURIComponent(id)}`);
  const inc = d.incident, a = inc.analysis, rb = d.runbook, t = inc.timings_ms || {};
  const pr = inc.actions.find((x) => x.url);
  const diff = inc.actions.find((x) => x.diff);
  $app.innerHTML = `
    <div class="pagehead">
      <a href="#/incidents">← Incidents</a>
      <h1>${esc(inc.alert.labels.alertname || "alert")}</h1>${statusBadge(inc.status)}
      ${inc.alert.labels.severity ? badge(inc.alert.labels.severity, `b-${inc.alert.labels.severity}`) : ""}
      <span class="sub">${target(inc.target)} · ${when(inc.created_at)} · <span class="mono">${esc(inc.id)}</span></span>
      <span style="margin-left:auto;display:flex;gap:6px">
        ${inc.status !== "acknowledged" && inc.status !== "resolved" ? '<button class="btn" data-st="acknowledged">Acknowledge</button>' : ""}
        ${inc.status !== "resolved" ? '<button class="btn primary" data-st="resolved">Resolve</button>' : '<button class="btn" data-st="open">Reopen</button>'}
      </span>
    </div>
    <div class="grid detail">
      <div class="stack">
        <div class="card">
          <h2>Root cause</h2>
          <p class="rc">${esc(a.root_cause) || '<span class="muted">No root cause</span>'}</p>
          <dl class="kv">
            <dt>Category</dt><dd>${esc(a.category)}</dd>
            <dt>Confidence</dt><dd>${meter(a.confidence)}</dd>
            <dt>Evidence score</dt><dd>${meter(a.evidence_score)} <span class="muted">independent of what the model says about itself</span></dd>
            <dt>Answered by</dt><dd>${whoBadge(a.answered_by)} ${a.answered_by.model && a.answered_by.kind !== "rules" ? `<span class="muted mono">${esc(a.answered_by.model)}</span>` : ""}</dd>
            <dt>Source</dt><dd><span class="mono">${esc(a.source)}</span></dd>
            <dt>Route</dt><dd>${badge(a.route, a.route === "cloud" ? "b-cloud" : "b-local")} <span class="muted">${esc(a.route_reason)}</span></dd>
          </dl>
          ${a.evidence.length ? `<h3>Evidence</h3><ul class="clean">${a.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
          ${a.confidence_reasons.length ? `<h3>Why this confidence</h3><ul class="clean">${a.confidence_reasons.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
          ${a.notes.length ? `<h3>Notes</h3><ul class="clean">${a.notes.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
        </div>
        <div class="card">
          <h2>Fix</h2>
          ${rb ? `<p><strong>${esc(rb.title)}</strong> <span class="chip">${esc(rb.id)}</span></p><p class="muted">${esc(rb.description)}</p>` : '<p class="muted">No matching runbook.</p>'}
          <p>${badge(a.remediation.kind, a.remediation.kind === "gitops_patch" ? "b-local" : "")} ${esc(a.remediation.description)}</p>
          ${rb && rb.steps.length ? `<h3>Steps</h3><ol class="steps">${rb.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : ""}
          ${pr ? `<h3>Pull request</h3><p><a href="${esc(pr.url)}" target="_blank" rel="noopener">${esc(pr.url)}</a> ${badge(pr.status)}</p>` : ""}
          ${diff ? `<h3>Proposed change ${badge(diff.status)}</h3><pre>${esc(diff.diff)}</pre>` : ""}
          ${inc.actions.filter((x) => !x.diff && !x.url).map((x) => `<p class="muted">${esc(x.kind)}: ${esc(x.status)} ${esc(x.detail)}</p>`).join("")}
        </div>
        <div class="card">
          <h2>Signals and context</h2>
          ${inc.signals.length ? `<table><thead><tr><th>Signal</th><th>Detail</th><th class="num">Weight</th></tr></thead><tbody>${inc.signals.map((s) => `<tr><td><span class="chip">${esc(s.kind)}</span></td><td>${esc(s.detail)}</td><td class="num">${s.weight.toFixed(1)}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">No deterministic signal.</p>'}
          ${inc.context.log_templates.length ? `<h3>Log patterns (Drain3)</h3><table><thead><tr><th class="num">Count</th><th>Template</th></tr></thead><tbody>${inc.context.log_templates.slice(0, 12).map((l) => `<tr><td class="num">${l.count}</td><td class="mono" style="${l.is_error ? "color:var(--bad)" : ""}">${esc(l.template)}</td></tr>`).join("")}</tbody></table>` : ""}
          ${inc.context.metrics.length ? `<h3>Metrics</h3><table><thead><tr><th>Metric</th><th class="num">Last</th><th class="num">Max</th></tr></thead><tbody>${inc.context.metrics.map((m) => `<tr><td>${esc(m.name)}</td><td class="num">${m.last ?? "–"} ${esc(m.unit)}</td><td class="num">${m.max ?? "–"} ${esc(m.unit)}</td></tr>`).join("")}</tbody></table>` : ""}
          ${inc.context.changes.length ? `<h3>Recent changes</h3><ul class="clean">${inc.context.changes.map((c) => `<li><span class="chip">${esc(c.source)}</span>${esc(c.description)}</li>`).join("")}</ul>` : ""}
          ${inc.context.events.length ? `<details><summary>Kubernetes events (${inc.context.events.length})</summary><pre>${esc(inc.context.events.join("\n"))}</pre></details>` : ""}
          ${inc.context.errors.length ? `<h3>Collector errors</h3><ul class="clean">${inc.context.errors.map((e) => `<li class="muted">${esc(e)}</li>`).join("")}</ul>` : ""}
        </div>
        ${a.second_opinion ? `<div class="card"><h2>Cloud second opinion</h2>
          <dl class="kv"><dt>Model</dt><dd class="mono">${esc(a.second_opinion.model)}</dd>
          <dt>Took</dt><dd>${ms(a.second_opinion.ms)}</dd>
          <dt>Said</dt><dd>${esc(a.second_opinion.category)} · ${pct(a.second_opinion.confidence)} · evidence ${pct(a.second_opinion.evidence_score)}</dd>
          <dt>Agrees with local</dt><dd>${a.second_opinion.agrees_with_local ? badge("yes", "b-correct") : badge("no", "b-wrong")}</dd>
          <dt>Masking</dt><dd>${a.second_opinion.placeholders} names replaced · ${a.second_opinion.secrets_removed} secrets removed</dd></dl>
          <p class="muted">${esc(a.second_opinion.root_cause)}</p>
          <details><summary>Exactly what was sent</summary><pre>${esc(a.second_opinion.sent_payload)}</pre></details></div>` : ""}
      </div>
      <div class="stack">
        <div class="card">
          <h2>Was this right?</h2>
          <form id="fb">
            <div class="muted">Root cause</div>
            <div class="vote" data-k="root_cause">${["correct", "partial", "wrong"].map((v) => `<button type="button" class="btn" data-v="${v}">${v === "correct" ? "Correct" : v === "partial" ? "Partly" : "Wrong"}</button>`).join("")}</div>
            <div class="muted">Fix</div>
            <div class="vote" data-k="fix">${["correct", "partial", "wrong"].map((v) => `<button type="button" class="btn" data-v="${v}">${v === "correct" ? "Correct" : v === "partial" ? "Partly" : "Wrong"}</button>`).join("")}</div>
            <textarea name="comment" placeholder="What was the real cause or the fix that worked? (optional)"></textarea>
            <div style="display:flex;gap:8px;margin-top:8px;align-items:center">
              <input type="text" name="author" placeholder="Your name" value="${esc(store.get("aiops.author", ""))}" style="flex:1">
              <button class="btn primary" type="submit">Save</button>
            </div>
          </form>
          ${d.feedback.length ? `<h3>Ratings (${d.feedback.length})</h3><ul class="timeline">${d.feedback.slice().reverse().map((f) => `<li><div>${f.root_cause ? `root cause ${voteBadge(f.root_cause)} ` : ""}${f.fix ? `fix ${voteBadge(f.fix)}` : ""}</div>${f.comment ? `<div>${esc(f.comment)}</div>` : ""}<div class="muted">${esc(f.author || "anonymous")} · ${when(f.created_at)}</div></li>`).join("")}</ul>` : ""}
        </div>
        <div class="card">
          <h2>Response time</h2>
          ${waterfall(t)}
          ${inc.model_calls.length ? `<h3>Model calls</h3><table><thead><tr><th>Model</th><th class="num">Took</th><th class="num">Tokens in/out</th><th></th></tr></thead><tbody>
            ${inc.model_calls.map((c) => `<tr><td>${badge(kindLabel[c.kind] || c.kind, `b-${c.kind}`)} <span class="mono">${esc(c.name)}</span></td><td class="num">${ms(c.ms)}</td><td class="num">${c.prompt_tokens ?? "–"} / ${c.completion_tokens ?? "–"}</td><td>${c.ok ? badge("ok", "b-correct") : `<span title="${esc(c.error)}">${badge("failed", "b-wrong")}</span>`}</td></tr>`).join("")}
          </tbody></table>` : '<p class="muted">No model was called (rules only).</p>'}
        </div>
        <div class="card">
          <h2>Alert</h2>
          <div>${Object.entries(inc.alert.labels).map(([k, v]) => `<span class="chip">${esc(k)}=${esc(v)}</span>`).join("")}</div>
          ${Object.keys(inc.alert.annotations).length ? `<dl class="kv" style="margin-top:8px">${Object.entries(inc.alert.annotations).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>` : ""}
          <p class="muted">Fired ${when(inc.alert.startsAt)} · received ${when(inc.received_at)}</p>
        </div>
        <div class="card">
          <h2>Timeline</h2>
          ${d.timeline.length ? `<ul class="timeline">${d.timeline.map((e) => `<li><strong>${esc(e.event.replace(/_/g, " "))}</strong> <span class="muted">${new Date(e.ts).toLocaleTimeString()}</span>${eventDetail(e)}</li>`).join("")}</ul>` : '<p class="muted">No audit events.</p>'}
        </div>
      </div>
    </div>`;

  document.querySelectorAll("[data-st]").forEach((b) => b.onclick = async () => {
    await api(`/api/incidents/${encodeURIComponent(id)}/status`, { method: "POST", body: JSON.stringify({ status: b.dataset.st }) });
    toast(`Marked ${b.dataset.st}`); viewIncident(id);
  });
  const chosen = { root_cause: null, fix: null };
  document.querySelectorAll(".vote").forEach((g) => g.querySelectorAll("button").forEach((b) => b.onclick = () => {
    g.querySelectorAll("button").forEach((x) => x.classList.remove("on")); b.classList.add("on"); chosen[g.dataset.k] = b.dataset.v;
  }));
  document.getElementById("fb").onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = { root_cause: chosen.root_cause, fix: chosen.fix, comment: fd.get("comment") || "", author: fd.get("author") || null };
    if (!body.root_cause && !body.fix && !body.comment) { toast("Pick a rating or write a comment"); return; }
    if (body.author) store.set("aiops.author", body.author);
    await api(`/api/incidents/${encodeURIComponent(id)}/feedback`, { method: "POST", body: JSON.stringify(body) });
    toast("Thanks, rating saved"); viewIncident(id);
  };
}

function waterfall(t) {
  const total = t.total || 1, rows = [];
  let offset = t.queue || 0;
  const full = total + offset;
  if (t.queue) rows.push(["Waiting in queue", 0, t.queue, "queue"]);
  const order = [["collect", "Collect context"], ["preprocess", "Log summary + signals"], ["llm", "Local AI"], ["cloud", "Cloud AI"], ["act", "Patch / PR / notify"]];
  for (const [k, label] of order) {
    if (t[k] == null) continue;
    rows.push([label, offset, t[k], k]);
    offset += t[k];
  }
  const sub = Object.entries(t).filter(([k]) => k.startsWith("collect.")).map(([k, v]) => `<span class="chip">${esc(k.slice(8))} ${ms(v)}</span>`).join("");
  return `<div class="wf">${rows.map(([label, start, dur, k]) => `<span>${label}</span><span class="track"><span class="seg-bar ${k}" style="left:${(start / full) * 100}%;width:${Math.max(0.8, (dur / full) * 100)}%"></span></span><span class="num">${ms(dur)}</span>`).join("")}
    <strong>Total</strong><span></span><strong class="num">${ms(t.total)}</strong></div>
    ${sub ? `<div style="margin-top:8px" class="muted">Collectors: ${sub}</div>` : ""}`;
}
function eventDetail(e) {
  const d = e.data || {};
  const bits = [];
  if (d.name || d.model) bits.push(esc(d.name || d.model));
  if (d.ms != null) bits.push(ms(d.ms));
  if (d.route) bits.push(`${esc(d.route)}: ${esc(d.reason || "")}`);
  if (d.category) bits.push(`${esc(d.category)} ${d.confidence != null ? pct(d.confidence) : ""}`);
  if (d.status) bits.push(esc(d.status));
  if (d.root_cause || d.fix) bits.push(`root cause ${esc(d.root_cause || "–")}, fix ${esc(d.fix || "–")}`);
  if (d.error) bits.push(`<span style="color:var(--bad)">${esc(String(d.error).slice(0, 140))}</span>`);
  return bits.length ? `<div class="muted">${bits.join(" · ")}</div>` : "";
}

// ---------- alerts ----------
async function viewAlerts() {
  const rows = await api("/api/alerts");
  const dec = { queued: "", analysed: "b-local", deduplicated: "", resolved: "b-resolved", failed: "b-wrong" };
  $app.innerHTML = `
    <div class="pagehead"><h1>Alerts</h1><span class="sub">Everything Alertmanager sent, including duplicates and resolved notifications</span></div>
    <div class="card tablewrap">${rows.length ? `<table><thead><tr><th>Received</th><th>Alert</th><th>Severity</th><th>State</th><th>What happened</th><th>Labels</th><th>Incident</th></tr></thead><tbody>
      ${rows.map((r) => `<tr>
        <td>${when(r.received_at)}</td><td><strong>${esc(r.name)}</strong>${r.annotations.summary ? `<div class="muted">${esc(r.annotations.summary)}</div>` : ""}</td>
        <td>${r.labels.severity ? badge(r.labels.severity, `b-${r.labels.severity}`) : "–"}</td>
        <td>${badge(r.status, r.status === "firing" ? "b-open" : "b-resolved")}</td>
        <td>${badge(r.decision, dec[r.decision] || "")}</td>
        <td>${Object.entries(r.labels).filter(([k]) => !["alertname", "severity"].includes(k)).map(([k, v]) => `<span class="chip">${esc(k)}=${esc(v)}</span>`).join("")}</td>
        <td>${r.incident_id ? `<a href="#/incident/${esc(r.incident_id)}" class="mono">${esc(r.incident_id)}</a>` : "–"}</td>
      </tr>`).join("")}
    </tbody></table>` : '<div class="empty">No alerts received yet.</div>'}</div>`;
}

// ---------- models ----------
async function viewModels() {
  const [s, i] = [await api("/api/stats"), state.info || (await api("/api/info"))];
  $app.innerHTML = `
    <div class="pagehead"><h1>Models</h1><span class="sub">Local models are tried in order; the cloud is asked only when the local evidence is weak</span></div>
    <div class="grid two">
      <div class="card"><h2>Configured</h2>
        <table><thead><tr><th>#</th><th>Name</th><th>Model</th><th>Where</th></tr></thead><tbody>
          ${i.local_models.map((m, n) => `<tr><td>${n + 1}</td><td>${esc(m.name)}</td><td class="mono">${esc(m.model)}</td><td>${badge("Local AI", "b-local")}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">No local model (rules only)</td></tr>'}
          <tr><td>☁</td><td>${i.cloud.enabled ? "second opinion" : "cloud"}</td><td class="mono">${esc(i.cloud.model || "–")}</td><td>${i.cloud.enabled ? badge(`Cloud AI · ${i.cloud.provider}`, "b-cloud") : badge("off")}</td></tr>
        </tbody></table>
        <p class="muted">Cloud is asked when min(confidence, evidence score) &lt; ${pct(i.cloud.min_local_confidence)}.${i.cloud.local_only_namespaces.length ? ` Never for: ${i.cloud.local_only_namespaces.map((n) => `<span class="chip">${esc(n)}</span>`).join("")}` : ""}</p>
      </div>
      <div class="card"><h2>Accuracy by answering model</h2>${answererTable(s.answered_by)}</div>
      <div class="card" style="grid-column:1/-1"><h2>Model calls</h2>
        ${s.models.length ? `<div class="tablewrap"><table><thead><tr><th>Model</th><th class="num">Calls</th><th class="num">Success</th><th class="num">p50</th><th class="num">p95</th><th class="num">max</th><th class="num">Avg tokens in</th><th class="num">Avg tokens out</th></tr></thead><tbody>
          ${s.models.map((m) => `<tr><td>${badge(kindLabel[m.kind] || m.kind, `b-${m.kind}`)} <span class="mono">${esc(m.name)}</span></td><td class="num">${m.calls}</td><td class="num">${pct(m.success_rate)}</td><td class="num">${ms(m.latency_ms.p50)}</td><td class="num">${ms(m.latency_ms.p95)}</td><td class="num">${ms(m.latency_ms.max)}</td><td class="num">${m.avg_prompt_tokens == null ? "–" : Math.round(m.avg_prompt_tokens)}</td><td class="num">${m.avg_completion_tokens == null ? "–" : Math.round(m.avg_completion_tokens)}</td></tr>`).join("")}
        </tbody></table></div>` : '<div class="empty">No model calls yet</div>'}
      </div>
    </div>`;
}

// ---------- router ----------
async function route() {
  const h = location.hash.replace(/^#\/?/, "");
  const [page, arg] = h.split("/");
  const key = page === "incident" ? "incidents" : page || "dashboard";
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.route === key));
  try {
    if (page === "incident" && arg) await viewIncident(decodeURIComponent(arg));
    else if (page === "incidents") await viewIncidents();
    else if (page === "alerts") await viewAlerts();
    else if (page === "models") await viewModels();
    else await viewDashboard();
  } catch (err) {
    $app.innerHTML = `<div class="card empty">Could not load: ${esc(err.message)}</div>`;
  }
}

function schedule() {
  clearInterval(state.timer);
  const on = document.getElementById("autorefresh").checked;
  store.set("aiops.live", on ? "1" : "0");
  if (on) state.timer = setInterval(() => {
    // Don't refresh under the operator's hands while they type feedback or a search.
    const a = document.activeElement;
    if (a && (a.tagName === "TEXTAREA" || a.tagName === "INPUT")) return;
    if (location.hash.startsWith("#/incident/")) return;
    route(); loadInfo();
  }, 10000);
}

document.getElementById("autorefresh").checked = store.get("aiops.live", "1") === "1";
document.getElementById("autorefresh").onchange = schedule;
window.addEventListener("hashchange", route);
loadInfo().then(route);
schedule();
