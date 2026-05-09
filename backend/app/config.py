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
    #: Rotating log file for full diagnostics (readable by agents / tail on disk).
    log_file: Path = Path("./data/logs/smart-fridge.log")
    log_file_max_bytes: int = 5_000_000
    log_file_backup_count: int = 5
    #: Console prints WARNING+ for most loggers; use ``smart_fridge.summary`` for scan one-liners
    #: and ``smart_fridge.recognition`` for decode/consensus steps at INFO.
    console_log_level: str = "WARNING"
    #: Rotating log file verbosity (DEBUG keeps pipeline / barcode decode diagnostics).
    file_log_level: str = "DEBUG"

    scheduler_enabled: bool = True

    host: str = "0.0.0.0"
    port: int = 8765
    reload: bool = False
    http_protocol: Literal["auto", "h11", "httptools"] = "auto"

    #: When True (default), CLI creates or uses data/certs/dev.pem + dev.key for LAN HTTPS.
    dev_https: bool = True

    ssl_certfile: Path | None = None
    ssl_keyfile: Path | None = None

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_poll_seconds: int = 30

    expiring_warning_days: int = 2
    duplicate_scan_window_seconds: int = 120

    #: National Catalog gateway — matches OpenAPI ``servers`` + paths under ``/gwp`` ([docs](https://nationalcatalog.kz/gwp/docs)).
    national_catalog_base_url: str = "https://nationalcatalog.kz/gwp"
    national_catalog_api_key: str = ""
    #: Default: ``GET /portal/api/v2/products/{tin}`` where ``{tin}`` is the path segment (GTIN or NTIN) per [OpenAPI](https://nationalcatalog.kz/gwp/portal/v3/api-docs/portal).
    #: Template may include ``{base}``, ``{tin}``, ``{gtin14}``, ``{gtin}``, ``{ntin}``.
    national_catalog_gtin_url_template: str = "{base}/portal/api/v2/products/{tin}"
    #: Optional; if empty, NTIN lookups use ``national_catalog_gtin_url_template`` with ``{tin}`` = NTIN.
    national_catalog_ntin_url_template: str = ""
    national_catalog_timeout_seconds: float = 20.0
    #: Docs require ``X-API-KEY`` ([docs](https://nationalcatalog.kz/gwp/docs)).
    national_catalog_auth_scheme: Literal["bearer", "api_key", "none"] = "api_key"
    national_catalog_auth_header: str = "X-API-KEY"

    #: Directory containing ``index.html`` for the PWA (default: discover ``web/`` upward from this package).
    web_root: Path | None = None

    #: ОФД bulk export (Postman «АПИ для ОФД») — ``GET .../ofd/ofd/?from=&limit=`` on ``nct.gov.kz``.
    ofd_catalog_export_url: str = "https://nct.gov.kz/api/integration/ofd/ofd"
    ofd_catalog_page_limit: int = 1000
    ofd_catalog_http_timeout_seconds: float = 120.0
    ofd_catalog_sleep_seconds_between_pages: float = 0.0
    #: Used when ``smart-fridge import-ofd`` is run without ``--from-ts`` / ``--since-days``.
    ofd_import_default_since_days: float = 7.0


settings = Settings()
