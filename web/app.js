const API = "";

function isLocalhost() {
  const h = location.hostname;
  return h === "localhost" || h === "127.0.0.1" || h === "[::1]";
}

/**
 * Mobile browsers only expose the camera in a "secure context" (HTTPS or localhost).
 * Plain http://<lan-ip> is not secure, so getUserMedia() is blocked.
 */
function cameraAccessHelp() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return "This browser does not support camera access from a web app.";
  }
  if (!window.isSecureContext && !isLocalhost()) {
    return [
      "The camera is blocked on HTTP when you open the app by network IP.",
      "Run the server with HTTPS (default: dev certificate in data/certs/) or pass your own PEM files.",
      "Open https://<this-PC-LAN-IP>:8765/ and accept the certificate warning if prompted.",
    ].join(" ");
  }
  return null;
}

async function getCameraStream() {
  const list = [
    { video: { facingMode: { ideal: "environment" } }, audio: false },
    { video: { facingMode: "environment" }, audio: false },
    { video: true, audio: false },
  ];
  let lastErr;
  for (const c of list) {
    try {
      return await navigator.mediaDevices.getUserMedia(c);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error("Could not open camera");
}

async function waitVideoReady(video) {
  if (video.readyState >= 2 && video.videoWidth > 0) return;
  await new Promise((resolve, reject) => {
    const ms = 15000;
    const t = setTimeout(() => reject(new Error("Camera preview timed out")), ms);
    const done = () => {
      clearTimeout(t);
      resolve();
    };
    video.addEventListener("loadedmetadata", done, { once: true });
    video.addEventListener("loadeddata", done, { once: true });
  });
  let n = 0;
  while (video.videoWidth === 0 && n++ < 75) {
    await new Promise((r) => setTimeout(r, 100));
  }
  if (video.videoWidth === 0) {
    throw new Error("Camera returned no picture — try HTTPS, another browser, or different lighting.");
  }
}

const pages = [
  { id: "scan", label: "Scan" },
  { id: "inventory", label: "Inventory" },
  { id: "expiring", label: "Expiring" },
  { id: "recipes", label: "Recipes" },
  { id: "audit", label: "Audit" },
];

let activeId = "scan";
let videoEl = null;
let stream = null;
let lastScanResult = null;

/** Live two-phase scan: product lock prevents overwriting name/barcode during expiry frames. */
let lockProductFields = false;
let liveScanControl = null;

const LIVE_SCAN_MAX_MS = 10000;
/** Minimum gap between upload attempts (~2 fps cap; slower if the server is busy). */
const LIVE_SCAN_MIN_GAP_MS = 450;

function isProductIdentified(data) {
  if (!data) return false;
  const tier = data.confidence_tier || "low";
  const bc = (data.barcode != null && String(data.barcode).trim() !== "") ? String(data.barcode).trim() : "";
  const name = ((data.product_guess && data.product_guess.canonical_name) || "").trim();
  if (bc) return true;
  if (name && name !== "Unknown product") {
    if (tier === "high") return true;
    if (tier === "medium" && name.length >= 3) return true;
  }
  return false;
}

function isExpiryIdentified(data) {
  if (!data) return false;
  const exp = data.normalized_date;
  if (!exp || String(exp).trim() === "") return false;
  const tier = data.confidence_tier || "low";
  if (tier === "high" || tier === "medium") return true;
  return false;
}

function scanConfidence(data) {
  const n = Number(data && data.confidence);
  return Number.isFinite(n) ? n : 0;
}

function setLiveRing(state) {
  const wrap = document.getElementById("scan-video-wrap");
  if (!wrap) return;
  wrap.classList.remove("live-ring--idle", "live-ring--scanning", "live-ring--success", "live-ring--error");
  wrap.classList.add(
    state === "scanning"
      ? "live-ring--scanning"
      : state === "success"
        ? "live-ring--success"
        : state === "error"
          ? "live-ring--error"
          : "live-ring--idle",
  );
}

function setLiveButtons({ scanning }) {
  const start = document.getElementById("btn-start-scan");
  const stop = document.getElementById("btn-stop-live");
  if (start) start.disabled = !!scanning;
  if (stop) stop.classList.toggle("hidden", !scanning);
}

function stopLiveScan() {
  if (liveScanControl) liveScanControl.stopped = true;
}

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

function playDoneSound() {
  try {
    const ctx = new AudioContext();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.connect(g);
    g.connect(ctx.destination);
    o.frequency.value = 880;
    g.gain.value = 0.08;
    o.start();
    setTimeout(() => {
      o.stop();
      ctx.close();
    }, 180);
  } catch {
    /* ignore */
  }
}

function formatFetchError(err) {
  const m = err && err.message ? String(err.message) : String(err);
  if (
    m === "Load failed" ||
    m === "Failed to fetch" ||
    /networkerror|load failed/i.test(m)
  ) {
    return "Network error — check Wi‑Fi, wait if OCR is still processing on the PC, and try again.";
  }
  return m;
}

/** Large readout + hints after /api/scan/upload (product vs expiry at a glance). */
function fillScanHero(data) {
  const pg = data.product_guess || {};
  const prod = (pg.canonical_name || "").trim();
  document.getElementById("hero-product").textContent = prod || "—";

  let exp = data.normalized_date || "";
  if (typeof exp === "string" && exp.length > 10) exp = exp.slice(0, 10);
  document.getElementById("hero-expiry").textContent = exp || "—";

  const tier = data.confidence_tier || "low";
  const confNum = Number(data.confidence);
  const tierEl = document.getElementById("tier");
  const confEl = document.getElementById("conf");
  if (tierEl) {
    tierEl.textContent = tier;
    tierEl.className = "status-pill " + tier;
  }
  if (confEl) {
    confEl.textContent = Number.isFinite(confNum) ? confNum.toFixed(2) : "—";
  }

  const hintEl = document.getElementById("scan-hint");
  const previewEl = document.getElementById("ocr-preview");
  const pv = (data.ocr_text_preview || "").trim();

  if (tier === "high") {
    hintEl.textContent =
      "Strong read — if this matches the package, scroll down and tap Save (edit fields if needed).";
  } else if (tier === "medium") {
    hintEl.textContent =
      "Fair read — glance at Product and Expiry above, fix any mistakes in the fields, then Save.";
  } else {
    hintEl.textContent =
      "Needs review — edit product name and expiry from the package. Optional: install PaddleOCR on the PC for sharper reads.";
  }

  if (previewEl) {
    if (pv && tier !== "high") {
      previewEl.textContent = "Machine read from label: " + pv;
      previewEl.classList.remove("hidden");
    } else {
      previewEl.textContent = "";
      previewEl.classList.add("hidden");
    }
  }
}

function wireConfirmHeroSync() {
  const pn = document.getElementById("product-name");
  const ex = document.getElementById("expiry");
  const hp = document.getElementById("hero-product");
  const he = document.getElementById("hero-expiry");
  if (pn && hp) {
    pn.addEventListener("input", () => {
      hp.textContent = pn.value.trim() || "—";
    });
  }
  if (ex && he) {
    const sync = () => {
      he.textContent = ex.value.trim() || "—";
    };
    ex.addEventListener("change", sync);
    ex.addEventListener("input", sync);
  }
}

let bestExpiryConf = -1;

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function captureSingleFrame() {
  const video = videoEl;
  const canvas = document.getElementById("snap-canvas");
  const ctx = canvas.getContext("2d");
  await waitVideoReady(video);
  const w = video.videoWidth;
  const h = video.videoHeight;
  canvas.width = w;
  canvas.height = h;
  ctx.drawImage(video, 0, 0, w, h);
  const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.8));
  if (!blob || blob.size < 100) {
    throw new Error("Captured frame was empty — hold steady and try again.");
  }
  return blob;
}

