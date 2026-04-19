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

Git alone cannot create a repository on GitHub servers; you either use the **website**, **`gh`**, or a **Personal Access Token** with `curl`.

### Option A — Git Bash + token (creates `smart-fridge-akyl` and pushes `master`)

1. Create a **classic** PAT with **`repo`** scope: https://github.com/settings/tokens/new  
2. In **Git Bash**:

```bash
cd /c/Users/user/Documents/Projects/smart-fridge-akyl
export GITHUB_TOKEN=ghp_your_token_here
bash scripts/create-and-push-gitbash.sh
```

The script removes the token from the saved `origin` URL after the first push.

### Option B — Git GUI / website only (no API token)

1. Create an **empty** repository named **`smart-fridge-akyl`** (no README): https://github.com/new  
2. Then in **Git Bash**, **CMD**, or **Git GUI → Remote**:

```bash
bash scripts/add-remote-and-push-gitbash.sh YOUR_USERNAME
```

(or add `origin` and `git push -u origin master` manually).

Use **Git Credential Manager** when Windows prompts for login.

### Option C — GitHub CLI (optional)

```powershell
$env:Path = "$env:ProgramFiles\GitHub CLI;$env:Path"
gh auth login -p https -h github.com -w
.\scripts\push-to-github.ps1
```

## Optional OCR

```powershell
pip install paddlepaddle paddleocr
```
