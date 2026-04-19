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
      "The camera is blocked on HTTP when you open the app by network IP or hostname.",
      "Start the server with TLS, for example: smart-fridge --dev-https (needs openssl in PATH), or",
      "smart-fridge --ssl-certfile cert.pem --ssl-keyfile key.pem",
      "then open https://<this-PC-LAN-IP>:8765/ (not 0.0.0.0) and accept the self-signed certificate.",
      "If the server is plain HTTP but you use https:// in the browser, uploads will fail.",
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
    return [
      "Could not complete the request (connection dropped before a response).",
      "Typical causes: OCR still running on the PC (wait longer—first PaddleOCR load can take 1–2 minutes), Wi‑Fi/firewall blocking the port, or the server process crashed.",
      "Check the terminal running smart-fridge for Python/OpenCV errors.",
    ].join(" ");
  }
  return m;
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
  if (ct.includes("application/json")) {
    try {
      return await r.json();
    } catch (e) {
      throw new Error(
        `Invalid JSON from ${path}: ${formatFetchError(e)}`,
      );
    }
  }
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

async function captureFrames(n = 3) {
  const video = videoEl;
  const canvas = document.getElementById("snap-canvas");
  const ctx = canvas.getContext("2d");
  await waitVideoReady(video);
  const w = video.videoWidth;
  const h = video.videoHeight;
  canvas.width = w;
  canvas.height = h;
  const blobs = [];
  for (let i = 0; i < n; i++) {
    ctx.drawImage(video, 0, 0, w, h);
    const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.85));
    if (!blob || blob.size < 100) {
      throw new Error("Captured frame was empty — wait for the preview to stabilize and try again.");
    }
    blobs.push(blob);
    await new Promise((r) => setTimeout(r, 220));
  }
  return blobs;
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
  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
    videoEl = null;
  }
}

async function runScanFlow() {
  const status = document.getElementById("scan-status");
  const confirmBox = document.getElementById("confirm-panel");
  status.textContent = "finding product…";
  confirmBox.classList.add("hidden");

  await startCamera();
  status.textContent = "capturing frames…";
  const blobs = await captureFrames(3);

  status.textContent = "uploading & reading date… (first scan can take 1–2 min)";
  const fd = new FormData();
  blobs.forEach((b, i) => fd.append("files", b, `frame-${i}.jpg`));

  const data = await api("/api/scan/upload", { method: "POST", body: fd });
  lastScanResult = data;

  status.textContent = "done";
  playDoneSound();

  const tierEl = document.getElementById("tier");
  const confEl = document.getElementById("conf");
  const tier = data.confidence_tier || "low";
  const confNum = Number(data.confidence);
  if (tierEl) {
    tierEl.textContent = tier;
    tierEl.className = "status-pill " + tier;
  }
  if (confEl) {
    confEl.textContent = Number.isFinite(confNum) ? confNum.toFixed(2) : "—";
  }

  const pg = data.product_guess || {};
  document.getElementById("product-name").value = pg.canonical_name || "";
  document.getElementById("barcode").value = data.barcode ?? "";
  let exp = data.normalized_date || "";
  if (typeof exp === "string" && exp.length > 10) exp = exp.slice(0, 10);
  document.getElementById("expiry").value = exp;
  document.getElementById("date-type").value = data.date_type || "";

  confirmBox.classList.remove("hidden");
}

async function confirmScan() {
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
  document.getElementById("scan-status").textContent = "saved to inventory";
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
        <p class="muted">Captures 2–3 stills, runs barcode + OCR pipeline, then confirm.</p>
        <div id="scan-video-box"></div>
        <p id="scan-status" class="muted">idle</p>
        <div class="row">
          <button class="primary" id="btn-scan">Scan</button>
          <button class="secondary" id="btn-stop-cam">Stop camera</button>
        </div>
      </section>
      <section class="panel hidden" id="confirm-panel">
        <h2>Confirmation</h2>
        <p>Tier <span id="tier" class="status-pill">—</span>
          · score <span id="conf">0</span></p>
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
        <button class="primary" id="btn-confirm">Save to inventory</button>
      </section>`;
    document.getElementById("btn-scan").onclick = () =>
      runScanFlow().catch((e) => {
        document.getElementById("scan-status").textContent =
          "Error: " + formatFetchError(e);
      });
    document.getElementById("btn-stop-cam").onclick = () => stopCamera();
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