function applyFullScanToForm(data) {
  const d = data || {};
  const pg = d.product_guess || {};
  document.getElementById("product-name").value = pg.canonical_name || "";
  document.getElementById("barcode").value = d.barcode ?? "";
  let exp = d.normalized_date || "";
  if (typeof exp === "string" && exp.length > 10) exp = exp.slice(0, 10);
  document.getElementById("expiry").value = exp;
  document.getElementById("date-type").value = d.date_type || "";
}

function mergeExpiryFromScan(data) {
  if (!data || !data.normalized_date) return;
  const c = scanConfidence(data);
  if (!(bestExpiryConf < 0 || c >= bestExpiryConf)) return;
  bestExpiryConf = c;
  let exp = data.normalized_date;
  if (typeof exp === "string" && exp.length > 10) exp = exp.slice(0, 10);
  document.getElementById("expiry").value = exp;
  if (data.date_type) document.getElementById("date-type").value = data.date_type;
}

async function uploadScanBlob(blob) {
  const compressed = await compressBlobForUpload(blob);
  const fd = new FormData();
  fd.append("files", compressed, "frame.jpg");
  return xhrPostMultipart("/api/scan/upload", fd);
}

async function flashRingSuccess() {
  setLiveRing("success");
  playDoneSound();
  await sleep(700);
  setLiveRing("idle");
}

