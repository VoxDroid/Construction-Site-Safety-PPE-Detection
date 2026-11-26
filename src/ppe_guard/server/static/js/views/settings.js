// Settings: general, cameras, detection rules, recording, alerts, security and system info.
import { $, api, confirmDialog, deepMerge, errorToast, fillForm, fmtBytes, html, icon, openDialog, raw, readForm, render, toast } from "../util.js";
import { loadSettings, state } from "../store.js";
import { statusPill } from "./live.js";

const TABS = [
  ["general", "General"], ["cameras", "Cameras"], ["detection", "Detection & rules"], ["recording", "Recording"],
  ["alerts", "Alerts"], ["security", "Security"], ["system", "System"],
];

const num = (name, label, attrs, hint = "") => html`<label class="field">${label}<input type="number" name="${name}" ${raw(attrs)}>${hint ? html`<span class="hint">${hint}</span>` : ""}</label>`;
const check = (name, label, hint = "") => html`<label class="check"><input type="checkbox" name="${name}"><span>${label}${hint ? html`<span class="hint">${hint}</span>` : ""}</span></label>`;

function formPage(title, desc, body) {
  return html`<form class="card" id="form">
    <div class="card-head"><div><h2>${title}</h2><p>${desc}</p></div></div>
    <div class="card-pad stack">${body}</div>
    <div class="dialog-foot"><button type="button" class="btn ghost" id="reset">Reset</button><button class="btn primary" type="submit">Save changes</button></div>
  </form>`;
}

