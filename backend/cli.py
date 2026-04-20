"""CLI entrypoint: `smart-fridge` runs the local API server."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _cmd_import_ofd(argv: list[str]) -> None:
    """Pull ОФД export pages into ``products_master`` (see Postman doc linked from NCT)."""
    import argparse
    import time

    from backend.app.config import settings
    from backend.app.database import SessionLocal, init_db
    from backend.app.modules.ofd_catalog_import import build_initial_export_url, import_ofd_catalog

    ap = argparse.ArgumentParser(prog="smart-fridge import-ofd")
    ap.add_argument(
        "--from-ts",
        type=int,
        default=None,
        help="Unix ``from`` watermark for the first request (default: now − since-days)",
    )
    ap.add_argument(
        "--since-days",
        type=float,
        default=None,
        help="Used when --from-ts is omitted (default: SMART_FRIDGE_OFD_IMPORT_DEFAULT_SINCE_DAYS)",
    )
    ap.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="Pagination safety cap; use 0 for no limit (millions of rows — be careful)",
    )
    ap.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional cap on rows processed across pages",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse but roll back DB changes each page",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=None,
        help="Seconds to sleep between HTTP pages (default: SMART_FRIDGE_OFD_CATALOG_SLEEP_SECONDS_BETWEEN_PAGES)",
    )
    args = ap.parse_args(argv)

    from_ts = args.from_ts
    if from_ts is None:
        days = (
            args.since_days
            if args.since_days is not None
            else settings.ofd_import_default_since_days
        )
        from_ts = int(time.time()) - int(days * 86400)

    max_pages = None if args.max_pages <= 0 else args.max_pages
    sleep_s = (
        args.sleep
        if args.sleep is not None
        else settings.ofd_catalog_sleep_seconds_between_pages
    )

    init_db()
    url = build_initial_export_url(from_timestamp=from_ts)
    print("smart-fridge import-ofd: first URL\n  " + url, file=sys.stderr)

    db = SessionLocal()
    try:
        stats = import_ofd_catalog(
            db,
            start_url=url,
            max_pages=max_pages,
            max_rows=args.max_rows,
            dry_run=args.dry_run,
            sleep_seconds=float(sleep_s),
        )
    finally:
        db.close()

    print(
        "smart-fridge import-ofd: done — pages={pages} rows_seen={rows_seen} "
        "upserts={upserts} skipped={skipped}".format(**stats),
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> None:
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "import-ofd":
        _cmd_import_ofd(argv[1:])
        return

    p = argparse.ArgumentParser(prog="smart-fridge", description="Smart Fridge local API + web UI")
    p.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=("run",),
        help="Command (default: run)",
    )
    p.add_argument("--host", default=None, help="Bind host (default: env SMART_FRIDGE_HOST or 0.0.0.0)")
    p.add_argument("--port", type=int, default=None, help="Port (default: env SMART_FRIDGE_PORT or 8765)")
    p.add_argument("--reload", action="store_true", help="Dev auto-reload")
    p.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    p.add_argument(
        "--ssl-certfile",
        default=None,
        help="PEM certificate (overrides auto dev certificate)",
    )
    p.add_argument("--ssl-keyfile", default=None, help="Private key (overrides auto dev certificate)")
    p.add_argument("--no-scheduler", action="store_true", help="Disable background jobs")
    p.add_argument(
        "--no-dev-https",
        action="store_true",
        help="Do not create or use data/certs/dev.pem — plain HTTP only (desktop / tests)",
    )

    args = p.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]

    if args.host is not None:
        os.environ["SMART_FRIDGE_HOST"] = args.host
    if args.port is not None:
        os.environ["SMART_FRIDGE_PORT"] = str(args.port)
    if args.reload:
        os.environ["SMART_FRIDGE_RELOAD"] = "true"
    if args.log_level:
        os.environ["SMART_FRIDGE_LOG_LEVEL"] = args.log_level
    if args.ssl_certfile:
        os.environ["SMART_FRIDGE_SSL_CERTFILE"] = args.ssl_certfile
    if args.ssl_keyfile:
        os.environ["SMART_FRIDGE_SSL_KEYFILE"] = args.ssl_keyfile
    if args.no_scheduler:
        os.environ["SMART_FRIDGE_SCHEDULER_ENABLED"] = "false"
    if args.no_dev_https:
        os.environ["SMART_FRIDGE_DEV_HTTPS"] = "false"

    from backend.app.config import settings

    import uvicorn

    cert_path = repo_root / "data" / "certs" / "dev.pem"
    key_path = repo_root / "data" / "certs" / "dev.key"

    ssl_cert = settings.ssl_certfile
    ssl_key = settings.ssl_keyfile
    ssl_kw: dict = {}

    if ssl_cert and ssl_key:
        ssl_kw["ssl_certfile"] = str(ssl_cert)
        ssl_kw["ssl_keyfile"] = str(ssl_key)
        print(
            "smart-fridge: TLS — using configured cert files. Open https://<this-PC-LAN-IP>:"
            f"{settings.port}/ on your phone.",
            file=sys.stderr,
        )
    elif ssl_cert or ssl_key:
        print(
            "smart-fridge: warning — provide both --ssl-certfile and --ssl-keyfile (or rely on dev HTTPS).",
            file=sys.stderr,
        )
        from backend.app.dev_tls import print_plain_http_warning

        print_plain_http_warning(settings.port)
    elif settings.dev_https:
        from backend.app.dev_tls import ensure_dev_tls_pair

        try:
            ensure_dev_tls_pair(cert_path, key_path)
            ssl_kw["ssl_certfile"] = str(cert_path.resolve())
            ssl_kw["ssl_keyfile"] = str(key_path.resolve())
            print(
                "smart-fridge: TLS — dev certificate ready. Open https://<this-PC-LAN-IP>:"
                f"{settings.port}/ (accept the security warning once).",
                file=sys.stderr,
            )
        except RuntimeError as exc:
            print(f"smart-fridge: could not enable HTTPS ({exc})", file=sys.stderr)
            print(
                "smart-fridge: falling back to HTTP — use --no-dev-https to silence this, "
                "or install OpenSSL / Git for Windows.",
                file=sys.stderr,
            )
            from backend.app.dev_tls import print_plain_http_warning

            print_plain_http_warning(settings.port)
    else:
        from backend.app.dev_tls import print_plain_http_warning

        print_plain_http_warning(settings.port)

    scheme = "https" if ssl_kw else "http"
    print(
        f"smart-fridge: {scheme}://{settings.host}:{settings.port}/ "
        f"(reload={settings.reload}, scheduler={settings.scheduler_enabled})",
        file=sys.stderr,
    )

    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
        http=settings.http_protocol,
        **ssl_kw,
    )


if __name__ == "__main__":
    main()
