// Live: KPIs, every camera with its stream and workers, and the event feed.
import { $, $$, api, errorToast, fmtPct, fmtTime, html, icon, ITEM_LABEL, raw, render, violationTitle, workerName } from "../util.js";
import { bus } from "../store.js";

const STATUS = {
  running: ["good", "Live"], starting: ["info", "Starting"], reconnecting: ["warn", "Reconnecting"],
  stopped: ["", "Stopped"], ended: ["", "Ended"], error: ["bad", "Error"],
};

export function statusPill(status) {
  const [cls, label] = STATUS[status] || ["", status];
  return html`<span class="pill ${cls}"><span class="dot"></span>${label}</span>`;
}

export function workerChip(w) {
  const mark = { worn: ["ok", "✓"], missing: ["no", "✗"], unknown: ["unk", "?"] };
  const items = (w.required?.length ? w.required : []).map((i) => {
    const [cls, m] = mark[w.items[i]] || mark.unknown;
    return html`<span class="${cls}">${ITEM_LABEL[i]} ${m}</span>`;
  });
  if (w.intrusion) items.push(html`<span class="no">in ${w.intrusion}</span>`);
  if (w.state === "unmonitored") items.push(html`<span class="unk">no PPE needed here</span>`);
  return html`<span class="chip ${w.state}" title="${workerName(w.key)}"><span class="id">${w.synthetic ? "?" : "#"}${w.key.slice(1)}</span>${items}</span>`;
}

export function workersFoot(workers) {
  const real = workers.filter((w) => !w.synthetic || w.state === "violation");
  if (!real.length) return html`<span class="muted">No workers in view</span>`;
  return html`${real.slice(0, 12).map(workerChip)}${real.length > 12 ? html`<span class="muted">+${real.length - 12} more</span>` : ""}`;
}

function camCard(c) {
  const running = c.status === "running" || c.status === "reconnecting" || c.status === "starting";
  const violating = c.workers?.some((w) => w.state === "violation");
  const err = c.status === "error" ? html`<p class="muted small" style="margin-top:6px">${c.error}</p>` : "";
  return html`
    <article class="card cam" data-cam="${c.id}">
      <div class="cam-head">
        <h3><a href="#/cameras/${c.id}">${c.name}</a></h3>
        <span data-status>${statusPill(c.status)}</span>
        <span class="muted small" data-fps>${c.status === "running" ? `${c.fps} fps` : ""}</span>
        <a class="icon-btn sm" href="#/cameras/${c.id}/zones" title="Edit zones" aria-label="Edit zones for ${c.name}">${icon("zone", "ic-sm")}</a>
        ${running
          ? html`<button class="btn sm" data-act="stop">${icon("stop", "ic-sm")}Stop</button>`
          : html`<button class="btn sm primary" data-act="start" ${c.enabled ? "" : raw("disabled")}>${icon("play", "ic-sm")}Start</button>`}
      </div>
      <a class="cam-view ${violating ? "violating" : ""}" href="#/cameras/${c.id}" aria-label="Open ${c.name}">
        ${running
          ? html`<img src="/api/cameras/${c.id}/stream?ts=${Date.now()}" alt="Live view of ${c.name}">`
          : html`<div class="placeholder">${icon("live")}<div>${c.enabled ? "Camera stopped" : "Camera disabled"}</div>${err}</div>`}
      </a>
      <div class="cam-foot" data-workers>${running ? workersFoot(c.workers || []) : html`<span class="muted">${c.source.match(/^\d+$/) ? `Webcam ${c.source}` : c.source.split(/[\\/]/).pop()}</span>`}</div>
    </article>`;
}

function feedItem(v, event) {
  const closed = event === "closed";
  const what = v.kind === "zone" ? `entered ${v.zone_name || "a no-entry zone"}` : violationTitle(v).toLowerCase();
  const text = closed ? `cleared after ${Math.max(1, Math.round((v.ended_at || v.last_seen) - v.started_at))}s` : what;
  return html`<a class="feed-item ${closed ? "closed" : ""}" href="#/violations/${v.id}">
    <span class="bar"></span>
    <span><b>${v.camera_name}</b> · ${workerName(v.worker_key)} ${text}<br><span class="t">${fmtTime(closed ? v.ended_at || v.last_seen : v.started_at)}</span></span>
  </a>`;
}

