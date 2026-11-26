// PPE Guard browser demo: YOLO26s (ONNX) + the same per-worker rules as the Python package.
/* global ort */

const MODEL_URL = "ppe-yolo26s.onnx";
const SIZE = 640;
const NAMES = ["Hardhat", "Mask", "NO-Hardhat", "NO-Mask", "NO-Safety Vest", "Person", "Safety Cone", "Safety Vest", "machinery", "vehicle"];
// class -> [kind, item]; mirrors ppe_guard.compliance.classify
const CLASS = {
  Hardhat: ["worn", "helmet"], Mask: ["worn", "mask"], "Safety Vest": ["worn", "vest"],
  "NO-Hardhat": ["missing", "helmet"], "NO-Mask": ["missing", "mask"], "NO-Safety Vest": ["missing", "vest"],
  Person: ["person", null],
};
const REGIONS = { helmet: [-0.15, 0.45], mask: [-0.05, 0.5], vest: [0.1, 0.9] };
const ITEMS = ["helmet", "vest", "mask"];
const LABEL = { helmet: "Hard hat", vest: "Vest", mask: "Mask" };
const COLORS = { violation: "#ff5547", compliant: "#3fdc8a", unknown: "#a9c3e6" };

const $ = (id) => document.getElementById(id);
const canvas = $("canvas");
const ctx = canvas.getContext("2d");
let session = null;
let source = null; // HTMLImageElement or HTMLVideoElement
let lastDetections = [];
let streaming = false;

ort.env.wasm.wasmPaths = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.20.1/dist/";
ort.env.wasm.numThreads = self.crossOriginIsolated ? Math.min(4, navigator.hardwareConcurrency || 1) : 1;

// ---- model -------------------------------------------------------------------------------

async function fetchWithProgress(url, onProgress) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`could not download the model (HTTP ${r.status})`);
  const total = Number(r.headers.get("content-length")) || 38e6;
  const reader = r.body.getReader();
  const chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    got += value.length;
    onProgress(Math.min(got / total, 1));
  }
  const out = new Uint8Array(got);
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}

async function loadModel() {
  const bytes = await fetchWithProgress(MODEL_URL, (p) => { $("bar").style.width = `${p * 100}%`; });
  $("msg-text").textContent = "Preparing the model…";
  session = await ort.InferenceSession.create(bytes, { executionProviders: ["wasm"], graphOptimizationLevel: "all" });
  $("cam").disabled = false;
  showMessage("Pick a sample or choose a photo.");
}

// ---- inference ------------------------------------------------------------------------------

function preprocess(src) {
  const w = src.videoWidth || src.naturalWidth;
  const h = src.videoHeight || src.naturalHeight;
  const scale = Math.min(SIZE / w, SIZE / h);
  const nw = Math.round(w * scale), nh = Math.round(h * scale);
  const px = Math.floor((SIZE - nw) / 2), py = Math.floor((SIZE - nh) / 2);
  const off = preprocess.canvas || (preprocess.canvas = new OffscreenCanvas(SIZE, SIZE));
  const octx = off.getContext("2d", { willReadFrequently: true });
  octx.fillStyle = "rgb(114,114,114)";
  octx.fillRect(0, 0, SIZE, SIZE);
  octx.drawImage(src, px, py, nw, nh);
  const data = octx.getImageData(0, 0, SIZE, SIZE).data;
  const area = SIZE * SIZE;
  const input = new Float32Array(3 * area);
  for (let i = 0; i < area; i++) {
    input[i] = data[i * 4] / 255;
    input[i + area] = data[i * 4 + 1] / 255;
    input[i + 2 * area] = data[i * 4 + 2] / 255;
  }
  return { tensor: new ort.Tensor("float32", input, [1, 3, SIZE, SIZE]), scale, px, py, w, h };
}

function iou(a, b) {
  const x1 = Math.max(a.box[0], b.box[0]), y1 = Math.max(a.box[1], b.box[1]);
  const x2 = Math.min(a.box[2], b.box[2]), y2 = Math.min(a.box[3], b.box[3]);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const area = (d) => (d.box[2] - d.box[0]) * (d.box[3] - d.box[1]);
  return inter / (area(a) + area(b) - inter + 1e-9);
}