function heroSnapshotFromForm(scanData) {
  const sd = scanData || {};
  const name = document.getElementById("product-name").value.trim();
  let exp = document.getElementById("expiry").value.trim();
  const pgName = name || (sd.product_guess && sd.product_guess.canonical_name) || "";
  if (typeof exp === "string" && exp.length > 10) exp = exp.slice(0, 10);
  return {
    ...sd,
    product_guess: { canonical_name: pgName },
    normalized_date: exp || sd.normalized_date,
    confidence: sd.confidence,
    confidence_tier: sd.confidence_tier || "low",
    ocr_text_preview: sd.ocr_text_preview,
  };
}

async function liveScanLoop(phase) {
  const ctrl = { stopped: false };
  liveScanControl = ctrl;
  setLiveButtons({ scanning: true });
  setLiveRing("scanning");

  const t0 = Date.now();
  if (phase === "expiry") bestExpiryConf = -1;

  while (Date.now() - t0 < LIVE_SCAN_MAX_MS && !ctrl.stopped) {
    const statusEl = document.getElementById("scan-status");
    const remain = Math.max(0, Math.ceil((LIVE_SCAN_MAX_MS - (Date.now() - t0)) / 1000));
    if (phase === "product") {
      statusEl.textContent = `Live: finding product… ~${remain}s left`;
    } else {
      statusEl.textContent = `Live: reading expiry… ~${remain}s left`;
    }

    let data;
    try {
      data = await uploadScanBlob(await captureSingleFrame());
    } catch (e) {
      statusEl.textContent = "Error: " + formatFetchError(e);
      await sleep(LIVE_SCAN_MIN_GAP_MS);
      continue;
    }

    lastScanResult = data;

    if (phase === "product") {
      applyFullScanToForm(data);
      if (isProductIdentified(data)) {
        setLiveButtons({ scanning: false });
        liveScanControl = null;
        return { ok: true, stopped: false };
      }
    } else {
      mergeExpiryFromScan(data);
      fillScanHero(heroSnapshotFromForm(data));
      if (isExpiryIdentified(data)) {
        setLiveButtons({ scanning: false });
        liveScanControl = null;
        return { ok: true, stopped: false };
      }
    }

    await sleep(LIVE_SCAN_MIN_GAP_MS);
  }

  setLiveButtons({ scanning: false });
  liveScanControl = null;
  return { ok: false, stopped: ctrl.stopped };
}

function setPhaseLabel(text) {
  const el = document.getElementById("scan-phase");
  if (el) el.textContent = text || "";
}

