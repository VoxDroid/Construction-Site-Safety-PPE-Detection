// Reports: a printable daily or weekly summary (Print → Save as PDF).
import { $, api, errorToast, fmtDuration, fmtNum, fmtPct, fmtTime, html, icon, isoDate, ITEM_LABEL, kpi, render, violationTitle, workerName } from "../util.js";
import { state } from "../store.js";
import { stackedBars } from "../charts.js";

const SERIES = [
  { key: "helmet", label: "Hard hat", color: "var(--s1)" },
  { key: "vest", label: "Vest", color: "var(--s2)" },
  { key: "mask", label: "Mask", color: "var(--s3)" },
  { key: "zone", label: "No-entry zone", color: "var(--s4)" },
];

function mondayOf(d) {
  const x = new Date(d);
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return x;
}
const longDate = (iso) => new Date(`${iso}T00:00`).toLocaleDateString([], { weekday: "long", year: "numeric", month: "long", day: "numeric" });

function reportDoc(r, f, cams) {
  const t = r.totals;
  const site = state.settings?.general.site_name || "Site";
  const cam = f.camera_id ? cams.find((c) => c.id === f.camera_id)?.name : "All cameras";
  const end = new Date(`${r.start}T00:00`);
  end.setDate(end.getDate() + r.days - 1);
  const period = r.days === 1 ? longDate(r.start) : `${longDate(r.start)} – ${longDate(isoDate(end))}`;
  const req = Object.entries(r.settings.required_ppe).filter(([, v]) => v).map(([k]) => ITEM_LABEL[k]).join(", ") || "none";
  const items = r.violations.items;
  const evidence = items.filter((v) => v.thumbnail).slice(0, 12);
  return html`
    <article class="report card" id="report">
      <header>
        <div>
          <div class="muted small">PPE compliance report</div>
          <h1>${site}</h1>
          <div class="text-2">${period} · ${cam}</div>
        </div>
        <div style="text-align:right" class="small text-2">
          <div class="row" style="justify-content:flex-end;gap:6px"><img src="/static/favicon.svg" width="20" height="20" alt="">PPE Guard</div>
          Generated ${fmtTime(r.generated_at, { full: true })}<br>Required PPE: ${req}
        </div>
      </header>

      <div class="kpis">
        ${kpi({ label: "Violations", value: fmtNum(t.violations), icon: "alert", sub: `${fmtNum(t.ppe)} PPE · ${fmtNum(t.zone)} no-entry` })}
        ${kpi({ label: "Compliance rate", value: fmtPct(t.compliance_rate), icon: "shield", sub: `${fmtNum(t.worker_hours)} worker-hours monitored` })}
        ${kpi({ label: "Median duration", value: fmtDuration(t.median_duration), icon: "timer", sub: `average ${fmtDuration(t.avg_duration)}` })}
        ${kpi({ label: "Reviewed", value: fmtPct(t.acknowledged_rate, 0), icon: "circle-check", sub: "of violations" })}
      </div>

      ${r.days > 1 ? html`<h2>Violations per day</h2><div class="legend">${SERIES.map((s) => html`<span><i style="background:${s.color}"></i>${s.label}</span>`)}</div><div id="r-daily"></div>` : ""}

      <h2>By camera</h2>
      ${r.by_camera.length ? html`<table><thead><tr><th>Camera</th><th class="num">Violations</th><th class="num">Worker-hours</th><th class="num">Compliance</th></tr></thead>
        <tbody>${r.by_camera.map((c) => html`<tr><td>${c.camera_name}</td><td class="num">${c.violations}</td><td class="num">${c.worker_hours}</td><td class="num">${fmtPct(c.compliance_rate)}</td></tr>`)}</tbody></table>`
        : html`<p class="muted">No camera activity in this period.</p>`}

      <h2>What was missing</h2>
      <table><thead><tr>${SERIES.map((s) => html`<th class="num">${s.label}</th>`)}</tr></thead>
        <tbody><tr>${SERIES.map((s) => html`<td class="num">${r.by_item[s.key] || 0}</td>`)}</tr></tbody></table>

      ${evidence.length ? html`<h2>Evidence</h2><div class="thumbs">${evidence.map((v) => html`
        <figure><img src="/snapshots/${v.thumbnail}" alt=""><figcaption>${fmtTime(v.started_at, { full: true })} · ${v.camera_name}<br>${violationTitle(v)}</figcaption></figure>`)}</div>` : ""}

      <h2>All violations (${items.length}${r.violations.total > items.length ? ` of ${r.violations.total}` : ""})</h2>
      ${items.length ? html`<table><thead><tr><th>Time</th><th>Camera</th><th>Worker</th><th>What</th><th class="num">Duration</th><th>Reviewed</th><th>Note</th></tr></thead>
        <tbody>${items.map((v) => html`<tr><td>${fmtTime(v.started_at, { full: true })}</td><td>${v.camera_name}</td><td>${workerName(v.worker_key)}</td><td>${violationTitle(v)}</td><td class="num">${fmtDuration(v.duration)}</td><td>${v.acknowledged ? "Yes" : "No"}</td><td>${v.note || ""}</td></tr>`)}</tbody></table>`
        : html`<p class="muted">No violations in this period.</p>`}

      <div class="sig"><div>Reviewed by</div><div>Date and signature</div></div>
      <footer>Detections are made by a computer vision model and can be wrong. Snapshots and clips are the evidence; check them before acting.</footer>
    </article>`;
}

