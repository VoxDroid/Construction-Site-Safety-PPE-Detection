// One camera: large live view, per-worker table and its recent violations.
import { $, api, errorToast, fmtDuration, fmtTime, html, icon, ITEM_LABEL, missingBadges, raw, render, STATE_LABEL, workerName } from "../util.js";
import { bus } from "../store.js";
import { statusPill } from "./live.js";

function workersTable(workers) {
  const rows = workers.filter((w) => !w.synthetic || w.state === "violation");
  if (!rows.length) return html`<div class="empty small">No workers in view.</div>`;
  const mark = { worn: html`<span class="pill good">worn</span>`, missing: html`<span class="pill bad">missing</span>`, unknown: html`<span class="muted">not visible</span>` };
  const cls = { violation: "bad", compliant: "good", pending: "warn", unknown: "", unmonitored: "" };
  return html`<div class="table-wrap"><table class="table workers-table">
    <thead><tr><th>Worker</th><th>Status</th><th>Required here</th><th>Zone</th></tr></thead>
    <tbody>${rows.map((w) => html`<tr>
      <td>${workerName(w.key)}</td>
      <td><span class="pill ${cls[w.state]}">${STATE_LABEL[w.state]}</span></td>
      <td>${w.required.length ? w.required.map((i) => html`<div class="row" style="gap:6px">${ITEM_LABEL[i]} ${mark[w.items[i]]}</div>`) : html`<span class="muted">nothing</span>`}</td>
      <td>${w.intrusion ? html`<span class="pill bad">${w.intrusion}</span>` : w.zones.length ? w.zones.join(", ") : html`<span class="muted">–</span>`}</td>
    </tr>`)}</tbody></table></div>`;
}

export async function mount(main, [id]) {
  let cam = await api(`/api/cameras/${id}`);
  const draw = () => {
    const running = ["running", "starting", "reconnecting"].includes(cam.status);
    render(main, html`
      <a class="crumb" href="#/live">${icon("back", "ic-sm")}Live</a>
      <div class="page-head">
        <div>
          <h1 class="row">${cam.name} <span data-status>${statusPill(cam.status)}</span></h1>
          <p class="mono">${cam.source.match(/^\d+$/) ? `Webcam ${cam.source}` : cam.source.split(/[\\/]/).pop()}${cam.weights ? ` · ${cam.weights}` : ""}${cam.frame_size ? ` · ${cam.frame_size[0]}×${cam.frame_size[1]}` : ""} · <span data-fps>${running ? `${cam.fps} fps` : ""}</span></p>
        </div>
        <div class="actions">
          <a class="btn" href="#/cameras/${cam.id}/zones">${icon("zone", "ic-sm")}Zones (${cam.zones.length})</a>
          <button class="btn" id="fullscreen" ${running ? "" : raw("disabled")}>${icon("expand", "ic-sm")}Full screen</button>
          ${running
            ? html`<button class="btn" data-act="stop">${icon("stop", "ic-sm")}Stop</button>`
            : html`<button class="btn primary" data-act="start" ${cam.enabled ? "" : raw("disabled")}>${icon("play", "ic-sm")}Start</button>`}
        </div>
      </div>
      ${cam.status === "error" ? html`<div class="notice bad" style="margin-bottom:14px">${cam.error}</div>` : ""}
      <div class="cam-page">
        <div class="stack">
          <div class="card cam">
            <div class="cam-view" id="view">${running
              ? html`<img src="/api/cameras/${cam.id}/stream?ts=${Date.now()}" alt="Live view of ${cam.name}">`
              : html`<div class="placeholder">${icon("live")}<div>Camera stopped</div></div>`}</div>
          </div>
          <section class="card">
            <div class="card-head"><h2>Workers in view</h2><span class="muted small" id="wcount"></span></div>
            <div id="workers">${workersTable(cam.workers || [])}</div>
          </section>
        </div>
        <section class="card">
          <div class="card-head"><h2>Recent violations</h2><a class="small muted" href="#/violations?camera_id=${cam.id}">See all</a></div>
          <div id="recent"></div>
        </section>
      </div>`);
  };
  const loadRecent = async () => {
    const r = await api(`/api/violations?camera_id=${encodeURIComponent(id)}&limit=8`);
    render($("#recent", main), r.items.length ? html`${r.items.map((v) => html`
      <a class="feed-item ${v.ongoing ? "" : "closed"}" href="#/violations/${v.id}">
        ${v.thumbnail ? html`<img class="thumb" src="/snapshots/${v.thumbnail}" alt="" loading="lazy">` : ""}
        <span>${missingBadges(v)}<br><span class="t">${fmtTime(v.started_at)} · ${v.ongoing ? "ongoing" : fmtDuration(v.duration)}</span></span>
      </a>`)}` : html`<div class="empty small">No violations recorded.</div>`);
  };
  draw();
  loadRecent().catch(errorToast);

  main.addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-act]");
    if (b) {
      b.disabled = true;
      try {
        await api(`/api/cameras/${id}/${b.dataset.act}`, { method: "POST" });
        cam = await api(`/api/cameras/${id}`);
        draw();
        loadRecent();
      } catch (err) { errorToast(err); b.disabled = false; }
    }
    if (e.target.closest("#fullscreen")) $("#view", main).requestFullscreen?.();
  });

  const off = bus.on(async (m) => {
    if (m.camera_id !== id && m.violation?.camera_id !== id) return;
    if (m.type === "frame") {
      render($("#workers", main), workersTable(m.workers));
      $("#view", main)?.classList.toggle("violating", m.violating);
      const fps = $("[data-fps]", main);
      if (fps) fps.textContent = `${m.fps} fps`;
    } else if (m.type === "camera") {
      cam = await api(`/api/cameras/${id}`);
      draw();
      loadRecent();
    } else if (m.type === "violation" && m.event !== "updated") {
      loadRecent();
    }
  });
  return () => {
    off();
    const img = $("#view img", main);
    if (img) img.src = "";
  };
}
