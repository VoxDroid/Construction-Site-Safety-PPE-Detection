// Violations: filterable, sortable list with bulk review, and a detail dialog.
import { $, $$, api, confirmDialog, errorToast, fmtDuration, fmtTime, html, icon, missingBadges, openDialog, raw, render, toast, violationTitle, workerName } from "../util.js";
import { bus, refreshBadge } from "../store.js";

const PAGE = 25;
const PERIODS = { "": "All time", 1: "Last 24 hours", 7: "Last 7 days", 30: "Last 30 days", 90: "Last 90 days" };

function statusPill(v) {
  const ongoing = v.ongoing ? html`<span class="pill bad"><span class="dot"></span>Ongoing</span> ` : "";
  if (v.acknowledged) return html`${ongoing}<span class="pill good">${icon("check", "ic-sm")}Reviewed</span>`;
  return v.ongoing ? ongoing : html`<span class="pill">Needs review</span>`;
}

export async function openViolation(id, { onChange } = {}) {
  let v;
  try { v = await api(`/api/violations/${id}`); } catch (e) { errorToast(e); return; }
  const media = [...(v.clip ? [{ type: "clip", src: `/clips/${v.clip}` }] : []),
    ...v.snapshots.map((s) => ({ type: "img", src: `/snapshots/${s.filename}`, t: s.taken_at, name: s.filename }))];
  let cur = 0;
  const d = openDialog(html`
    <div class="dialog-head">
      <h2>${violationTitle(v)}</h2>
      <a class="btn sm ghost" href="#/cameras/${v.camera_id}">${v.camera_name}</a>
      <button class="icon-btn" data-close aria-label="Close" autofocus>${icon("x")}</button>
    </div>
    <div class="dialog-body">
      <div class="detail-grid">
        <div>
          <div class="media-main" id="media"></div>
          <div class="strip" id="strip">${media.map((m, i) => m.type === "clip"
            ? html`<button data-m="${i}" title="Video clip"><span class="clip-tab">${icon("film")}</span></button>`
            : html`<button data-m="${i}" title="${fmtTime(m.t, { full: true, seconds: true })}"><img src="${m.src}" alt=""></button>`)}</div>
          ${media.length ? "" : html`<p class="muted small">No snapshots were saved for this violation.</p>`}
        </div>
        <div class="stack">
          <div class="row">${missingBadges(v)} ${statusPill(v)}</div>
          <dl class="meta">
            <dt>Camera</dt><dd>${v.camera_name}</dd>
            <dt>Worker</dt><dd>${workerName(v.worker_key)}</dd>
            <dt>Started</dt><dd>${fmtTime(v.started_at, { full: true, seconds: true })}</dd>
            <dt>${v.ongoing ? "Last seen" : "Ended"}</dt><dd>${fmtTime(v.ended_at || v.last_seen, { full: true, seconds: true })}</dd>
            <dt>Duration</dt><dd>${fmtDuration(v.duration)}${v.ongoing ? " so far" : ""}</dd>
            <dt>Evidence</dt><dd>${v.snapshots.length} snapshot(s)${v.clip ? ", 1 clip" : ""}</dd>
            <dt>ID</dt><dd class="mono">${v.id}</dd>
          </dl>
          <label class="field">Note<textarea id="note" rows="3" maxlength="2000" placeholder="e.g. Spoke to the worker, hard hat issued">${v.note || ""}</textarea></label>
          <label class="check"><input type="checkbox" id="ack" ${v.acknowledged ? raw("checked") : ""}>Reviewed</label>
        </div>
      </div>
    </div>
    <div class="dialog-foot">
      <button class="btn danger" id="del">${icon("trash", "ic-sm")}Delete</button>
      <span class="spacer"></span>
      <a class="btn" id="dl" href="#" download>${icon("download", "ic-sm")}Download</a>
      <button class="btn primary" id="save">Save</button>
    </div>`, { onClose: () => { if (location.hash.startsWith(`#/violations/${id}`)) history.replaceState(null, "", "#/violations"); } });

  const show = (i) => {
    cur = i;
    const m = media[i];
    if (!m) return;
    render($("#media", d), m.type === "clip"
      ? html`<video src="${m.src}" controls autoplay muted playsinline></video>`
      : html`<img src="${m.src}" alt="Snapshot">`);
    $$("#strip button", d).forEach((b) => b.classList.toggle("active", Number(b.dataset.m) === i));
    const dl = $("#dl", d);
    dl.href = `${m.src}?download=1`;
  };
  if (media.length) show(0); else $("#dl", d).hidden = true;
  $("#strip", d).addEventListener("click", (e) => { const b = e.target.closest("[data-m]"); if (b) show(Number(b.dataset.m)); });
  d.addEventListener("keydown", (e) => {
    if (e.target.tagName === "TEXTAREA") return;
    if (e.key === "ArrowRight") show(Math.min(media.length - 1, cur + 1));
    if (e.key === "ArrowLeft") show(Math.max(0, cur - 1));
  });
  $("#save", d).addEventListener("click", async () => {
    try {
      await api(`/api/violations/${id}/ack`, { method: "POST", body: { acknowledged: $("#ack", d).checked, note: $("#note", d).value } });
      toast("Saved");
      d.close();
      refreshBadge();
      onChange?.();
    } catch (e) { errorToast(e); }
  });
  $("#del", d).addEventListener("click", async () => {
    d.close();
    if (!(await confirmDialog("Delete violation?", "Its snapshots and clip are deleted too. This cannot be undone.", "Delete"))) return;
    try { await api(`/api/violations/${id}`, { method: "DELETE" }); toast("Deleted"); refreshBadge(); onChange?.(); } catch (e) { errorToast(e); }
  });
}

