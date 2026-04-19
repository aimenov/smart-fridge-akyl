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

Same as `uvicorn backend.app.main:app --host 0.0.0.0 --port 8765`, but respects `.env` and CLI flags.

### CLI

| Flag | Meaning |
|------|---------|
| `--host`, `--port` | Bind address |
| `--reload` | Dev autoreload |
| `--log-level` | DEBUG, INFO, WARNING, ERROR |
| `--ssl-certfile`, `--ssl-keyfile` | HTTPS (needed for **camera on a phone over LAN**) |
| `--dev-https` | Same as HTTPS, but creates `data/certs/dev.pem` + `dev.key` with **openssl** (easiest LAN phone setup) |
| `--no-scheduler` | Disable background jobs (tests default to this via env) |

If the server logs **plain HTTP** but you open **`https://…` in the browser**, uvicorn will show `Invalid HTTP request received` (TLS handshake bytes are not HTTP). Either open **`http://…`** for desktop-only use, or run with TLS enabled so the URL scheme matches what uvicorn speaks.

Example self-signed TLS (trusted warning on devices until you install your own CA):

```powershell
smart-fridge --dev-https
```

(`--dev-https` runs **openssl**; on Windows Git for Win often provides `Git\usr\bin\openssl.exe` automatically.)

Or manually:

```powershell
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=smart-fridge"
smart-fridge --ssl-certfile cert.pem --ssl-keyfile key.pem
```

Open `https://<your-pc-lan-ip>:8765` on the phone, accept the certificate, then use **Scan**.

Browsers treat `http://192.168.x.x` as **not a secure context**, so `getUserMedia` (camera) is blocked unless you use **HTTPS** or **localhost**.

## Configuration

Copy `.env.example` to `.env`. Telegram variables are prefixed with `SMART_FRIDGE_` (see `backend/app/config.py`).

**Observability:** Set `SMART_FRIDGE_LOG_LEVEL=INFO` (default) to see per-request **`[trace=uuid]`** correlation, specialist pipeline **stage timings** (`timing_ms` in scan responses / DB), PaddleOCR stats when OCR runs, and **VLM** HTTP timing plus redacted request/response previews (`SMART_FRIDGE_VLM_LOG_PREVIEW_CHARS`). Use `DEBUG` for more verbose third-party logs.

## Tests

```powershell
pytest tests/ --cov=backend --cov-report=term-missing
```

## Repository

Source: [github.com/aimenov/smart-fridge-akyl](https://github.com/aimenov/smart-fridge-akyl)

## Optional OCR

```powershell
pip install paddlepaddle paddleocr
```
