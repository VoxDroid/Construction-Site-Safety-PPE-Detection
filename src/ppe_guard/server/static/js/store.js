// Shared state and the live event bus (fed by the WebSocket in main.js).
import { $, api } from "./util.js";

const listeners = new Set();
export const bus = {
  on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  emit(msg) { listeners.forEach((fn) => { try { fn(msg); } catch (e) { console.error(e); } }); },
};

export const state = { settings: null, auth: null };

export async function loadSettings() {
  state.settings = await api("/api/settings");
  $("#site-name").textContent = state.settings.general.site_name;
  $("#sb-site").textContent = state.settings.general.site_name;
  document.title = `PPE Guard · ${state.settings.general.site_name}`;
  return state.settings;
}

/** Refresh the sidebar badge and the status bar from /api/stats. */
export async function refreshBadge() {
  try {
    const s = await api("/api/stats");
    const b = $("#badge-unack");
    b.hidden = !s.unacknowledged;
    b.textContent = s.unacknowledged > 999 ? "999+" : s.unacknowledged;
    $("#sb-cams").textContent = `${s.cameras_running}/${s.cameras_total} cameras live`;
    $("#sb-open").textContent = s.ongoing ? `${s.ongoing} open violation${s.ongoing > 1 ? "s" : ""}` : "No open violations";
    $("#sb-alert-wrap").classList.toggle("on", s.ongoing > 0);
    $("#statusbar").classList.toggle("alarm", s.ongoing > 0);
    $("#sb-rate").textContent = s.compliance_today === null ? "Compliance –" : `Compliance ${(s.compliance_today * 100).toFixed(1)}%`;
    document.body.classList.toggle("has-live", s.cameras_running > 0);
  } catch { /* offline */ }
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
