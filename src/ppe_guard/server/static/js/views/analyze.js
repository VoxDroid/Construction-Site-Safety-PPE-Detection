// Analyze: upload a photo or a video and get a verdict for every worker.
import { $, api, confirmDialog, errorToast, fmtDuration, fmtPct, fmtTime, html, icon, ITEM_LABEL, kpi, render, STATE_LABEL, toast, workerName } from "../util.js";
import { bus } from "../store.js";

const ACCEPT = ".jpg,.jpeg,.png,.bmp,.webp,.jfif,.tif,.tiff,.mp4,.avi,.mov,.mkv,.webm,.m4v,.mpg,.mpeg,.wmv";
const STATUS = { uploading: ["info", "Uploading"], queued: ["", "Queued"], running: ["info", "Processing"], done: ["good", "Done"], failed: ["bad", "Failed"], cancelled: ["", "Cancelled"] };

function jobRow(j, selected) {
  const [cls, label] = STATUS[j.status] || ["", j.status];
  const summary = j.status === "done" && j.result
    ? j.kind === "image"
      ? `${j.result.counts.workers} worker(s), ${j.result.counts.violations} violation(s)`
      : `${fmtDuration(j.result.duration)} · ${j.result.violations.length} violation(s)`
    : j.status === "failed" ? j.error : "";
  return html`<div class="job ${selected ? "selected" : ""}" data-job="${j.id}">
    ${icon(j.kind === "image" ? "image" : "film")}
    <div style="flex:1;min-width:0">
      <div class="name">${j.filename}</div>
      <div class="meta">${fmtTime(j.created_at)}${summary ? ` · ${summary}` : ""}</div>
      ${["running", "queued", "uploading"].includes(j.status) ? html`<div class="progress"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></div>` : ""}
    </div>
    <span class="pill ${cls}">${label}${j.status === "running" ? ` ${Math.round((j.progress || 0) * 100)}%` : ""}</span>
  </div>`;
}

function itemsCell(w) {
  const mark = { worn: html`<span class="pill good">worn</span>`, missing: html`<span class="pill bad">missing</span>`, unknown: html`<span class="muted">not visible</span>` };
  return html`${w.required.map((i) => html`<div class="row" style="gap:6px">${ITEM_LABEL[i]} ${mark[w.items[i]]}</div>`)}`;
}

