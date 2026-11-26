// Analytics: trends, compliance rate, when and where violations happen.
import { $, api, errorToast, fmtDuration, fmtNum, fmtPct, html, icon, kpi, rateClass, render } from "../util.js";
import { bus } from "../store.js";
import { hbars, heatmap, rateLine, stackedBars } from "../charts.js";

const SERIES = [
  { key: "helmet", label: "Hard hat", color: "var(--s1)" },
  { key: "vest", label: "Vest", color: "var(--s2)" },
  { key: "mask", label: "Mask", color: "var(--s3)" },
  { key: "zone", label: "No-entry zone", color: "var(--s4)" },
];

const shortDate = (iso) => new Date(`${iso}T00:00`).toLocaleDateString([], { month: "short", day: "numeric" });
const longDate = (iso) => new Date(`${iso}T00:00`).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });

export async function mount(main, _params, query) {
  const cams = await api("/api/cameras");
  const f = { days: Number(query.get("days")) || 7, camera_id: query.get("camera_id") || "" };
  render(main, html`
    <div class="page-head">
      <div><div class="eyebrow">Sheet B-01 · Insight</div><h1>Analytics</h1><p>How compliance is trending, and when and where violations happen.</p></div>
      <div class="actions">
        <div class="seg" id="range">${[7, 30, 90].map((d) => html`<button data-days="${d}">${d} days</button>`)}</div>
        <select id="cam" style="width:auto"><option value="">All cameras</option>${cams.map((c) => html`<option value="${c.id}">${c.name}</option>`)}</select>
        <a class="btn" href="#/reports">${icon("report", "ic-sm")}Report</a>
      </div>
    </div>
    <div class="kpis" id="kpis"></div>
    <div class="grid-2">
      <section class="card"><div class="card-head"><div><h2>${icon("chart")}Violations per day</h2><p>Counted by what was missing. One violation can miss two items.</p></div></div>
        <div class="card-pad"><div class="legend">${SERIES.map((s) => html`<span><i style="background:${s.color}"></i>${s.label}</span>`)}</div><div id="c-daily"></div></div></section>
      <section class="card"><div class="card-head"><div><h2>${icon("shield")}Compliance rate</h2><p>Share of monitored worker-time without an open violation.</p></div></div>
        <div class="card-pad"><div id="c-rate"></div></div></section>
      <section class="card"><div class="card-head"><div><h2>${icon("clock")}When violations start</h2><p>Day of the week by hour of the day, local time.</p></div></div>
        <div class="card-pad"><div id="c-heat"></div></div></section>
      <section class="card"><div class="card-head"><div><h2>${icon("hardhat")}What is missing</h2><p>Violations by item or zone.</p></div></div>
        <div class="card-pad"><div id="c-items"></div></div></section>
      <section class="card"><div class="card-head"><h2>${icon("video")}By camera</h2></div><div id="c-cams"></div></section>
      <section class="card"><div class="card-head"><div><h2>${icon("zone")}No-entry zones</h2><p>Entries per zone.</p></div></div>
        <div class="card-pad"><div id="c-zones"></div></div></section>
    </div>`);

  const cleanups = [];
  const syncControls = () => {
    $("#range", main).querySelectorAll("button").forEach((b) => b.classList.toggle("active", Number(b.dataset.days) === f.days));
    $("#cam", main).value = f.camera_id;
  };

  async function load() {
    syncControls();
    const p = new URLSearchParams({ days: f.days });
    if (f.camera_id) p.set("camera_id", f.camera_id);
    history.replaceState(null, "", `#/analytics?${p}`);
    const a = await api(`/api/analytics?${p}`);
    cleanups.splice(0).forEach((fn) => fn());
    const t = a.totals;
    render($("#kpis", main), html`
      ${kpi({ label: "Violations", value: fmtNum(t.violations), icon: "alert", sub: `${fmtNum(t.ppe)} PPE · ${fmtNum(t.zone)} no-entry` })}
      ${kpi({ label: "Compliance rate", value: fmtPct(t.compliance_rate), tone: rateClass(t.compliance_rate), icon: "shield", sub: `over ${fmtNum(t.worker_hours)} worker-hours` })}
      ${kpi({ label: "Typical duration", value: fmtDuration(t.median_duration), icon: "timer", sub: `median · average ${fmtDuration(t.avg_duration)}` })}
      ${kpi({ label: "Reviewed", value: fmtPct(t.acknowledged_rate, 0), icon: "circle-check", sub: "of violations in this period" })}
      ${kpi({ label: "Peak workers", value: fmtNum(t.peak_workers), icon: "users", sub: "on one camera at once" })}`);

    const labels = a.daily.map((d) => shortDate(d.date));
    cleanups.push(stackedBars($("#c-daily", main), {
      labels,
      series: SERIES.map((s) => ({ ...s, values: a.daily.map((d) => d[s.key]) })),
      tipTitle: (i) => `${longDate(a.daily[i].date)} · ${a.daily[i].violations} violation(s)`,
    }));
    const hasRate = a.daily.some((d) => d.compliance_rate !== null);
    if (hasRate) {
      cleanups.push(rateLine($("#c-rate", main), {
        labels, values: a.daily.map((d) => d.compliance_rate),
        tipTitle: (i) => `${longDate(a.daily[i].date)} · ${a.daily[i].worker_hours} worker-hours`,
      }));
    } else {
      render($("#c-rate", main), html`<div class="empty small">No monitored worker-time yet. Run a camera to start collecting it.</div>`);
    }
    heatmap($("#c-heat", main), a.heatmap);
    hbars($("#c-items", main), SERIES.map((s) => ({ label: s.label, value: a.by_item[s.key] || 0, color: s.color })).filter((r) => r.value > 0 || r.label !== "Mask"));
    render($("#c-cams", main), a.by_camera.length ? html`<div class="table-wrap"><table class="table">
      <thead><tr><th>Camera</th><th class="num">Violations</th><th class="num">Worker-hours</th><th class="num">Compliance</th></tr></thead>
      <tbody>${a.by_camera.map((c) => html`<tr><td><a href="#/cameras/${c.camera_id}">${c.camera_name}</a></td><td class="num">${fmtNum(c.violations)}</td><td class="num">${c.worker_hours}</td><td class="num"><span class="rate ${rateClass(c.compliance_rate)}">${fmtPct(c.compliance_rate)}</span></td></tr>`)}</tbody>
      </table></div>` : html`<div class="empty small">No camera data for this period.</div>`);
    hbars($("#c-zones", main), a.by_zone.map((z) => ({ label: z.zone, value: z.violations, color: "var(--s4)" })));
  }

  $("#range", main).addEventListener("click", (e) => {
    const b = e.target.closest("[data-days]");
    if (b) { f.days = Number(b.dataset.days); load().catch(errorToast); }
  });
  $("#cam", main).addEventListener("change", (e) => { f.camera_id = e.target.value; load().catch(errorToast); });
  await load();
  const off = bus.on((m) => { if (m.type === "theme") load().catch(() => {}); });
  return () => { off(); cleanups.forEach((fn) => fn()); };
}