const PAGES = {
  general: () => formPage("General", "Site details and how long data is kept.", html`
    <div class="field-grid">
      <label class="field">Site name<input name="general.site_name" maxlength="80" required pattern=".*\\S.*" title="Type at least one letter or digit"><span class="hint">Shown in the sidebar, reports and alerts.</span></label>
      ${num("storage.retention_days", "Keep violations for (days)", 'min="0" max="3650" step="1"', "0 keeps everything. Older violations, snapshots and clips are deleted hourly.")}
      ${num("analysis.max_upload_mb", "Largest upload for Analyze (MB)", 'min="1" max="10000" step="1"')}
      ${num("analysis.keep_jobs", "Analyze results to keep", 'min="1" max="500" step="1"')}
    </div>`),
  detection: (models) => formPage("Detection and PPE rules", "What counts as a violation, and how the model runs. Changes apply to running cameras within a second, except the model, which needs a camera restart.", html`
    <h3>Required PPE</h3>
    <div class="row" style="gap:26px">${check("compliance.required_ppe.helmet", raw('<span class="row-tight" style="gap:8px"><svg class="sign"><use href="#i-sign-helmet"/></svg>Hard hat</span>'))}${check("compliance.required_ppe.vest", raw('<span class="row-tight" style="gap:8px"><svg class="sign"><use href="#i-sign-vest"/></svg>Safety vest</span>'))}${check("compliance.required_ppe.mask", raw('<span class="row-tight" style="gap:8px"><svg class="sign"><use href="#i-sign-mask"/></svg>Face mask</span>'))}</div>
    <p class="muted small">PPE zones on a camera can override this for their area.</p>
    <h3>Timing</h3>
    <div class="field-grid">
      ${num("compliance.violation_delay", "Violation delay (s)", 'min="0" max="60" step="any"', "How long an item must look missing before a violation opens.")}
      ${num("compliance.clear_after", "Clear after (s)", 'min="0" max="300" step="any"', "Compliant this long and the violation closes.")}
      ${num("compliance.lost_timeout", "Forget worker after (s)", 'min="0.1" max="300" step="any"', "Out of view this long and the worker is dropped.")}
      ${num("compliance.snapshot_interval", "Snapshot every (s)", 'min="1" max="3600" step="1"')}
      ${num("compliance.max_snapshots", "Max snapshots per violation", 'min="1" max="100" step="1"')}
      ${num("compliance.min_overlap", "Overlap to assign PPE", 'min="0.05" max="1" step="any"', "Share of a PPE box that must lie inside the person.")}
    </div>
    ${check("compliance.treat_unseen_as_missing", "Strict mode", "A required item that cannot be seen at all counts as missing.")}
    <h3>Model</h3>
    <div class="field-grid">
      <label class="field">Model<input name="detection.weights" list="models-list" placeholder="auto"><span class="hint">"auto", a model name, or a path to a .pt file. Restart cameras to apply.</span></label>
      <datalist id="models-list">${raw('<option value="auto">')}${models.map((m) => html`<option value="${m.name}">${m.description}${m.downloaded ? "" : " (downloads on first use)"}</option>`)}</datalist>
      ${num("detection.confidence", "Confidence", 'min="0.01" max="1" step="any"', "Lower finds more, with more false alarms.")}
      ${num("detection.iou", "IoU (NMS)", 'min="0.05" max="1" step="any"', "Ignored by NMS-free models such as YOLO26.")}
      ${num("detection.imgsz", "Image size", 'min="160" max="1920" step="32"')}
      <label class="field">Device<input name="detection.device" data-nullable placeholder="auto (GPU if available)"><span class="hint">cpu, 0, cuda:0, mps…</span></label>
      <label class="field">Tracker<select name="detection.tracker"><option value="bytetrack.yaml">ByteTrack</option><option value="botsort.yaml">BoT-SORT</option></select></label>
      ${num("detection.max_fps", "Max FPS per camera", 'min="1" max="120" step="1"', "Caps GPU/CPU use.")}
    </div>`),
  recording: () => formPage("Recording", "Short video clips saved with each violation, next to its snapshots.", html`
    ${check("recording.clips", "Save a clip with every violation", "Includes a few seconds from before the violation started.")}
    <div class="field-grid">
      ${num("recording.pre_seconds", "Seconds before", 'min="0" max="30" step="1"')}
      ${num("recording.max_seconds", "Longest clip (s)", 'min="2" max="120" step="1"')}
      ${num("recording.fps", "Clip frame rate", 'min="1" max="30" step="1"')}
      ${num("recording.max_width", "Clip width (px)", 'min="320" max="1920" step="16"')}
    </div>
    <p class="muted small" id="codec"></p>`),
  alerts: () => formPage("Alerts", "Post a message to a webhook when a violation opens (Slack, Discord or any endpoint that accepts JSON).", html`
    <div class="field-grid">
      <label class="field" style="grid-column:1/-1">Webhook URL<input name="alerts.webhook_url" data-nullable type="url" placeholder="https://hooks.slack.com/services/…"></label>
      <label class="field">Format<select name="alerts.format"><option value="generic">Generic JSON</option><option value="slack">Slack</option><option value="discord">Discord</option></select></label>
      ${num("alerts.cooldown", "Cooldown per camera (s)", 'min="0" max="86400" step="1"', "Minimum time between two alerts from one camera.")}
      <label class="field">Dashboard address<input name="alerts.public_url" data-nullable type="url" placeholder="https://ppe.example.com"><span class="hint">Adds a link to the violation in each alert.</span></label>
    </div>
    <div class="row"><button type="button" class="btn" id="test-alert">Send a test alert</button><span class="muted small">Save first if you changed the URL.</span></div>`),
};