function kpis(s) {
  return html`
    <div class="card kpi ${s.ongoing ? "alert" : ""}"><div class="label">Open violations</div><div class="value">${s.ongoing}</div><div class="sub">${s.violating_now} worker(s) right now</div></div>
    <div class="card kpi"><div class="label">Violations today</div><div class="value">${s.today}</div><div class="sub">${s.unacknowledged} awaiting review</div></div>
    <div class="card kpi"><div class="label">Compliance today</div><div class="value">${fmtPct(s.compliance_today)}</div><div class="sub">share of monitored worker-time</div></div>
    <div class="card kpi"><div class="label">Workers in view</div><div class="value">${s.workers_in_view}</div><div class="sub">across running cameras</div></div>
    <div class="card kpi"><div class="label">Cameras live</div><div class="value">${s.cameras_running}<span class="muted" style="font-size:18px">/${s.cameras_total}</span></div><div class="sub">${s.alert_error ? "alert delivery failing" : "streams running"}</div></div>`;
}

export async function mount(main) {
  render(main, html`
    <div class="page-head">
      <div><h1>Live</h1><p>Every camera, its workers and what they're missing, as it happens.</p></div>
      <div class="actions">
        <button class="btn" id="start-all">${icon("play", "ic-sm")}Start all</button>
        <a class="btn" href="#/settings/cameras">${icon("plus", "ic-sm")}Add camera</a>
      </div>
    </div>
    <div class="kpis" id="kpis">${raw('<div class="card kpi skeleton" style="height:96px"></div>'.repeat(5))}</div>
    <div class="live-layout">
      <div class="cams" id="cams"></div>
      <section class="card">
        <div class="card-head"><h2>Live events</h2><a class="small muted" href="#/violations">All violations</a></div>
        <div class="feed" id="feed"><div class="empty small">No events yet.</div></div>
      </section>
    </div>`);

  let cams = [];
  const drawCams = () => {
    const el = $("#cams", main);
    if (!cams.length) {
      render(el, html`<div class="card empty">${icon("live")}<h3>No cameras yet</h3><p>Add a webcam, video file or RTSP stream in <a href="#/settings/cameras">Settings → Cameras</a>.</p></div>`);
      return;
    }
    render(el, html`${cams.map(camCard)}`);
  };
  const loadCams = async () => { cams = await api("/api/cameras"); drawCams(); };
  const loadStats = async () => { render($("#kpis", main), kpis(await api("/api/stats"))); };

  $("#cams", main).addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const id = b.closest("[data-cam]").dataset.cam;
    b.disabled = true;
    try {
      await api(`/api/cameras/${id}/${b.dataset.act}`, { method: "POST" });
      await loadCams();
    } catch (err) { errorToast(err); b.disabled = false; }
  });
  $("#start-all", main).addEventListener("click", async () => {
    for (const c of cams.filter((x) => x.enabled && !["running", "starting"].includes(x.status))) {
      try { await api(`/api/cameras/${c.id}/start`, { method: "POST" }); } catch (err) { errorToast(err); }
    }
    loadCams();
  });

  const feed = $("#feed", main);
  const addFeed = (v, event) => {
    if (feed.querySelector(".empty")) feed.innerHTML = "";
    feed.insertAdjacentHTML("afterbegin", feedItem(v, event).__raw);
    while (feed.children.length > 60) feed.lastElementChild.remove();
  };
  try {
    const recent = await api("/api/violations?limit=12");
    if (recent.items.length) feed.innerHTML = "";
    recent.items.forEach((v) => feed.insertAdjacentHTML("beforeend", feedItem(v, v.ongoing ? "opened" : "closed").__raw));
  } catch (err) { errorToast(err); }

  await Promise.all([loadCams(), loadStats()]);

  const off = bus.on((m) => {
    if (m.type === "frame") {
      const card = $(`[data-cam="${CSS.escape(m.camera_id)}"]`, main);
      if (!card) return;
      $("[data-fps]", card).textContent = `${m.fps} fps`;
      render($("[data-workers]", card), workersFoot(m.workers));
      $(".cam-view", card).classList.toggle("violating", m.violating);
    } else if (m.type === "camera") {
      const c = cams.find((x) => x.id === m.camera_id);
      if (c && c.status !== m.status) loadCams();
    } else if (m.type === "violation" && m.event !== "updated") {
      addFeed(m.violation, m.event);
      loadStats();
    } else if (m.type === "connected") {
      loadCams(); loadStats();
    }
  });
  const timer = setInterval(loadStats, 10000);
  return () => {
    off();
    clearInterval(timer);
    // Stop MJPEG downloads when leaving the page.
    $$("img[src*='/stream']", main).forEach((img) => { img.src = ""; });
  };
}