async function detect(src, conf) {
  const pre = preprocess(src);
  const out = await session.run({ [session.inputNames[0]]: pre.tensor });
  const t = out[session.outputNames[0]];
  const [, rows, n] = t.dims; // [1, 4 + classes, anchors]
  const d = t.data;
  const found = [];
  for (let i = 0; i < n; i++) {
    let best = -1, score = conf;
    for (let c = 4; c < rows; c++) {
      const s = d[c * n + i];
      if (s > score) { score = s; best = c - 4; }
    }
    if (best < 0) continue;
    const cx = d[i], cy = d[n + i], bw = d[2 * n + i], bh = d[3 * n + i];
    const box = [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2].map((v, k) =>
      Math.min(Math.max((v - (k % 2 ? pre.py : pre.px)) / pre.scale, 0), k % 2 ? pre.h : pre.w));
    found.push({ cls: NAMES[best], conf: score, box });
  }
  // class-wise non-maximum suppression
  found.sort((a, b) => b.conf - a.conf);
  const keep = [];
  for (const f of found) {
    if (keep.every((k) => k.cls !== f.cls || iou(k, f) < 0.5)) keep.push(f);
    if (keep.length >= 300) break;
  }
  return keep;
}

// ---- per-worker rules (same as ppe_guard.compliance.associate / evaluate_still) ---------------

function containment(inner, outer) {
  const ix = Math.max(0, Math.min(inner[2], outer[2]) - Math.max(inner[0], outer[0]));
  const iy = Math.max(0, Math.min(inner[3], outer[3]) - Math.max(inner[1], outer[1]));
  const a = (inner[2] - inner[0]) * (inner[3] - inner[1]);
  return a > 0 ? (ix * iy) / a : 0;
}
function inRegion(item, ppe, person) {
  const h = person[3] - person[1];
  if (h <= 0) return false;
  const rel = ((ppe[1] + ppe[3]) / 2 - person[1]) / h;
  return rel >= REGIONS[item][0] && rel <= REGIONS[item][1];
}

function evaluate(dets, required) {
  const persons = dets.filter((d) => CLASS[d.cls]?.[0] === "person");
  const workers = persons.map((p, i) => ({ id: i + 1, box: p.box, items: { helmet: "unknown", vest: "unknown", mask: "unknown" }, best: {}, synthetic: false }));
  for (const d of dets) {
    const [kind, item] = CLASS[d.cls] || [];
    if (!item) continue;
    let bi = -1, bs = 0;
    persons.forEach((p, i) => {
      const s = containment(d.box, p.box);
      if (s >= 0.5 && s > bs && inRegion(item, d.box, p.box)) { bi = i; bs = s; }
    });
    const status = kind === "worn" ? "worn" : "missing";
    if (bi >= 0) {
      const w = workers[bi];
      if (d.conf > (w.best[item] ?? -1)) { w.best[item] = d.conf; w.items[item] = status; }
    } else if (status === "missing") {
      const items = { helmet: "unknown", vest: "unknown", mask: "unknown" };
      items[item] = "missing";
      workers.push({ id: workers.length + 1, box: d.box, items, best: {}, synthetic: true });
    }
  }
  for (const w of workers) {
    w.missing = required.filter((i) => w.items[i] === "missing");
    w.state = w.missing.length ? "violation" : required.some((i) => w.items[i] === "unknown") ? "unknown" : "compliant";
  }
  return workers;
}

// ---- drawing ---------------------------------------------------------------------------------

function label(text, x, y, color, scale) {
  ctx.font = `500 ${Math.round(12 * scale)}px "Plex Mono", monospace`;
  const w = ctx.measureText(text).width + 10 * scale, h = 20 * scale;
  y = Math.max(y, h);
  ctx.fillStyle = color;
  ctx.fillRect(x, y - h, w, h);
  ctx.fillStyle = "#fff";
  ctx.fillText(text, x + 5 * scale, y - 6 * scale);
}

function draw(dets, workers, required) {
  const w = source.videoWidth || source.naturalWidth;
  const h = source.videoHeight || source.naturalHeight;
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  ctx.drawImage(source, 0, 0, w, h);
  // Scale strokes and labels to how big the image is on screen.
  const shown = canvas.clientWidth || $("stage").clientWidth || w;
  const s = Math.max(0.5, w / shown);
  if ($("show-items").checked) {
    ctx.lineWidth = 1.5 * s;
    for (const d of dets) {
      if (!CLASS[d.cls] || d.cls === "Person") continue;
      ctx.strokeStyle = d.cls.startsWith("NO-") ? "#e5484d" : "#3fc5c5";
      ctx.strokeRect(d.box[0], d.box[1], d.box[2] - d.box[0], d.box[3] - d.box[1]);
    }
  }
  for (const wk of workers) {
    const c = COLORS[wk.state];
    ctx.lineWidth = (wk.state === "violation" ? 3 : 2) * s;
    ctx.strokeStyle = c;
    ctx.strokeRect(wk.box[0], wk.box[1], wk.box[2] - wk.box[0], wk.box[3] - wk.box[1]);
    const parts = required.map((i) => `${LABEL[i]} ${wk.items[i] === "worn" ? "✓" : wk.items[i] === "missing" ? "✗" : "?"}`);
    label(`${wk.synthetic ? "?" : "#"}${wk.id} ${parts.join(" ")}`, wk.box[0], wk.box[1], c, s);
  }
}