function cameraDialog(cam, onSaved) {
  const isNew = !cam;
  cam = cam || { id: "", name: "", source: "", enabled: true, autostart: true, loop: true, zones: [] };
  const d = openDialog(html`
    <form id="cam-form">
      <div class="dialog-head"><h2>${isNew ? "Add camera" : `Edit ${cam.name}`}</h2><button type="button" class="icon-btn" data-close aria-label="Close">${icon("x")}</button></div>
      <div class="dialog-body stack">
        <label class="field">Name<input name="name" required maxlength="80" pattern=".*\\S.*" title="Type at least one letter or digit" placeholder="North gate" value="${cam.name}"></label>
        <label class="field">ID<input name="id" required pattern="[a-z0-9][a-z0-9_\\-]{0,39}" placeholder="north-gate" value="${cam.id}" ${isNew ? "" : raw("readonly")}><span class="hint">Lowercase letters, digits, - and _. Cannot be changed later.</span></label>
        <label class="field">Source<input name="source" required pattern=".*\\S.*" title="Type at least one letter or digit" placeholder="0, /videos/site.mp4 or rtsp://user:pass@192.168.1.20/stream1" value="${cam.source}">
          <span class="hint"><b>0</b>, <b>1</b>… for webcams, a video file path on the server, or an <b>rtsp://</b>, <b>http://</b> stream URL.</span></label>
        <div class="row" style="gap:22px">
          <label class="check"><input type="checkbox" name="enabled" ${cam.enabled ? raw("checked") : ""}>Enabled</label>
          <label class="check"><input type="checkbox" name="autostart" ${cam.autostart ? raw("checked") : ""}>Start with the server</label>
          <label class="check"><input type="checkbox" name="loop" ${cam.loop ? raw("checked") : ""}>Loop video files</label>
        </div>
      </div>
      <div class="dialog-foot"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">${isNew ? "Add camera" : "Save"}</button></div>
    </form>`, { narrow: true });
  const form = $("#cam-form", d);
  form.name.addEventListener("input", () => {
    if (isNew && !form.id.dataset.touched) form.id.value = form.name.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
  });
  form.id.addEventListener("input", () => { form.id.dataset.touched = "1"; });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { ...readForm(form), zones: cam.zones };
    try {
      await api(isNew ? "/api/cameras" : `/api/cameras/${cam.id}`, { method: isNew ? "POST" : "PUT", body });
      d.close();
      toast(isNew ? "Camera added" : "Camera saved");
      onSaved();
    } catch (err) { errorToast(err); }
  });
}

async function camerasPage(el) {
  const draw = async () => {
    const cams = await api("/api/cameras");
    render(el, html`<section class="card">
      <div class="card-head"><div><h2>Cameras</h2><p>Webcams, video files and network streams. Each camera runs on its own thread.</p></div>
        <button class="btn primary" id="add-cam">${icon("plus", "ic-sm")}Add camera</button></div>
      ${cams.length ? html`<div class="table-wrap"><table class="table">
        <thead><tr><th>Name</th><th>Source</th><th>Status</th><th>Zones</th><th>Start with server</th><th></th></tr></thead>
        <tbody>${cams.map((c) => html`<tr data-id="${c.id}">
          <td><b>${c.name}</b><br><span class="muted mono">${c.id}</span></td>
          <td class="mono" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${c.source}">${c.source}</td>
          <td>${c.enabled ? statusPill(c.status) : html`<span class="pill">Disabled</span>`}</td>
          <td><a href="#/cameras/${c.id}/zones">${c.zones.length ? `${c.zones.length} zone(s)` : "Add zones"}</a></td>
          <td>${c.autostart ? "Yes" : "No"}</td>
          <td style="text-align:right;white-space:nowrap">
            <button class="icon-btn sm" data-edit title="Edit" aria-label="Edit ${c.name}">${icon("edit", "ic-sm")}</button>
            <button class="icon-btn sm" data-del title="Delete" aria-label="Delete ${c.name}">${icon("trash", "ic-sm")}</button></td>
        </tr>`)}</tbody></table></div>` : html`<div class="empty">${icon("live")}<h3>No cameras</h3><p>Add one to start monitoring.</p></div>`}
    </section>`);
    $("#add-cam", el).addEventListener("click", () => cameraDialog(null, draw));
    el.querySelectorAll("tr[data-id]").forEach((tr) => {
      const cam = cams.find((c) => c.id === tr.dataset.id);
      $("[data-edit]", tr).addEventListener("click", () => cameraDialog(cam, draw));
      $("[data-del]", tr).addEventListener("click", async () => {
        if (!(await confirmDialog(`Delete ${cam.name}?`, "The camera and its zones are removed. Its recorded violations stay.", "Delete"))) return;
        try { await api(`/api/cameras/${cam.id}`, { method: "DELETE" }); toast("Camera deleted"); draw(); } catch (err) { errorToast(err); }
      });
    });
  };
  await draw();
}