export async function mount(main, [openId], query) {
  const cams = await api("/api/cameras");
  const f = { camera_id: query.get("camera_id") || "", kind: "", item: "", period: "", status: "", review: "", sort: "started_at:desc", page: 0 };
  const selected = new Set();

  render(main, html`
    <div class="page-head">
      <div><div class="eyebrow">Sheet A-02 · Monitor</div><h1>Violations</h1><p>Every PPE and no-entry violation, with snapshots and clips. Review them to keep a record of what was done.</p></div>
      <div class="actions"><a class="btn" id="export" href="#">${icon("download", "ic-sm")}Export CSV</a></div>
    </div>
    <section class="card">
      <form class="filters" id="filters">
        <label class="field">Camera<select name="camera_id"><option value="">All cameras</option>${cams.map((c) => html`<option value="${c.id}" ${f.camera_id === c.id ? raw("selected") : ""}>${c.name}</option>`)}</select></label>
        <label class="field">Type<select name="kind"><option value="">All types</option><option value="ppe">Missing PPE</option><option value="zone">No-entry zone</option></select></label>
        <label class="field">Missing<select name="item"><option value="">Any item</option><option value="helmet">Hard hat</option><option value="vest">Vest</option><option value="mask">Mask</option></select></label>
        <label class="field">Period<select name="period">${Object.entries(PERIODS).map(([k, v]) => html`<option value="${k}">${v}</option>`)}</select></label>
        <label class="field">Status<select name="status"><option value="">Any</option><option value="ongoing">Ongoing</option><option value="ended">Ended</option></select></label>
        <label class="field">Review<select name="review"><option value="">Any</option><option value="no">Needs review</option><option value="yes">Reviewed</option></select></label>
        <label class="field">Sort<select name="sort">
          <option value="started_at:desc">Newest first</option><option value="started_at:asc">Oldest first</option>
          <option value="duration:desc">Longest first</option><option value="camera_id:asc">Camera</option></select></label>
      </form>
      <div class="bulkbar" id="bulk" hidden><span id="bulk-n"></span><span class="spacer"></span>
        <button class="btn sm" data-bulk="ack">${icon("check", "ic-sm")}Mark reviewed</button>
        <button class="btn sm" data-bulk="unack">Mark unreviewed</button>
        <button class="btn sm danger" data-bulk="delete">${icon("trash", "ic-sm")}Delete</button></div>
      <div class="table-wrap"><table class="table">
        <thead><tr><th style="width:32px"><input type="checkbox" id="all" aria-label="Select all on this page"></th><th>Snapshot</th><th>What</th><th>Camera</th><th>Worker</th><th>Started</th><th class="num">Duration</th><th>Status</th></tr></thead>
        <tbody id="rows"></tbody>
      </table></div>
      <div class="pager"><span id="count"></span><span class="row-tight"><button class="btn sm" id="prev">Previous</button><button class="btn sm" id="next">Next</button></span></div>
    </section>`);

  const params = (forExport = false) => {
    const p = new URLSearchParams();
    if (f.camera_id) p.set("camera_id", f.camera_id);
    if (f.kind) p.set("kind", f.kind);
    if (f.item) p.set("item", f.item);
    if (f.period) p.set("since", String(Date.now() / 1000 - Number(f.period) * 86400));
    if (f.status) p.set("ongoing", String(f.status === "ongoing"));
    if (f.review) p.set("acknowledged", String(f.review === "yes"));
    if (!forExport) {
      const [sort, order] = f.sort.split(":");
      p.set("sort", sort); p.set("order", order);
      p.set("limit", PAGE); p.set("offset", f.page * PAGE);
    }
    return p;
  };

  let items = [];
  const syncBulk = () => {
    $("#bulk", main).hidden = !selected.size;
    $("#bulk-n", main).textContent = `${selected.size} selected`;
    $("#all", main).checked = items.length > 0 && items.every((v) => selected.has(v.id));
  };
  async function load() {
    const data = await api(`/api/violations?${params()}`);
    items = data.items;
    render($("#rows", main), items.length ? html`${items.map((v) => html`
      <tr class="clickable ${selected.has(v.id) ? "selected" : ""}" data-id="${v.id}">
        <td><input type="checkbox" data-sel="${v.id}" ${selected.has(v.id) ? raw("checked") : ""} aria-label="Select"></td>
        <td>${v.thumbnail ? html`<img class="thumb" src="/snapshots/${v.thumbnail}" alt="" loading="lazy">` : html`<div class="thumb"></div>`}</td>
        <td>${missingBadges(v)}${v.clip ? html` <span class="muted" title="Has a video clip">${icon("film", "ic-sm")}</span>` : ""}</td>
        <td>${v.camera_name}</td>
        <td>${workerName(v.worker_key)}</td>
        <td>${fmtTime(v.started_at, { full: true })}</td>
        <td class="num">${fmtDuration(v.duration)}</td>
        <td>${statusPill(v)}</td>
      </tr>`)}` : html`<tr><td colspan="8"><div class="empty">${icon("check")}<h3>No violations</h3><p>Nothing matches these filters.</p></div></td></tr>`);
    const from = data.total ? f.page * PAGE + 1 : 0;
    $("#count", main).textContent = `${from}–${f.page * PAGE + items.length} of ${data.total.toLocaleString()}`;
    $("#prev", main).disabled = f.page === 0;
    $("#next", main).disabled = (f.page + 1) * PAGE >= data.total;
    $("#export", main).href = `/api/violations/export.csv?${params(true)}`;
    syncBulk();
  }

  $("#filters", main).addEventListener("change", (e) => { f[e.target.name] = e.target.value; f.page = 0; selected.clear(); load().catch(errorToast); });
  $("#prev", main).addEventListener("click", () => { f.page--; load().catch(errorToast); });
  $("#next", main).addEventListener("click", () => { f.page++; load().catch(errorToast); });
  $("#rows", main).addEventListener("click", (e) => {
    const cb = e.target.closest("[data-sel]");
    if (cb) {
      cb.checked ? selected.add(cb.dataset.sel) : selected.delete(cb.dataset.sel);
      cb.closest("tr").classList.toggle("selected", cb.checked);
      syncBulk();
      return;
    }
    const tr = e.target.closest("tr[data-id]");
    if (tr) {
      history.replaceState(null, "", `#/violations/${tr.dataset.id}`);
      openViolation(tr.dataset.id, { onChange: load });
    }
  });
  $("#all", main).addEventListener("change", (e) => {
    items.forEach((v) => (e.target.checked ? selected.add(v.id) : selected.delete(v.id)));
    load().catch(errorToast);
  });
  $("#bulk", main).addEventListener("click", async (e) => {
    const b = e.target.closest("[data-bulk]");
    if (!b) return;
    const action = b.dataset.bulk;
    if (action === "delete" && !(await confirmDialog(`Delete ${selected.size} violation(s)?`, "Their snapshots and clips are deleted too. This cannot be undone.", "Delete"))) return;
    try {
      const r = await api("/api/violations/bulk", { method: "POST", body: { ids: [...selected], action } });
      toast(`${r.affected} violation(s) updated`);
      selected.clear();
      refreshBadge();
      load();
    } catch (err) { errorToast(err); }
  });

  await load();
  if (openId) openViolation(openId, { onChange: load });

  let pending = null;
  const off = bus.on((m) => {
    if (m.type === "violation" && m.event !== "updated" && f.page === 0 && !selected.size) {
      clearTimeout(pending);
      pending = setTimeout(() => load().catch(() => {}), 800);
    }
  });
  return () => { off(); clearTimeout(pending); };
}