function imageResult(j) {
  const r = j.result;
  const workers = r.workers.filter((w) => !w.synthetic || w.state === "violation");
  const verdict = r.counts.violations
    ? html`<div class="notice bad">${icon("alert")}<span><b>${r.counts.violations} worker(s) missing required PPE.</b> Required: ${r.required.map((i) => ITEM_LABEL[i]).join(", ")}.</span></div>`
    : r.counts.workers
      ? html`<div class="notice info">${icon("check")}<span><b>No violations found.</b> ${r.counts.unknown ? `${r.counts.unknown} worker(s) had an item that could not be seen.` : "Every worker wears the required PPE."}</span></div>`
      : html`<div class="notice info">${icon("image")}<span>No workers were detected in this image.</span></div>`;
  return html`
    <div class="stack">
      ${verdict}
      <div class="media-main" style="aspect-ratio:${r.width}/${r.height}"><img src="/analysis/${j.id}/${r.image}" alt="Annotated result"></div>
      <section class="card"><div class="card-head"><h2>${icon("users")}Workers</h2><span class="muted small">model ${r.weights}</span></div>
        ${workers.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Worker</th><th>Verdict</th><th>Equipment</th></tr></thead>
          <tbody>${workers.map((w) => html`<tr><td>${workerName(w.key)}</td><td><span class="pill ${w.state === "violation" ? "bad" : w.state === "compliant" ? "good" : ""}">${STATE_LABEL[w.state]}</span></td><td>${itemsCell(w)}</td></tr>`)}</tbody></table></div>`
          : html`<div class="empty small">No workers detected.</div>`}
      </section>
      <div class="row"><a class="btn" href="/analysis/${j.id}/${r.image}?download=1">${icon("download", "ic-sm")}Download image</a></div>
    </div>`;
}

function videoResult(j) {
  const r = j.result;
  const dur = Math.max(r.duration, 0.01);
  return html`
    <div class="stack">
      <div class="kpis" style="margin:0">
        ${kpi({ label: "Violations", value: r.violations.length, icon: "alert", alert: r.violations.length > 0, sub: `${r.workers_seen} worker(s) tracked` })}
        ${kpi({ label: "Compliance rate", value: fmtPct(r.compliance_rate), icon: "shield", sub: "of worker-time" })}
        ${kpi({ label: "Video", value: fmtDuration(r.duration), icon: "film", sub: `${r.frames} frames at ${r.fps} fps` })}
        ${kpi({ label: "Processing", value: r.processing_fps, icon: "gauge", sub: `frames per second · ${r.weights}` })}
      </div>
      ${r.playable
        ? html`<div class="media-main"><video id="player" src="/analysis/${j.id}/${r.video}" controls playsinline preload="metadata"></video></div>`
        : html`<div class="notice">This OpenCV build cannot write a browser-playable video. Download the result to watch it.</div>`}
      <div>
        <div class="timeline" id="timeline" title="Violations over the video">
          ${r.violations.map((v) => html`<span class="seg ${v.kind === "zone" ? "zone" : ""}" data-t="${v.start}" style="left:${(v.start / dur) * 100}%;width:${(((v.end ?? r.duration) - v.start) / dur) * 100}%" title="${v.kind === "zone" ? `In ${v.zone}` : `Missing ${v.missing.map((m) => ITEM_LABEL[m]).join(", ")}`} at ${fmtDuration(v.start)}"></span>`)}
          <span class="head" id="head" style="left:0"></span>
        </div>
        <div class="row small muted"><span>0:00</span><span class="spacer"></span><span>${fmtDuration(r.duration)}</span></div>
      </div>
      <section class="card"><div class="card-head"><h2>${icon("film")}Violations in this video</h2></div>
        ${r.violations.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>At</th><th>Snapshot</th><th>Worker</th><th>What</th><th class="num">Lasted</th></tr></thead>
          <tbody>${r.violations.map((v) => html`<tr class="clickable" data-t="${v.start}">
            <td class="mono">${fmtDuration(v.start)}</td>
            <td>${v.snapshot ? html`<img class="thumb" src="/analysis/${j.id}/${v.snapshot}" alt="" loading="lazy">` : ""}</td>
            <td>${workerName(v.worker)}</td>
            <td>${v.kind === "zone" ? html`<span class="pill warn">In ${v.zone}</span>` : v.missing.map((m) => html`<span class="pill bad">${ITEM_LABEL[m]}</span> `)}</td>
            <td class="num">${v.end === null ? "to the end" : fmtDuration(v.end - v.start)}</td></tr>`)}</tbody></table></div>`
          : html`<div class="empty small">${icon("check")}<h3>No violations</h3></div>`}
      </section>
      ${r.video ? html`<div class="row"><a class="btn" href="/analysis/${j.id}/${r.video}?download=1">${icon("download", "ic-sm")}Download annotated video</a></div>` : ""}
    </div>`;
}

