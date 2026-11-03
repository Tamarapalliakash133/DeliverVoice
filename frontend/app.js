/* DeliverCall frontend — plain JS, no build step. */
const API_BASE = "https://delivervoice.onrender.com"; // set to e.g. "http://localhost:8000" if you serve this folder from a different server

// ---------- helpers ----------
const $ = (s, r = document) => r.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = (p, o) => fetch(API_BASE + "/api" + p, { headers: { "Content-Type": "application/json" }, ...o }).then(r => r.json());
const post = (p, b) => api(p, { method: "POST", body: JSON.stringify(b || {}) });

const META = {
  pending: ["Pending", "⏳"], calling: ["On call", "📞"], confirmed: ["Confirmed", "✅"],
  rescheduled: ["Rescheduled", "🔁"], cancelled: ["Cancelled", "✖️"], return_requested: ["Return requested", "↩️"],
  no_answer: ["No answer", "📵"], handoff: ["Human handoff", "🙋"],
};
const LANG = { "hi-IN": "Hindi", "en-IN": "English", "te-IN": "Telugu", "ta-IN": "Tamil", "kn-IN": "Kannada" };

const tag = s => `<span class="tag s-${esc(s)}">${esc(META[s]?.[0] || s)}</span>`;
const lang = l => LANG[l] || l;

// Polls fn every `ms`, passes the result to render. Returns a stop function.
function poll(fn, render, ms = 2000) {
  let on = true;
  const tick = async () => { try { const x = await fn(); if (on) render(x); } catch { /* keep last state */ } };
  tick();
  const id = setInterval(tick, ms);
  return () => { on = false; clearInterval(id); };
}

function chatHTML(t) {
  if (!t || !t.length) return `<div class="empty">No conversation yet</div>`;
  return t.map(m => `<div style="display:flex;flex-direction:column">
    <div class="who" style="align-self:${m.role === "agent" ? "flex-start" : "flex-end"}">${m.role === "agent" ? "🤖 DeliverCall" : "👤 Customer"}</div>
    <div class="msg ${esc(m.role)}">${esc(m.text)}</div></div>`).join("");
}
// Re-renders a chat box only when the transcript changed (keeps scroll position stable).
function setChat(el, t) {
  const sig = JSON.stringify(t || []);
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.innerHTML = chatHTML(t);
  el.scrollTo(0, 1e6);
}

// ---------- app shell ----------
const view = $("#view");
let cfg = {};
let stops = [];
let tab = null;

function renderHeader() {
  const pill = $("#pill-mode");
  pill.classList.toggle("live", !!cfg.live_calls);
  $("#pill-mode-text").textContent = cfg.live_calls ? "Live calls" : "Calls not connected";
  $("#pill-llm").textContent = cfg.llm ? "LLM on" : "Rules mode";
}

function switchTab(t) {
  if (t === tab) return;
  tab = t;
  stops.forEach(s => s()); stops = [];
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("on", b.dataset.tab === t));
  t === "agent" ? mountAgent() : mountDash();
}

// ---------- Delivery agent tab ----------
function mountAgent() {
  view.innerHTML = `<main>
    <div class="card">
      <div class="row">
        <div class="grow"><h2>Today's deliveries</h2><div class="muted">Route re-sorts automatically as customers confirm their time windows</div></div>
        <select id="aid"></select>
        <button class="btn go" id="toggle">▶ Start calling customers</button>
      </div>
      <div class="bar"><div id="pct" style="width:0%"></div></div>
      <div class="muted" id="progress" style="margin-top:6px"></div>
    </div>
    <div class="card row">
      <b>📱 Demo phone</b>
      <input type="text" id="ph" placeholder="+919876543210 — all calls ring this number"/>
      <button class="btn sm ghost" id="phsave">Save</button>
      <span class="muted" id="phnote">Empty = real customer numbers</span>
    </div>
    <div class="card" id="notice" style="display:none;border-color:#fcd34d;background:#fffbeb"></div>
    <div class="contents" id="route"></div>
  </main>`;

  let aid = 1, running = false, agentsSig = "";
  const sel = $("#aid"), toggle = $("#toggle"), ph = $("#ph");

  const savePhone = () => post("/demo-phone", { phone: ph.value });
  const updatePhoneNote = () => { $("#phnote").textContent = ph.value ? "Calls go to this number" : "Empty = real customer numbers"; };
  api("/demo-phone").then(d => { ph.value = d.phone || ""; updatePhoneNote(); }).catch(() => {});
  ph.addEventListener("input", updatePhoneNote);
  ph.addEventListener("blur", savePhone);
  $("#phsave").addEventListener("click", savePhone);

  sel.addEventListener("change", () => { aid = +sel.value; $("#route").innerHTML = ""; restartAgentPolls(); });
  toggle.addEventListener("click", async () => {
    const r = await post(`/agents/${aid}/${running ? "stop" : "start"}`);
    if (r && r.error) showNotice(r.error);
  });

  $("#route").addEventListener("click", e => {
    const b = e.target.closest("[data-call]");
    if (b) post(`/orders/${b.dataset.call}/call`);
  });

  function showNotice(msg, title) {
    const n = $("#notice");
    n.style.display = msg ? "" : "none";
    n.innerHTML = msg ? `<b>⚠️ ${esc(title || "Real calls aren't set up yet.")}</b><div class="muted" style="margin-top:4px">${esc(msg)}</div>` : "";
  }
  stops.push(poll(() => Promise.resolve(cfg), c => showNotice(c.live_calls || !c.hasOwnProperty("live_calls") ? "" : "Add the Vobiz + Gnani keys and PUBLIC_BASE_URL in backend/.env, then restart the server."), 3000));

  function renderAgents(agents) {
    const sig = JSON.stringify(agents);
    if (sig === agentsSig) return;
    agentsSig = sig;
    sel.innerHTML = agents.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join("");
    sel.value = aid;
  }

  function renderStatus(st) {
    running = !!st?.running;
    if (st && st.error) showNotice(st.error, "Vobiz could not place the call");
    toggle.className = "btn " + (running ? "stop" : "go");
    toggle.textContent = running ? "■ Stop calling" : "▶ Start calling customers";
  }

  function renderRoute(route) {
    const done = route.filter(o => !["pending", "calling", "no_answer"].includes(o.status)).length;
    const pct = route.length ? Math.round(done / route.length * 100) : 0;
    $("#pct").style.width = pct + "%";
    $("#progress").textContent = `${done} of ${route.length} customers reached · ${pct}%`;
    $("#route").innerHTML = route.map(o => `<div class="card stop-c${o.skip ? " skip" : ""}">
      <div class="rail"><div class="pos">${esc(o.route_pos || "–")}</div></div>
      <div class="avatar">${esc(o.customer.split(" ").map(w => w[0]).join(""))}</div>
      <div class="grow">
        <div class="row">${o.status === "calling" ? `<span class="pulse"></span>` : ""}<b>${esc(o.customer)}</b>${tag(o.status)}${o.slot ? `<span class="tag">🕒 ${esc(o.slot)}</span>` : ""}<span class="tag">${esc(lang(o.lang))}</span></div>
        <div style="margin:4px 0">${esc(o.address)}</div>
        <div class="muted">📍 ${esc(o.landmark)} · 📦 ${esc(o.product)} · ₹${esc(o.amount)}${o.addr_note ? ` · 📝 ${esc(o.addr_note)}` : ""}${o.reason ? ` · Reason: ${esc(o.reason)}` : ""}</div>
      </div>
      ${o.status !== "calling" ? `<button class="btn sm ghost" style="align-self:center" data-call="${o.id}">Call</button>` : ""}
    </div>`).join("");
  }

  let aidStops = [];
  function restartAgentPolls() {
    aidStops.forEach(s => s());
    aidStops = [
      poll(() => api(`/agents/${aid}/route`), renderRoute),
      poll(() => api(`/agents/${aid}/status`), renderStatus),
    ];
  }
  stops.push(poll(() => api("/agents"), renderAgents, 10000));
  restartAgentPolls();
  stops.push(() => aidStops.forEach(s => s()));
}

