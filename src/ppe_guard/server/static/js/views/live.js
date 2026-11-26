// Live: KPIs, every camera with its stream and workers, and the event feed.
import { $, $$, api, errorToast, fmtPct, fmtTime, html, icon, ITEM_LABEL, kpi, raw, rateClass, render, violationTitle, workerName } from "../util.js";
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

export const sourceLabel = (src) => (src.match(/^\d+$/) ? `Webcam ${src}` : src.split(/[\\/]/).pop());

function camCard(c) {
  const running = c.status === "running" || c.status === "reconnecting" || c.status === "starting";
  const violating = c.workers?.some((w) => w.state === "violation");
  const err = c.status === "error" ? html`<p class="small" style="margin-top:6px;color:#ff8f86">${c.error}</p>` : "";
  return html`
    <article class="card cam ${violating ? "alarm" : ""}" data-cam="${c.id}">
      <div class="cam-media">
        <a class="cam-view ${violating ? "violating" : ""}" href="#/cameras/${c.id}" aria-label="Open ${c.name}">
          ${running
            ? html`<img src="/api/cameras/${c.id}/stream?ts=${Date.now()}" alt="Live view of ${c.name}">`
            : html`<div class="placeholder">${icon("video")}<div>${c.enabled ? "Camera stopped" : "Camera disabled"}</div>${err}</div>`}
        </a>
        <div class="cam-top">
          <span data-status>${statusPill(c.status)}</span>
          <h3><a href="#/cameras/${c.id}">${c.name}</a></h3>
          <span class="fps" data-fps>${c.status === "running" ? `${c.fps} FPS` : ""}</span>
        </div>
      </div>
      <div class="cam-head">
        <span class="src" title="${c.source}">${sourceLabel(c.source)}${c.zones?.length ? ` · ${c.zones.length} zone${c.zones.length > 1 ? "s" : ""}` : ""}</span>
        <a class="icon-btn sm" href="#/cameras/${c.id}/zones" title="Edit zones" aria-label="Edit zones for ${c.name}">${icon("zone", "ic-sm")}</a>
        ${running
          ? html`<button class="btn sm" data-act="stop">${icon("stop", "ic-sm")}Stop</button>`
          : html`<button class="btn sm primary" data-act="start" ${c.enabled ? "" : raw("disabled")}>${icon("play", "ic-sm")}Start</button>`}
      </div>
      <div class="cam-foot" data-workers>${running ? workersFoot(c.workers || []) : html`<span class="muted">Not monitoring</span>`}</div>
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
    ${kpi({ label: "Open violations", value: s.ongoing, icon: "siren", alert: s.ongoing > 0, sub: `${s.violating_now} worker(s) right now` })}
    ${kpi({ label: "Violations today", value: s.today, icon: "alert", sub: `${s.unacknowledged} awaiting review` })}
    ${kpi({ label: "Compliance today", value: fmtPct(s.compliance_today), tone: rateClass(s.compliance_today), icon: "shield", sub: "share of monitored worker-time" })}
    ${kpi({ label: "Workers in view", value: s.workers_in_view, icon: "users", sub: "across running cameras" })}
    ${kpi({ label: "Cameras live", value: s.cameras_running, of: s.cameras_total, icon: "video", sub: s.alert_error ? "alert delivery failing" : "streams running" })}`;
}

export async function mount(main) {
  render(main, html`
    <div class="page-head">
      <div><div class="eyebrow">Sheet A-01 · Monitor</div><h1>Live</h1><p>Every camera, its workers and what they're missing, as it happens.</p></div>
      <div class="actions">
        <button class="btn" id="start-all">${icon("play", "ic-sm")}Start all</button>
        <a class="btn" href="#/settings/cameras">${icon("plus", "ic-sm")}Add camera</a>
      </div>
    </div>
    <div class="kpis" id="kpis">${raw('<div class="card kpi skeleton" style="height:96px"></div>'.repeat(5))}</div>
    <div class="live-layout">
      <div class="cams" id="cams"></div>
      <section class="card">
        <div class="card-head"><h2>${icon("activity")}Live events</h2><a class="small muted" href="#/violations">All violations →</a></div>
        <div class="feed" id="feed"><div class="empty small">No events yet.</div></div>
      </section>
    </div>`);

  let cams = [];
  const drawCams = () => {
    const el = $("#cams", main);
    if (!cams.length) {
      render(el, html`<div class="card empty">${icon("video")}<h3>No cameras yet</h3><p>Add a webcam, video file or RTSP stream in <a href="#/settings/cameras">Settings → Cameras</a>.</p></div>`);
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
      $("[data-fps]", card).textContent = `${m.fps} FPS`;
      render($("[data-workers]", card), workersFoot(m.workers));
      $(".cam-view", card).classList.toggle("violating", m.violating);
      card.classList.toggle("alarm", m.violating);
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

