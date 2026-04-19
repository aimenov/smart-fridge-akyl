from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMART_FRIDGE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/smart_fridge.db"
    scan_storage: Path = Path("./data/scans")
    uploads_storage: Path = Path("./data/uploads")

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_poll_seconds: int = 30

    vlm_endpoint: str = "http://127.0.0.1:1234/v1/chat/completions"

    expiring_warning_days: int = 2
    duplicate_scan_window_seconds: int = 120


settings = Settings()
