// Small dependency-free SVG charts with hover tooltips.
import { esc } from "./util.js";

const NS = "http://www.w3.org/2000/svg";

function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}

function setup(el, height) {
  el.classList.add("chart");
  el.innerHTML = "";
  const w = Math.max(el.clientWidth, 280);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${height}`);
  svg.setAttribute("height", height);
  svg.setAttribute("role", "img");
  el.append(svg);
  const tip = document.createElement("div");
  tip.className = "tooltip";
  tip.hidden = true;
  el.append(tip);
  return { svg, tip, w };
}

function add(parent, tag, attrs = {}, text) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    // CSS variables only work as style properties, not as SVG presentation attributes.
    if ((k === "fill" || k === "stroke") && String(v).startsWith("var(")) n.style.setProperty(k, v);
    else n.setAttribute(k, v);
  }
  if (text !== undefined) n.textContent = text;
  parent.append(n);
  return n;
}

function showTip(el, tip, x, y, htmlStr) {
  tip.innerHTML = htmlStr;
  tip.hidden = false;
  const r = el.getBoundingClientRect();
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = x + 14;
  if (left + tw > r.width) left = x - tw - 14;
  tip.style.left = `${Math.max(0, left)}px`;
  tip.style.top = `${Math.max(0, Math.min(y - th / 2, r.height - th))}px`;
}

// Rect with only the top corners rounded (data end), anchored on the baseline.
function topRounded(x, y, w, h, r) {
  r = Math.min(r, w / 2, h);
  if (h <= 0) return "";
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function observe(el, draw) {
  draw();
  let last = el.clientWidth;
  const ro = new ResizeObserver(() => {
    if (Math.abs(el.clientWidth - last) > 4) { last = el.clientWidth; draw(); }
  });
  ro.observe(el);
  return () => ro.disconnect();
}

/** Stacked columns. series: [{label, color, values[]}] aligned with labels[]. */
export function stackedBars(el, { labels, series, height = 220, tipTitle = (i) => labels[i], unit = "" }) {
  return observe(el, () => {
    const { svg, tip, w } = setup(el, height);
    const m = { t: 10, r: 8, b: 24, l: 34 };
    const iw = w - m.l - m.r, ih = height - m.t - m.b;
    const totals = labels.map((_, i) => series.reduce((s, x) => s + (x.values[i] || 0), 0));
    const tick = Math.max(1, niceMax(Math.max(...totals, 1) / 4));
    const max = tick * Math.max(1, Math.ceil(Math.max(...totals, 1) / tick));
    const y = (v) => m.t + ih - (v / max) * ih;
    const g = add(svg, "g", { class: "grid" });
    for (let v = 0; v <= max + 1e-9; v += tick) {
      add(g, "line", { x1: m.l, x2: w - m.r, y1: y(v), y2: y(v) });
      add(svg, "text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, Math.round(v));
    }
    const step = iw / labels.length;
    const bw = Math.max(2, Math.min(36, step * 0.62));
    const every = Math.ceil(labels.length / Math.max(1, Math.floor(iw / 56)));
    labels.forEach((lab, i) => {
      const x = m.l + step * i + (step - bw) / 2;
      let base = 0;
      const segs = series.map((s) => ({ s, v: s.values[i] || 0 })).filter((d) => d.v > 0);
      segs.forEach((d, j) => {
        const y0 = y(base), y1 = y(base + d.v);
        const gap = j > 0 ? 2 : 0; // 2px surface gap between stacked segments
        const top = j === segs.length - 1;
        const hgt = Math.max(0, y0 - y1 - gap);
        if (top) add(svg, "path", { d: topRounded(x, y1, bw, hgt, 4), fill: d.s.color });
        else add(svg, "rect", { x, y: y1, width: bw, height: hgt, fill: d.s.color });
        base += d.v;
      });
      if (i % every === 0) add(svg, "text", { x: x + bw / 2, y: height - 6, "text-anchor": "middle" }, lab);
      const hit = add(svg, "rect", { class: "hit", x: m.l + step * i, y: m.t, width: step, height: ih });
      hit.addEventListener("mousemove", (e) => {
        const r = el.getBoundingClientRect();
        const rows = series.map((s) => `<div class="r"><span><i style="background:${s.color}"></i>${esc(s.label)}</span><b>${s.values[i] || 0}</b></div>`).join("");
        showTip(el, tip, e.clientX - r.left, e.clientY - r.top, `<b>${esc(tipTitle(i))}</b>${rows}<div class="r muted"><span>Total</span><span>${totals[i]}${unit}</span></div>`);
      });
      hit.addEventListener("mouseleave", () => { tip.hidden = true; });
    });
    add(svg, "line", { class: "baseline", x1: m.l, x2: w - m.r, y1: y(0), y2: y(0) });
  });
}

/** Single line (values may contain null gaps), y in [min, 1] formatted as percent. */
export function rateLine(el, { labels, values, height = 200, color = "var(--s1)", tipTitle = (i) => labels[i], fmt = (v) => `${(v * 100).toFixed(1)}%` }) {
  return observe(el, () => {
    const { svg, tip, w } = setup(el, height);
    const m = { t: 12, r: 10, b: 24, l: 44 };
    const iw = w - m.l - m.r, ih = height - m.t - m.b;
    const present = values.filter((v) => v !== null && v !== undefined);
    const lo = present.length ? Math.max(0, Math.floor((Math.min(...present) - 0.02) * 20) / 20) : 0;
    const min = Math.min(lo, 0.8);
    const y = (v) => m.t + ih - ((v - min) / (1 - min)) * ih;
    const x = (i) => m.l + (labels.length === 1 ? iw / 2 : (iw * i) / (labels.length - 1));
    const g = add(svg, "g", { class: "grid" });
    for (let k = 0; k <= 4; k++) {
      const v = min + ((1 - min) / 4) * k;
      add(g, "line", { x1: m.l, x2: w - m.r, y1: y(v), y2: y(v) });
      add(svg, "text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, `${Math.round(v * 100)}%`);
    }
    const every = Math.ceil(labels.length / Math.max(1, Math.floor(iw / 56)));
    labels.forEach((lab, i) => { if (i % every === 0) add(svg, "text", { x: x(i), y: height - 6, "text-anchor": "middle" }, lab); });
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v === null || v === undefined) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i)},${y(v)}`;
      pen = true;
    });
    add(svg, "path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" });
    values.forEach((v, i) => {
      const prev = values[i - 1], next = values[i + 1];
      const isolated = v !== null && v !== undefined && (prev === null || prev === undefined) && (next === null || next === undefined);
      if (isolated) add(svg, "circle", { cx: x(i), cy: y(v), r: 4, fill: color });
    });
    const hl = add(svg, "line", { class: "hover-line", y1: m.t, y2: m.t + ih, visibility: "hidden" });
    const dot = add(svg, "circle", { r: 4.5, fill: color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
    const hit = add(svg, "rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih });
    hit.addEventListener("mousemove", (e) => {
      const r = svg.getBoundingClientRect();
      const px = ((e.clientX - r.left) / r.width) * w;
      const i = Math.max(0, Math.min(labels.length - 1, Math.round(((px - m.l) / iw) * (labels.length - 1))));
      hl.setAttribute("x1", x(i)); hl.setAttribute("x2", x(i)); hl.setAttribute("visibility", "visible");
      const v = values[i];
      if (v !== null && v !== undefined) {
        dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(v)); dot.setAttribute("visibility", "visible");
      } else dot.setAttribute("visibility", "hidden");
      const er = el.getBoundingClientRect();
      showTip(el, tip, e.clientX - er.left, e.clientY - er.top,
        `<b>${esc(tipTitle(i))}</b>${v === null || v === undefined ? '<span class="muted">No workers monitored</span>' : `<div class="r"><span>Compliance</span><b>${fmt(v)}</b></div>`}`);
    });
    hit.addEventListener("mouseleave", () => { tip.hidden = true; hl.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
  });
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Weekday x hour heatmap (sequential single hue). */
export function heatmap(el, matrix) {
  const max = Math.max(1, ...matrix.flat());
  const step = (v) => (v === 0 ? 0 : Math.min(7, 1 + Math.floor((v / max) * 6.999)));
  let h = '<div class="heat"><span></span>';
  for (let hr = 0; hr < 24; hr++) h += `<span class="hl">${hr % 3 === 0 ? hr : ""}</span>`;
  matrix.forEach((row, d) => {
    h += `<span class="lbl">${DAYS[d]}</span>`;
    row.forEach((v, hr) => {
      h += `<span class="c" style="background:var(--seq-${step(v)})" data-tip="${DAYS[d]} ${String(hr).padStart(2, "0")}:00–${String(hr + 1).padStart(2, "0")}:00|${v}"></span>`;
    });
  });
  h += "</div>";
  el.classList.add("chart");
  el.innerHTML = h + '<div class="tooltip" hidden></div>';
  const tip = el.querySelector(".tooltip");
  el.querySelectorAll(".c").forEach((c) => {
    c.addEventListener("mousemove", (e) => {
      const [title, v] = c.dataset.tip.split("|");
      const r = el.getBoundingClientRect();
      showTip(el, tip, e.clientX - r.left, e.clientY - r.top, `<b>${title}</b><div class="r"><span>Violations</span><b>${v}</b></div>`);
    });
    c.addEventListener("mouseleave", () => { tip.hidden = true; });
  });
}

/** Horizontal bars as HTML rows: [{label, value, display, color}] */
export function hbars(el, rows, { color = "var(--s1)" } = {}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  el.innerHTML = rows.length
    ? `<div class="hbar">${rows.map((r) => `<span>${esc(r.label)}</span><span class="track"><span class="fill" style="display:block;width:${(r.value / max) * 100}%;background:${r.color || color}"></span></span><span class="v">${esc(r.display ?? r.value)}</span>`).join("")}</div>`
    : '<p class="muted small">No data for this period.</p>';
}
