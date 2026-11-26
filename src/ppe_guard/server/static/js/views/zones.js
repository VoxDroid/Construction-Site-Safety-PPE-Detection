// Zone editor: draw PPE zones and no-entry zones on a camera image.
import { $, $$, api, errorToast, html, icon, ITEM_LABEL, raw, render, toast } from "../util.js";

const W = 1000; // SVG units across the image; height follows the aspect ratio

function slug(name, taken) {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 32) || "zone";
  let id = base, n = 2;
  while (taken.has(id)) id = `${base}-${n++}`;
  return id;
}

export async function mount(main, [id]) {
  const cam = await api(`/api/cameras/${id}`);
  let zones = structuredClone(cam.zones);
  let selected = zones.length ? 0 : -1;
  let drawing = null; // {kind, points[]}
  let dirty = false;
  let H = 562;

  render(main, html`
    <a class="crumb" href="#/cameras/${cam.id}">${icon("back", "ic-sm")}${cam.name}</a>
    <div class="page-head">
      <div><div class="eyebrow">Detail · Zone plan</div><h1>Zones · ${cam.name}</h1>
        <p>PPE is only checked inside <b>PPE zones</b> (or everywhere if there are none). Anyone who stays in a <b>no-entry zone</b> raises a violation. A worker's position is where their feet are.</p></div>
      <div class="actions">
        <button class="btn" id="add-ppe">${icon("plus", "ic-sm")}PPE zone</button>
        <button class="btn" id="add-restricted">${icon("plus", "ic-sm")}No-entry zone</button>
        <button class="btn primary" id="save" disabled>${icon("check", "ic-sm")}Save zones</button>
      </div>
    </div>
    <div class="zone-editor">
      <div class="stack">
        <div class="zone-canvas" id="canvas">
          <img id="still" alt="Camera image for drawing zones">
          <svg id="svg" preserveAspectRatio="none"></svg>
        </div>
        <div class="row small muted" id="hint">Loading a frame from the camera…</div>
      </div>
      <aside class="stack">
        <section class="card"><div class="card-head"><h2>${icon("layers")}Zones</h2><span class="muted small" id="zcount"></span></div>
          <div class="card-pad"><div class="zone-list" id="list"></div></div></section>
        <section class="card" id="editor-card"><div class="card-head"><h2>${icon("edit")}Selected zone</h2></div><div class="card-pad" id="editor"></div></section>
      </aside>
    </div>`);

  const svg = $("#svg", main);
  const canvas = $("#canvas", main);
  const img = $("#still", main);
  img.onload = () => {
    H = Math.round((W * img.naturalHeight) / img.naturalWidth);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    setHint();
    drawSvg();
  };
  img.onerror = () => { $("#hint", main).textContent = "Could not read a frame from this camera. Check the source in Settings."; };
  img.src = `/api/cameras/${cam.id}/still.jpg?ts=${Date.now()}`;

  const setDirty = (v = true) => { dirty = v; $("#save", main).disabled = !dirty; };
  const setHint = () => {
    $("#hint", main).textContent = drawing
      ? `Click to add points (${drawing.points.length} so far). Click the first point or press Enter to finish, Backspace to undo, Esc to cancel.`
      : "Select a zone to drag its corners. Double-click an edge to add a corner, right-click a corner to remove it.";
  };

  function toPoint(e) {
    const r = svg.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  }
  const P = ([x, y]) => `${(x * W).toFixed(1)},${(y * H).toFixed(1)}`;

  function drawSvg() {
    let s = "";
    zones.forEach((z, i) => {
      const cls = `${z.kind} ${i === selected ? "selected" : ""} ${z.enabled ? "" : "disabled"}`;
      s += `<polygon class="${cls}" data-i="${i}" points="${z.points.map(P).join(" ")}"></polygon>`;
      const [cx, cy] = z.points.reduce(([a, b], [x, y]) => [a + x / z.points.length, b + y / z.points.length], [0, 0]);
      s += `<text x="${cx * W}" y="${cy * H}" text-anchor="middle">${z.name.replace(/</g, "&lt;")}${z.kind === "restricted" ? " (no entry)" : ""}</text>`;
    });
    if (selected >= 0 && zones[selected] && !drawing) {
      zones[selected].points.forEach((p, j) => {
        s += `<circle class="handle" data-j="${j}" cx="${p[0] * W}" cy="${p[1] * H}" r="${W / 120}"></circle>`;
      });
    }
    if (drawing) {
      if (drawing.points.length) s += `<polyline points="${[...drawing.points, drawing.cursor || drawing.points.at(-1)].map(P).join(" ")}"></polyline>`;
      drawing.points.forEach((p, j) => { s += `<circle class="handle" data-first="${j === 0}" cx="${p[0] * W}" cy="${p[1] * H}" r="${W / (j === 0 ? 90 : 140)}"></circle>`; });
    }
    svg.innerHTML = s;
    canvas.classList.toggle("drawing", !!drawing);
  }

  function drawList() {
    $("#zcount", main).textContent = `${zones.length}`;
    render($("#list", main), zones.length ? html`${zones.map((z, i) => html`
      <div class="zone-item ${i === selected ? "selected" : ""}" data-i="${i}">
        <span class="swatch ${z.kind}"></span>
        <span style="flex:1"><b>${z.name}</b><br><span class="muted small">${z.kind === "restricted" ? "No entry" : z.required_ppe ? Object.entries(z.required_ppe).filter(([, v]) => v).map(([k]) => ITEM_LABEL[k]).join(", ") || "Nothing required" : "Global PPE rule"}${z.enabled ? "" : " · disabled"}</span></span>
      </div>`)}` : html`<p class="muted small">No zones: PPE is checked on the whole image.</p>`);
  }

  function drawEditor() {
    const z = zones[selected];
    $("#editor-card", main).hidden = !z;
    if (!z) return;
    const custom = !!z.required_ppe;
    render($("#editor", main), html`
      <div class="stack">
        <label class="field">Name<input id="z-name" value="${z.name}" maxlength="60"></label>
        <label class="field">Type<select id="z-kind">
          <option value="ppe" ${z.kind === "ppe" ? raw("selected") : ""}>PPE zone (check equipment inside)</option>
          <option value="restricted" ${z.kind === "restricted" ? raw("selected") : ""}>No-entry zone (nobody allowed)</option>
        </select></label>
        ${z.kind === "ppe" ? html`
          <label class="check"><input type="checkbox" id="z-custom" ${custom ? raw("checked") : ""}><span>Custom PPE for this zone<span class="hint">Otherwise the global rule from Settings applies.</span></span></label>
          <div class="row" id="z-items" ${custom ? "" : raw("hidden")}>${["helmet", "vest", "mask"].map((k) => html`
            <label class="check"><input type="checkbox" data-item="${k}" ${z.required_ppe?.[k] ? raw("checked") : ""}>${ITEM_LABEL[k]}</label>`)}</div>` : ""}
        <label class="check"><input type="checkbox" id="z-enabled" ${z.enabled ? raw("checked") : ""}>Enabled</label>
        <div class="row"><span class="muted small">${z.points.length} corners · id <code>${z.id}</code></span><span class="spacer"></span>
          <button class="btn sm danger" id="z-delete">${icon("trash", "ic-sm")}Delete</button></div>
      </div>`);
    const ed = $("#editor", main);
    $("#z-name", ed).addEventListener("input", (e) => { z.name = e.target.value || "Zone"; setDirty(); drawSvg(); drawList(); });
    $("#z-kind", ed).addEventListener("change", (e) => { z.kind = e.target.value; if (z.kind === "restricted") z.required_ppe = null; setDirty(); refresh(); });
    $("#z-enabled", ed).addEventListener("change", (e) => { z.enabled = e.target.checked; setDirty(); drawSvg(); drawList(); });
    $("#z-custom", ed)?.addEventListener("change", (e) => {
      z.required_ppe = e.target.checked ? { helmet: true, vest: true, mask: false } : null;
      setDirty(); refresh();
    });
    $$("[data-item]", ed).forEach((cb) => cb.addEventListener("change", () => {
      z.required_ppe[cb.dataset.item] = cb.checked; setDirty(); drawList();
    }));
    $("#z-delete", ed).addEventListener("click", () => {
      zones.splice(selected, 1);
      selected = Math.min(selected, zones.length - 1);
      setDirty(); refresh();
    });
  }
  const refresh = () => { drawSvg(); drawList(); drawEditor(); setHint(); };

  function startDrawing(kind) {
    drawing = { kind, points: [] };
    selected = -1;
    refresh();
  }
  function finishDrawing() {
    if (!drawing || drawing.points.length < 3) return;
    const taken = new Set(zones.map((z) => z.id));
    const n = zones.filter((z) => z.kind === drawing.kind).length + 1;
    const name = drawing.kind === "ppe" ? `PPE zone ${n}` : `No-entry ${n}`;
    zones.push({ id: slug(name, taken), name, kind: drawing.kind, points: drawing.points, required_ppe: null, enabled: true });
    selected = zones.length - 1;
    drawing = null;
    setDirty();
    refresh();
  }

  $("#add-ppe", main).addEventListener("click", () => startDrawing("ppe"));
  $("#add-restricted", main).addEventListener("click", () => startDrawing("restricted"));
  $("#list", main).addEventListener("click", (e) => {
    const it = e.target.closest("[data-i]");
    if (it) { selected = Number(it.dataset.i); drawing = null; refresh(); }
  });

  // Pointer interaction: draw points, select zones, drag corners or whole zones.
  let drag = null;
  svg.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    if (drawing) {
      const first = e.target.closest("circle[data-first='true']");
      if (first && drawing.points.length >= 3) { finishDrawing(); return; }
      drawing.points.push(toPoint(e));
      drawSvg(); setHint();
      return;
    }
    const handle = e.target.closest("circle.handle");
    if (handle && selected >= 0) {
      drag = { type: "corner", j: Number(handle.dataset.j) };
    } else {
      const poly = e.target.closest("polygon");
      if (poly) {
        selected = Number(poly.dataset.i);
        drag = { type: "move", start: toPoint(e), orig: structuredClone(zones[selected].points) };
        refresh();
      } else { selected = -1; refresh(); return; }
    }
    svg.setPointerCapture(e.pointerId);
  });
  svg.addEventListener("pointermove", (e) => {
    if (drawing) { drawing.cursor = toPoint(e); drawSvg(); return; }
    if (!drag || selected < 0) return;
    const p = toPoint(e);
    const z = zones[selected];
    if (drag.type === "corner") z.points[drag.j] = p;
    else {
      const dx = p[0] - drag.start[0], dy = p[1] - drag.start[1];
      z.points = drag.orig.map(([x, y]) => [Math.min(1, Math.max(0, x + dx)), Math.min(1, Math.max(0, y + dy))]);
    }
    setDirty();
    drawSvg();
  });
  svg.addEventListener("pointerup", () => { if (drag) { drag = null; drawList(); } });
  svg.addEventListener("dblclick", (e) => {
    if (drawing || selected < 0) return;
    // Insert a corner on the nearest edge of the selected zone.
    const p = toPoint(e);
    const pts = zones[selected].points;
    let best = 0, bestD = Infinity;
    pts.forEach((a, i) => {
      const b = pts[(i + 1) % pts.length];
      const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
      const d = (mx - p[0]) ** 2 + (my - p[1]) ** 2;
      if (d < bestD) { bestD = d; best = i; }
    });
    pts.splice(best + 1, 0, p);
    setDirty(); refresh();
  });
  svg.addEventListener("contextmenu", (e) => {
    const handle = e.target.closest("circle.handle");
    if (!handle || drawing || selected < 0) return;
    e.preventDefault();
    const pts = zones[selected].points;
    if (pts.length <= 3) { toast("A zone needs at least 3 corners"); return; }
    pts.splice(Number(handle.dataset.j), 1);
    setDirty(); refresh();
  });

  const onKey = (e) => {
    if (!drawing || ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
    if (e.key === "Enter") finishDrawing();
    else if (e.key === "Escape") { drawing = null; refresh(); }
    else if (e.key === "Backspace") { drawing.points.pop(); drawSvg(); setHint(); e.preventDefault(); }
  };
  document.addEventListener("keydown", onKey);

  $("#save", main).addEventListener("click", async () => {
    try {
      const saved = await api(`/api/cameras/${cam.id}/zones`, { method: "PUT", body: zones });
      zones = structuredClone(saved.zones);
      setDirty(false);
      toast("Zones saved. Running cameras use them within a second.");
      refresh();
    } catch (err) { errorToast(err); }
  });

  const beforeUnload = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", beforeUnload);
  refresh();

  return () => {
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("beforeunload", beforeUnload);
    if (dirty) toast("Unsaved zone changes were discarded");
  };
}