async function securityPage(el) {
  const draw = async () => {
    const a = await api("/api/auth/status");
    state.auth = a;
    $("#logout-btn").hidden = !a.enabled;
    render(el, html`<div class="stack">
      ${a.enabled ? "" : html`<div class="notice">${icon("alert")}<span><b>No password is set.</b> Anyone who can reach this address can view cameras and change settings. Set a password before exposing the dashboard on a network.</span></div>`}
      ${a.from_env ? html`<div class="notice info">The password comes from the <code>PPE_GUARD_PASSWORD</code> environment variable. A password changed here is replaced by it at the next restart.</div>` : ""}
      <form class="card" id="pw-form">
        <div class="card-head"><div><h2>${a.enabled ? "Change password" : "Set a password"}</h2><p>One password for the dashboard. Changing it logs out every other session.</p></div></div>
        <div class="card-pad field-grid">
          ${a.enabled ? html`<label class="field">Current password<input type="password" name="current" autocomplete="current-password" required></label>` : ""}
          <label class="field">New password<input type="password" name="new" minlength="8" autocomplete="new-password" required><span class="hint">At least 8 characters.</span></label>
          <label class="field">Repeat new password<input type="password" name="repeat" minlength="8" autocomplete="new-password" required></label>
        </div>
        <div class="dialog-foot">${a.enabled ? html`<button type="button" class="btn danger" id="pw-clear">Remove password</button><span class="spacer"></span>` : ""}<button class="btn primary">${a.enabled ? "Change password" : "Set password"}</button></div>
      </form>
      <section class="card">
        <div class="card-head"><div><h2>API token</h2><p>For scripts and integrations: send <code>Authorization: Bearer &lt;token&gt;</code>, or add <code>?token=</code> to GET URLs such as a camera's MJPEG stream.</p></div></div>
        <div class="card-pad stack">
          <div class="row"><span>${a.token_set ? "A token is active." : "No token."}</span><span class="spacer"></span>
            ${a.token_set ? html`<button class="btn danger" id="tok-revoke">Revoke</button>` : ""}
            <button class="btn" id="tok-new" ${a.enabled ? "" : raw("disabled")}>${a.token_set ? "Replace token" : "Create token"}</button></div>
          ${a.enabled ? "" : html`<p class="muted small">Set a password first.</p>`}
          <div id="tok-out"></div>
        </div>
      </section>
    </div>`);
    $("#pw-form", el).addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.target;
      if (f.new.value !== f.repeat.value) { errorToast(new Error("The new passwords do not match")); return; }
      try { await api("/api/auth/password", { method: "POST", body: { current: f.current?.value, new: f.new.value } }); toast("Password saved"); draw(); } catch (err) { errorToast(err); }
    });
    $("#pw-clear", el)?.addEventListener("click", async () => {
      const cur = $("#pw-form", el).current.value;
      if (!cur) { errorToast(new Error("Enter the current password first")); return; }
      if (!(await confirmDialog("Remove the password?", "The dashboard will be open to anyone who can reach it, and the API token is revoked.", "Remove"))) return;
      try { await api("/api/auth/password/clear", { method: "POST", body: { current: cur } }); toast("Password removed"); draw(); } catch (err) { errorToast(err); }
    });
    $("#tok-new", el)?.addEventListener("click", async () => {
      try {
        const { token } = await api("/api/auth/token", { method: "POST" });
        await draw();
        render($("#tok-out", el), html`<div class="notice info" style="flex-direction:column"><b>Copy this token now. It is not shown again.</b><code style="word-break:break-all">${token}</code></div>`);
      } catch (err) { errorToast(err); }
    });
    $("#tok-revoke", el)?.addEventListener("click", async () => {
      try { await api("/api/auth/token", { method: "DELETE" }); toast("Token revoked"); draw(); } catch (err) { errorToast(err); }
    });
  };
  await draw();
}