export async function mount(main, [openId]) {
  let jobs = await api("/api/analyze/jobs");
  let current = openId || jobs[0]?.id || null;
  render(main, html`
    <div class="page-head">
      <div><div class="eyebrow">Sheet B-03 · Tools</div><h1>Analyze</h1><p>Check a photo or a video file. Results use the same model and PPE rules as the cameras.</p></div>
    </div>
    <div class="analyze-layout">
      <div class="stack">
        <label class="drop" id="drop">
          ${icon("cloud")}
          <h3>Drop a photo or video here</h3>
          <p class="muted small">or click to choose a file · JPG, PNG, MP4, MOV, AVI, MKV, WEBM…</p>
          <input type="file" id="file" accept="${ACCEPT}" multiple hidden>
        </label>
        <div class="jobs" id="jobs"></div>
      </div>
      <div id="result"></div>
    </div>`);

  const drawJobs = () => {
    render($("#jobs", main), jobs.length ? html`${jobs.map((j) => jobRow(j, j.id === current))}` : html`<p class="muted small">Nothing analyzed yet.</p>`);
  };
  const drawResult = () => {
    const j = jobs.find((x) => x.id === current);
    const el = $("#result", main);
    if (!j) { render(el, html`<div class="card empty">${icon("upload")}<h3>Upload something to analyze</h3><p>Photos are checked in a second or two. Videos are processed in the background; you can leave this page.</p></div>`); return; }
    const head = html`<div class="row" style="margin-bottom:12px"><h2 style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis">${j.filename}</h2>
      ${["running", "queued"].includes(j.status) ? html`<button class="btn sm" id="cancel">Cancel</button>` : ""}
      <button class="btn sm danger" id="delete">${icon("trash", "ic-sm")}Delete</button></div>`;
    let body;
    if (j.status === "done") body = j.kind === "image" ? imageResult(j) : videoResult(j);
    else if (j.status === "failed") body = html`<div class="notice bad">${j.error || "Failed"}</div>`;
    else if (j.status === "cancelled") body = html`<div class="notice">Cancelled.</div>`;
    else body = html`<div class="card card-pad"><p>${STATUS[j.status]?.[1]}… ${j.frames ? `${j.frames} frames so far` : ""}</p><div class="progress"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></div></div>`;
    render(el, html`${head}${body}`);
    wireResult(j);
  };
  function wireResult(j) {
    const el = $("#result", main);
    $("#delete", el)?.addEventListener("click", async () => {
      if (!(await confirmDialog("Delete this result?", "The annotated image or video is deleted.", "Delete"))) return;
      try { await api(`/api/analyze/jobs/${j.id}`, { method: "DELETE" }); jobs = jobs.filter((x) => x.id !== j.id); current = jobs[0]?.id || null; drawJobs(); drawResult(); } catch (e) { errorToast(e); }
    });
    $("#cancel", el)?.addEventListener("click", async () => {
      try { await api(`/api/analyze/jobs/${j.id}/cancel`, { method: "POST" }); } catch (e) { errorToast(e); }
    });
    const player = $("#player", el);
    if (player) {
      const head = $("#head", el);
      player.addEventListener("timeupdate", () => { head.style.left = `${(player.currentTime / Math.max(j.result.duration, 0.01)) * 100}%`; });
      el.addEventListener("click", (e) => {
        const t = e.target.closest("[data-t]");
        if (t) { player.currentTime = Number(t.dataset.t); player.play().catch(() => {}); player.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
      });
    }
  }

  async function upload(file) {
    const fd = new FormData();
    fd.append("file", file);
    const temp = { id: `tmp-${Math.random().toString(36).slice(2)}`, kind: /\.(mp4|avi|mov|mkv|webm|m4v|mpg|mpeg|wmv)$/i.test(file.name) ? "video" : "image", filename: file.name, status: "uploading", progress: 0, created_at: Date.now() / 1000 };
    jobs.unshift(temp);
    current = temp.id;
    drawJobs(); drawResult();
    try {
      const job = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/analyze");
        xhr.upload.onprogress = (e) => { if (e.lengthComputable) { temp.progress = e.loaded / e.total; drawJobs(); } };
        xhr.onload = () => {
          let body = null;
          try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
          if (xhr.status === 401) { location.href = "/login"; return; }
          xhr.status < 300 ? resolve(body) : reject(new Error(body?.detail || xhr.statusText));
        };
        xhr.onerror = () => reject(new Error("upload failed"));
        xhr.send(fd);
      });
      jobs = jobs.map((x) => (x.id === temp.id ? job : x));
      if (current === temp.id) current = job.id;
      history.replaceState(null, "", `#/analyze/${job.id}`);
    } catch (e) {
      jobs = jobs.filter((x) => x.id !== temp.id);
      if (current === temp.id) current = jobs[0]?.id || null;
      errorToast(e);
    }
    drawJobs(); drawResult();
  }

  const input = $("#file", main);
  const drop = $("#drop", main);
  input.addEventListener("change", () => { [...input.files].forEach(upload); input.value = ""; });
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); [...e.dataTransfer.files].forEach(upload); });
  $("#jobs", main).addEventListener("click", (e) => {
    const r = e.target.closest("[data-job]");
    if (!r || r.dataset.job.startsWith("tmp-")) return;
    current = r.dataset.job;
    history.replaceState(null, "", `#/analyze/${current}`);
    drawJobs(); drawResult();
  });

  drawJobs(); drawResult();
  const off = bus.on((m) => {
    if (m.type !== "job") return;
    const i = jobs.findIndex((x) => x.id === m.job.id);
    const was = jobs[i]?.status;
    if (i >= 0) jobs[i] = m.job; else jobs.unshift(m.job);
    drawJobs();
    if (m.job.id === current && (m.job.status !== "running" || was !== "running")) drawResult();
    else if (m.job.id === current) {
      const bar = $("#result .progress i", main);
      if (bar) bar.style.width = `${Math.round(m.job.progress * 100)}%`;
    }
    if (m.job.status === "done" && was !== "done") toast(`${m.job.filename} analyzed`);
  });
  return off;
}