function report(workers, required, ms) {
  const real = workers.filter((w) => !w.synthetic || w.state === "violation");
  const bad = real.filter((w) => w.state === "violation").length;
  const v = $("verdict");
  if (!real.length) { v.className = "verdict none"; v.textContent = "No workers found"; }
  else if (bad) { v.className = "verdict bad"; v.textContent = `${bad} of ${real.length} worker(s) missing required PPE`; }
  else { v.className = "verdict good"; v.textContent = `${real.length} worker(s), no violations`; }
  const pill = (st) => (st === "worn" ? '<span class="pill good">worn</span>' : st === "missing" ? '<span class="pill bad">missing</span>' : '<span class="pill na">not visible</span>');
  $("workers").innerHTML = real.map((w) => `<div class="worker"><span class="id" style="color:${COLORS[w.state]}">${w.synthetic ? "?" : "#"}${w.id}</span>${required.map((i) => `<span>${LABEL[i]} ${pill(w.items[i])}</span>`).join("")}</div>`).join("");
  if (ms !== undefined) $("timing").textContent = `${Math.round(ms)} ms on this device`;
}

function required() {
  return ITEMS.filter((i) => document.querySelector(`[data-req="${i}"]`).checked);
}

async function analyze() {
  if (!session || !source) return;
  const t0 = performance.now();
  lastDetections = await detect(source, Number($("conf").value));
  const ms = performance.now() - t0;
  render(ms);
}
function render(ms) {
  const req = required();
  const workers = evaluate(lastDetections, req);
  canvas.hidden = false;
  $("msg").hidden = true;
  draw(lastDetections, workers, req);
  report(workers, req, ms);
}

function showMessage(text) {
  $("msg").hidden = false;
  $("msg-text").textContent = text;
  document.querySelector(".bar").hidden = true;
}

// ---- inputs ------------------------------------------------------------------------------------

function useImage(url, button) {
  stopCamera();
  document.querySelectorAll("#samples button").forEach((b) => b.classList.toggle("active", b === button));
  const img = new Image();
  img.onload = () => { source = img; analyze().catch(fail); };
  img.onerror = () => fail(new Error("could not open that image"));
  img.src = url;
}

let stream = null;
async function startCamera() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1280 } }, audio: false });
  } catch (e) { fail(new Error(`camera not available: ${e.message}`)); return; }
  const video = document.createElement("video");
  video.srcObject = stream;
  video.muted = true;
  video.playsInline = true;
  await video.play();
  source = video;
  streaming = true;
  $("cam").textContent = "Stop camera";
  document.querySelectorAll("#samples button").forEach((b) => b.classList.remove("active"));
  const loop = async () => {
    if (!streaming) return;
    const t0 = performance.now();
    try {
      lastDetections = await detect(video, Number($("conf").value));
      render();
      $("timing").textContent = `${(1000 / (performance.now() - t0)).toFixed(1)} frames per second`;
    } catch (e) { fail(e); return; }
    requestAnimationFrame(loop);
  };
  loop();
}
function stopCamera() {
  streaming = false;
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  $("cam").textContent = "Use my camera";
}

function fail(e) {
  console.error(e);
  showMessage(`Something went wrong: ${e.message || e}`);
}

$("samples").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-src]");
  if (b && session) useImage(b.dataset.src, b);
});
$("file").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (f && session) useImage(URL.createObjectURL(f), null);
});
$("cam").addEventListener("click", () => (streaming ? stopCamera() : startCamera()));
$("conf").addEventListener("input", (e) => { $("conf-v").textContent = Number(e.target.value).toFixed(2); });
$("conf").addEventListener("change", () => { if (!streaming) analyze().catch(fail); });
document.querySelectorAll("[data-req], #show-items").forEach((el) => el.addEventListener("change", () => { if (source && lastDetections) render(); }));
const drop = $("drop");
drop.addEventListener("click", () => $("file").click());
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  const f = e.dataTransfer.files[0];
  if (f && session) useImage(URL.createObjectURL(f), null);
});

loadModel()
  .then(() => { const first = document.querySelector("#samples button"); useImage(first.dataset.src, first); })
  .catch(fail);
