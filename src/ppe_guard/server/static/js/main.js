// App shell: routing, live WebSocket, theme and the sidebar.
import { $, $$, api, errorToast } from "./util.js";
import { bus, loadSettings, refreshBadge, state } from "./store.js";
import * as live from "./views/live.js";
import * as camera from "./views/camera.js";
import * as zones from "./views/zones.js";
import * as violations from "./views/violations.js";
import * as analytics from "./views/analytics.js";
import * as reports from "./views/reports.js";
import * as analyze from "./views/analyze.js";
import * as settings from "./views/settings.js";

// ---- live events -------------------------------------------------------------------------

function setConn(ok, text) {
  for (const el of [$("#conn"), $("#conn-m")]) {
    el.classList.toggle("ok", ok);
    el.classList.toggle("bad", !ok);
    const span = el.querySelector("span");
    if (span) span.textContent = text;
  }
}

let retry = 1000;
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { retry = 1000; setConn(true, "live"); bus.emit({ type: "connected" }); };
  ws.onmessage = (e) => { try { bus.emit(JSON.parse(e.data)); } catch { /* ignore */ } };
  ws.onclose = (e) => {
    setConn(false, "offline");
    if (e.code === 4401) { location.href = "/login"; return; }
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 15000);
  };
}

// ---- theme -----------------------------------------------------------------------------------

function currentTheme() {
  return document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function paintThemeIcon() {
  $("#theme-btn use").setAttribute("href", currentTheme() === "dark" ? "#i-sun" : "#i-moon");
}
$("#theme-btn").addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("ppe-theme", next); } catch { /* private mode */ }
  paintThemeIcon();
  bus.emit({ type: "theme" });
});

// ---- mobile menu -----------------------------------------------------------------------------

const closeMenu = () => { $("#sidebar").classList.remove("open"); $("#scrim").hidden = true; };
$("#menu-btn").addEventListener("click", () => { $("#sidebar").classList.add("open"); $("#scrim").hidden = false; });
$("#scrim").addEventListener("click", closeMenu);

$("#logout-btn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  location.href = "/login";
});

// ---- router ----------------------------------------------------------------------------------

const routes = [
  [/^#\/live$/, live, "live"],
  [/^#\/cameras\/([\w-]+)\/zones$/, zones, "live"],
  [/^#\/cameras\/([\w-]+)$/, camera, "live"],
  [/^#\/violations(?:\/(\w+))?$/, violations, "violations"],
  [/^#\/analytics$/, analytics, "analytics"],
  [/^#\/reports$/, reports, "reports"],
  [/^#\/analyze(?:\/(\w+))?$/, analyze, "analyze"],
  [/^#\/settings(?:\/([\w-]+))?$/, settings, "settings"],
];

let unmount = null;
let navToken = 0;
async function route() {
  const hash = location.hash || "#/live";
  const [path, query] = hash.split("?");
  const match = routes.find(([re]) => re.test(path));
  if (!match) { location.hash = "#/live"; return; }
  const [re, view, nav] = match;
  const params = path.match(re).slice(1);
  $$(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
  closeMenu();
  const token = ++navToken;
  if (unmount) { try { unmount(); } catch (e) { console.error(e); } unmount = null; }
  // A fresh container per page, so listeners from the previous page go away with it.
  const main = document.createElement("div");
  $("#main").replaceChildren(main);
  window.scrollTo(0, 0);
  try {
    const u = await view.mount(main, params, new URLSearchParams(query || ""));
    if (token !== navToken) { u?.(); return; }
    unmount = u || null;
  } catch (e) {
    if (token === navToken) {
      main.innerHTML = `<div class="empty"><h3>Could not load this page</h3><p>${String(e.message || e).replace(/</g, "&lt;")}</p></div>`;
    }
    console.error(e);
  }
  $("#main").focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);

// ---- boot ----------------------------------------------------------------------------------------

(async function boot() {
  paintThemeIcon();
  try {
    state.auth = await api("/api/auth/status");
    $("#logout-btn").hidden = !state.auth.enabled;
    await loadSettings();
  } catch (e) { errorToast(e); }
  connect();
  refreshBadge();
  setInterval(refreshBadge, 15000);
  bus.on((m) => { if (m.type === "violation" && m.event === "opened") refreshBadge(); });
  route();
})();

