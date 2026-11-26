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
  document.title = `PPE Guard · ${state.settings.general.site_name}`;
  return state.settings;
}

export async function refreshBadge() {
  try {
    const s = await api("/api/stats");
    const b = $("#badge-unack");
    b.hidden = !s.unacknowledged;
    b.textContent = s.unacknowledged > 999 ? "999+" : s.unacknowledged;
  } catch { /* offline */ }
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
