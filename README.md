# Smart Fridge (personal)

Local FastAPI backend, SQLite, PWA UI, background worker, optional PaddleOCR / local VLM.

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
mkdir data\scans data\uploads 2>nul
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8765
```

Open `http://<your-pc-lan-ip>:8765` on your phone.

## Configuration

Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` for notifications.

## Optional OCR

```bash
pip install paddlepaddle paddleocr
```
