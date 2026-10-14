/* BidIQ web UI — dependency-free SPA (hash routing). */
"use strict";

// ------------------------------------------------------------------ utilities
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => (v === null || v === undefined || v === "" || isNaN(v)) ? "—" : "$" + Math.round(v).toLocaleString();
const num = (v) => (v === null || v === undefined || v === "") ? "—" : Math.round(v).toLocaleString();
const pct = (p) => (p === null || p === undefined) ? "—" : Math.round(p * 100) + "%";
const nn = (v) => (v === "" || v === undefined ? null : v);
const TH = [["best_buy", "Best buy"], ["target", "Target"], ["walk_away", "Walk-away"], ["hard_ceiling", "Hard ceiling"]];

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 2600);
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

const store = {
  get token() { try { return localStorage.getItem("bidiq_token"); } catch { return null; } },
  set token(v) { try { v ? localStorage.setItem("bidiq_token", v) : localStorage.removeItem("bidiq_token"); } catch { } },
};

async function api(path, opts = {}) {
  const headers = opts.headers || {};
  if (store.token) headers["Authorization"] = "Bearer " + store.token;
  let body = opts.body;
  if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  const r = await fetch("/api" + path, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  if (r.status === 401) { store.token = null; location.hash = "#/login"; throw new Error("Please log in"); }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { }
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}

let META = null;
async function meta() { if (!META) META = await api("/meta"); return META; }

function formData(form) {
  const o = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") o[el.name] = el.checked;
    else if (el.type === "number") o[el.name] = el.value === "" ? null : Number(el.value);
    else o[el.name] = el.value === "" ? null : el.value;
  }
  return o;
}
const opt = (vals, sel, blank = true) => (blank ? '<option value=""></option>' : "") +
  vals.map((v) => `<option ${v == sel ? "selected" : ""} value="${esc(v)}">${esc(v)}</option>`).join("");