// ---------- Order updates tab ----------
function mountDash() {
  view.innerHTML = `<main>
    <div class="row">
      <div class="grow"><h2>Order updates</h2><div class="muted">Live outcomes from every call · click a row for the transcript</div></div>
      <button class="btn sm ghost" id="reset">Reset demo data</button>
    </div>
    <div class="stats" id="stats"></div>
    <div class="card" style="overflow-x:auto">
      <table>
        <thead><tr><th>Customer</th><th>Product</th><th>Language</th><th>Outcome</th><th>Slot</th><th>Reason / note</th><th>Tries</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <div class="contents" id="modalslot"></div>
  </main>`;

  let orders = [], selId = null, modalSig = null;

  $("#reset").addEventListener("click", () => post("/reset"));
  $("#rows").addEventListener("click", e => {
    const tr = e.target.closest("tr[data-id]");
    if (tr) { selId = +tr.dataset.id; renderModal(); }
  });

  function renderStats(s) {
    $("#stats").innerHTML = Object.entries(META).map(([k, [l, ic]]) =>
      `<div class="stat"><div class="ic">${ic}</div><b>${esc(s?.[k] ?? 0)}</b><span>${esc(l)}</span></div>`).join("");
  }

  function renderOrders(list) {
    orders = list || [];
    $("#rows").innerHTML = orders.map(o => `<tr class="click" data-id="${o.id}">
      <td><b>${esc(o.customer)}</b></td><td>${esc(o.product)}</td><td>${esc(lang(o.lang))}</td><td>${tag(o.status)}</td>
      <td>${esc(o.slot || "–")}</td><td>${esc(o.reason || o.addr_note || "–")}</td><td>${esc(o.attempts)}${o.sms_sent ? " +SMS" : ""}</td></tr>`).join("");
    renderModal();
  }

  function renderModal() {
    const slot = $("#modalslot");
    const open = orders.find(o => o.id === selId);
    if (!open) { slot.innerHTML = ""; modalSig = null; return; }
    const sig = JSON.stringify(open);
    if (sig === modalSig) return;
    modalSig = sig;
    slot.innerHTML = `<div class="modal" id="modal"><div class="card">
      <div class="row"><h2 class="grow">${esc(open.customer)}</h2>${tag(open.status)}</div>
      <div class="muted">${esc(open.address)}</div>
      <div class="chat" id="modalchat"></div>
      <button class="btn sm" id="modalclose">Close</button>
    </div></div>`;
    const close = () => { selId = null; renderModal(); };
    $("#modal").addEventListener("click", close);
    $("#modal .card").addEventListener("click", e => e.stopPropagation());
    $("#modalclose").addEventListener("click", close);
    setChat($("#modalchat"), open.transcript);
  }

  stops.push(poll(() => api("/stats"), renderStats));
  stops.push(poll(() => api("/orders"), renderOrders));
}

// ---------- boot ----------
document.querySelectorAll("nav button").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.tab)));
poll(() => api("/config"), c => { cfg = c || {}; renderHeader(); }, 15000);
switchTab("agent");
