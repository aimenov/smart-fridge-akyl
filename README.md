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
| `--no-scheduler` | Disable background jobs (tests default to this via env) |

Example self-signed TLS (trusted warning on devices until you install your own CA):

```powershell
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=smart-fridge"
smart-fridge --ssl-certfile cert.pem --ssl-keyfile key.pem
```

Open `https://<your-pc-lan-ip>:8765` on the phone, accept the certificate, then use **Scan**.

Browsers treat `http://192.168.x.x` as **not a secure context**, so `getUserMedia` (camera) is blocked unless you use **HTTPS** or **localhost**.

## Configuration

Copy `.env.example` to `.env`. Telegram variables are prefixed with `SMART_FRIDGE_` (see `backend/app/config.py`).

## Tests

```powershell
pytest tests/ --cov=backend --cov-report=term-missing
```

## Publish to GitHub

GitHub CLI (`winget install GitHub.cli`) must be logged in once:

```powershell
$env:Path = "$env:ProgramFiles\GitHub CLI;$env:Path"
gh auth login -p https -h github.com -w
.\scripts\push-to-github.ps1
```

That creates **`smart-fridge-akyl`** under your account, adds **`origin`**, and pushes **`master`**. If the repo name is taken, edit `scripts/push-to-github.ps1` or run:

```powershell
gh repo create YOUR-NAME --public --source=. --remote=origin --push
```

If **`origin`** already exists: `git remote remove origin`, then run the script again.

Alternatively, create an **empty** repository on GitHub (no README), then:

```powershell
git remote add origin https://github.com/YOU/REPO.git
git push -u origin master
```

## Optional OCR

```powershell
pip install paddlepaddle paddleocr
```
