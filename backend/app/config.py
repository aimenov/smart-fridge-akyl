from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMART_FRIDGE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/smart_fridge.db"
    scan_storage: Path = Path("./data/scans")
    uploads_storage: Path = Path("./data/uploads")

    log_level: str = "INFO"
    json_logs: bool = False

    scheduler_enabled: bool = True

    host: str = "0.0.0.0"
    port: int = 8765
    reload: bool = False
    #: Passed to uvicorn as ``http=``. ``h11`` avoids httptools parse failures on some mobile
    #: browsers + large multipart POSTs (symptom: ``Invalid HTTP request received``).
    http_protocol: Literal["auto", "h11", "httptools"] = "h11"

    ssl_certfile: Path | None = None
    ssl_keyfile: Path | None = None

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_poll_seconds: int = 30

    vlm_enabled: bool = False
    vlm_endpoint: str = "http://127.0.0.1:1234/v1/chat/completions"
    vlm_confidence_below: float = 0.50
    #: Max chars of redacted JSON to log at INFO for VLM request/response previews.
    vlm_log_preview_chars: int = 4000

    expiring_warning_days: int = 2
    duplicate_scan_window_seconds: int = 120


settings = Settings()