async function handleProductPhaseEnd(stopped) {
  lockProductFields = false;
  setLiveRing("error");
  await sleep(750);
  setLiveRing("idle");
  setPhaseLabel(
    stopped
      ? "Stopped — confirm product below, then continue to expiry."
      : "Product not detected automatically — edit below, then continue.",
  );
  const status = document.getElementById("scan-status");
  status.textContent = stopped
    ? "Scan stopped. Fix product details if needed, then tap Continue to expiry scan."
    : "Edit product if needed, then tap Continue to expiry scan.";
  const blank = {
    confidence: 0,
    confidence_tier: "low",
    product_guess: {},
    normalized_date: null,
    ocr_text_preview: "",
  };
  if (lastScanResult) {
    applyFullScanToForm(lastScanResult);
    fillScanHero(lastScanResult);
  } else {
    fillScanHero(blank);
  }
  document.getElementById("confirm-panel").classList.remove("hidden");
  document.getElementById("btn-continue-expiry").classList.remove("hidden");
  document.getElementById("confirm-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function handleExpiryPhaseEnd(stopped) {
  setLiveRing("error");
  await sleep(750);
  setLiveRing("idle");
  setPhaseLabel(
    stopped ? "Stopped — set expiry manually if needed." : "Expiry not read cleanly — pick the date manually.",
  );
  document.getElementById("scan-status").textContent = stopped
    ? "Scan stopped. Choose the expiry date below, then save."
    : "Choose the expiry date on the calendar, then save.";
  finalizeConfirmPanel();
}

function finalizeConfirmPanel() {
  setLiveRing("idle");
  const base = lastScanResult || {};
  fillScanHero(heroSnapshotFromForm(base));
  document.getElementById("confirm-panel").classList.remove("hidden");
  document.getElementById("btn-continue-expiry").classList.add("hidden");
  document.getElementById("confirm-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function continueToExpiryAfterProductEdit() {
  const name = document.getElementById("product-name").value.trim();
  const bc = document.getElementById("barcode").value.trim();
  if (!name && !bc) {
    alert("Enter a product name or barcode before continuing.");
    return;
  }
  lockProductFields = true;
  document.getElementById("btn-continue-expiry").classList.add("hidden");
  try {
    await startCamera();
    setPhaseLabel("Phase 2 — scan the expiry date.");
    document.getElementById("scan-status").textContent = "Show the printed date to the camera.";
    const er = await liveScanLoop("expiry");
    if (er.ok) {
      await flashRingSuccess();
      setPhaseLabel("Check details and save.");
      document.getElementById("scan-status").textContent = "Review product and expiry, then save.";
      finalizeConfirmPanel();
    } else {
      await handleExpiryPhaseEnd(er.stopped);
    }
  } catch (e) {
    document.getElementById("scan-status").textContent = "Error: " + formatFetchError(e);
  }
}

async function beginFullScanFlow() {
  const status = document.getElementById("scan-status");
  const confirmBox = document.getElementById("confirm-panel");
  lockProductFields = false;
  stopLiveScan();
  setLiveRing("idle");

  try {
    status.textContent = "Starting camera…";
    confirmBox.classList.add("hidden");
    const cont = document.getElementById("btn-continue-expiry");
    if (cont) cont.classList.add("hidden");

    await startCamera();

    setPhaseLabel("Phase 1 — scan the product (barcode or name).");
    const pr = await liveScanLoop("product");
    if (pr.ok) {
      lockProductFields = true;
      await flashRingSuccess();
      setPhaseLabel("Product found. Phase 2 — scan the expiry date.");
      status.textContent = "Aim at the printed expiry / best-before date.";
      const er = await liveScanLoop("expiry");
      if (er.ok) {
        await flashRingSuccess();
        setPhaseLabel("Review and save.");
        status.textContent = "Confirm details below, then save to inventory.";
        finalizeConfirmPanel();
      } else {
        await handleExpiryPhaseEnd(er.stopped);
      }
    } else {
      await handleProductPhaseEnd(pr.stopped);
    }
  } catch (e) {
    setLiveRing("idle");
    setLiveButtons({ scanning: false });
    liveScanControl = null;
    status.textContent = "Error: " + formatFetchError(e);
    setPhaseLabel("");
  }
}

function resetScanSessionAfterSave() {
  lockProductFields = false;
  liveScanControl = null;
  lastScanResult = null;
  setLiveRing("idle");
  setPhaseLabel("");
  document.getElementById("confirm-panel").classList.add("hidden");
  const cont = document.getElementById("btn-continue-expiry");
  if (cont) cont.classList.add("hidden");
  document.getElementById("product-name").value = "";
  document.getElementById("barcode").value = "";
  document.getElementById("expiry").value = "";
  document.getElementById("date-type").value = "";
  document.getElementById("qty").value = "1";
  document.getElementById("unit").value = "each";
  document.getElementById("scan-status").textContent = "Saved. Tap Start scanning to add another item.";
}

/** Max longest edge (px) for upload bodies (keeps mobile uploads reliable). */
const UPLOAD_MAX_EDGE = 1600;
const UPLOAD_JPEG_QUALITY = 0.72;

async function compressBlobForUpload(blob) {
  if (!(blob instanceof Blob) || blob.size < 1) return blob;

  let bitmap;
  try {
    bitmap = await createImageBitmap(blob);
  } catch {
    return blob;
  }
  const w0 = bitmap.width;
  const h0 = bitmap.height;
  const scale = Math.min(1, UPLOAD_MAX_EDGE / Math.max(w0, h0));
  const w = Math.max(1, Math.round(w0 * scale));
  const h = Math.max(1, Math.round(h0 * scale));
  const bigFile = blob.size > 450000;
  if (scale >= 1 && !bigFile) {
    bitmap.close();
    return blob;
  }

  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d");
  ctx.drawImage(bitmap, 0, 0, w, h);
  bitmap.close();

  return new Promise((resolve, reject) => {
    c.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("JPEG encode failed"))),
      "image/jpeg",
      UPLOAD_JPEG_QUALITY,
    );
  });
}

