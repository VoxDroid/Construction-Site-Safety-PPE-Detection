// PPE Guard dashboard: no build step, no dependencies.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const ITEM_LABEL = { helmet: "Helmet", vest: "Vest", mask: "Mask" };
const fmtTime = (t) => new Date(t * 1000).toLocaleString();
const fmtClock = (t) => new Date(t * 1000).toLocaleTimeString();
const fmtDur = (s) => (s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${(s / 3600).toFixed(1)} h`);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = Array.isArray(d) ? d.map((e) => `${e.loc.slice(1).join(".")}: ${e.msg}`).join("; ") : d || res.statusText;
    throw new Error(msg);
  }
  return data;
}

let toastTimer;
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (error ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3500);
}

// ---------------------------------------------------------------- stats

async function refreshStats() {
  try {
    const s = await api("/api/stats");
    $("#s-ongoing").textContent = s.ongoing;
    $("#s-ongoing").parentElement.classList.toggle("active", s.ongoing > 0);
    $("#s-today").textContent = s.today;
    $("#s-workers").textContent = s.workers_in_view;
    $("#s-cams").textContent = `${s.cameras_running}/${s.cameras_total}`;
    $("#s-unack").textContent = s.unacknowledged;
  } catch { /* server restarting */ }
}

// ---------------------------------------------------------------- live view

const cams = new Map(); // id -> camera info

function workerChip(w) {
  const items = Object.entries(w.items)
    .filter(([k]) => required.includes(k))
    .map(([k, v]) => `<span class="pill ${v}" title="${ITEM_LABEL[k]}: ${v}">${ITEM_LABEL[k]} ${v === "worn" ? "✓" : v === "missing" ? "✗" : "?"}</span>`)
    .join("");
  const id = (w.synthetic ? "?" : "#") + esc(w.key.slice(1));
  return `<span class="worker"><b class="pill ${w.state}">${id}</b>${items}</span>`;
}

function camCard(c) {
  const running = c.status === "running" || c.status === "starting" || c.status === "reconnecting";
  const el = document.createElement("article");
  el.className = "panel cam";
  el.dataset.id = c.id;
  el.innerHTML = `
    <div class="cam-head">
      <h3>${esc(c.name)}</h3>
      <span class="pill ${esc(c.status)}">${esc(c.status)}</span>
      <span class="fps"></span>
      <button class="btn" data-act="${running ? "stop" : "start"}" ${c.enabled ? "" : "disabled"}>${running ? "Stop" : "Start"}</button>
    </div>
    <div class="cam-view">${running
      ? `<img alt="Live view of ${esc(c.name)}" src="/api/cameras/${encodeURIComponent(c.id)}/stream?t=${Date.now()}">`
      : `<div class="placeholder">${c.error ? `<strong>${esc(c.error)}</strong><br>` : ""}${c.enabled ? "Camera stopped" : "Camera disabled in settings"}<br><small>${/^\d+$/.test(c.source) ? `Webcam ${esc(c.source)}` : esc(c.source)}</small></div>`}
    </div>
    <div class="cam-workers"><span class="muted">No workers in view</span></div>`;
  el.querySelector("button").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      await api(`/api/cameras/${encodeURIComponent(c.id)}/${e.target.dataset.act}`, { method: "POST" });
    } catch (err) { toast(err.message, true); }
    await loadCameras();
  });
  return el;
}

function renderWorkers(id, workers, fps, violating) {
  const card = $(`.cam[data-id="${CSS.escape(id)}"]`);
  if (!card) return;
  card.classList.toggle("alarm", !!violating);
  $(".fps", card).textContent = fps ? `${fps} fps` : "";
  $(".cam-workers", card).innerHTML = workers.length
    ? workers.map(workerChip).join("")
    : '<span class="muted">No workers in view</span>';
}

async function loadCameras() {
  const list = await api("/api/cameras");
  cams.clear();
  const grid = $("#camera-grid");
  grid.replaceChildren();
  for (const c of list) {
    cams.set(c.id, c);
    grid.append(camCard(c));
    renderWorkers(c.id, c.workers || [], c.fps, false);
  }
  if (!list.length) grid.innerHTML = '<div class="panel empty">No cameras configured. Add one in <a href="#/settings">Settings</a>.</div>';
  fillCameraFilter(list);
}

function addFeed(msg) {
  const v = msg.violation;
  if (msg.event === "updated") return;
  const ol = $("#feed");
  if (ol.firstElementChild?.classList.contains("muted")) ol.replaceChildren();
  const li = document.createElement("li");
  li.className = msg.event;
  const what = v.missing.map((m) => ITEM_LABEL[m] || m).join(", ");
  li.innerHTML = msg.event === "opened"
    ? `<a href="#/violations/${esc(v.id)}"><strong>${esc(v.camera_name)}</strong>: worker missing ${esc(what)}</a><time>${fmtClock(v.started_at)}</time>`
    : `<a href="#/violations/${esc(v.id)}"><strong>${esc(v.camera_name)}</strong>: violation cleared after ${fmtDur(v.ended_at - v.started_at)}</a><time>${fmtClock(v.ended_at)}</time>`;
  ol.prepend(li);
  while (ol.children.length > 50) ol.lastElementChild.remove();
}

// ---------------------------------------------------------------- websocket

function connect() {
  const conn = $("#conn");
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { conn.textContent = "live"; conn.className = "conn on"; };
  ws.onclose = () => {
    conn.textContent = "disconnected"; conn.className = "conn off";
    setTimeout(connect, 2000);
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "frame") renderWorkers(msg.camera_id, msg.workers, msg.fps, msg.violating);
    else if (msg.type === "camera") { if (view === "live") loadCameras(); refreshStats(); }
    else if (msg.type === "violation") {
      addFeed(msg);
      refreshStats();
      if (view === "violations" && msg.event !== "updated") loadViolations();
    }
  };
}

// ---------------------------------------------------------------- violations

let page = 0;
const PAGE = 25;

function fillCameraFilter(list) {
  const sel = $('#filters [name="camera_id"]');
  const cur = sel.value;
  sel.innerHTML = '<option value="">All</option>' + list.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  sel.value = cur;
}

function filterParams() {
  const f = new FormData($("#filters"));
  const p = new URLSearchParams();
  for (const k of ["camera_id", "item", "ongoing", "acknowledged", "sort"]) if (f.get(k)) p.set(k, f.get(k));
  if (f.get("period")) p.set("since", String(Date.now() / 1000 - Number(f.get("period"))));
  return p;
}

const statusPill = (v) => v.ongoing ? '<span class="pill violation">ongoing</span>'
  : v.acknowledged ? '<span class="pill compliant">reviewed</span>' : '<span class="pill">ended</span>';

async function loadViolations() {
  const p = filterParams();
  $("#export").href = `/api/violations/export.csv?${p}`;
  p.set("limit", PAGE);
  p.set("offset", page * PAGE);
  const data = await api(`/api/violations?${p}`);
  $("#v-rows").innerHTML = data.items.map((v) => `
    <tr data-id="${esc(v.id)}">
      <td>${v.thumbnail ? `<img class="thumb" loading="lazy" alt="" src="/snapshots/${encodeURIComponent(v.thumbnail)}">` : '<div class="thumb"></div>'}</td>
      <td>${esc(v.camera_name)}</td>
      <td>${v.missing.map((m) => `<span class="pill missing">${esc(ITEM_LABEL[m] || m)}</span>`).join(" ")}</td>
      <td>${fmtTime(v.started_at)}</td>
      <td>${fmtDur(v.duration)}</td>
      <td>${statusPill(v)}</td>
    </tr>`).join("");
  $("#v-empty").hidden = data.items.length > 0;
  const pages = Math.max(1, Math.ceil(data.total / PAGE));
  $("#page-info").textContent = `Page ${page + 1} of ${pages} · ${data.total} total`;
  $("#prev").disabled = page === 0;
  $("#next").disabled = page + 1 >= pages;
}

async function openDetail(id) {
  let v;
  try { v = await api(`/api/violations/${encodeURIComponent(id)}`); }
  catch (e) { toast(e.message, true); return; }
  $("#d-title").textContent = `Violation on ${v.camera_name}`;
  $("#d-body").innerHTML = `
    <dl class="kv">
      <dt>Missing</dt><dd>${v.missing.map((m) => `<span class="pill missing">${esc(ITEM_LABEL[m] || m)}</span>`).join(" ")}</dd>
      <dt>Started</dt><dd>${fmtTime(v.started_at)}</dd>
      <dt>${v.ongoing ? "Last seen" : "Ended"}</dt><dd>${fmtTime(v.ended_at || v.last_seen)} (${fmtDur(v.duration)})</dd>
      <dt>Worker track</dt><dd>${esc(v.worker_key)}</dd>
      <dt>Status</dt><dd>${statusPill(v)}</dd>
      <dt>ID</dt><dd><code>${esc(v.id)}</code></dd>
    </dl>
    <div class="gallery">${v.snapshots.map((s) => `
      <figure style="margin:0"><a href="/snapshots/${encodeURIComponent(s.filename)}" target="_blank" rel="noopener">
        <img loading="lazy" alt="Snapshot" src="/snapshots/${encodeURIComponent(s.filename)}"></a>
        <figcaption>${fmtClock(s.taken_at)} · <a href="/snapshots/${encodeURIComponent(s.filename)}?download=true">download</a></figcaption></figure>`).join("") || '<p class="muted">No snapshots.</p>'}
    </div>
    <div class="row-actions">
      <input id="d-note" placeholder="Review note (optional)" value="${esc(v.note || "")}">
      <button class="btn primary" id="d-ack">${v.acknowledged ? "Mark unreviewed" : "Mark reviewed"}</button>
      <button class="btn danger" id="d-del">Delete</button>
    </div>`;
  $("#d-ack").onclick = async () => {
    try {
      await api(`/api/violations/${encodeURIComponent(id)}/ack`, { method: "POST", body: { acknowledged: !v.acknowledged, note: $("#d-note").value || null } });
      toast(v.acknowledged ? "Marked unreviewed" : "Marked reviewed");
      $("#detail").close();
      loadViolations(); refreshStats();
    } catch (e) { toast(e.message, true); }
  };
  $("#d-del").onclick = async () => {
    if (!confirm("Delete this violation and its snapshots?")) return;
    try {
      await api(`/api/violations/${encodeURIComponent(id)}`, { method: "DELETE" });
      toast("Deleted");
      $("#detail").close();
      loadViolations(); refreshStats();
    } catch (e) { toast(e.message, true); }
  };
  if (!$("#detail").open) $("#detail").showModal();
}

$("#filters").addEventListener("change", () => { page = 0; loadViolations(); });
$("#prev").onclick = () => { page = Math.max(0, page - 1); loadViolations(); };
$("#next").onclick = () => { page += 1; loadViolations(); };
$("#v-rows").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-id]");
  if (tr) location.hash = `#/violations/${tr.dataset.id}`;
});
$("#detail").addEventListener("close", () => {
  if (location.hash.startsWith("#/violations/")) history.replaceState(null, "", "#/violations");
});

