const API = "";

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

async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    headers: opts.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...opts,
  });
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

async function captureFrames(n = 3) {
  const video = videoEl;
  const canvas = document.getElementById("snap-canvas");
  const ctx = canvas.getContext("2d");
  const w = video.videoWidth;
  const h = video.videoHeight;
  canvas.width = w;
  canvas.height = h;
  const blobs = [];
  for (let i = 0; i < n; i++) {
    ctx.drawImage(video, 0, 0, w, h);
    const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.85));
    blobs.push(blob);
    await new Promise((r) => setTimeout(r, 180));
  }
  return blobs;
}

async function startCamera() {
  const box = document.getElementById("scan-video-box");
  if (!box) return;
  if (stream) return;
  stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "environment" },
    audio: false,
  });
  videoEl = document.createElement("video");
  videoEl.autoplay = true;
  videoEl.playsInline = true;
  videoEl.srcObject = stream;
  box.innerHTML = "";
  box.appendChild(videoEl);
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

  status.textContent = "reading date…";
  const fd = new FormData();
  blobs.forEach((b, i) => fd.append("files", b, `frame-${i}.jpg`));

  const data = await api("/api/scan/upload", { method: "POST", body: fd });
  lastScanResult = data;

  status.textContent = "done";
  playDoneSound();

  document.getElementById("tier").textContent = data.confidence_tier;
  document.getElementById("tier").className = "status-pill " + data.confidence_tier;
  document.getElementById("conf").textContent = data.confidence.toFixed(2);
  document.getElementById("product-name").value = data.product_guess?.canonical_name || "";
  document.getElementById("barcode").value = data.barcode || "";
  document.getElementById("expiry").value = data.normalized_date || "";
  document.getElementById("date-type").value = data.date_type || "";

  confirmBox.classList.remove("hidden");
}

async function confirmScan() {
  const body = {
    scan_id: lastScanResult.scan_id,
    product: {
      canonical_name: document.getElementById("product-name").value || "Unknown product",
      barcode: document.getElementById("barcode").value || null,
      brand: null,
      default_unit: null,
      category: null,
    },
    quantity: parseFloat(document.getElementById("qty").value || "1"),
    unit: document.getElementById("unit").value || "each",
    expiry_date: document.getElementById("expiry").value || null,
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
    pr.innerHTML = `
      <section class="panel">
        <h2>Scan item</h2>
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
    document.getElementById("btn-scan").onclick = () => runScanFlow().catch((e) => {
      document.getElementById("scan-status").textContent = "Error: " + e.message;
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