export async function mount(main) {
  const cams = await api("/api/cameras");
  const f = { period: "day", date: isoDate(new Date()), camera_id: "" };
  render(main, html`
    <div class="page-head no-print">
      <div><div class="eyebrow">Sheet B-02 · Insight</div><h1>Reports</h1><p>A summary for a day or a week, ready to print or save as PDF.</p></div>
      <div class="actions">
        <div class="seg" id="period"><button data-p="day">Day</button><button data-p="week">Week</button></div>
        <input type="date" id="date" style="width:auto" value="${f.date}" max="${f.date}">
        <select id="cam" style="width:auto"><option value="">All cameras</option>${cams.map((c) => html`<option value="${c.id}">${c.name}</option>`)}</select>
        <button class="btn primary" id="print">${icon("print", "ic-sm")}Print / PDF</button>
      </div>
    </div>
    <div id="out"><div class="skeleton" style="height:400px"></div></div>`);

  let cleanup = null;
  async function load() {
    $("#period", main).querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.p === f.period));
    const start = f.period === "week" ? isoDate(mondayOf(new Date(`${f.date}T00:00`))) : f.date;
    const p = new URLSearchParams({ start, days: f.period === "week" ? 7 : 1 });
    if (f.camera_id) p.set("camera_id", f.camera_id);
    const r = await api(`/api/report?${p}`);
    cleanup?.();
    render($("#out", main), reportDoc(r, f, cams));
    if (r.days > 1) {
      cleanup = stackedBars($("#r-daily", main), {
        labels: r.daily.map((d) => new Date(`${d.date}T00:00`).toLocaleDateString([], { weekday: "short", day: "numeric" })),
        series: SERIES.map((s) => ({ ...s, values: r.daily.map((d) => d[s.key]) })),
        height: 180,
      });
    } else cleanup = null;
  }
  $("#period", main).addEventListener("click", (e) => { const b = e.target.closest("[data-p]"); if (b) { f.period = b.dataset.p; load().catch(errorToast); } });
  $("#date", main).addEventListener("change", (e) => { if (e.target.value) { f.date = e.target.value; load().catch(errorToast); } });
  $("#cam", main).addEventListener("change", (e) => { f.camera_id = e.target.value; load().catch(errorToast); });
  $("#print", main).addEventListener("click", () => window.print());
  await load();
  return () => cleanup?.();
}