// ---------------------------------------------------------------- settings

let settings = null;
let required = ["helmet", "vest"];
const get = (obj, path) => path.split(".").reduce((o, k) => o?.[k], obj);
const set = (obj, path, val) => {
  const ks = path.split(".");
  const last = ks.pop();
  ks.reduce((o, k) => (o[k] ??= {}), obj)[last] = val;
};

function camRow(c = { id: "", name: "", source: "", enabled: true, autostart: false, loop: true }, isNew = true) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><input name="id" value="${esc(c.id)}" ${isNew ? "" : "readonly"} placeholder="gate-north" required></td>
    <td><input name="name" value="${esc(c.name)}" placeholder="North gate" required></td>
    <td><input name="source" value="${esc(c.source)}" placeholder="0 or rtsp://…" required></td>
    <td><input type="checkbox" name="enabled" ${c.enabled ? "checked" : ""}></td>
    <td><input type="checkbox" name="autostart" ${c.autostart ? "checked" : ""}></td>
    <td><input type="checkbox" name="loop" ${c.loop ? "checked" : ""}></td>
    <td><button type="button" class="btn ghost danger" aria-label="Remove camera">✕</button></td>`;
  $("button", tr).onclick = () => tr.remove();
  return tr;
}

function fillSettings(s) {
  settings = s;
  required = Object.entries(s.compliance.required_ppe).filter(([, v]) => v).map(([k]) => k);
  for (const el of $$("#settings-form [name]")) {
    if (el.closest("#cam-rows")) continue;
    const v = get(s, el.name);
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v ?? "";
  }
  $("#cam-rows").replaceChildren(...s.cameras.map((c) => camRow(c, false)));
}

function readSettings() {
  const s = structuredClone(settings);
  for (const el of $$("#settings-form [name]")) {
    if (el.closest("#cam-rows")) continue;
    let v = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value.trim();
    if (v === "" && ["detection.device", "alerts.webhook_url", "alerts.public_url"].includes(el.name)) v = null;
    set(s, el.name, v);
  }
  s.cameras = $$("#cam-rows tr").map((tr) => ({
    id: $('[name="id"]', tr).value.trim(),
    name: $('[name="name"]', tr).value.trim(),
    source: $('[name="source"]', tr).value.trim(),
    enabled: $('[name="enabled"]', tr).checked,
    autostart: $('[name="autostart"]', tr).checked,
    loop: $('[name="loop"]', tr).checked,
  }));
  return s;
}

async function loadSettings() { fillSettings(await api("/api/settings")); }

$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    fillSettings(await api("/api/settings", { method: "PUT", body: readSettings() }));
    toast("Settings saved");
  } catch (err) { toast(err.message, true); }
});
$("#add-cam").onclick = () => $("#cam-rows").append(camRow());
$("#reset-settings").onclick = async () => {
  if (!confirm("Reset detection, compliance and alert settings to defaults? Cameras are kept.")) return;
  try { fillSettings(await api("/api/settings/reset", { method: "POST" })); toast("Defaults restored"); }
  catch (err) { toast(err.message, true); }
};
$("#test-alert").onclick = async () => {
  try {
    await api("/api/settings", { method: "PUT", body: readSettings() });
    const r = await api("/api/alerts/test", { method: "POST" });
    toast(`Webhook answered ${r.status}`);
  } catch (err) { toast(err.message, true); }
};

// ---------------------------------------------------------------- router

let view = "live";
async function route() {
  const [, name = "live", id] = location.hash.split("/");
  view = ["live", "violations", "settings"].includes(name) ? name : "live";
  for (const v of $$(".view")) v.hidden = v.id !== `view-${view}`;
  for (const a of $$(".tabs a")) a.classList.toggle("active", a.dataset.view === view);
  // Release MJPEG connections when the live view is hidden.
  if (view !== "live") $("#camera-grid").replaceChildren();
  try {
    if (view === "live") await loadCameras();
    if (view === "violations") { await loadViolations(); if (id) openDetail(decodeURIComponent(id)); }
    if (view === "settings") await loadSettings();
  } catch (e) { toast(e.message, true); }
}

window.addEventListener("hashchange", route);
(async () => {
  try { const s = await api("/api/settings"); required = Object.entries(s.compliance.required_ppe).filter(([, v]) => v).map(([k]) => k); } catch {}
  await route();
  if (view !== "live") api("/api/cameras").then(fillCameraFilter).catch(() => {});
  // Seed the live feed with the most recent violations.
  api("/api/violations?limit=8").then((d) => d.items.reverse().forEach((v) => {
    addFeed({ event: "opened", violation: v });
    if (!v.ongoing) addFeed({ event: "closed", violation: v });
  })).catch(() => {});
  refreshStats();
  setInterval(refreshStats, 10000);
  connect();
})();