/** WebKit multipart upload: prefer XHR over fetch for large FormData on iOS Safari. */
function xhrPostMultipart(path, formData) {
  const url = new URL(path, location.origin).href;

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.responseType = "text";
    xhr.timeout = 180000;

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText || "{}"));
        } catch {
          reject(new Error(`Invalid JSON from ${path}`));
        }
      } else {
        reject(new Error((xhr.responseText || "").slice(0, 2000) || xhr.statusText || `HTTP ${xhr.status}`));
      }
    };

    xhr.onerror = () => reject(new Error(formatFetchError({ message: "Load failed" })));

    xhr.ontimeout = () =>
      reject(new Error("Upload timed out — the PC may still be loading OCR; try again."));

    xhr.send(formData);
  });
}

async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(API + path, {
      headers: opts.body instanceof FormData ? {} : { "Content-Type": "application/json" },
      ...opts,
    });
  } catch (e) {
    throw new Error(formatFetchError(e));
  }
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || r.statusText);
  }
  if (r.status === 204) return null;
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("application/json")) return r.json();
  return r.text();
}

function renderNav() {
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  for (const p of pages) {
    const b = document.createElement("button");
    b.textContent = p.label;
    b.className = p.id === activeId ? "active" : "";
    b.onclick = () => {
      activeId = p.id;
      renderNav();
      renderPage();
    };
    nav.appendChild(b);
  }
}

async function startCamera() {
  const box = document.getElementById("scan-video-box");
  if (!box) return;
  if (stream) return;
  const hint = cameraAccessHelp();
  if (hint) {
    throw new Error(hint);
  }
  stream = await getCameraStream();
  videoEl = document.createElement("video");
  videoEl.autoplay = true;
  videoEl.muted = true;
  videoEl.playsInline = true;
  videoEl.setAttribute("playsinline", "");
  videoEl.srcObject = stream;
  box.innerHTML = "";
  box.appendChild(videoEl);
  try {
    await videoEl.play();
  } catch {
    /* some browsers need a gesture; preview may still work */
  }
  await waitVideoReady(videoEl);
}

async function stopCamera() {
  stopLiveScan();
  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
    videoEl = null;
  }
}

async function confirmScan() {
  if (!lastScanResult || lastScanResult.scan_id == null) {
    alert("Run a scan first so the server has a capture to attach.");
    return;
  }
  let qty = parseFloat(document.getElementById("qty").value || "1");
  if (!Number.isFinite(qty) || qty <= 0) qty = 1;

  const body = {
    scan_id: lastScanResult.scan_id,
    product: {
      canonical_name: document.getElementById("product-name").value.trim() || "Unknown product",
      barcode: document.getElementById("barcode").value.trim() || null,
      brand: null,
      default_unit: null,
      category: null,
    },
    quantity: qty,
    unit: document.getElementById("unit").value.trim() || "each",
    expiry_date: document.getElementById("expiry").value.trim() || null,
    location: document.getElementById("location").value || "fridge",
    inferred_date_type: document.getElementById("date-type").value || null,
  };
  await api("/api/scan/confirm", {
    method: "POST",
    body: JSON.stringify(body),
  });
  resetScanSessionAfterSave();
}