// ------------------------------------------------------------------ router
const routes = {
  login: viewLogin, dashboard: viewDashboard, price: viewPrice, targets: viewTargets, target: viewTarget,
  live: viewLive, bidsheet: viewBidsheet, multilot: viewMultilot, ingest: viewIngest, comps: viewComps,
  fmv: viewFmv, outcomes: viewOutcomes, settings: viewSettings, users: viewUsers,
};
let ME = null;
async function router() {
  const [path, query] = (location.hash.slice(2) || "dashboard").split("?");
  const [name, arg] = path.split("/");
  const params = new URLSearchParams(query || "");
  if (!store.token && name !== "login") { location.hash = "#/login"; return; }
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#/" + name));
  $("#nav").style.visibility = name === "login" ? "hidden" : "visible";
  const app = $("#app");
  app.innerHTML = '<div class="muted">Loading…</div>';
  try { await (routes[name] || viewDashboard)(app, arg, params); }
  catch (e) { app.innerHTML = `<div class="note bad">${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", router);
window.addEventListener("load", async () => {
  if (store.token) { try { const me = await api("/auth/me"); ME = me; $("#navUsers").hidden = !me.is_admin; $("#who").innerHTML = `${esc(me.tenant)} · ${esc(me.email)} · <a href="#" id="logout">Log out</a>`; $("#logout").onclick = (e) => { e.preventDefault(); store.token = null; location.hash = "#/login"; }; } catch { } }
  router();
});

// ------------------------------------------------------------------ login
async function viewLogin(app) {
  app.innerHTML = `<div class="panel login">
    <h1>BidIQ</h1><p class="muted">Turn sold comps into one defensible all-in max bid.</p>
    <div class="tabs"><button class="active" data-t="login">Log in</button><button data-t="register">Create account</button></div>
    <form id="f">
      <div id="tenantRow" hidden><label>Company / tenant name</label><input name="tenant_name"></div>
      <label>Email</label><input name="email" type="email" required autocomplete="username">
      <label>Password</label><input name="password" type="password" required minlength="8" autocomplete="current-password">
      <p><button class="primary" style="width:100%">Continue</button></p>
    </form>
    <p class="small muted">Demo data: run <span class="mono">python scripts/seed_demo.py</span> (credentials in the script's docstring).</p></div>`;
  let mode = "login";
  $$(".tabs button", app).forEach((b) => b.onclick = () => {
    mode = b.dataset.t; $$(".tabs button", app).forEach((x) => x.classList.toggle("active", x === b));
    $("#tenantRow").hidden = mode !== "register";
  });
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    const d = formData(e.target);
    try {
      const r = await api("/auth/" + mode, { body: mode === "login" ? { email: d.email, password: d.password } : d });
      store.token = r.token; META = null; location.hash = "#/dashboard"; location.reload();
    } catch (err) { toast(err.message); }
  };
}

// ------------------------------------------------------------------ dashboard
async function viewDashboard(app) {
  const d = await api("/dashboard");
  app.innerHTML = `
    <div class="grid g4">
      <div class="panel"><div class="kpi">${num(d.comps)}</div><div class="kpi-label">Sold comps in scope</div></div>
      <div class="panel"><div class="kpi">${num(d.excluded)}</div><div class="kpi-label">Non-op / branded (excluded from stats)</div></div>
      <div class="panel"><div class="kpi">${num(d.targets)}</div><div class="kpi-label">Target lots</div></div>
      <div class="panel"><div class="kpi">${num(d.fmv_rows)}</div><div class="kpi-label">Retail FMV comps</div></div>
    </div>
    <div class="grid g2">
      <div class="panel"><h2>Comp coverage by model group</h2><div class="table-wrap"><table>
        <tr><th>Group</th><th>Class</th><th class="num">N</th><th>Years</th><th class="num">Avg hammer</th><th></th></tr>
        ${d.coverage.map((c) => `<tr><td>${esc(c.model_group)}</td><td>${esc(c.body_class)}</td><td class="num">${c.n}</td>
          <td>${c.year_min}–${c.year_max}</td><td class="num">${money(c.avg_price)}</td>
          <td><a href="#/comps?model_group=${encodeURIComponent(c.model_group)}">comps</a></td></tr>`).join("") || '<tr><td colspan="6" class="muted">No comps yet — ingest result sheets.</td></tr>'}
      </table></div></div>
      <div>
        <div class="panel"><h2>Upcoming sales</h2>
          ${d.upcoming_sales.map((s) => `<div class="row"><b>${esc(s.sale_id)}</b> ${esc(s.sale_date || "")} ${esc(s.location || "")}
            <span class="spacer"></span><a href="#/targets?sale_id=${encodeURIComponent(s.sale_id)}">targets</a>
            <a href="#/bidsheet?sale_id=${encodeURIComponent(s.sale_id)}">bid sheet</a></div>`).join("") || '<div class="muted">None. Ingest a catalog or add target lots.</div>'}
        </div>
        <div class="panel"><h2>Recent result sheets</h2><table>
          <tr><th>Sale</th><th>Date</th><th>Location</th><th class="num">Lots</th></tr>
          ${d.recent_sales.map((s) => `<tr><td><a href="#/comps?sale_id=${encodeURIComponent(s.sale_id)}">${esc(s.sale_id)}</a></td><td>${esc(s.sale_date || "")}</td><td>${esc(s.location || "")}</td><td class="num">${s.lots}</td></tr>`).join("")}
        </table></div>
      </div>
    </div>`;
}

// ------------------------------------------------------------------ spec form (shared)
function specFormHtml(m, s = {}) {
  return `<div class="form-grid">
    <div><label>Year</label><input name="year" type="number" value="${esc(s.year ?? "")}"></div>
    <div><label>Make</label><input name="make" value="${esc(s.make ?? "")}" placeholder="FRTL, INTER, VOLVO…"></div>
    <div style="grid-column:span 2"><label>Model (raw, as listed)</label><input name="model_raw" value="${esc(s.model_raw ?? "")}" placeholder="M2 106 BUSINESS CLAS"></div>
    <div style="grid-column:span 2"><label>Model group (optional override)</label><select name="model_group">${opt(m.model_groups, s.model_group)}</select></div>
    <div><label>Body class</label><select name="body_class">${opt(m.body_classes, s.body_class)}</select></div>
    <div><label>Engine (raw)</label><input name="engine_raw" value="${esc(s.engine_raw ?? "")}" placeholder="CUMMINS ISB 6.7"></div>
    <div><label>Transmission</label><input name="trans_raw" value="${esc(s.trans_raw ?? "")}" placeholder="ALLISON 2500"></div>
    <div><label>Desc / sleeper</label><input name="desc_raw" value="${esc(s.desc_raw ?? "")}" placeholder="73IN RAISED SLP"></div>
    <div><label>Miles</label><input name="miles" type="number" value="${esc(s.miles ?? "")}"></div>
    <div><label>Hours (reefer)</label><input name="hours" type="number" value="${esc(s.hours ?? "")}"></div>
    <div><label>Hours metric</label><select name="hours_metric">${opt(["engine", "total", "electric"], s.hours_metric)}</select></div>
    <div><label>Door (reefer)</label><select name="door_type">${opt(["swing", "rollup", "liftgate"], s.door_type)}</select></div>
    <div><label>Sale location</label><input name="location" list="locs" value="${esc(s.location ?? "")}"></div>
    <div><label>Sale ID (optional)</label><input name="sale_id" value="${esc(s.sale_id ?? "")}"></div>
    <div><label>GVWR</label><input name="gvwr" type="number" value="${esc(s.gvwr ?? "")}"></div>
    <div><label>Rear axle lbs</label><input name="rear_axle_lbs" type="number" value="${esc(s.rear_axle_lbs ?? "")}"></div>
    <div style="grid-column:span 2"><label>VIN</label><input name="vin" value="${esc(s.vin ?? "")}" maxlength="17"></div>
    <div><label><input type="checkbox" name="liftgate" ${s.liftgate ? "checked" : ""}> Liftgate</label></div>
    <div><label><input type="checkbox" name="blind_buy" ${s.blind_buy ? "checked" : ""}> Blind buy (no inspection)</label></div>
  </div>
  <datalist id="locs">${m.locations.map((l) => `<option value="${esc(l)}">`).join("")}</datalist>`;
}

// ------------------------------------------------------------------ what-if panel
function whatIfHtml(ev, settings) {
  const rec = ev.recommendation || {};
  const cb = rec.cost_basis || {};
  const tr = cb.transport || {};
  const miles = ev.spec.miles ?? "";
  return `<div class="panel no-print"><h2>What-if</h2>
    <div class="slider-row"><div><label>Aggressiveness (0 = best-buy, 1 = fair)</label><input type="range" name="aggressiveness" min="0" max="1" step="0.05" value="${rec.aggressiveness ?? settings.aggressiveness}"></div><output></output></div>
    <div class="slider-row"><div><label>Miles</label><input type="range" name="miles" min="0" max="1500000" step="5000" value="${miles || 0}" ${miles === "" ? "data-unset=1" : ""}></div><output></output></div>
    <div class="slider-row"><div><label>Buyer's premium %</label><input type="range" name="buyers_premium_pct" min="0" max="12" step="0.5" value="${cb.buyers_premium_pct ?? 5}"></div><output></output></div>
    <div class="slider-row"><div><label>Transport miles</label><input type="range" name="transport_miles" min="0" max="2500" step="10" value="${tr.miles ?? 0}"></div><output></output></div>
    <div class="slider-row"><div><label>Transport $/mile</label><input type="range" name="transport_rate" min="0" max="6" step="0.05" value="${tr.rate ?? 1.75}"></div><output></output></div>
    <label><input type="checkbox" name="blind_buy" ${rec.reserve && rec.reserve.applied ? "checked" : ""}> Blind buy (apply itemized reserve)</label>
    <label>Budget cap (all-in $)</label><input type="number" name="budget_cap_all_in" placeholder="none">
    <p><button type="button" id="wiReset">Reset</button></p></div>`;
}
function bindWhatIf(root, run) {
  const fmt = { aggressiveness: (v) => (+v).toFixed(2), miles: (v) => num(v), buyers_premium_pct: (v) => v + "%", transport_miles: (v) => num(v), transport_rate: (v) => "$" + (+v).toFixed(2) };
  const els = $$("input[type=range]", root);
  const upd = () => els.forEach((e) => { e.parentElement.nextElementSibling.textContent = e.dataset.unset ? "—" : fmt[e.name](e.value); });
  const collect = () => {
    const o = {};
    for (const e of $$("input", root)) {
      if (e.type === "range") { if (!e.dataset.unset && e.dataset.touched) o[e.name] = Number(e.value); }
      else if (e.type === "checkbox") { if (e.dataset.touched) o[e.name] = e.checked; }
      else if (e.value !== "") o[e.name] = Number(e.value);
    }
    return o;
  };
  const fire = debounce(() => run(collect()), 250);
  $$("input", root).forEach((e) => e.addEventListener("input", () => { e.dataset.touched = 1; delete e.dataset.unset; upd(); fire(); }));
  $("#wiReset", root).onclick = () => run({}, true);
  upd();
}

// ------------------------------------------------------------------ recommendation renderer
function winChart(rec, pred) {
  const pts = rec.curve; if (!pts || !pts.length) return "";
  const W = 640, H = 220, L = 44, R = 12, T = 12, B = 30;
  const x0 = pts[0].bid, x1 = pts[pts.length - 1].bid;
  const X = (b) => L + (b - x0) / (x1 - x0 || 1) * (W - L - R);
  const Y = (p) => T + (1 - p) * (H - T - B);
  const d = pts.map((p, i) => (i ? "L" : "M") + X(p.bid).toFixed(1) + "," + Y(p.p).toFixed(1)).join(" ");
  const area = d + ` L${X(x1)},${Y(0)} L${X(x0)},${Y(0)} Z`;
  const colors = { best_buy: "var(--info)", target: "var(--good)", walk_away: "var(--warn)", hard_ceiling: "var(--bad)" };
  const markers = TH.map(([k, lbl]) => { const v = rec.hammer[k]; if (v < x0 || v > x1) return ""; return `<line class="marker" x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${H - B}" stroke="${colors[k]}"/><text x="${X(v) + 3}" y="${T + 10 + TH.findIndex((t) => t[0] === k) * 12}" style="fill:${colors[k]}">${lbl}</text>`; }).join("");
  const ticks = [0, .25, .5, .75, 1].map((p) => `<line class="axis" x1="${L}" x2="${W - R}" y1="${Y(p)}" y2="${Y(p)}"/><text x="${L - 6}" y="${Y(p) + 4}" text-anchor="end">${p * 100}%</text>`).join("");
  const xt = [0, .25, .5, .75, 1].map((f) => { const b = x0 + f * (x1 - x0); return `<text x="${X(b)}" y="${H - 10}" text-anchor="middle">$${Math.round(b / 1000)}k</text>`; }).join("");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Win probability by hammer bid">${ticks}${xt}<path class="area" d="${area}"/><path class="curve" d="${d}"/>${markers}</svg></div>
    <div class="small muted">P(win at bid B) = share of cohort comps that sold ≤ B (${esc(rec.win_basis)}${rec.win_basis === "modelled" ? " — normal approx. from blended p25/p75, cohort too thin or extrapolated" : ""}).
    For ≥80% odds bid ${money(rec.bid_for_probability["80"])}; ≥90% bid ${money(rec.bid_for_probability["90"])}.</div>`;
}

function renderRec(el, ev) {
  const p = ev.prediction, rec = ev.recommendation, risk = ev.risk;
  if (!p.ok || !rec.ok) { el.innerHTML = `<div class="note bad">${esc(p.error || rec.error)}</div>`; return; }
  const cf = p.confidence;
  const eq = rec.equity;
  const methodRows = Object.entries(p.methods).map(([k, m]) => `<tr><td><b>${k}</b> ${esc(m.label)}</td><td class="num">${(p.weights[k] * 100).toFixed(0)}%</td>
    <td class="num">${money(m.p25)}</td><td class="num">${money(m.p50)}</td><td class="num">${money(m.p75)}</td>
    <td class="small">${k === "A" ? `N=${m.n}; ${esc(m.band)}; raw median ${money(m.raw_p50)}` : k === "B" ? `N=${m.n}, R²=${m.r2}, σ=${money(m.sigma)}; ${m.dollars_per_year != null ? money(m.dollars_per_year) + "/yr" : ""} ${m.dollars_per_100k_miles != null ? "· " + money(m.dollars_per_100k_miles) + "/100k mi" : ""}${m.dollars_per_1k_hours != null ? "· " + money(m.dollars_per_1k_hours) + "/1k hrs" : ""}` : `retail ${money(m.retail)} (${esc(m.fmv.tier)}, as of ${esc(m.fmv.as_of)}${m.fmv.stale ? ", STALE" : ""})`}</td></tr>`).join("");
  const tr = rec.cost_basis.transport;
  const flags = risk.flags.map((f) => `<span class="chip ${f.severity}" title="${esc(f.message)}">${esc(f.code)}</span>`).join("");
  el.innerHTML = `
    <div class="panel">
      <div class="row"><h2 style="margin:0">${esc(ev.spec.year || "")} ${esc(ev.spec.make || "")} ${esc(ev.spec.model_group || "")}</h2>
        <span class="chip">${esc(ev.spec.body_class || "")}</span><span class="chip">${esc(ev.spec.engine_family || "engine ?")}</span>
        <span class="chip">${ev.spec.miles != null ? num(ev.spec.miles) + " mi" : ev.spec.hours != null ? num(ev.spec.hours) + " hrs (" + esc(ev.spec.hours_metric || "?") + ")" : "usage ?"}</span>
        <span class="spacer"></span><span class="chip conf-${cf.level}" title="${esc(cf.reasons.join("\n"))}">Price confidence: ${cf.level} (${cf.score})</span></div>
      <div class="small muted" style="margin:4px 0 10px">Fair (p50) <b>${money(p.p50)}</b> · p25 ${money(p.p25)} · p75 ${money(p.p75)} · market top ${money(p.max)} — ${esc(p.max_source)}</div>
      ${rec.notes.map((n) => `<div class="note">${esc(n)}</div>`).join("")}
      ${p.cohort_notes.map((n) => `<div class="note info">${esc(n)}</div>`).join("")}
      <div class="thresholds">${TH.map(([k, l]) => `<div class="th-card ${k === "target" ? "target" : k === "hard_ceiling" ? "ceiling" : ""}">
        <div class="name">${l}</div><div class="hammer">${money(rec.hammer[k])}</div>
        <div class="allin">All-in <b>${money(rec.all_in[k])}</b></div>
        <div class="p">Win ~${pct(rec.win_prob[k])}${eq ? ` · equity ${money(eq.by_threshold[k].dollars)} (${eq.by_threshold[k].pct}%)` : ""}</div></div>`).join("")}</div>
      <div style="margin-top:10px">${flags}</div>
    </div>
    <div class="grid g2">
      <div class="panel"><h2>Win probability</h2>${winChart(rec, p)}</div>
      <div class="panel"><h2>Cost basis &amp; equity</h2>
        <table>
          <tr><td>Buyer's premium</td><td class="num">${rec.cost_basis.buyers_premium_pct}%</td><td class="small muted">${esc(rec.cost_basis.premium_source)}</td></tr>
          <tr><td>Transport (${esc(tr.mode)})</td><td class="num">${money(tr.cost)}</td><td class="small muted">${tr.miles != null ? num(tr.miles) + " mi × $" + tr.rate + " · " + esc(tr.source) + " → " + esc(tr.home || "") : "unknown origin"}</td></tr>
          <tr><td>Title / doc fees</td><td class="num">${money(rec.cost_basis.title_doc_fees)}</td><td></td></tr>
          ${rec.reserve.applied ? rec.reserve.items.map((i) => `<tr><td>Reserve: ${esc(i.item)}</td><td class="num">−${money(i.amount)}</td><td class="small muted">deducted from ceiling</td></tr>`).join("") : ""}
          ${rec.ceiling_basis.map((c) => `<tr><td class="small">Ceiling ← ${esc(c.label)}</td><td class="num small">${money(c.hammer)}</td><td></td></tr>`).join("")}
        </table>
        <div class="small muted mono" style="margin-top:6px">${esc(rec.cost_basis.formula)}</div>
        <h3>Retail equity</h3>
        ${eq ? `<div>Retail FMV (${esc(eq.tier)}) <b>${money(eq.retail)}</b> as of ${esc(eq.as_of)} ${eq.stale ? '<span class="chip medium">STALE</span>' : ""}</div>
          <div class="small">Target all-in = ${eq.allin_pct_of_retail.target}% of retail; ceiling all-in = ${eq.allin_pct_of_retail.hard_ceiling}% of retail.</div>`
      : '<div class="muted small">No retail FMV on file for this config — add comps on the Retail FMV page.</div>'}
        ${rec.deposit ? `<h3>Deposit &amp; logistics</h3><div class="small">Deposit ${money(rec.deposit.amount)} · wire by ${esc(rec.deposit.wire_deadline)} · full pay within ${esc(rec.deposit.payment_window_hours)}h${rec.deposit.absolute ? " · <b>absolute sale, no arbitration</b>" : ""}${rec.deposit.terms_notes ? " · " + esc(rec.deposit.terms_notes) : ""}</div>` : ""}
      </div>
    </div>
    <div class="panel"><h2>How the price was built</h2><div class="table-wrap"><table>
      <tr><th>Method</th><th class="num">Weight</th><th class="num">p25</th><th class="num">p50</th><th class="num">p75</th><th>Detail</th></tr>${methodRows}</table></div>
      <div class="small muted">Confidence: ${cf.reasons.map(esc).join(" · ")}. Confidence never changes the price itself.</div>
      ${p.assumptions.length ? `<div class="note">Assumptions: ${p.assumptions.map(esc).join("; ")}</div>` : ""}
    </div>
    <div class="grid g3">
      <div class="panel"><h2>CDL / GVWR</h2><div>${esc(risk.cdl.message)}</div><div class="small muted">Status: ${esc(risk.cdl.status)}</div></div>
      <div class="panel"><h2>VIN</h2>${risk.vin.status === "missing" ? '<div class="muted">No VIN on file.</div>' :
      `<div>${risk.vin.valid ? '<span class="chip good">check digit OK</span>' : '<span class="chip high">check failed</span>'} <span class="mono">${esc(risk.vin.vin)}</span></div>
         <div class="small">Decoded: ${esc(risk.vin.decoded.make || "?")} · ${esc(risk.vin.decoded.year || "?")} · ${esc(risk.vin.decoded.country || "?")} · plant ${esc(risk.vin.decoded.plant || "?")}</div>
         ${risk.vin.issues.map((i) => `<div class="note bad small">${esc(i)}</div>`).join("")}`}
        <div class="small"><a href="${risk.vin.links.nicb}" target="_blank" rel="noopener">NICB VINCheck</a> · <a href="${risk.vin.links.nmvtis}" target="_blank" rel="noopener">NMVTIS</a></div></div>
      <div class="panel"><h2>Warranty</h2><div><span class="chip ${risk.warranty.eligible ? "good" : "medium"}">${esc(risk.warranty.status)}</span></div>
        <div class="small">${esc(risk.warranty.terms)}</div><div class="small"><b>${esc(risk.warranty.not_covered)}</b></div>
        ${risk.warranty.reasons.map((r) => `<div class="small muted">• ${esc(r)}</div>`).join("")}
        ${risk.reefer ? `<h3>Reefer</h3>${risk.reefer.notes.map((n) => `<div class="small">• ${esc(n)}</div>`).join("")}${risk.reefer.flags.map((f) => `<span class="chip medium">${esc(f)}</span>`).join("")}` : ""}
      </div>
    </div>
    <div class="panel"><h2>Comps behind this number (${p.comps.length})</h2><div class="table-wrap"><table>
      <tr><th>Sale</th><th>Date</th><th>Lot</th><th>Yr</th><th>Model</th><th>Engine</th><th class="num">Miles</th><th class="num">Hrs</th><th class="num">Hammer</th><th class="num">Adj. to target</th><th>Source</th></tr>
      ${p.comps.map((c) => `<tr><td>${esc(c.sale_id)}</td><td>${esc(c.sale_date || "")}</td><td>${esc(c.lot_no)}</td><td>${c.year}</td><td>${esc(c.model_raw)}</td><td class="small">${esc(c.engine_family || "")}</td>
        <td class="num">${num(c.miles)}</td><td class="num">${num(c.hours)}</td><td class="num">${money(c.sold_price)}</td><td class="num"><b>${money(c.adjusted_price)}</b></td><td class="small muted">${esc(c.source_file || "")}${c.shared ? "" : " (private)"}</td></tr>`).join("")}
    </table></div></div>`;
}

// ------------------------------------------------------------------ quick price
async function viewPrice(app, _arg, params) {
  const m = await meta();
  const { settings } = await api("/settings");
  let spec = {};
  try { spec = JSON.parse(sessionStorage.getItem("bidiq_quick") || "{}"); } catch { }
  app.innerHTML = `<div class="grid side">
    <div><form class="panel" id="specForm"><h2>Lot spec</h2>${specFormHtml(m, spec)}
      <p class="row"><button class="primary">Price it</button><button type="button" id="saveTarget">Save as target lot</button></p></form>
      <div id="wi"></div></div>
    <div id="out"><div class="panel muted">Enter a spec and press <b>Price it</b>. Every number traces back to the sold comps listed below it.</div></div></div>`;
  let lastSpec = null;
  const run = async (overrides = {}, reset = false) => {
    const ev = await api("/recommend", { body: { spec: lastSpec, overrides } });
    renderRec($("#out"), ev);
    if (reset || !$("#wi").innerHTML) { $("#wi").innerHTML = whatIfHtml(ev, settings); bindWhatIf($("#wi"), run); }
  };
  $("#specForm").onsubmit = async (e) => {
    e.preventDefault();
    lastSpec = formData(e.target);
    try { sessionStorage.setItem("bidiq_quick", JSON.stringify(lastSpec)); } catch { }
    $("#wi").innerHTML = "";
    try { await run({}, true); } catch (err) { toast(err.message); }
  };
  $("#saveTarget").onclick = async () => {
    const d = formData($("#specForm"));
    try { const t = await api("/targets", { body: d }); toast("Saved target lot"); location.hash = "#/target/" + t.id; } catch (err) { toast(err.message); }
  };
  if (spec.model_raw || spec.model_group) $("#specForm").requestSubmit();
}

// ------------------------------------------------------------------ targets list
async function viewTargets(app, _arg, params) {
  const saleId = params.get("sale_id") || "";
  const [sales, sheet] = await Promise.all([api("/sales"), api("/bidsheet" + (saleId ? "?sale_id=" + encodeURIComponent(saleId) : ""))]);
  app.innerHTML = `<div class="panel"><div class="row">
      <h1 style="margin:0">Target lots</h1>
      <select id="saleSel" style="width:auto"><option value="">All sales</option>${sales.map((s) => `<option ${s.sale_id === saleId ? "selected" : ""} value="${esc(s.sale_id)}">${esc(s.sale_id)} ${esc(s.sale_date || "")} ${esc(s.location || "")}</option>`).join("")}</select>
      <span class="spacer"></span>
      <a class="btn" href="#/price">+ Add via Quick price</a>
      <a class="btn" href="#/bidsheet${saleId ? "?sale_id=" + encodeURIComponent(saleId) : ""}">Printable bid sheet</a>
      <button id="csv">Export CSV</button></div></div>
    <details class="panel" id="catPanel"${sheet.rows.length ? "" : " open"}><summary><b>Import upcoming lots from catalog files</b> <span class="muted small">— several PDFs/CSVs at once</span></summary>
      <form id="tc" style="margin-top:8px">${uploaderHtml("catalog PDFs / CSVs")}
        <div class="grid g3"><div><label>Sale ID <span class="muted">(blank = read from each filename)</span></label><input name="sale_id" value="${esc(saleId)}" placeholder="e.g. 2026101"></div>
        <div><label>Sale date</label><input type="date" name="sale_date"></div><div><label>Location</label><input name="location"></div></div>
        <p><button class="primary">Import catalogs</button></p></form></details>
    ${saleId ? `<div id="saleEdit"></div>` : ""}
    <div class="panel"><div class="table-wrap"><table>
      <tr><th>Sale</th><th>Lot</th><th>Unit</th><th class="num">Miles/Hrs</th><th>CDL</th><th class="num">N</th><th>Conf.</th>
        <th class="num">Target</th><th class="num">Walk</th><th class="num">Ceiling</th><th class="num">Ceiling all-in</th><th class="num">Win@target</th><th class="num">Equity@target</th><th>Flags</th><th>Status</th><th></th></tr>
      ${sheet.rows.map((r) => `<tr>
        <td>${esc(r.sale_id || "")}</td><td><b>${esc(r.lot_no || "")}</b></td>
        <td><a href="#/target/${r.id}">${esc(r.year || "")} ${esc(r.make || "")} ${esc(r.model_raw || r.model_group || "")}</a><div class="small muted">${esc(r.model_group || "")} · ${esc(r.engine_family || "")}</div></td>
        <td class="num">${r.miles != null ? num(r.miles) : r.hours != null ? num(r.hours) + " h" : "—"}</td>
        <td class="small">${r.cdl_required === true ? '<span class="chip medium">CDL</span>' : r.cdl_required === false ? '<span class="chip good">non-CDL</span>' : r.cdl_status === "n/a" ? '<span class="chip">n/a</span>' : '<span class="chip medium" title="' + esc(r.cdl) + '">?</span>'}</td>
        ${r.ok ? `<td class="num">${r.n}</td><td><span class="chip conf-${r.confidence}">${r.confidence}</span></td>
        <td class="num">${money(r.hammer.target)}</td><td class="num">${money(r.hammer.walk_away)}</td><td class="num"><b>${money(r.hammer.hard_ceiling)}</b></td>
        <td class="num">${money(r.all_in.hard_ceiling)}</td><td class="num">${pct(r.win_prob.target)}</td>
        <td class="num">${r.equity_target ? money(r.equity_target.dollars) : "—"}</td>` : `<td colspan="8" class="small muted">${esc(r.error)}</td>`}
        <td>${r.flags.map((f) => `<span class="chip">${esc(f)}</span>`).join("")}</td>
        <td>${esc(r.status || "")}</td>
        <td class="small"><a href="#/live/${r.id}">live</a></td></tr>`).join("") || '<tr><td colspan="16" class="muted">No target lots. Ingest a catalog (Ingest page) or save one from Quick price.</td></tr>'}
    </table></div></div>`;
  $("#saleSel").onchange = (e) => location.hash = "#/targets" + (e.target.value ? "?sale_id=" + encodeURIComponent(e.target.value) : "");
  $("#csv").onclick = () => downloadAuthed("/bidsheet.csv" + (saleId ? "?sale_id=" + encodeURIComponent(saleId) : ""), `bidsheet_${saleId || "all"}.csv`);
  bindUploader($("#tc"), "/ingest/catalog", { prepare: catalogPrepare, done: catalogDone, after: () => setTimeout(router, 1500) });
  if (saleId) renderSaleEditor($("#saleEdit"), sales.find((s) => s.sale_id === saleId) || { sale_id: saleId });
}

function renderSaleEditor(el, s) {
  el.innerHTML = `<form class="panel" id="saleForm"><h2>Sale terms — ${esc(s.sale_id)}</h2><div class="grid g4">
    <div><label>Sale date</label><input type="date" name="sale_date" value="${esc(s.sale_date || "")}"></div>
    <div><label>Location</label><input name="location" value="${esc(s.location || "")}"></div>
    <div><label>Buyer's premium % (this sale)</label><input type="number" step="0.5" name="buyers_premium_pct" value="${esc(s.buyers_premium_pct ?? "")}"></div>
    <div><label>Deposit $</label><input type="number" name="deposit_amount" value="${esc(s.deposit_amount ?? "")}"></div>
    <div><label>Wire deadline</label><input name="wire_deadline" value="${esc(s.wire_deadline || "")}"></div>
    <div><label>Payment window (h)</label><input type="number" name="payment_window_hours" value="${esc(s.payment_window_hours ?? "")}"></div>
    <div><label><input type="checkbox" name="absolute" ${s.absolute !== false ? "checked" : ""}> Absolute / no arbitration</label></div>
    <div><label>Terms notes</label><input name="terms_notes" value="${esc(s.terms_notes || "")}"></div>
    </div><p><button class="primary">Save sale terms</button> <span class="small muted">Premium varies per sale/lot — never hardcoded.</span></p></form>`;
  $("#saleForm").onsubmit = async (e) => {
    e.preventDefault();
    const d = formData(e.target); d.sale_id = s.sale_id;
    try { await api("/sales", { body: d }); toast("Saved"); router(); } catch (err) { toast(err.message); }
  };
}

async function downloadAuthed(path, filename) {
  const r = await fetch("/api" + path, { headers: { Authorization: "Bearer " + store.token } });
  if (!r.ok) { toast("Export failed"); return; }
  const blob = await r.blob();
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ------------------------------------------------------------------ target detail
async function viewTarget(app, id) {
  const m = await meta();
  const [{ settings }, data] = await Promise.all([api("/settings"), api(`/targets/${id}/recommendation`)]);
  const t = data.target;
  const ck = t.checklist || {};
  app.innerHTML = `<div class="row no-print" style="margin-bottom:10px"><a href="#/targets?sale_id=${encodeURIComponent(t.sale_id || "")}">← Targets</a>
      <span class="spacer"></span><a class="btn primary" href="#/live/${t.id}">Open live bid card</a></div>
    <div class="grid side">
      <div>
        <form class="panel" id="tForm"><h2>Lot ${esc(t.lot_no || "")} · sale ${esc(t.sale_id || "—")}</h2>
          <div class="form-grid"><div><label>Lot #</label><input name="lot_no" value="${esc(t.lot_no || "")}"></div>
          <div><label>Run order</label><input type="number" name="run_order" value="${esc(t.run_order ?? "")}"></div></div>
          ${specFormHtml(m, t)}
          <div class="form-grid">
            <div><label>Body length ft</label><input type="number" name="body_length_ft" value="${esc(t.body_length_ft ?? "")}"></div>
            <div><label>Premium % (this lot)</label><input type="number" step="0.5" name="buyers_premium_pct" value="${esc(t.buyers_premium_pct ?? "")}"></div>
            <div><label>Transport miles override</label><input type="number" name="transport_miles" value="${esc(t.transport_miles ?? "")}"></div>
            <div><label>Transport mode</label><select name="transport_mode">${opt(["driveaway", "haul"], t.transport_mode)}</select></div>
            <div><label>Retail FMV override $</label><input type="number" name="retail_override" value="${esc(t.retail_override ?? "")}"></div>
            <div><label>Status</label><select name="status">${opt(["watch", "bid", "won", "lost", "skipped"], t.status, false)}</select></div>
            <div><label><input type="checkbox" name="apu" ${t.apu ? "checked" : ""}> APU</label></div>
            <div><label><input type="checkbox" name="branded_title" ${t.branded_title ? "checked" : ""}> Branded title</label></div>
            <div><label><input type="checkbox" name="non_operable" ${t.non_operable ? "checked" : ""}> Non-operable</label></div>
            <div><label><input type="checkbox" name="tamper_flag" ${t.tamper_flag ? "checked" : ""}> ODOM/ECM mismatch</label></div>
          </div>
          <label>Condition notes</label><textarea name="condition_notes">${esc(t.condition_notes || "")}</textarea>
          <h3>Pre-bid checklist</h3>
          ${[["vin_sticker_match", "VIN matches door sticker"], ["gvwr_confirmed", "GVWR confirmed on door sticker"], ["nicb", "NICB VINCheck clean"], ["nmvtis", "NMVTIS title check clean"]]
      .map(([k, l]) => `<label><input type="checkbox" data-ck="${k}" ${ck[k] ? "checked" : ""}> ${l}</label>`).join("")}
          <p class="row"><button class="primary">Save &amp; reprice</button><button type="button" class="danger" id="del">Delete</button></p>
        </form>
        <div id="wi"></div>
        <form class="panel" id="oForm"><h2>Log outcome</h2>
          <div class="form-grid">
            <div><label><input type="checkbox" name="did_bid"> We bid</label></div><div><label><input type="checkbox" name="won"> We won</label></div>
            <div><label>Our max bid</label><input type="number" name="our_max_bid"></div><div><label>Final hammer</label><input type="number" name="final_hammer"></div>
            <div><label>All-in paid</label><input type="number" name="all_in_paid"></div><div><label>Repair costs</label><input type="number" name="repair_costs"></div>
            <div><label>Resale price</label><input type="number" name="resale_price"></div><div><label><input type="checkbox" name="share_with_pool"> Share with pool (opt-in)</label></div>
          </div>
          <label>Condition on arrival</label><textarea name="condition_on_arrival"></textarea>
          <label>Lease / resale outcome</label><input name="lease_outcome">
          <p><button>Save outcome</button></p></form>
      </div>
      <div id="out"></div>
    </div>`;
  renderRec($("#out"), data);
  const run = async (overrides = {}, reset = false) => {
    const ev = await api("/whatif", { body: { target_id: t.id, overrides } });
    renderRec($("#out"), ev);
    if (reset) { $("#wi").innerHTML = whatIfHtml(ev, settings); bindWhatIf($("#wi"), run); }
  };
  $("#wi").innerHTML = whatIfHtml(data, settings); bindWhatIf($("#wi"), run);
  $("#tForm").onsubmit = async (e) => {
    e.preventDefault();
    const d = formData(e.target);
    d.checklist = {}; $$("[data-ck]", e.target).forEach((c) => d.checklist[c.dataset.ck] = c.checked);
    try { await api(`/targets/${t.id}`, { method: "PUT", body: d }); toast("Saved"); router(); } catch (err) { toast(err.message); }
  };
  $("#del").onclick = async () => { if (!confirm("Delete this target lot?")) return; await api(`/targets/${t.id}`, { method: "DELETE" }); location.hash = "#/targets"; };
  $("#oForm").onsubmit = async (e) => {
    e.preventDefault();
    const d = formData(e.target); d.target_lot_id = t.id;
    try { await api("/outcomes", { body: d }); toast("Outcome logged"); router(); } catch (err) { toast(err.message); }
  };
}

// ------------------------------------------------------------------ live bid card
async function viewLive(app, id) {
  const data = await api(`/targets/${id}/recommendation`);
  const t = data.target, rec = data.recommendation, p = data.prediction;
  if (!rec.ok) { app.innerHTML = `<div class="note bad">${esc(rec.error)}</div>`; return; }
  const cb = rec.cost_basis;
  const allIn = (h) => h * (1 + cb.buyers_premium_pct / 100) + cb.transport.cost + cb.title_doc_fees;
  const winAt = (b) => { // interpolate the curve returned by the server
    const c = rec.curve; if (b <= c[0].bid) return c[0].p; if (b >= c[c.length - 1].bid) return c[c.length - 1].p;
    for (let i = 1; i < c.length; i++) if (b <= c[i].bid) { const f = (b - c[i - 1].bid) / (c[i].bid - c[i - 1].bid); return c[i - 1].p + f * (c[i].p - c[i - 1].p); }
  };
  app.innerHTML = `<div class="live">
    <div class="row"><a href="#/target/${t.id}">← Lot detail</a><span class="spacer"></span><span class="chip conf-${p.confidence.level}">Confidence ${p.confidence.level}</span></div>
    <h1 style="text-align:center;margin:8px 0">Lot ${esc(t.lot_no || "")} · ${esc(t.year || "")} ${esc(t.make || "")} ${esc(t.model_raw || "")}</h1>
    <div class="small muted" style="text-align:center;margin-bottom:10px">${t.miles != null ? num(t.miles) + " mi" : ""} ${esc(t.engine_family || "")} · premium ${cb.buyers_premium_pct}% · freight ${money(cb.transport.cost)}${rec.reserve.applied ? " · blind-buy reserve " + money(rec.reserve.total) : ""}</div>
    <div class="big">
      <div style="border-color:var(--good)"><div class="l">Target</div><div class="v">${money(rec.hammer.target)}</div><div class="a">all-in ${money(rec.all_in.target)}</div></div>
      <div style="border-color:var(--warn)"><div class="l">Walk-away</div><div class="v">${money(rec.hammer.walk_away)}</div><div class="a">all-in ${money(rec.all_in.walk_away)}</div></div>
      <div style="border-color:var(--bad)"><div class="l">Ceiling</div><div class="v">${money(rec.hammer.hard_ceiling)}</div><div class="a">all-in ${money(rec.all_in.hard_ceiling)}</div></div>
    </div>
    <div class="panel" style="margin-top:14px"><label>Current bid (hammer)</label>
      <input class="bid" id="bid" type="number" inputmode="numeric" step="250" placeholder="0">
      <div class="row" style="justify-content:center;margin-top:8px">${[-500, -250, 250, 500, 1000].map((d) => `<button data-d="${d}">${d > 0 ? "+" : ""}${d}</button>`).join("")}</div>
      <div class="status" id="status">Enter the current bid</div>
      <div class="row" style="justify-content:center;margin-top:8px;font-size:18px"><div>Win odds <b id="wp">—</b></div><div>·</div><div>All-in <b id="ai">—</b></div>${rec.equity ? `<div>·</div><div>Equity <b id="eq">—</b></div>` : ""}</div>
    </div>
    ${rec.notes.map((n) => `<div class="note">${esc(n)}</div>`).join("")}
  </div>`;
  const bid = $("#bid");
  const upd = () => {
    const b = Number(bid.value || 0); const st = $("#status");
    if (!bid.value) { st.className = "status"; st.textContent = "Enter the current bid"; return; }
    $("#wp").textContent = pct(winAt(b)); $("#ai").textContent = money(allIn(b));
    if (rec.equity) $("#eq").textContent = money(rec.equity.retail - allIn(b));
    if (b > rec.hammer.hard_ceiling) { st.className = "status stop"; st.textContent = "STOP — over ceiling"; }
    else if (b > rec.hammer.walk_away) { st.className = "status caution"; st.textContent = "CAUTION — past walk-away"; }
    else if (b > rec.hammer.target) { st.className = "status caution"; st.textContent = "Above target — OK to walk-away"; }
    else { st.className = "status go"; st.textContent = "GO — at/below target"; }
  };
  bid.addEventListener("input", upd);
  $$("[data-d]", app).forEach((b) => b.onclick = () => { bid.value = Math.max(0, Number(bid.value || 0) + Number(b.dataset.d)); upd(); });
  bid.focus();
}

// ------------------------------------------------------------------ printable bid sheet
async function viewBidsheet(app, _arg, params) {
  const saleId = params.get("sale_id") || "";
  const d = await api("/bidsheet" + (saleId ? "?sale_id=" + encodeURIComponent(saleId) : ""));
  const s = d.sale;
  const dep = (d.rows.find((r) => r.deposit) || {}).deposit;
  app.innerHTML = `<div class="panel sheet">
    <div class="row no-print" style="margin-bottom:8px"><a href="#/targets${saleId ? "?sale_id=" + encodeURIComponent(saleId) : ""}">← Targets</a><span class="spacer"></span><button class="primary" onclick="window.print()">Print</button></div>
    <h1>Bid sheet ${s ? esc(s.sale_id) + " · " + esc(s.sale_date || "") + " · " + esc(s.location || "") : "(all targets)"}</h1>
    <div class="small">Home: ${esc(d.settings.home_label)} · premium default ${d.settings.default_buyers_premium_pct}% ${s && s.buyers_premium_pct != null ? "· this sale " + s.buyers_premium_pct + "%" : ""}
      ${dep ? `· Deposit ${money(dep.amount)}, wire by ${esc(dep.wire_deadline)}, pay in full within ${esc(dep.payment_window_hours)}h` : ""} · <b>Absolute / as-is — no arbitration</b></div>
    <table style="margin-top:8px">
      <tr><th>Lot</th><th>Unit</th><th class="num">Miles</th><th>GVWR/CDL</th><th>VIN</th><th>Checks (sticker · GVWR · NICB · NMVTIS)</th><th>N/conf</th>
        <th class="num">Target<br>hammer / all-in</th><th class="num">Walk<br>hammer / all-in</th><th class="num">Ceiling<br>hammer / all-in</th><th class="num">Win% T/C</th><th class="num">Equity@T</th><th>Flags</th><th>Max bid / result</th></tr>
      ${d.rows.map((r) => { const ck = r.checklist || {}; const bx = (v) => v ? "☑" : '<span class="box"></span>'; return `<tr>
        <td><b>${esc(r.lot_no || "")}</b></td><td>${esc(r.year || "")} ${esc(r.make || "")} ${esc(r.model_raw || "")}<br><span class="muted">${esc(r.engine_family || "")} ${r.body_length_ft ? esc(r.body_length_ft) + "ft" : ""}</span></td>
        <td class="num">${r.miles != null ? num(r.miles) : r.hours != null ? num(r.hours) + "h" : "—"}</td>
        <td>${r.cdl_status === "n/a" ? "n/a" : (r.gvwr ? num(r.gvwr) : "____") + " " + (r.cdl_required === true ? "CDL" : r.cdl_required === false ? "non-CDL" : "?")}</td>
        <td class="mono">${esc(r.vin || "_________________")}</td>
        <td>${bx(ck.vin_sticker_match)} ${bx(ck.gvwr_confirmed)} ${bx(ck.nicb)} ${bx(ck.nmvtis)}</td>
        ${r.ok ? `<td>${r.n} / ${r.confidence}</td>
        <td class="num">${money(r.hammer.target)}<br>${money(r.all_in.target)}</td>
        <td class="num">${money(r.hammer.walk_away)}<br>${money(r.all_in.walk_away)}</td>
        <td class="num"><b>${money(r.hammer.hard_ceiling)}</b><br>${money(r.all_in.hard_ceiling)}</td>
        <td class="num">${pct(r.win_prob.target)} / ${pct(r.win_prob.hard_ceiling)}</td>
        <td class="num">${r.equity_target ? money(r.equity_target.dollars) : "—"}</td>` : `<td colspan="6">${esc(r.error)}</td>`}
        <td class="small">${r.flags.map(esc).join(", ")}</td><td>__________</td></tr>`; }).join("")}
    </table></div>`;
}

// ------------------------------------------------------------------ multi-lot
async function viewMultilot(app) {
  const [targets, { settings }] = await Promise.all([api("/targets"), api("/settings")]);
  app.innerHTML = `<div class="grid side">
    <form class="panel" id="mf"><h2>Multi-lot budget plan</h2>
      <p class="small muted">Bidding several similar lots in one sale? Set a total all-in budget and max wins; the plan de-escalates ceilings after each win and skips lots once the budget can't cover their target.</p>
      <label>Total budget (all-in $)</label><input type="number" name="budget_all_in" required value="100000">
      <label>Max wins</label><input type="number" name="max_wins" value="${settings.multi_lot_max_wins}">
      <label>De-escalate ceiling after each win (%)</label><input type="number" step="0.5" name="deescalate_pct" value="${settings.multi_lot_deescalate_pct}">
      <h3>Lots (run order)</h3>
      ${targets.map((t) => `<label><input type="checkbox" name="t" value="${t.id}"> ${esc(t.sale_id || "")} · lot ${esc(t.lot_no || "")} · ${esc(t.year || "")} ${esc(t.make || "")} ${esc(t.model_raw || "")}</label>`).join("") || '<div class="muted">No targets yet.</div>'}
      <p><button class="primary">Plan</button></p></form>
    <div id="plan" class="panel muted">Select lots and press Plan. During the sale, mark each lot Won/Lost to re-plan the rest.</div></div>`;
  let results = {};
  const doPlan = async () => {
    const f = $("#mf"); const d = formData(f);
    const ids = $$("input[name=t]:checked", f).map((c) => Number(c.value));
    if (!ids.length) { toast("Pick at least one lot"); return; }
    const r = await api("/multilot/plan", { body: { target_ids: ids, budget_all_in: d.budget_all_in, max_wins: d.max_wins, deescalate_pct: d.deescalate_pct, results } });
    $("#plan").classList.remove("muted");
    $("#plan").innerHTML = `<h2>Plan · remaining budget ${money(r.remaining_all_in)} · wins ${r.wins}/${r.max_wins}</h2><table>
      <tr><th>Lot</th><th>Unit</th><th>Action</th><th class="num">Max hammer</th><th class="num">Max all-in</th><th>Why</th><th>Live result</th></tr>
      ${r.plan.map((p) => `<tr><td>${esc(p.lot_no)}</td><td>${esc(p.label || "")}</td>
        <td><span class="chip ${p.action === "BID" ? "good" : p.action === "SKIP" ? "medium" : "info"}">${p.action}</span></td>
        <td class="num">${money(p.max_hammer)}</td><td class="num">${money(p.max_all_in ?? p.all_in)}</td>
        <td class="small">${esc(p.reason || (p.deescalation ? "ceiling de-escalated " + Math.round(p.deescalation * 100) + "%" : ""))}</td>
        <td>${p.action === "BID" ? `<div class="row"><input type="number" placeholder="hammer" style="width:100px" data-h="${p.id}"><button type="button" data-won="${p.id}">Won</button><button type="button" data-lost="${p.id}">Lost</button></div>` : p.action === "WON" || p.action === "LOST" ? `<button type="button" data-undo="${p.id}">undo</button>` : ""}</td></tr>`).join("")}</table>`;
    $$("[data-won]").forEach((b) => b.onclick = () => { const h = $(`[data-h="${b.dataset.won}"]`).value; if (!h) { toast("Enter the hammer price"); return; } results[b.dataset.won] = { won: true, hammer: Number(h) }; doPlan(); });
    $$("[data-lost]").forEach((b) => b.onclick = () => { results[b.dataset.lost] = { won: false }; doPlan(); });
    $$("[data-undo]").forEach((b) => b.onclick = () => { delete results[b.dataset.undo]; doPlan(); });
  };
  $("#mf").onsubmit = async (e) => { e.preventDefault(); results = {}; try { await doPlan(); } catch (err) { toast(err.message); } };
}

// ------------------------------------------------------------------ multi-file uploader (shared)
const UPLOAD_EXT = /\.(pdf|csv)$/i;
const fmtSize = (b) => b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
// "2026101_catalog.pdf" / "T&M 2026055 results.pdf" -> "2026101" / "2026055"
const saleIdFromName = (name) => { const m = name.match(/(?:^|[^0-9])(\d{6,8})(?![0-9])/); return m ? m[1] : null; };

function uploaderHtml(label) {
  return `<div class="drop" tabindex="0" role="button" aria-label="Choose ${label} to upload">
      <input type="file" class="files" accept=".pdf,.csv" multiple hidden>
      <div><b>Drop ${label} here</b> or <a href="#" class="pick">choose files</a></div>
      <div class="small muted">Select several files at once — they upload one at a time.</div>
    </div>
    <ul class="filelist small"></ul>`;
}

/* Wire a form containing uploaderHtml().
   opts.prepare(file, form, many) -> {fields} sent with that file, or a string error for that file.
   opts.done(result) -> one-line summary. opts.after() runs once when the batch finishes. */
function bindUploader(form, path, opts) {
  const input = $(".files", form), list = $(".filelist", form), drop = $(".drop", form), btn = $("button.primary", form);
  btn.dataset.label = btn.textContent;
  let files = [];
  const render = () => {
    list.innerHTML = files.map((f, i) => `<li><span class="st">${f.status || "•"}</span> ${esc(f.file.name)} <span class="muted">${fmtSize(f.file.size)}</span>
      ${f.status ? "" : `<a href="#" data-rm="${i}" title="Remove">✕</a>`}${f.msg ? `<div class="msg ${f.ok === false ? "err" : ""}">${esc(f.msg)}</div>` : ""}</li>`).join("");
    $$("[data-rm]", list).forEach((a) => a.onclick = (e) => { e.preventDefault(); files.splice(+a.dataset.rm, 1); render(); });
    const pending = files.filter((f) => !f.status).length;
    btn.textContent = btn.dataset.label + (pending > 1 ? ` (${pending} files)` : "");
    if (opts.onCount) opts.onCount(pending, form);
  };
  const add = (fl) => {
    if (files.length && files.every((f) => f.status)) files = [];  // a finished batch is replaced by the next one
    for (const f of fl) {
      if (!UPLOAD_EXT.test(f.name)) { toast(`Skipped ${f.name} — only PDF or CSV`); continue; }
      if (!files.some((x) => x.file.name === f.name && x.file.size === f.size)) files.push({ file: f });
    }
    render();
  };
  $(".pick", form).onclick = (e) => { e.preventDefault(); e.stopPropagation(); input.click(); };
  drop.onclick = () => input.click();
  drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
  input.onclick = (e) => e.stopPropagation();
  input.onchange = () => { add(input.files); input.value = ""; };
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => add(e.dataTransfer.files));
  form.onsubmit = async (e) => {
    e.preventDefault();
    const todo = files.filter((f) => !f.status);
    if (!todo.length) { toast("Choose at least one PDF or CSV file"); return; }
    btn.disabled = true;
    let ok = 0;
    for (const f of todo) {
      const prep = opts.prepare(f.file, form, todo.length > 1);
      if (typeof prep === "string") { Object.assign(f, { status: "✗", ok: false, msg: prep }); render(); continue; }
      Object.assign(f, { status: "⏳", msg: "uploading…" });
      render();
      const fd = new FormData();
      fd.append("file", f.file);
      for (const [k, v] of Object.entries(prep)) if (v !== null && v !== undefined && v !== "") fd.append(k, v);
      try { const r = await api(path, { body: fd }); Object.assign(f, { status: "✓", ok: true, msg: opts.done(r) }); ok++; }
      catch (err) { Object.assign(f, { status: "✗", ok: false, msg: err.message }); }
      render();
    }
    btn.disabled = false;
    toast(`${ok} of ${todo.length} file(s) ingested`);
    META = null;
    if (opts.after) opts.after();
  };
  render();
}

// One typed sale ID applies to every file; if blank, read it from each filename.
function saleIdFor(file, form, many) {
  const typed = (form.elements.sale_id.value || "").trim();
  if (typed) return { sale_id: typed };
  const guess = saleIdFromName(file.name);
  if (guess) return { sale_id: guess };
  return many ? "No sale ID in this filename — type a Sale ID above" : "Enter the Sale ID";
}

function catalogPrepare(file, form, many) {
  const sid = saleIdFor(file, form, many);
  if (typeof sid === "string") return sid;
  return { ...sid, sale_date: form.elements.sale_date.value, location: form.elements.location.value };
}
const catalogDone = (r) => `Sale ${r.sale_id}: ${r.created} new / ${r.updated} updated target lots`;

// ------------------------------------------------------------------ ingest
function ingestLogRows(log) {
  return log.map((l) => `<tr><td class="small">${esc(l.created_at.replace("T", " ").slice(0, 16))}</td><td>${esc(l.kind)}${l.shared ? ' <span class="chip">shared</span>' : ""}</td><td class="small">${esc(l.filename)}</td><td>${esc(l.sale_id || "")}</td><td class="num">${l.rows}</td><td><span class="chip ${l.status === "ok" ? "good" : "medium"}">${esc(l.status)}</span></td><td class="small">${esc(l.message || "")}</td></tr>`).join("");
}

async function viewIngest(app) {
  const log = await api("/ingest/log");
  app.innerHTML = `<div class="grid g3">
    <form class="panel" id="fr"><h2>Auction result sheets</h2>
      <p class="small muted">T&amp;M "PRICE LISTING BY TYPE/LOT#" PDFs (coordinate-bucketed parse) or comps CSVs. Each sheet's header gives its sale; re-ingesting a sale replaces its rows.</p>
      ${uploaderHtml("result sheets")}
      <div id="ovr"><label>Sale ID override (single file, only if the header is missing)</label><input name="sale_id"></div>
      <label>Visibility</label><select name="visibility"><option value="shared">Shared comp pool</option><option value="private">Private to my workspace</option></select>
      <p><button class="primary">Ingest results</button></p>
      <details><summary>CSV columns</summary><div class="small mono">sale_id, sale_date, location, lot_no, lot_type, year, make, class, model, engine, desc, trans, miles, hours, hours_metric, door_type, branded, non_op, sold_price</div></details></form>
    <form class="panel" id="fc"><h2>Catalogs (upcoming sale)</h2>
      <p class="small muted">Catalog PDFs (heuristic extraction — review each lot) or CSVs. Creates/updates target lots.</p>
      ${uploaderHtml("catalogs")}
      <label>Sale ID <span class="muted">(blank = read from each filename)</span></label><input name="sale_id" placeholder="e.g. 2026101">
      <div class="form-grid"><div><label>Sale date</label><input type="date" name="sale_date"></div><div><label>Location</label><input name="location"></div></div>
      <p><button class="primary">Ingest catalogs</button></p>
      <details><summary>CSV columns</summary><div class="small mono">lot_no, year, make, model, class, engine, desc, trans, miles, hours, hours_metric, vin, gvwr, front_axle, rear_axle, body_length, apu, sleeper_size, liftgate, door, engine_brake, notes</div></details></form>
    <form class="panel" id="fm"><h2>Mileage / reefer-hours sheets</h2>
      <p class="small muted">Joins on lot #. Flags ODOM vs ECM mismatch; records whether hours are Total, Engine or Electric (not comparable).</p>
      ${uploaderHtml("sheets")}
      <label>Sale ID <span class="muted">(blank = read from each filename)</span></label><input name="sale_id" placeholder="e.g. 2026101">
      <p><button class="primary">Ingest sheets</button></p>
      <details><summary>CSV columns</summary><div class="small mono">lot, odometer (thousands ok), ecm_miles, total_hours, engine_hours, electric_hours</div></details></form>
  </div>
  <div class="panel"><h2>Ingest log</h2><div class="table-wrap"><table id="logTable"><tr><th>When</th><th>Kind</th><th>File</th><th>Sale</th><th class="num">Rows</th><th>Status</th><th>Message</th></tr>
    ${ingestLogRows(log)}
  </table></div></div>`;
  const refreshLog = async () => {
    const l = await api("/ingest/log");
    const t = $("#logTable");
    t.innerHTML = t.rows[0].outerHTML + ingestLogRows(l);
  };
  bindUploader($("#fr"), "/ingest/results", {
    // an override would merge every file into one sale, so it only applies to a single file
    prepare: (file, form, many) => ({ visibility: form.elements.visibility.value, sale_id: many ? "" : form.elements.sale_id.value.trim() }),
    done: (r) => r.sale_id
      ? `Sale ${r.sale_id} ${r.location || ""}: ${r.rows} lots (replaced ${r.replaced})${r.skipped_lines && r.skipped_lines.length ? `, ${r.skipped_lines.length} unparsed lines — see log` : ""}`
      : `${r.rows} comps across ${r.sales} sales`,
    onCount: (n, form) => { $("#ovr", form).hidden = n > 1; },
    after: refreshLog,
  });
  bindUploader($("#fc"), "/ingest/catalog", { prepare: catalogPrepare, done: catalogDone, after: refreshLog });
  bindUploader($("#fm"), "/ingest/mileage", {
    prepare: (file, form, many) => saleIdFor(file, form, many),
    done: (r) => `Sale ${r.sale_id}: ${r.matched}/${r.rows} lots matched, ${r.tamper_flags} tamper flags`,
    after: refreshLog,
  });
}

// ------------------------------------------------------------------ comps
async function viewComps(app, _arg, params) {
  const m = await meta();
  const q = Object.fromEntries(params.entries());
  const rows = await api("/comps?" + new URLSearchParams(q).toString());
  app.innerHTML = `<form class="panel row" id="cf">
      <select name="model_group" style="width:auto">${opt(m.model_groups, q.model_group)}</select>
      <select name="body_class" style="width:auto">${opt(m.body_classes, q.body_class)}</select>
      <input name="year_min" type="number" placeholder="year ≥" style="width:100px" value="${esc(q.year_min || "")}">
      <input name="year_max" type="number" placeholder="year ≤" style="width:100px" value="${esc(q.year_max || "")}">
      <input name="sale_id" placeholder="sale id" style="width:120px" value="${esc(q.sale_id || "")}">
      <input name="q" placeholder="search model/engine/lot" style="width:200px" value="${esc(q.q || "")}">
      <button class="primary">Filter</button><span class="spacer"></span><span class="muted">${rows.length} rows · greyed = excluded from stats (non-op/branded)</span></form>
    <div class="panel"><div class="table-wrap"><table>
      <tr><th>Sale</th><th>Date</th><th>Loc</th><th>Lot</th><th>T</th><th>Yr</th><th>Make</th><th>Model raw → group</th><th>Class</th><th>Engine → family</th><th>Trans</th><th class="num">Miles</th><th class="num">Hrs</th><th>Flags</th><th class="num">Hammer</th></tr>
      ${rows.map((c) => `<tr class="${c.non_operable || c.branded_title ? "excluded" : ""}"><td>${esc(c.sale_id)}</td><td class="small">${esc(c.sale_date || "")}</td><td class="small">${esc(c.location || "")}</td><td>${esc(c.lot_no)}</td><td>${esc(c.lot_type || "")}</td><td>${c.year ?? ""}</td><td>${esc(c.make || "")}</td>
        <td>${esc(c.model_raw || "")}<div class="small muted">${esc(c.model_group || "")}</div></td><td class="small">${esc(c.body_class || "")}</td>
        <td class="small">${esc(c.engine_raw || "")}<div class="muted">${esc(c.engine_family || "")}</div></td><td class="small">${esc(c.transmission || "")}</td>
        <td class="num">${num(c.miles)}</td><td class="num">${num(c.hours)}</td>
        <td>${c.non_operable ? '<span class="chip high">non-op</span>' : ""}${c.branded_title ? '<span class="chip high">branded</span>' : ""}${c.tamper_flag ? '<span class="chip medium">ODOM≠ECM</span>' : ""}${c.shared ? "" : '<span class="chip">private</span>'}</td>
        <td class="num"><b>${money(c.sold_price)}</b></td></tr>`).join("")}
    </table></div></div>`;
  $("#cf").onsubmit = (e) => { e.preventDefault(); const d = formData(e.target); const p = new URLSearchParams(); for (const [k, v] of Object.entries(d)) if (v !== null) p.set(k, v); location.hash = "#/comps?" + p.toString(); };
}

// ------------------------------------------------------------------ FMV
async function viewFmv(app) {
  const m = await meta();
  const rows = await api("/fmv");
  app.innerHTML = `<div class="grid side">
    <div><form class="panel" id="ff"><h2>Add retail FMV comp</h2>
      <p class="small muted">Open-market retail only (TruckPaper, Commercial Truck Trader, Comvoy, dealer networks). Auction/wholesale sources are rejected for retail tiers. Asking prices are discounted to transaction level automatically.</p>
      <label>Model group</label><select name="model_group" required>${opt(m.model_groups)}</select>
      <div class="form-grid"><div><label>Year from</label><input type="number" name="year_lo" required></div><div><label>Year to</label><input type="number" name="year_hi" required></div>
      <div><label>Miles from</label><input type="number" name="miles_lo"></div><div><label>Miles to</label><input type="number" name="miles_hi"></div>
      <div><label>Tier</label><select name="tier">${opt(m.fmv_tiers, "private", false)}</select></div><div><label>Price $</label><input type="number" name="price" required></div>
      <div><label>As of</label><input type="date" name="as_of_date"></div><div><label>Region</label><input name="region" value="DFW / TX"></div></div>
      <label><input type="checkbox" name="is_asking" checked> Asking price (discount to transaction)</label>
      <label>Source</label><input name="source" placeholder="TruckPaper"><label>URL</label><input name="url">
      <p><button class="primary">Add</button></p></form>
      <form class="panel" id="fi"><h2>Import CSV</h2><input type="file" name="file" accept=".csv" required>
        <div class="small mono muted">model_group, year_lo, year_hi, miles_lo, miles_hi, tier, price, is_asking, as_of_date, source, url, region, notes</div>
        <p><button>Import</button></p></form></div>
    <div class="panel"><h2>Retail FMV comps</h2><div class="table-wrap"><table>
      <tr><th>Group</th><th>Years</th><th>Miles</th><th>Tier</th><th class="num">Price</th><th>Asking</th><th>As of</th><th>Source</th><th></th></tr>
      ${rows.map((r) => `<tr><td>${esc(r.model_group)}</td><td>${r.year_lo}–${r.year_hi}</td><td class="small">${r.miles_lo != null ? num(r.miles_lo) : "…"}–${r.miles_hi != null ? num(r.miles_hi) : "…"}</td><td>${esc(r.tier)}</td><td class="num">${money(r.price)}</td><td>${r.is_asking ? "yes" : "no"}</td>
        <td>${esc(r.as_of_date)} ${r.stale ? '<span class="chip medium">stale ' + r.age_days + "d</span>" : ""}</td><td class="small">${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.source || "link")}</a>` : esc(r.source || "")}</td>
        <td><button class="danger" data-del="${r.id}">✕</button></td></tr>`).join("") || '<tr><td colspan="9" class="muted">No retail comps yet.</td></tr>'}
    </table></div></div></div>`;
  $("#ff").onsubmit = async (e) => { e.preventDefault(); try { await api("/fmv", { body: formData(e.target) }); toast("Added"); router(); } catch (err) { toast(err.message); } };
  $("#fi").onsubmit = async (e) => { e.preventDefault(); try { const r = await api("/fmv/import", { body: new FormData(e.target) }); toast(`Imported ${r.imported}${r.errors.length ? ", " + r.errors.length + " errors" : ""}`); if (r.errors.length) alert(r.errors.join("\n")); router(); } catch (err) { toast(err.message); } };
  $$("[data-del]").forEach((b) => b.onclick = async () => { await api("/fmv/" + b.dataset.del, { method: "DELETE" }); router(); });
}

// ------------------------------------------------------------------ outcomes & backtest
async function viewOutcomes(app) {
  const [o, sales] = await Promise.all([api("/outcomes"), api("/sales")]);
  app.innerHTML = `<div class="grid g2">
    <div class="panel"><h2>Logged outcomes</h2>
      ${o.calibration ? `<div class="small">Calibration over ${o.calibration.n} lots: actual/predicted = <b>${o.calibration.mean_actual_over_predicted}</b>, MAPE ${o.calibration.mape}%</div>` : '<div class="small muted">Log outcomes on each target lot to calibrate the model.</div>'}
      ${o.avg_repair_on_wins != null ? `<div class="small">Avg repair cost on wins: <b>${money(o.avg_repair_on_wins)}</b> — compare with your blind-buy reserve in Settings.</div>` : ""}
      <div class="table-wrap"><table><tr><th>Sale</th><th>Lot</th><th>Bid</th><th>Won</th><th class="num">Predicted p50</th><th class="num">Hammer</th><th class="num">Repairs</th><th>Condition</th></tr>
      ${o.outcomes.map((x) => `<tr><td>${esc(x.sale_id || "")}</td><td>${esc(x.lot_no || "")}</td><td>${x.did_bid ? "✓" : ""}</td><td>${x.won ? "✓" : ""}</td><td class="num">${money(x.predicted_p50)}</td><td class="num">${money(x.final_hammer)}</td><td class="num">${money(x.repair_costs)}</td><td class="small">${esc(x.condition_on_arrival || "")}</td></tr>`).join("")}
      </table></div></div>
    <div class="panel"><h2>Backtest</h2><p class="small muted">Walk-forward: each comp is priced using only comps from earlier sale dates. "Model's ceiling vs actual hammer."</p>
      <div class="row"><select id="bs" style="width:auto"><option value="">Most recent 200 lots</option>${sales.map((s) => `<option value="${esc(s.sale_id)}">${esc(s.sale_id)} ${esc(s.sale_date || "")}</option>`).join("")}</select><button class="primary" id="bt">Run backtest</button></div>
      <div id="btOut"></div></div></div>`;
  $("#bt").onclick = async () => {
    $("#btOut").innerHTML = '<div class="muted">Running…</div>';
    const sid = $("#bs").value;
    const r = await api("/backtest" + (sid ? "?sale_id=" + encodeURIComponent(sid) : ""));
    if (!r.n) { $("#btOut").innerHTML = `<div class="note">${esc(r.message)}</div>`; return; }
    $("#btOut").innerHTML = `<div class="grid g3" style="margin-top:10px">
        <div><div class="kpi">${r.mape}%</div><div class="kpi-label">MAPE of fair value (N=${r.n})</div></div>
        <div><div class="kpi">${r.coverage_p25_p75}%</div><div class="kpi-label">Actual inside p25–p75 (ideal ≈50%)</div></div>
        <div><div class="kpi">${r.within_ceiling_pct}%</div><div class="kpi-label">Actual ≤ model ceiling</div></div></div>
      <div class="small">MAE ${money(r.mae)} · median abs err ${money(r.median_abs_err)} · would have won ${r.won_at_target_pct}% at target · by confidence: ${Object.entries(r.by_confidence).map(([k, v]) => `${k} ${v.mape}% (N=${v.n})`).join(", ")}</div>
      <div class="table-wrap" style="max-height:420px;overflow:auto;margin-top:8px"><table><tr><th>Sale</th><th>Lot</th><th>Yr</th><th>Group</th><th class="num">Miles</th><th class="num">Actual</th><th class="num">p50</th><th class="num">Ceiling</th><th class="num">Err</th><th>Conf</th></tr>
      ${r.rows.map((x) => `<tr><td>${esc(x.sale_id)}</td><td>${esc(x.lot_no)}</td><td>${x.year}</td><td class="small">${esc(x.model_group)}</td><td class="num">${num(x.miles)}</td><td class="num">${money(x.actual)}</td><td class="num">${money(x.p50)}</td><td class="num">${money(x.ceiling)}</td><td class="num">${money(x.err)}</td><td><span class="chip conf-${x.confidence}">${x.confidence}</span></td></tr>`).join("")}</table></div>`;
  };
}

// ------------------------------------------------------------------ settings
async function viewSettings(app) {
  const { settings: s } = await api("/settings");
  const f = (k, label, type = "number", step = "any") => `<div><label>${label}</label><input type="${type}" step="${step}" name="${k}" value="${esc(s[k] ?? "")}"></div>`;
  app.innerHTML = `<form class="panel" id="sf"><h1>Tenant settings</h1>
    <p class="small muted">Every value here is an explicit, visible assumption used by the recommender. Change them to match your operation.</p>
    <h3>Home &amp; transport</h3><div class="grid g4">${f("home_label", "Home label", "text")}${f("home_lat", "Home lat")}${f("home_lon", "Home lon")}
      <div><label>Default transport</label><select name="transport_mode">${opt(["driveaway", "haul"], s.transport_mode, false)}</select></div>
      ${f("driveaway_rate_per_mile", "Driveaway $/mile")}${f("haul_rate_per_mile", "Haul $/mile")}${f("road_factor", "Road-miles factor")}${f("transport_min_charge", "Min transport $")}</div>
    <h3>Costs &amp; recommender</h3><div class="grid g4">${f("default_buyers_premium_pct", "Default buyer's premium %")}${f("title_doc_fees", "Title/doc fees $")}
      ${f("aggressiveness", "Aggressiveness 0–1")}${f("walk_buffer_pct", "Walk = fair + x% (blank = p75)")}${f("budget_cap_all_in", "Per-lot budget cap (all-in $)")}${f("min_cohort_n", "Min cohort N")}</div>
    <h3>Retail-minus fallback &amp; FMV</h3><div class="grid g4">${f("auction_discount_low", "Auction discount low (p25)")}${f("auction_discount_factor", "Auction discount (p50)")}${f("auction_discount_high", "Auction discount high (p75)")}
      <div><label>FMV tier for equity</label><select name="fmv_tier">${opt(["dealer", "private", "wholesale"], s.fmv_tier, false)}</select></div>
      ${f("fmv_stale_days", "FMV stale after (days)")}${f("asking_discount_pct", "Asking → transaction discount %")}</div>
    <h3>Blind-buy reserve (itemized)</h3><div class="grid g4">${Object.entries(s.risk_reserves).map(([k, v]) => `<div><label>${esc(k)}</label><input type="number" data-rr="${esc(k)}" value="${esc(v)}"></div>`).join("")}</div>
    <h3>Risk</h3><div class="grid g4">${f("reefer_max_hours", "Reefer max hours")}${f("odom_ecm_tolerance_pct", "ODOM/ECM tolerance %")}${f("warranty_max_age_years", "Warranty max age (yrs)")}${f("warranty_class2_7_mile_cap", "Warranty cap Class 2–7 (mi)")}${f("warranty_class8_mile_cap", "Warranty cap Class 8 (mi)")}</div>
    <h3>Multi-lot &amp; deposits</h3><div class="grid g4">${f("multi_lot_max_wins", "Max wins per sale")}${f("multi_lot_deescalate_pct", "De-escalate after win %")}${f("default_deposit_amount", "Default deposit $")}${f("default_wire_deadline", "Default wire deadline", "text")}${f("default_payment_window_hours", "Payment window (h)")}</div>
    <h3>Comp store</h3><label><input type="checkbox" name="include_private_comps" ${s.include_private_comps ? "checked" : ""}> Include my private comps in pricing</label>
      <label><input type="checkbox" name="contribute_to_shared" ${s.contribute_to_shared ? "checked" : ""}> Allow uploading result sheets to the shared pool</label>
    <p>${ME && !ME.is_admin ? '<span class="muted small">Only workspace admins can change settings.</span>' : '<button class="primary">Save settings</button>'}</p></form>
    <form class="panel" id="pwf" style="max-width:420px"><h2>Change my password</h2>
      <label>Current password</label><input type="password" name="current_password" required autocomplete="current-password">
      <label>New password (8+ characters)</label><input type="password" name="new_password" required minlength="8" autocomplete="new-password">
      <p><button>Change password</button></p></form>`;
  $("#pwf").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/auth/password", { body: formData(e.target) }); e.target.reset(); toast("Password changed"); } catch (err) { toast(err.message); }
  };
  $("#sf").onsubmit = async (e) => {
    e.preventDefault();
    const d = formData(e.target);
    d.risk_reserves = {}; $$("[data-rr]", e.target).forEach((i) => d.risk_reserves[i.dataset.rr] = Number(i.value || 0));
    try { await api("/settings", { method: "PUT", body: d }); toast("Settings saved"); } catch (err) { toast(err.message); }
  };
}

// ------------------------------------------------------------------ users
async function viewUsers(app) {
  const d = await api("/users");
  const wsOpts = (d.can_create_workspace ? '<option value="new">➕ New separate workspace (partner buys on their own)</option>' : "") +
    d.workspaces.map((w) => `<option value="${w.id === (ME && ME.tenant_id) ? "mine" : w.id}">${w.id === (ME && ME.tenant_id) ? "My workspace — " : ""}${esc(w.name)}${w.id === (ME && ME.tenant_id) ? " (shares my targets & bid sheets)" : ""}</option>`).join("");
  app.innerHTML = `<div class="grid side">
    <form class="panel" id="uf"><h2>Add a login</h2>
      <label>Email</label><input name="email" type="email" required autocomplete="off">
      <label>Temporary password (8+ characters)</label><input name="password" type="text" required minlength="8" autocomplete="off">
      <label>Role</label><select name="role"><option value="member">Member — can use everything except users &amp; settings</option><option value="admin">Admin — can also manage logins &amp; settings</option></select>
      <label>Workspace</label><select name="workspace" id="ws">${wsOpts.replace('value="mine"', 'value="mine" selected')}</select>
      <div id="wsName" hidden><label>New workspace name</label><input name="workspace_name" placeholder="Partner's company name"></div>
      <p><button class="primary">Create login</button></p>
      <p class="small muted">Share the email and temporary password with them privately; they can change it under Settings → Change my password.
      Everyone shares the sold-comp pool. Target lots, bid sheets, retail prices and outcomes are shared only within a workspace.</p></form>
    <div class="panel"><h2>Logins</h2><div class="table-wrap"><table>
      <tr><th>Email</th><th>Workspace</th><th>Role</th><th>Created</th><th></th></tr>
      ${d.users.map((u) => `<tr><td>${esc(u.email)}${u.id === d.me ? ' <span class="chip">you</span>' : ""}</td><td>${esc(u.tenant || "")}</td>
        <td><select data-role="${u.id}" style="width:auto" ${u.id === d.me ? "disabled" : ""}>${opt(["admin", "member"], u.role, false)}</select></td>
        <td class="small">${esc((u.created_at || "").slice(0, 10))}</td>
        <td class="row"><button type="button" data-reset="${u.id}" data-email="${esc(u.email)}">Reset password</button>${u.id === d.me ? "" : `<button type="button" class="danger" data-del="${u.id}" data-email="${esc(u.email)}">Remove</button>`}</td></tr>`).join("")}
    </table></div></div></div>`;
  $("#ws").onchange = (e) => { $("#wsName").hidden = e.target.value !== "new"; };
  $("#uf").onsubmit = async (e) => {
    e.preventDefault();
    try { const u = await api("/users", { body: formData(e.target) }); toast(`Created ${u.email}`); router(); } catch (err) { toast(err.message); }
  };
  $$("[data-role]").forEach((sel) => sel.onchange = async () => {
    try { await api("/users/" + sel.dataset.role, { method: "PUT", body: { role: sel.value } }); toast("Role updated"); } catch (err) { toast(err.message); router(); }
  });
  $$("[data-reset]").forEach((b) => b.onclick = async () => {
    const pw = prompt(`New password for ${b.dataset.email} (8+ characters):`);
    if (!pw) return;
    try { await api("/users/" + b.dataset.reset, { method: "PUT", body: { password: pw } }); toast("Password reset — they've been signed out"); } catch (err) { toast(err.message); }
  });
  $$("[data-del]").forEach((b) => b.onclick = async () => {
    if (!confirm(`Remove the login ${b.dataset.email}? Their workspace data is kept.`)) return;
    try { await api("/users/" + b.dataset.del, { method: "DELETE" }); toast("Removed"); router(); } catch (err) { toast(err.message); }
  });
}
