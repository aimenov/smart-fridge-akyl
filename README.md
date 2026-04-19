# Smart Fridge (personal)

Local FastAPI backend, SQLite (WAL), mobile PWA UI, APScheduler worker, optional PaddleOCR and local VLM fallback.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
mkdir data\scans, data\uploads -Force 2>$null
smart-fridge
```

By default the CLI **creates or reuses** `data/certs/dev.pem` + `dev.key` (needs **openssl** on PATH; Git for Windows includes it) and serves **HTTPS**. Open **`https://<your-pc-lan-ip>:8765`** on your phone and accept the certificate warning once.

Use plain HTTP only when you want (no TLS):

```powershell
smart-fridge --no-dev-https
```

Same as `uvicorn backend.app.main:app --host 0.0.0.0 --port 8765`, but respects `.env` and CLI flags.

### CLI

| Flag | Meaning |
|------|---------|
| `--host`, `--port` | Bind address |
| `--reload` | Dev autoreload |
| `--log-level` | DEBUG, INFO, WARNING, ERROR |
| `--ssl-certfile`, `--ssl-keyfile` | Your own PEM files (skip auto dev cert) |
| `--no-dev-https` | Plain HTTP only — no auto certificate |
| `--no-scheduler` | Disable background jobs (tests default to this via env) |

Manual certificate example:

```powershell
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=smart-fridge"
smart-fridge --ssl-certfile cert.pem --ssl-keyfile key.pem
```

Browsers treat `http://192.168.x.x` as **not a secure context**, so `getUserMedia` (camera) is blocked unless you use **HTTPS** or **localhost**.

## Configuration

Copy `.env.example` to `.env`. Telegram variables are prefixed with `SMART_FRIDGE_` (see `backend/app/config.py`).

**Web UI missing (`GET /` returns `{"detail":"Not Found"}`)?** The server looks for a `web/` folder next to the installed package by walking up from `backend/app/main.py`. Plain `pip install` without the repo tree can skip that folder. Fix: install editable from the clone (`pip install -e ".[dev]"`) **or** set **`SMART_FRIDGE_WEB_ROOT`** to the absolute path of the project’s **`web`** directory (the one containing `index.html`).

**Observability:** Set `SMART_FRIDGE_LOG_LEVEL=INFO` (default) to see per-request **`[trace=uuid]`** correlation, specialist pipeline **stage timings** (`timing_ms` in scan responses / DB), PaddleOCR stats when OCR runs, and **VLM** HTTP timing plus redacted request/response previews (`SMART_FRIDGE_VLM_LOG_PREVIEW_CHARS`). Use `DEBUG` for more verbose third-party logs.

## Tests

```powershell
pytest tests/ --cov=backend --cov-report=term-missing
```

## Repository

Source: [github.com/aimenov/smart-fridge-akyl](https://github.com/aimenov/smart-fridge-akyl)

## OCR (same install as development)

Barcode/QR work out of the box; **printed names and expiry dates** need OCR. **`pip install -e ".[dev]"`** pulls in **Pillow**, **pytesseract**, tests, and — on Python **below 3.14** — **Paddle** + **PaddleOCR** (PyPI has no Paddle wheels for 3.14 yet; use Tesseract only, or Python **3.12** if you want Paddle).

Install the **Tesseract** binary and put `tesseract` on `PATH` for the Tesseract fallback ([Windows builds](https://github.com/UB-Mannheim/tesseract/wiki)). If the app cannot find it, set **`SMART_FRIDGE_TESSERACT_CMD`** to the full path of `tesseract.exe`.

**Blank scans / “PaddleOCR not available”:** (1) **Restart the API** after upgrading — Paddle stays off for the rest of the process after one failed init. (2) The **“Checking connectivity to the model hosters”** delay is avoided by default via **`PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK`** (see **`SMART_FRIDGE_PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK`**, default **true**). (3) On **Python 3.14**, Paddle wheels are missing — use **3.12** for Paddle or rely on **Tesseract** only.

Optional env (see `backend/app/config.py`): **`SMART_FRIDGE_OCR_LANG`** (first Paddle language — pipeline tries **`ru`** then **`multilingual`** then **`en`** for Cyrillic packaging). **`SMART_FRIDGE_TESSERACT_LANGS`** defaults to **`rus+eng`**; the fallback chain also tries **`rus`** and **`eng+rus`**. Cyrillic requires **Russian language data** next to Tesseract (`rus.traineddata`; the UB Mannheim Windows installer includes it). Add **`+kaz`** if you install Kazakh traineddata.

Regression image (optional): put **`tests/integration/fixtures/Nestle NAN На козьем молоке 3.jpg`** in the repo and run `pytest tests/integration/test_nestle_label_fixture.py -m integration` to verify OCR + product-title extraction against that label.

The server keeps one shared Paddle model in memory when Paddle is installed. Point the phone at the **flat label** with text in focus and reasonable light.
