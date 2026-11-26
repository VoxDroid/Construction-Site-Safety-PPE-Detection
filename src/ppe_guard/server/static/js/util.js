// Shared helpers: API calls, escaping, formatting, toasts and dialogs.

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (opts.body !== undefined && !(opts.body instanceof FormData)) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  if (r.status === 401 && !path.startsWith("/api/auth/")) {
    location.href = "/login?next=" + encodeURIComponent(location.pathname + location.hash);
    throw new ApiError(401, "login required");
  }
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail
        : Array.isArray(j.detail) ? j.detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ")
        : msg;
    } catch { /* not JSON */ }
    throw new ApiError(r.status, msg);
  }
  if (r.status === 204) return null;
  const ct = r.headers.get("content-type") || "";
  return ct.includes("application/json") ? r.json() : r.text();
}

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

// Tagged template that escapes interpolations unless wrapped in raw().
export function html(strings, ...vals) {
  let out = strings[0];
  vals.forEach((v, i) => {
    if (Array.isArray(v)) out += v.map((x) => (x && x.__raw !== undefined ? x.__raw : esc(x))).join("");
    else if (v && v.__raw !== undefined) out += v.__raw;
    else if (v === false || v === null || v === undefined) out += "";
    else out += esc(v);
    out += strings[i + 1];
  });
  return { __raw: out };
}
export const raw = (s) => ({ __raw: String(s) });
export const render = (el, tpl) => { el.innerHTML = tpl.__raw ?? tpl; };
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const icon = (name, cls = "") => raw(`<svg class="ic ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`);

export const ITEM_LABEL = { helmet: "Hard hat", vest: "Vest", mask: "Mask" };
export const STATE_LABEL = {
  compliant: "Compliant", pending: "Checking", violation: "Violation", unknown: "Not visible", unmonitored: "Outside zones",
};

export function fmtTime(ts, opts = {}) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  if (sameDay && !opts.full) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", ...(opts.seconds ? { second: "2-digit" } : {}) });
}
export function fmtDuration(s) {
  if (s === null || s === undefined) return "–";
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}
export const fmtPct = (v, d = 1) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(d)}%`);
export function fmtBytes(b) {
  if (!b) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(b) / Math.log(1024)));
  return `${(b / 1024 ** i).toFixed(i ? 1 : 0)} ${u[i]}`;
}
export const fmtNum = (n) => (n ?? 0).toLocaleString();
export function rateClass(v) {
  if (v === null || v === undefined) return "";
  return v >= 0.95 ? "good" : v >= 0.85 ? "warn" : "bad";
}
export function isoDate(d) {
  const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
  return z.toISOString().slice(0, 10);
}

export function missingBadges(v) {
  if (v.kind === "zone") return html`<span class="pill warn">No-entry zone${v.zone_name ? ": " + v.zone_name : ""}</span>`;
  return html`${(v.missing || []).map((m) => html`<span class="pill bad">${ITEM_LABEL[m] || m}</span> `)}`;
}
export function violationTitle(v) {
  if (v.kind === "zone") return `Entered ${v.zone_name || "restricted zone"}`;
  return "Missing " + (v.missing || []).map((m) => (ITEM_LABEL[m] || m).toLowerCase()).join(" and ");
}
export const workerName = (key) => (key?.startsWith("u") ? "Unmatched head" : `Worker #${key?.slice(1)}`);

export function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "bad" ? 6000 : 3200);
}
export const errorToast = (e) => toast(e.message || String(e), "bad");

const dialog = () => $("#dialog");
export function openDialog(tpl, { narrow = false, onClose } = {}) {
  const d = dialog();
  d.className = `dialog${narrow ? " narrow" : ""}`;
  render(d, tpl);
  d.onclose = () => { d.innerHTML = ""; onClose?.(); };
  $$("[data-close]", d).forEach((b) => b.addEventListener("click", () => d.close()));
  if (!d.open) d.showModal();
  return d;
}
export const closeDialog = () => dialog().open && dialog().close();

export function confirmDialog(title, body, okLabel = "Confirm", danger = true) {
  return new Promise((resolve) => {
    let ok = false;
    const d = openDialog(html`
      <div class="dialog-head"><h2>${title}</h2></div>
      <div class="dialog-body"><p class="text-2">${body}</p></div>
      <div class="dialog-foot">
        <button class="btn" data-close>Cancel</button>
        <button class="btn ${danger ? "danger" : "primary"}" id="confirm-ok">${okLabel}</button>
      </div>`, { narrow: true, onClose: () => resolve(ok) });
    $("#confirm-ok", d).addEventListener("click", () => { ok = true; d.close(); });
  });
}

// Read a nested-name form ("detection.confidence") into an object with typed values.
export function readForm(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    let v;
    if (el.type === "checkbox") v = el.checked;
    else if (el.type === "number" || el.dataset.type === "number") v = el.value === "" ? null : Number(el.value);
    else v = el.value === "" && el.dataset.nullable !== undefined ? null : el.value;
    const parts = el.name.split(".");
    let o = out;
    parts.slice(0, -1).forEach((p) => { o = o[p] ??= {}; });
    o[parts.at(-1)] = v;
  }
  return out;
}
export function fillForm(form, data) {
  for (const el of form.elements) {
    if (!el.name) continue;
    const v = el.name.split(".").reduce((o, k) => (o == null ? undefined : o[k]), data);
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v ?? "";
  }
}
export function deepMerge(a, b) {
  const out = structuredClone(a);
  for (const [k, v] of Object.entries(b)) {
    out[k] = v && typeof v === "object" && !Array.isArray(v) && out[k] && typeof out[k] === "object" ? deepMerge(out[k], v) : v;
  }
  return out;
}
