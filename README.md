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