async function loadInventory() {
  const list = await api("/api/items");
  const root = document.getElementById("inventory-list");
  root.innerHTML = "";
  if (!list.length) {
    root.innerHTML = `<p class="muted">No active items.</p>`;
    return;
  }
  for (const it of list) {
    const card = el(`
      <div class="card" data-id="${it.id}">
        <strong>${escapeHtml(it.canonical_name)}</strong>
        <div class="muted">${it.quantity} ${it.unit} · ${it.status} · ${it.location}</div>
        <div class="muted">Expiry: ${it.expiry_date || "—"}</div>
        <div class="row" style="margin-top:.5rem;">
          <button class="secondary" data-act="consume">Consumed</button>
          <button class="secondary" data-act="discard">Discard</button>
          <button class="secondary" data-act="opened">Mark opened</button>
        </div>
      </div>`);
    card.querySelectorAll("button").forEach((btn) => {
      btn.onclick = async () => {
        const act = btn.getAttribute("data-act");
        if (act === "consume") {
          await api(`/api/items/${it.id}`, {
            method: "PATCH",
            body: JSON.stringify({ status: "consumed" }),
          });
        } else if (act === "discard") {
          await api(`/api/items/${it.id}`, {
            method: "PATCH",
            body: JSON.stringify({ status: "discarded" }),
          });
        } else if (act === "opened") {
          await api(`/api/items/${it.id}`, {
            method: "PATCH",
            body: JSON.stringify({ opened_now: true }),
          });
        }
        loadInventory();
      };
    });
    root.appendChild(card);
  }
}

async function loadExpiring() {
  const list = await api("/api/items/expiring");
  const root = document.getElementById("expiring-list");
  root.innerHTML = "";
  if (!list.length) {
    root.innerHTML = `<p class="muted">Nothing in the warning window.</p>`;
    return;
  }
  for (const it of list) {
    root.appendChild(
      el(`<div class="card"><strong>${escapeHtml(it.canonical_name)}</strong>
        <div class="muted">Expires ${it.expiry_date}</div></div>`),
    );
  }
}

async function loadRecipes() {
  const inc = document.getElementById("include-expired").checked;
  const data = await api(`/api/recipes/suggest?include_expired=${inc ? "true" : "false"}`);
  const root = document.getElementById("recipe-buckets");
  root.innerHTML = `
    <p class="muted">${escapeHtml(data.pantry_note)}</p>
    <h3>Can cook now</h3>
    ${renderRecipeList(data.can_cook_now)}
    <h3>Need 1–2 extras</h3>
    ${renderRecipeList(data.need_one_or_two_items)}
    <h3>Best for expiring soon</h3>
    ${renderRecipeList(data.best_for_expiring_soon)}
  `;
}

function renderRecipeList(items) {
  if (!items.length) return `<p class="muted">None</p>`;
  return `<div class="list">${items
    .map(
      (r) => `
    <div class="card">
      <strong>${escapeHtml(r.title)}</strong>
      <div class="muted">${r.prep_minutes} min · coverage ${(r.pantry_coverage * 100).toFixed(
        0,
      )}%</div>
      <div class="muted">Missing: ${r.missing_from_pantry.map(escapeHtml).join(", ") || "—"}</div>
    </div>`,
    )
    .join("")}</div>`;
}