async function systemPage(el) {
  const s = await api("/api/system");
  const st = s.storage;
  render(el, html`<div class="grid-2">
    <section class="card"><div class="card-head"><h2>${icon("cpu")}Software</h2></div><div class="card-pad"><dl class="meta">
      <dt>PPE Guard</dt><dd>${s.version}</dd><dt>Python</dt><dd>${s.python} (${s.platform})</dd>
      <dt>PyTorch</dt><dd>${s.torch || "–"}</dd><dt>Ultralytics</dt><dd>${s.ultralytics || "–"}</dd>
      <dt>Compute</dt><dd>${s.device}</dd>
      <dt>Video codec</dt><dd>${s.video_codec.fourcc} (${s.video_codec.mime})${s.video_codec.playable ? "" : " · not playable in browsers"}</dd>
      <dt>Live viewers</dt><dd>${s.websocket_clients}</dd>
    </dl></div></section>
    <section class="card"><div class="card-head"><h2>${icon("disk")}Storage</h2></div><div class="card-pad"><dl class="meta">
      <dt>Data folder</dt><dd class="mono" style="word-break:break-all">${s.data_dir}</dd>
      <dt>Database</dt><dd>${fmtBytes(st.database_bytes)}</dd><dt>Snapshots</dt><dd>${fmtBytes(st.snapshots_bytes)}</dd>
      <dt>Clips</dt><dd>${fmtBytes(st.clips_bytes)}</dd><dt>Analyze results</dt><dd>${fmtBytes(st.analysis_bytes)}</dd>
      <dt>Free disk</dt><dd>${fmtBytes(st.disk_free_bytes)} of ${fmtBytes(st.disk_total_bytes)}</dd>
    </dl></div></section>
    <section class="card" style="grid-column:1/-1"><div class="card-head"><h2>${icon("external")}API</h2></div><div class="card-pad">
      <p>Interactive API documentation is at <a href="/docs">/docs</a> (OpenAPI schema at <a href="/openapi.json">/openapi.json</a>).</p>
    </div></section>
  </div>`);
}

export async function mount(main, [tab = "general"]) {
  if (!TABS.some(([k]) => k === tab)) tab = "general";
  render(main, html`
    <div class="page-head"><div><div class="eyebrow">Sheet S-01 · System</div><h1>Settings</h1><p>Saved to <code>settings.json</code> in the data folder.</p></div></div>
    <nav class="tabs">${TABS.map(([k, label]) => html`<a href="#/settings/${k}" class="${k === tab ? "active" : ""}">${label}</a>`)}</nav>
    <div id="page"></div>`);
  const el = $("#page", main);
  if (tab === "cameras") return camerasPage(el);
  if (tab === "security") return securityPage(el);
  if (tab === "system") return systemPage(el);

  const models = tab === "detection" ? await api("/api/models") : [];
  render(el, PAGES[tab](models));
  const form = $("#form", el);
  const fill = async () => fillForm(form, await loadSettings());
  await fill();
  if (tab === "recording") {
    api("/api/system").then((s) => {
      $("#codec", el).textContent = s.video_codec.playable
        ? `Clips are saved as ${s.video_codec.mime} (${s.video_codec.fourcc}).`
        : `This OpenCV build can only write ${s.video_codec.fourcc} video, which browsers cannot play; clips can still be downloaded.`;
    }).catch(() => {});
  }
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const current = await api("/api/settings");
      await api("/api/settings", { method: "PUT", body: deepMerge(current, readForm(form)) });
      await loadSettings();
      toast("Settings saved");
    } catch (err) { errorToast(err); }
  });
  $("#reset", el).addEventListener("click", fill);
  $("#test-alert", el)?.addEventListener("click", async () => {
    try { const r = await api("/api/alerts/test", { method: "POST" }); toast(`Test alert sent (HTTP ${r.status})`); } catch (err) { errorToast(err); }
  });
  if (tab === "detection") {
    const restore = html`<button type="button" class="btn ghost" id="defaults">Restore defaults</button>`;
    $("#reset", el).insertAdjacentHTML("afterend", restore.__raw);
    $("#defaults", el).addEventListener("click", async () => {
      if (!(await confirmDialog("Restore default settings?", "Detection, rules, recording, alerts and storage go back to their defaults. Cameras and zones are kept.", "Restore", false))) return;
      try { await api("/api/settings/reset", { method: "POST" }); await fill(); toast("Defaults restored"); } catch (err) { errorToast(err); }
    });
  }
  return () => {};
}