async function loadAudit() {
  const rows = await api("/api/scans/recent?limit=40");
  const root = document.getElementById("audit-list");
  root.innerHTML = "";
  for (const s of rows) {
    root.appendChild(
      el(`<div class="card"><div class="muted">${escapeHtml(s.created_at)}</div>
        <div>Confidence: ${s.confidence?.toFixed?.(2) ?? s.confidence}</div>
        <div class="muted">${escapeHtml((s.ocr_excerpt || "").slice(0, 240))}</div></div>`),
    );
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderPage() {
  const root = document.getElementById("pages");
  root.innerHTML = `<div id="page-root"></div>`;
  const pr = document.getElementById("page-root");

  if (activeId === "scan") {
    const warn = cameraAccessHelp();
    pr.innerHTML = `
      <section class="panel">
        <h2>Scan item</h2>
        ${
          warn
            ? `<div class="camera-warning" id="camera-banner"><strong>Camera on phone:</strong> ${escapeHtml(warn)}</div>`
            : `<div class="muted" id="camera-banner">Camera ready (secure context).</div>`
        }
        <p class="muted">Two steps: live scan finds the product (green ring), then the expiry date (green ring). Max ~10s each step; tap Stop anytime.</p>
        <div id="scan-video-wrap" class="scan-video-wrap live-ring--idle">
          <div id="scan-video-box"></div>
        </div>
        <p id="scan-phase" class="scan-phase" aria-live="polite"></p>
        <p id="scan-status" class="muted">Idle — tap Start scanning.</p>
        <div class="row">
          <button class="primary" id="btn-start-scan">Start scanning</button>
          <button class="secondary hidden" id="btn-stop-live" type="button">Stop scanning</button>
          <button class="secondary" id="btn-stop-cam" type="button">Stop camera</button>
        </div>
      </section>
      <section class="panel hidden" id="confirm-panel">
        <h2>Confirm</h2>
        <div class="scan-readout" aria-live="polite">
          <div class="scan-readout-row">
            <span class="scan-readout-label">Product name</span>
            <div class="scan-readout-value" id="hero-product">—</div>
          </div>
          <div class="scan-readout-row">
            <span class="scan-readout-label">Expiry date</span>
            <div class="scan-readout-value scan-readout-expiry" id="hero-expiry">—</div>
          </div>
        </div>
        <p class="scan-hint muted" id="scan-hint"></p>
        <p class="ocr-preview muted hidden" id="ocr-preview"></p>
        <p class="muted scan-meta">Tier <span id="tier" class="status-pill">—</span>
          · score <span id="conf">0</span></p>
        <h3 class="fine-print-heading">Adjust if needed</h3>
        <label class="field">Product<input id="product-name" /></label>
        <label class="field">Barcode<input id="barcode" /></label>
        <label class="field">Expiry (YYYY-MM-DD)<input id="expiry" type="date" /></label>
        <label class="field">Date type<select id="date-type">
          <option value="">Unknown</option>
          <option value="best_before">best_before</option>
          <option value="use_by">use_by</option>
          <option value="expiry">expiry</option>
          <option value="packed_on">packed_on</option>
          <option value="produced_on">produced_on</option>
        </select></label>
        <label class="field">Quantity<input id="qty" type="number" step="0.1" value="1" /></label>
        <label class="field">Unit<input id="unit" value="each" /></label>
        <label class="field">Location<select id="location">
          <option value="fridge">fridge</option>
          <option value="freezer">freezer</option>
          <option value="pantry">pantry</option>
        </select></label>
        <button class="secondary hidden" id="btn-continue-expiry" type="button">Continue to expiry scan</button>
        <button class="primary" id="btn-confirm">Save to inventory</button>
      </section>`;
    document.getElementById("btn-start-scan").onclick = () =>
      beginFullScanFlow().catch((e) => {
        document.getElementById("scan-status").textContent = "Error: " + formatFetchError(e);
      });
    document.getElementById("btn-stop-live").onclick = () => stopLiveScan();
    document.getElementById("btn-stop-cam").onclick = () => stopCamera();
    document.getElementById("btn-continue-expiry").onclick = () =>
      continueToExpiryAfterProductEdit().catch((e) => alert(e.message));
    wireConfirmHeroSync();
    document.getElementById("btn-confirm").onclick = () =>
      confirmScan().catch((e) => alert(e.message));
  } else if (activeId === "inventory") {
    pr.innerHTML = `<section class="panel"><h2>Inventory</h2><div id="inventory-list" class="list"></div></section>`;
    loadInventory().catch((e) => (document.getElementById("inventory-list").textContent = e.message));
  } else if (activeId === "expiring") {
    pr.innerHTML = `<section class="panel"><h2>Expiring soon</h2><div id="expiring-list" class="list"></div></section>`;
    loadExpiring().catch((e) => (document.getElementById("expiring-list").textContent = e.message));
  } else if (activeId === "recipes") {
    pr.innerHTML = `
      <section class="panel">
        <h2>Recipes</h2>
        <label class="row"><input type="checkbox" id="include-expired" /> Include expired (explicit)</label>
        <button class="primary" id="btn-recipes">Suggest</button>
        <div id="recipe-buckets" style="margin-top:1rem;"></div>
      </section>`;
    document.getElementById("btn-recipes").onclick = () =>
      loadRecipes().catch((e) => alert(e.message));
    loadRecipes().catch(() => {});
  } else if (activeId === "audit") {
    pr.innerHTML = `<section class="panel"><h2>Scan audit</h2><div id="audit-list" class="list"></div></section>`;
    loadAudit().catch((e) => (document.getElementById("audit-list").textContent = e.message));
  }
}

renderNav();
renderPage();
